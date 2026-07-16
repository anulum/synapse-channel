# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed Kimi Code Edit/Write claim guard
"""Adapt Kimi Code ``PreToolUse`` file events to live Synapse claims."""

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

SUPPORTED_TOOLS = frozenset({"Edit", "Write"})


class KimiClaimGuardError(FileClaimGuardError):
    """Kimi hook input is malformed or outside the guarded surface."""

    code = "kimi_claim_guard"


def _required_string(data: Mapping[str, Any], key: str, *, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KimiClaimGuardError(f"Kimi hook input needs a non-empty {location}.{key} string.")
    return value.strip()


def parse_hook_request(raw: str) -> MutationRequest:
    """Parse and validate one Kimi Code ``PreToolUse`` Edit/Write event."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise KimiClaimGuardError("Kimi hook input is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise KimiClaimGuardError("Kimi hook input must be a JSON object.")
    if _required_string(decoded, "hook_event_name", location="input") != "PreToolUse":
        raise KimiClaimGuardError("Kimi claim guard accepts only PreToolUse events.")
    tool_name = _required_string(decoded, "tool_name", location="input")
    if tool_name not in SUPPORTED_TOOLS:
        raise KimiClaimGuardError("Kimi claim guard accepts only Edit or Write calls.")
    tool_input = decoded.get("tool_input")
    if not isinstance(tool_input, dict):
        raise KimiClaimGuardError("Kimi hook input needs a tool_input object.")
    cwd = Path(_required_string(decoded, "cwd", location="input"))
    if not cwd.is_absolute():
        raise KimiClaimGuardError("Kimi hook cwd must be absolute.")
    return MutationRequest(
        session_id=_required_string(decoded, "session_id", location="input"),
        tool_use_id=_required_string(decoded, "tool_call_id", location="input"),
        cwd=cwd,
        file_paths=(Path(_required_string(tool_input, "path", location="tool_input")),),
        allow_semantic_source=tool_name == "Edit",
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
    """Evaluate one raw Kimi event against the authoritative live claims."""
    try:
        request = parse_hook_request(raw)
    except KimiClaimGuardError as exc:
        return GuardVerdict(False, str(exc))
    return await evaluate_mutation_request(
        request,
        provider="Kimi",
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=state_fetcher,
        git_runner=git_runner,
    )
