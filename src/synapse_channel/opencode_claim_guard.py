# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed OpenCode native mutation claim guard
"""Adapt OpenCode ``tool.execute.before`` events to live Synapse claims."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from synapse_channel.apply_patch_paths import ApplyPatchPathError, parse_apply_patch_paths
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

HOOK_EVENT = "tool.execute.before"
SUPPORTED_TOOLS = frozenset({"edit", "write", "apply_patch", "bash"})
MAX_HOOK_EVENT_BYTES = 1_048_576
"""Maximum UTF-8 size accepted for one native OpenCode hook event."""


class OpenCodeClaimGuardError(FileClaimGuardError):
    """OpenCode hook input is malformed or outside the guarded surface."""

    code = "opencode_claim_guard"


def _required_string(data: Mapping[str, Any], key: str, *, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OpenCodeClaimGuardError(
            f"OpenCode hook input needs a non-empty {location}.{key} string."
        )
    return value.strip()


def _required_exact_path(data: Mapping[str, Any], key: str, *, location: str) -> str:
    """Return a non-empty filesystem value without changing its semantics."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OpenCodeClaimGuardError(
            f"OpenCode hook input needs a non-empty {location}.{key} string."
        )
    if value != value.strip():
        raise OpenCodeClaimGuardError(
            f"OpenCode hook input {location}.{key} must not have surrounding whitespace."
        )
    return value


def _mutation_paths(tool: str, tool_input: Mapping[str, Any]) -> tuple[Path, ...]:
    if tool in {"edit", "write"}:
        return (Path(_required_exact_path(tool_input, "filePath", location="tool_input")),)
    try:
        patch = tool_input.get("patchText")
        if not isinstance(patch, str) or not patch.strip():
            raise OpenCodeClaimGuardError(
                "OpenCode hook input needs a non-empty tool_input.patchText string."
            )
        return parse_apply_patch_paths(patch)
    except ApplyPatchPathError as exc:
        raise OpenCodeClaimGuardError(f"OpenCode {exc}") from exc


def parse_hook_request(raw: str) -> ProviderClaimRequest:
    """Parse one OpenCode native plugin event into a provider-neutral request."""
    if not isinstance(raw, str):
        raise OpenCodeClaimGuardError("OpenCode hook input must be UTF-8 text.")
    if len(raw) > MAX_HOOK_EVENT_BYTES or len(raw.encode("utf-8")) > MAX_HOOK_EVENT_BYTES:
        raise OpenCodeClaimGuardError(
            f"OpenCode hook input exceeds the {MAX_HOOK_EVENT_BYTES}-byte limit."
        )
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise OpenCodeClaimGuardError("OpenCode hook input is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise OpenCodeClaimGuardError("OpenCode hook input must be a JSON object.")
    if _required_string(decoded, "hook_event_name", location="input") != HOOK_EVENT:
        raise OpenCodeClaimGuardError(
            "OpenCode claim guard accepts only tool.execute.before events."
        )
    tool = _required_string(decoded, "tool_name", location="input")
    if tool not in SUPPORTED_TOOLS:
        raise OpenCodeClaimGuardError(
            "OpenCode claim guard accepts only edit, write, apply_patch, or bash calls."
        )
    tool_input = decoded.get("tool_input")
    if not isinstance(tool_input, dict):
        raise OpenCodeClaimGuardError("OpenCode hook input needs a tool_input object.")
    cwd = Path(_required_exact_path(decoded, "cwd", location="input"))
    if not cwd.is_absolute():
        raise OpenCodeClaimGuardError("OpenCode hook cwd must be absolute.")
    session_id = _required_string(decoded, "session_id", location="input")
    tool_use_id = _required_string(decoded, "tool_use_id", location="input")
    if tool == "bash":
        return ShellRequest(session_id=session_id, tool_use_id=tool_use_id, cwd=cwd)
    return MutationRequest(
        session_id=session_id,
        tool_use_id=tool_use_id,
        cwd=cwd,
        file_paths=_mutation_paths(tool, tool_input),
        allow_semantic_source=tool == "edit",
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
    """Evaluate one raw OpenCode event against authoritative live claims."""
    try:
        request = parse_hook_request(raw)
    except OpenCodeClaimGuardError as exc:
        return GuardVerdict(False, str(exc))
    return await evaluate_provider_request(
        request,
        provider="OpenCode",
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=state_fetcher,
        git_runner=git_runner,
    )
