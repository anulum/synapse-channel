# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed Codex mutation claim guard
"""Adapt Codex ``PreToolUse`` apply-patch and Bash events to live claims."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from synapse_channel.apply_patch_paths import (
    ApplyPatchPathError,
)
from synapse_channel.apply_patch_paths import (
    parse_apply_patch_paths as _parse_apply_patch_paths,
)
from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.file_claim_guard import (
    FileClaimGuardError,
    GuardVerdict,
    MutationRequest,
    StateFetcher,
)
from synapse_channel.git.gitclaim import GitRunner, _default_git_runner
from synapse_channel.shell_claim_guard import (
    ProviderClaimRequest,
    ShellRequest,
    evaluate_provider_request,
)

SUPPORTED_TOOL = "apply_patch"
SUPPORTED_TOOLS = frozenset({SUPPORTED_TOOL, "Bash"})


class CodexClaimGuardError(FileClaimGuardError):
    """Codex hook input is malformed or outside the guarded surface."""

    code = "codex_claim_guard"


def parse_apply_patch_paths(command: str) -> tuple[Path, ...]:
    """Extract Codex patch targets while preserving the provider error contract."""
    try:
        return _parse_apply_patch_paths(command)
    except ApplyPatchPathError as exc:
        raise CodexClaimGuardError(f"Codex {exc}") from exc


def _required_string(data: Mapping[str, Any], key: str, *, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CodexClaimGuardError(f"Codex hook input needs a non-empty {location}.{key} string.")
    return value.strip()


def parse_hook_request(raw: str) -> ProviderClaimRequest:
    """Parse and validate one Codex ``PreToolUse`` mutation event."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CodexClaimGuardError("Codex hook input is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise CodexClaimGuardError("Codex hook input must be a JSON object.")
    if _required_string(decoded, "hook_event_name", location="input") != "PreToolUse":
        raise CodexClaimGuardError("Codex claim guard accepts only PreToolUse events.")
    tool_name = _required_string(decoded, "tool_name", location="input")
    if tool_name not in SUPPORTED_TOOLS:
        raise CodexClaimGuardError("Codex claim guard accepts only apply_patch or Bash calls.")
    tool_input = decoded.get("tool_input")
    if not isinstance(tool_input, dict):
        raise CodexClaimGuardError("Codex hook input needs a tool_input object.")
    cwd = Path(_required_string(decoded, "cwd", location="input"))
    if not cwd.is_absolute():
        raise CodexClaimGuardError("Codex hook cwd must be absolute.")
    command = _required_string(tool_input, "command", location="tool_input")
    session_id = _required_string(decoded, "session_id", location="input")
    tool_use_id = _required_string(decoded, "tool_use_id", location="input")
    if tool_name == "Bash":
        return ShellRequest(session_id=session_id, tool_use_id=tool_use_id, cwd=cwd)
    file_paths = parse_apply_patch_paths(command)
    return MutationRequest(
        session_id=session_id,
        tool_use_id=tool_use_id,
        cwd=cwd,
        file_paths=file_paths,
    )


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
    """Evaluate one raw Codex event against the authoritative live claims."""
    try:
        request = parse_hook_request(raw)
    except CodexClaimGuardError as exc:
        return GuardVerdict(False, str(exc))
    return await evaluate_provider_request(
        request,
        provider="Codex",
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=state_fetcher,
        git_runner=git_runner,
    )
