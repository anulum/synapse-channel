# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral shell claim decisions
"""Require an exclusive whole-worktree claim before intercepted shell calls.

Arbitrary shell text has no trustworthy declared write set. This guard therefore
does not attempt command parsing: an intercepted shell call is allowed only when
the configured identity owns the editable claim for the complete current Git
worktree and no other active claim exists there for a competing owner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.claim_state import ClaimStateError, fetch_state_snapshot
from synapse_channel.file_claim_guard import (
    FileClaimGuardError,
    GuardVerdict,
    MutationRequest,
    StateFetcher,
    evaluate_mutation_request,
    requester_name,
)
from synapse_channel.git.claim_coverage import EDITABLE_STATUSES, ClaimCoverageError
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner
from synapse_channel.path_resolution import resolve_weakly_fail_closed


class ShellClaimGuardError(FileClaimGuardError):
    """A shell claim cannot be verified safely."""

    code = "shell_claim_guard"


@dataclass(frozen=True)
class ShellRequest:
    """Provider-neutral metadata for one intercepted shell call."""

    session_id: str
    tool_use_id: str
    cwd: Path


@dataclass(frozen=True)
class ShellRepository:
    """Canonical Git context containing an intercepted shell call."""

    root: Path
    branch: str


ProviderClaimRequest = MutationRequest | ShellRequest


def resolve_shell_repository(
    request: ShellRequest,
    *,
    provider: str,
    runner: GitRunner = _default_git_runner,
) -> ShellRepository:
    """Resolve the shell cwd to one canonical worktree and branch."""
    if not request.cwd.is_absolute():
        raise ShellClaimGuardError(f"{provider} shell hook cwd must be absolute.")
    try:
        cwd = resolve_weakly_fail_closed(request.cwd)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ShellClaimGuardError(f"{provider} shell cwd is not a valid path.") from exc
    if not cwd.is_dir():
        raise ShellClaimGuardError(f"{provider} shell cwd must be a readable directory.")
    try:
        output = runner(["-C", str(cwd), "rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"])
    except (GitError, OSError, RuntimeError) as exc:
        raise ShellClaimGuardError(
            f"{provider} shell cwd is not inside a readable Git worktree."
        ) from exc
    lines = output.splitlines()
    if len(lines) != 2 or not lines[0].strip() or not lines[1].strip():
        raise ShellClaimGuardError("Git returned an invalid worktree or branch context.")
    try:
        root = Path(lines[0]).resolve(strict=True)
        cwd.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ShellClaimGuardError(f"{provider} shell cwd escapes its Git worktree.") from exc
    return ShellRepository(root=root, branch=lines[1].strip())


def _repository_claims(
    snapshot: Mapping[str, Any], repository: ShellRepository
) -> tuple[Mapping[str, Any], ...]:
    claims = snapshot.get("active_claims")
    if not isinstance(claims, list):
        raise ClaimCoverageError("Hub state snapshot has no valid active_claims list.")
    try:
        canonical_root = resolve_weakly_fail_closed(repository.root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ClaimCoverageError("Shell claim worktree is invalid.") from exc

    matching: list[Mapping[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            raise ClaimCoverageError("Hub state snapshot contains a malformed claim.")
        worktree = claim.get("worktree")
        if worktree is None or (isinstance(worktree, str) and not worktree.strip()):
            continue
        if not isinstance(worktree, str):
            raise ClaimCoverageError("Hub returned an invalid claim worktree.")
        try:
            claimed_root = resolve_weakly_fail_closed(Path(worktree))
        except (OSError, RuntimeError, ValueError) as exc:
            raise ClaimCoverageError("Hub returned an invalid claim worktree.") from exc
        git = claim.get("git")
        if claimed_root != canonical_root:
            continue
        if not isinstance(git, dict) or not isinstance(git.get("branch"), str):
            raise ClaimCoverageError("Hub returned malformed claim Git context.")
        paths = claim.get("paths")
        owner = claim.get("owner")
        status = claim.get("status")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise ClaimCoverageError("Hub returned malformed claim paths.")
        if not isinstance(owner, str) or not owner:
            raise ClaimCoverageError("Hub returned a malformed claim owner.")
        if not isinstance(status, str):
            raise ClaimCoverageError("Hub returned a malformed claim status.")
        matching.append(claim)
    return tuple(matching)


def decide_shell_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    identity: str,
    provider: str,
    repository: ShellRepository,
) -> GuardVerdict:
    """Require one editable whole-worktree claim and no competing owner."""
    try:
        claims = _repository_claims(snapshot, repository)
    except ClaimCoverageError as exc:
        raise ShellClaimGuardError(str(exc)) from exc
    branch_claims = tuple(claim for claim in claims if claim["git"]["branch"] == repository.branch)
    whole = tuple(claim for claim in branch_claims if not claim["paths"])
    if not whole:
        return GuardVerdict(
            False,
            f"Synapse whole-worktree claim required before {provider} shell execution.",
        )
    if any(claim["owner"] != identity for claim in claims):
        return GuardVerdict(
            False,
            "Synapse whole-worktree claim ownership is missing or ambiguous for "
            f"{provider} shell execution.",
        )
    if any(claim["status"] not in EDITABLE_STATUSES for claim in claims):
        return GuardVerdict(
            False,
            f"The Synapse whole-worktree claim is not editable for {provider} shell execution.",
        )
    return GuardVerdict(True)


async def evaluate_shell_request(
    request: ShellRequest,
    *,
    provider: str,
    identity: str,
    uri: str,
    token: str | None,
    timeout: float,
    state_fetcher: StateFetcher = fetch_state_snapshot,
    git_runner: GitRunner = _default_git_runner,
) -> GuardVerdict:
    """Evaluate one shell call against the authoritative live claim state."""
    try:
        repository = resolve_shell_repository(request, provider=provider, runner=git_runner)
        snapshot = await state_fetcher(
            uri=uri,
            requester=requester_name(request, identity),
            token=token,
            timeout=timeout,
        )
        return decide_shell_from_snapshot(
            snapshot,
            identity=identity,
            provider=provider,
            repository=repository,
        )
    except (ShellClaimGuardError, ClaimStateError) as exc:
        return GuardVerdict(False, str(exc))


async def evaluate_provider_request(
    request: ProviderClaimRequest,
    *,
    provider: str,
    identity: str,
    uri: str,
    token: str | None,
    timeout: float,
    state_fetcher: StateFetcher = fetch_state_snapshot,
    git_runner: GitRunner = _default_git_runner,
) -> GuardVerdict:
    """Dispatch a provider request to its file or shell claim policy."""
    if isinstance(request, ShellRequest):
        return await evaluate_shell_request(
            request,
            provider=provider,
            identity=identity,
            uri=uri,
            token=token,
            timeout=timeout,
            state_fetcher=state_fetcher,
            git_runner=git_runner,
        )
    return await evaluate_mutation_request(
        request,
        provider=provider,
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=state_fetcher,
        git_runner=git_runner,
    )
