# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed Grok PreToolUse file-mutation claim guard
"""Adapt Grok PreToolUse file events to authoritative Synapse claims.

Grok 0.2.93 sends camelCase hook JSON on stdin and blocks only when a
PreToolUse hook returns a top-level decision=deny object. Native file edits use
search_replace; compatibility aliases and the older write spelling are accepted
because Grok maps those matcher names onto its native tool surface.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.file_claim_guard import (
    FileClaimGuardError,
    GuardVerdict,
    MutationRequest,
    StateFetcher,
    evaluate_mutation_request,
)
from synapse_channel.git.gitclaim import GitRunner, _default_git_runner

__all__ = [
    "GrokClaimGuardError",
    "SUPPORTED_TOOLS",
    "denial_payload",
    "evaluate_hook_event",
    "parse_hook_request",
]

SUPPORTED_TOOLS = frozenset({"search_replace", "write", "Edit", "Write", "MultiEdit"})
"""Grok's native file editor plus supported historical and compatibility aliases."""

_PATH_KEYS = ("path", "file_path", "target_file", "file")
_SESSION_KEYS = ("sessionId", "session_id")
_TOOL_ID_KEYS = ("toolUseId", "tool_use_id", "tool_call_id")
_EVENT_KEYS = ("hookEventName", "hook_event_name")
_TOOL_NAME_KEYS = ("toolName", "tool_name")
_CWD_KEYS = ("cwd", "workspaceRoot", "workspace_root")
_TOOL_INPUT_KEYS = ("toolInput", "tool_input")
_PRE_TOOL_EVENTS = frozenset({"PreToolUse", "pre_tool_use", "preToolUse"})


class GrokClaimGuardError(FileClaimGuardError):
    """Grok hook input is malformed or outside the guarded surface."""

    code = "grok_claim_guard"


def _first_string(data: Mapping[str, Any], keys: tuple[str, ...], *, location: str) -> str:
    """Return the first non-empty string among the candidate keys."""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise GrokClaimGuardError(
        f"Grok hook input needs a non-empty {location}.{'/'.join(keys)} string."
    )


def _tool_input(decoded: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return Grok's camelCase or compatibility snake_case tool input."""
    for key in _TOOL_INPUT_KEYS:
        candidate = decoded.get(key)
        if isinstance(candidate, dict):
            return candidate
    raise GrokClaimGuardError("Grok hook input needs a toolInput object.")


def parse_hook_request(raw: str) -> MutationRequest:
    """Parse one Grok PreToolUse file-mutation event."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GrokClaimGuardError("Grok hook input is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise GrokClaimGuardError("Grok hook input must be a JSON object.")

    event = _first_string(decoded, _EVENT_KEYS, location="input")
    if event not in _PRE_TOOL_EVENTS:
        raise GrokClaimGuardError("Grok claim guard accepts only PreToolUse events.")
    tool_name = _first_string(decoded, _TOOL_NAME_KEYS, location="input")
    if tool_name not in SUPPORTED_TOOLS:
        raise GrokClaimGuardError(
            "Grok claim guard accepts only search_replace, write, Edit, Write, or MultiEdit."
        )

    cwd = Path(_first_string(decoded, _CWD_KEYS, location="input"))
    if not cwd.is_absolute():
        raise GrokClaimGuardError("Grok hook cwd must be absolute.")
    path = Path(_first_string(_tool_input(decoded), _PATH_KEYS, location="toolInput"))
    return MutationRequest(
        session_id=_first_string(decoded, _SESSION_KEYS, location="input"),
        tool_use_id=_first_string(decoded, _TOOL_ID_KEYS, location="input"),
        cwd=cwd,
        file_paths=(path,),
    )


def denial_payload(reason: str) -> dict[str, Any]:
    """Return the blocking deny object documented by Grok 0.2.93."""
    return {"decision": "deny", "reason": reason}


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
    """Evaluate one raw Grok event against authoritative live claims."""
    try:
        request = parse_hook_request(raw)
    except GrokClaimGuardError as exc:
        return GuardVerdict(False, str(exc))
    return await evaluate_mutation_request(
        request,
        provider="Grok",
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=state_fetcher,
        git_runner=git_runner,
    )
