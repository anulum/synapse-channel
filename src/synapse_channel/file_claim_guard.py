# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral file-mutation claim decisions
"""Resolve provider file mutations and require authoritative Synapse claims."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.claim_state import ClaimStateError, fetch_state_snapshot
from synapse_channel.core.errors import SynapseError
from synapse_channel.git.claim_coverage import ClaimCoverageError, decide_claim_coverage
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner
from synapse_channel.path_resolution import resolve_weakly_fail_closed

StateFetcher = Callable[..., Awaitable[dict[str, Any]]]


class FileClaimGuardError(SynapseError, RuntimeError):
    """A controlled verification failure that must deny the mutation."""

    code = "file_claim_guard"


@dataclass(frozen=True)
class MutationRequest:
    """Provider-neutral identity and path data for one mutation tool call.

    Attributes
    ----------
    session_id : str
        Provider session identifier used only to bound state-query identities.
    tool_use_id : str
        Provider call identifier used only to bound state-query identities.
    cwd : pathlib.Path
        Absolute provider working directory.
    file_paths : tuple[pathlib.Path, ...]
        Relative or absolute mutation targets from the provider payload.
    allow_semantic_source : bool
        Whether this precise edit tool may provisionally use an unambiguous
        symbol claim for the target's physical source.
    """

    session_id: str
    tool_use_id: str
    cwd: Path
    file_paths: tuple[Path, ...]
    allow_semantic_source: bool = False


@dataclass(frozen=True)
class RepositoryTarget:
    """Canonical Git context and repository-relative mutation target."""

    root: Path
    branch: str
    relative_path: str


@dataclass(frozen=True)
class GuardVerdict:
    """Whether the tool call may continue and the denial reason when it may not."""

    allowed: bool
    reason: str = ""


def _existing_anchor(path: Path) -> Path:
    current = path if path.is_dir() else path.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _absolute_target(path: Path, cwd: Path) -> Path:
    if not cwd.is_absolute():
        raise FileClaimGuardError("Provider hook cwd must be absolute.")
    candidate = path if path.is_absolute() else cwd / path
    try:
        return resolve_weakly_fail_closed(candidate)
    except (OSError, RuntimeError, ValueError) as exc:
        raise FileClaimGuardError("Provider mutation target is not a valid path.") from exc


def resolve_repository_targets(
    request: MutationRequest,
    *,
    provider: str,
    runner: GitRunner = _default_git_runner,
) -> tuple[RepositoryTarget, ...]:
    """Canonicalise all mutation paths and resolve their Git roots and branches."""
    if not request.file_paths:
        raise FileClaimGuardError(f"{provider} hook input contains no mutation paths.")

    targets: list[RepositoryTarget] = []
    seen: set[tuple[Path, str, str]] = set()
    for path in request.file_paths:
        target = _absolute_target(path, request.cwd)
        if target.is_dir():
            raise FileClaimGuardError(f"{provider} mutation target must be a file path.")
        try:
            output = runner(
                [
                    "-C",
                    str(_existing_anchor(target)),
                    "rev-parse",
                    "--show-toplevel",
                    "--abbrev-ref",
                    "HEAD",
                ]
            )
        except (GitError, OSError, RuntimeError) as exc:
            raise FileClaimGuardError(
                f"{provider} target is not inside a readable Git worktree."
            ) from exc

        lines = output.splitlines()
        if len(lines) != 2 or not lines[0].strip() or not lines[1].strip():
            raise FileClaimGuardError("Git returned an invalid worktree or branch context.")
        try:
            root = Path(lines[0]).resolve(strict=True)
            relative = target.relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError) as exc:
            raise FileClaimGuardError(
                f"{provider} target escapes the claimed Git worktree."
            ) from exc
        key = (root, lines[1].strip(), relative)
        if key in seen:
            continue
        seen.add(key)
        targets.append(RepositoryTarget(*key))
    return tuple(targets)


def _path_list(paths: tuple[str, ...]) -> str:
    return ", ".join(repr(path) for path in paths)


def decide_targets_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    identity: str,
    targets: tuple[RepositoryTarget, ...],
    allow_semantic_source: bool = False,
) -> GuardVerdict:
    """Decide whether ``identity`` unambiguously owns every mutation target.

    Parameters
    ----------
    snapshot : Mapping[str, Any]
        Authoritative hub state containing active claims.
    identity : str
        Exact provider identity performing the mutation.
    targets : tuple[RepositoryTarget, ...]
        Canonical worktree, branch, and relative path targets.
    allow_semantic_source : bool, optional
        Permit one-owner editable semantic claims to cover their physical
        source provisionally. Whole-file writers leave this disabled.

    Returns
    -------
    GuardVerdict
        Allow or bounded denial reason for all targets.

    Raises
    ------
    FileClaimGuardError
        If authoritative claim state is malformed.
    """
    grouped: dict[tuple[Path, str], list[str]] = {}
    for target in targets:
        grouped.setdefault((target.root, target.branch), []).append(target.relative_path)

    missing: list[str] = []
    ownership: list[str] = []
    non_editable: list[str] = []
    try:
        for (root, branch), paths in grouped.items():
            coverage = decide_claim_coverage(
                snapshot,
                identity=identity,
                root=root,
                branch=branch,
                paths=paths,
                allow_semantic_source=allow_semantic_source,
            )
            missing.extend(coverage.missing_paths)
            ownership.extend(coverage.ownership_mismatch_paths)
            non_editable.extend(coverage.non_editable_paths)
    except ClaimCoverageError as exc:
        raise FileClaimGuardError(str(exc)) from exc

    if missing:
        missing_paths = tuple(dict.fromkeys(missing))
        noun = "claim" if len(missing_paths) == 1 else "claims"
        return GuardVerdict(
            False,
            f"Synapse {noun} required before {_path_list(missing_paths)} can be edited.",
        )
    if ownership:
        return GuardVerdict(
            False,
            "Synapse claim ownership is missing or ambiguous for: "
            f"{_path_list(tuple(dict.fromkeys(ownership)))}.",
        )
    if non_editable:
        return GuardVerdict(
            False,
            "The covering Synapse claim is not in an editable task state for: "
            f"{_path_list(tuple(dict.fromkeys(non_editable)))}.",
        )
    return GuardVerdict(True)


def requester_name(request: MutationRequest, identity: str) -> str:
    """Return one of sixteen stable state-query identities for this claim owner."""
    owner = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    call = f"{request.session_id}\0{request.tool_use_id}".encode()
    slot = int(hashlib.sha256(call).hexdigest()[:2], 16) % 16
    return f"claim-hook/{owner}-{slot:x}"


async def evaluate_mutation_request(
    request: MutationRequest,
    *,
    provider: str,
    identity: str,
    uri: str,
    token: str | None,
    timeout: float,
    state_fetcher: StateFetcher = fetch_state_snapshot,
    git_runner: GitRunner = _default_git_runner,
) -> GuardVerdict:
    """Evaluate one provider mutation against the authoritative live claims."""
    try:
        targets = resolve_repository_targets(request, provider=provider, runner=git_runner)
        snapshot = await state_fetcher(
            uri=uri,
            requester=requester_name(request, identity),
            token=token,
            timeout=timeout,
        )
        return decide_targets_from_snapshot(
            snapshot,
            identity=identity,
            targets=targets,
            allow_semantic_source=request.allow_semantic_source,
        )
    except (FileClaimGuardError, ClaimStateError) as exc:
        return GuardVerdict(False, str(exc))


def denial_payload(reason: str) -> dict[str, Any]:
    """Return the structured ``PreToolUse`` denial accepted by all three providers."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
