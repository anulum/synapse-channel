# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed Claude Code Edit/Write claim guard
"""Evaluate Claude Code file mutations against live Synapse claims.

Claude Code sends a ``PreToolUse`` JSON object on stdin before an ``Edit`` or
``Write`` call. This module validates that object, canonicalises the target inside
its Git worktree, requests the authoritative hub snapshot, and permits the call
only when the configured identity owns every live claim covering that exact file.

The guard deliberately returns no decision on success, preserving Claude Code's
ordinary permission flow. A malformed event, an unavailable hub, or an absent,
stale, wrong-branch, or competing claim produces a structured denial. Bash and
other tools are outside this bounded integration; the generated hook recipe
matches only ``Edit|Write``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.claude_claim_state import StateSnapshotError, fetch_state_snapshot
from synapse_channel.core.errors import SynapseError
from synapse_channel.git.claim_coverage import (
    EDITABLE_STATUSES as EDITABLE_STATUSES,
)
from synapse_channel.git.claim_coverage import (
    ClaimCoverageError,
    decide_claim_coverage,
)
from synapse_channel.git.claim_coverage import (
    claim_path_covers as claim_path_covers,
)
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner

SUPPORTED_TOOLS = frozenset({"Edit", "Write"})
"""Claude Code tools covered by this guard."""

StateFetcher = Callable[..., Awaitable[dict[str, Any]]]


class ClaimGuardError(SynapseError, RuntimeError):
    """A controlled verification failure that must deny the tool call."""

    code = "claude_claim_guard"


@dataclass(frozen=True)
class HookRequest:
    """Validated subset of one Claude Code ``PreToolUse`` event."""

    session_id: str
    tool_use_id: str
    tool_name: str
    cwd: Path
    file_path: Path


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


def _required_string(data: Mapping[str, Any], key: str, *, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ClaimGuardError(f"Claude hook input needs a non-empty {location}.{key} string.")
    return value.strip()


def parse_hook_request(raw: str) -> HookRequest:
    """Parse and validate one Claude Code ``PreToolUse`` JSON object.

    Parameters
    ----------
    raw : str
        JSON text received from the hook's stdin.

    Returns
    -------
    HookRequest
        The validated fields used for claim verification.

    Raises
    ------
    ClaimGuardError
        If the JSON or its event/tool/path fields are outside the supported shape.
    """
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClaimGuardError("Claude hook input is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ClaimGuardError("Claude hook input must be a JSON object.")

    event = _required_string(decoded, "hook_event_name", location="input")
    if event != "PreToolUse":
        raise ClaimGuardError("Claude claim guard accepts only PreToolUse events.")
    tool_name = _required_string(decoded, "tool_name", location="input")
    if tool_name not in SUPPORTED_TOOLS:
        raise ClaimGuardError("Claude claim guard accepts only Edit or Write calls.")

    tool_input = decoded.get("tool_input")
    if not isinstance(tool_input, dict):
        raise ClaimGuardError("Claude hook input needs a tool_input object.")
    file_path_text = _required_string(tool_input, "file_path", location="tool_input")
    cwd_text = _required_string(decoded, "cwd", location="input")
    file_path = Path(file_path_text)
    cwd = Path(cwd_text)
    if not file_path.is_absolute():
        raise ClaimGuardError("Claude Edit/Write file_path must be absolute.")
    if not cwd.is_absolute():
        raise ClaimGuardError("Claude hook cwd must be absolute.")

    return HookRequest(
        session_id=_required_string(decoded, "session_id", location="input"),
        tool_use_id=_required_string(decoded, "tool_use_id", location="input"),
        tool_name=tool_name,
        cwd=cwd,
        file_path=file_path,
    )


def _existing_anchor(path: Path) -> Path:
    """Return the nearest existing parent used to resolve a target's Git worktree."""
    current = path if path.is_dir() else path.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def resolve_repository_target(
    request: HookRequest, *, runner: GitRunner = _default_git_runner
) -> RepositoryTarget:
    """Canonicalise the target and resolve its Git root and current branch.

    Resolution begins from the target's nearest existing parent, not the session
    cwd, so an explicitly added repository is checked against its own claim scope.
    A symlink that escapes the reported worktree is denied after canonicalisation.
    """
    try:
        target = request.file_path.resolve(strict=False)
        if target.is_dir():
            raise ClaimGuardError("Claude Edit/Write target must be a file path.")
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
    except ClaimGuardError:
        raise
    except (GitError, OSError, RuntimeError) as exc:
        raise ClaimGuardError("Claude target is not inside a readable Git worktree.") from exc

    lines = output.splitlines()
    if len(lines) != 2 or not lines[0].strip() or not lines[1].strip():
        raise ClaimGuardError("Git returned an invalid worktree or branch context.")
    try:
        root = Path(lines[0]).resolve(strict=True)
        relative = target.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ClaimGuardError("Claude target escapes the claimed Git worktree.") from exc
    return RepositoryTarget(root=root, branch=lines[1].strip(), relative_path=relative.as_posix())


def decide_from_snapshot(
    snapshot: Mapping[str, Any], *, identity: str, target: RepositoryTarget
) -> GuardVerdict:
    """Decide whether ``identity`` unambiguously owns ``target`` in a hub snapshot."""
    try:
        coverage = decide_claim_coverage(
            snapshot,
            identity=identity,
            root=target.root,
            branch=target.branch,
            paths=(target.relative_path,),
        )
    except ClaimCoverageError as exc:
        raise ClaimGuardError(str(exc)) from exc
    if coverage.missing_paths:
        return GuardVerdict(
            False,
            f"Synapse claim required before {target.relative_path!r} can be edited.",
        )
    if coverage.ownership_mismatch_paths:
        return GuardVerdict(False, "Synapse claim ownership is missing or ambiguous for this file.")
    if coverage.non_editable_paths:
        return GuardVerdict(False, "The covering Synapse claim is not in an editable task state.")
    return GuardVerdict(True)


def _requester_name(request: HookRequest, identity: str) -> str:
    """Return one of sixteen stable query identities for this configured owner.

    A fresh identity per tool call would grow the hub's trust-on-first-use pin
    store without bound. A small deterministic pool supports parallel Claude tool
    calls while capping that state at sixteen names per configured claim owner.
    """
    owner = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    call = f"{request.session_id}\0{request.tool_use_id}".encode()
    slot = int(hashlib.sha256(call).hexdigest()[:2], 16) % 16
    return f"claim-hook/{owner}-{slot:x}"


async def evaluate_hook_event(
    raw: str,
    *,
    identity: str,
    uri: str,
    token: str | None,
    timeout: float,
    state_fetcher: StateFetcher = fetch_state_snapshot,
    git_runner: GitRunner = _default_git_runner,
) -> GuardVerdict:
    """Evaluate one raw Claude event against the authoritative live claims."""
    try:
        request = parse_hook_request(raw)
        target = resolve_repository_target(request, runner=git_runner)
        snapshot = await state_fetcher(
            uri=uri,
            requester=_requester_name(request, identity),
            token=token,
            timeout=timeout,
        )
        return decide_from_snapshot(snapshot, identity=identity, target=target)
    except (ClaimGuardError, StateSnapshotError) as exc:
        return GuardVerdict(False, str(exc))


def denial_payload(reason: str) -> dict[str, Any]:
    """Return the current structured Claude Code ``PreToolUse`` denial shape."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
