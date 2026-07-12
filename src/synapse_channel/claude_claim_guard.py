# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed Claude Code Edit/Write claim guard
"""Adapt Claude Code ``PreToolUse`` file events to live Synapse claims.

The released Claude-facing types and functions remain import-compatible. Shared
path resolution and multi-provider claim decisions live in
:mod:`synapse_channel.file_claim_guard` so Codex and Kimi cannot drift onto weaker
ownership semantics.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.claude_claim_state import StateSnapshotError, fetch_state_snapshot
from synapse_channel.file_claim_guard import (
    FileClaimGuardError,
    GuardVerdict,
    MutationRequest,
    RepositoryTarget,
    StateFetcher,
    decide_targets_from_snapshot,
    denial_payload,
    evaluate_mutation_request,
    resolve_repository_targets,
)
from synapse_channel.git.claim_coverage import EDITABLE_STATUSES as EDITABLE_STATUSES
from synapse_channel.git.claim_coverage import claim_path_covers as claim_path_covers
from synapse_channel.git.gitclaim import GitRunner, _default_git_runner

SUPPORTED_TOOLS = frozenset({"Edit", "Write"})
"""Claude Code tools covered by this guard."""


class ClaimGuardError(FileClaimGuardError):
    """A controlled verification failure that must deny the Claude tool call."""

    code = "claude_claim_guard"


@dataclass(frozen=True)
class HookRequest:
    """Validated subset of one Claude Code ``PreToolUse`` event."""

    session_id: str
    tool_use_id: str
    tool_name: str
    cwd: Path
    file_path: Path


def _required_string(data: Mapping[str, Any], key: str, *, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ClaimGuardError(f"Claude hook input needs a non-empty {location}.{key} string.")
    return value.strip()


def parse_hook_request(raw: str) -> HookRequest:
    """Parse and validate one Claude Code ``PreToolUse`` Edit/Write event."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClaimGuardError("Claude hook input is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ClaimGuardError("Claude hook input must be a JSON object.")
    if _required_string(decoded, "hook_event_name", location="input") != "PreToolUse":
        raise ClaimGuardError("Claude claim guard accepts only PreToolUse events.")
    tool_name = _required_string(decoded, "tool_name", location="input")
    if tool_name not in SUPPORTED_TOOLS:
        raise ClaimGuardError("Claude claim guard accepts only Edit or Write calls.")
    tool_input = decoded.get("tool_input")
    if not isinstance(tool_input, dict):
        raise ClaimGuardError("Claude hook input needs a tool_input object.")
    file_path = Path(_required_string(tool_input, "file_path", location="tool_input"))
    cwd = Path(_required_string(decoded, "cwd", location="input"))
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


def _mutation_request(request: HookRequest) -> MutationRequest:
    return MutationRequest(
        session_id=request.session_id,
        tool_use_id=request.tool_use_id,
        cwd=request.cwd,
        file_paths=(request.file_path,),
    )


def resolve_repository_target(
    request: HookRequest, *, runner: GitRunner = _default_git_runner
) -> RepositoryTarget:
    """Canonicalise the Claude target and resolve its Git root and branch."""
    try:
        return resolve_repository_targets(
            _mutation_request(request), provider="Claude", runner=runner
        )[0]
    except FileClaimGuardError as exc:
        raise ClaimGuardError(str(exc)) from exc


def decide_from_snapshot(
    snapshot: Mapping[str, Any], *, identity: str, target: RepositoryTarget
) -> GuardVerdict:
    """Decide whether ``identity`` unambiguously owns one Claude target."""
    try:
        return decide_targets_from_snapshot(snapshot, identity=identity, targets=(target,))
    except FileClaimGuardError as exc:
        raise ClaimGuardError(str(exc)) from exc


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
    except ClaimGuardError as exc:
        return GuardVerdict(False, str(exc))
    return await evaluate_mutation_request(
        _mutation_request(request),
        provider="Claude",
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=state_fetcher,
        git_runner=git_runner,
    )


__all__ = [
    "ClaimGuardError",
    "EDITABLE_STATUSES",
    "GuardVerdict",
    "HookRequest",
    "RepositoryTarget",
    "SUPPORTED_TOOLS",
    "StateFetcher",
    "StateSnapshotError",
    "claim_path_covers",
    "decide_from_snapshot",
    "denial_payload",
    "evaluate_hook_event",
    "parse_hook_request",
    "resolve_repository_target",
]
