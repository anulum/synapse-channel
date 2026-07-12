# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed Gemini CLI BeforeTool claim guard
"""Adapt Gemini CLI ``BeforeTool`` file events to live Synapse claims.

The hook contract below was read on 2026-07-12 directly from the installed
``gemini`` 0.47.0 bundle source (``hookRunner.js`` / ``hook-utils.js`` in
``/usr/lib/node_modules/@google/gemini-cli/bundle``):

- The hook receives one JSON object on stdin:
  ``{"session_id": …, "transcript_path": …, "cwd": …, "hook_event_name": "BeforeTool",
  "timestamp": …, "tool_name": …, "tool_input": …}`` — Gemini's native event names
  (``BeforeTool``, not Claude's ``PreToolUse``) with no per-call tool id.
- File mutations arrive as ``tool_name`` ``"replace"`` (``tool_input.file_path`` /
  ``old_string`` / ``new_string``) or ``"write_file"`` (``tool_input.file_path`` /
  ``content``); both type guards require a string ``file_path``.
- A blocking response is top-level JSON ``{"decision": "deny", "reason": …}`` on exit 0
  (``DefaultHookOutput.isBlockingDecision`` accepts ``deny`` or ``block``); plain text
  with exit ≥ 2 also denies, and exit 1 is a non-blocking warning.

Because the input carries no tool-call id, the stable state-query slot is derived from
the event's ``session_id`` and ``timestamp`` instead.
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

SUPPORTED_TOOLS = frozenset({"replace", "write_file"})
"""Gemini CLI file-mutation tools covered by this guard."""


class GeminiClaimGuardError(FileClaimGuardError):
    """Gemini hook input is malformed or outside the guarded surface."""

    code = "gemini_claim_guard"


def _required_string(data: Mapping[str, Any], key: str, *, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GeminiClaimGuardError(f"Gemini hook input needs a non-empty {location}.{key} string.")
    return value.strip()


def parse_hook_request(raw: str) -> MutationRequest:
    """Parse and validate one Gemini CLI ``BeforeTool`` replace/write_file event."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GeminiClaimGuardError("Gemini hook input is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise GeminiClaimGuardError("Gemini hook input must be a JSON object.")
    if _required_string(decoded, "hook_event_name", location="input") != "BeforeTool":
        raise GeminiClaimGuardError("Gemini claim guard accepts only BeforeTool events.")
    tool_name = _required_string(decoded, "tool_name", location="input")
    if tool_name not in SUPPORTED_TOOLS:
        raise GeminiClaimGuardError("Gemini claim guard accepts only replace or write_file calls.")
    tool_input = decoded.get("tool_input")
    if not isinstance(tool_input, dict):
        raise GeminiClaimGuardError("Gemini hook input needs a tool_input object.")
    cwd = Path(_required_string(decoded, "cwd", location="input"))
    if not cwd.is_absolute():
        raise GeminiClaimGuardError("Gemini hook cwd must be absolute.")
    return MutationRequest(
        session_id=_required_string(decoded, "session_id", location="input"),
        tool_use_id=_required_string(decoded, "timestamp", location="input"),
        cwd=cwd,
        file_paths=(Path(_required_string(tool_input, "file_path", location="tool_input")),),
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
    """Evaluate one raw Gemini event against the authoritative live claims."""
    try:
        request = parse_hook_request(raw)
    except GeminiClaimGuardError as exc:
        return GuardVerdict(False, str(exc))
    return await evaluate_mutation_request(
        request,
        provider="Gemini",
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=state_fetcher,
        git_runner=git_runner,
    )


def gemini_denial_payload(reason: str) -> dict[str, Any]:
    """Return the top-level ``decision``/``reason`` denial Gemini's hook runner blocks on."""
    return {"decision": "deny", "reason": reason}
