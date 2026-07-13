# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pinned OpenCode run JSONL parser
"""Parse the source-verified OpenCode 1.17.20 ``run --format json`` stream."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from synapse_channel.participants.stream_json import StreamOutcome

OPENCODE_SCHEMA_VERSION = "1.17.20"
OPENCODE_SCHEMA_VERIFIED = True
"""True only for the source and real-process captured 1.17.20 emitter contract."""

_KNOWN_TYPES = frozenset({"step_start", "tool_use", "text", "reasoning", "step_finish", "error"})


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _nonnegative_float(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return 0.0


def _error_name(value: object) -> str:
    if isinstance(value, Mapping):
        name = value.get("name")
        if isinstance(name, str) and name:
            return name
    return "opencode_error"


def parse_opencode_stream(lines: Iterable[str]) -> StreamOutcome:
    """Normalize one complete OpenCode JSONL turn, failing closed on schema drift."""
    session_id = ""
    texts: list[str] = []
    reasoning: list[str] = []
    saw_finish = False
    subtype = ""
    stop_reason = ""
    input_tokens = 0
    output_tokens = 0
    cost = 0.0

    for line in lines:
        if not line.strip():
            continue
        try:
            event: Any = json.loads(line)
        except json.JSONDecodeError:
            subtype = "malformed_event"
            continue
        if not isinstance(event, dict) or event.get("type") not in _KNOWN_TYPES:
            subtype = "schema_drift"
            continue
        current_session = event.get("sessionID")
        if not isinstance(current_session, str) or not current_session:
            subtype = "schema_drift"
            continue
        if session_id and session_id != current_session:
            subtype = "session_mismatch"
            continue
        session_id = current_session
        event_type = event["type"]
        part = event.get("part")
        if event_type in {"text", "reasoning"}:
            if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                subtype = "schema_drift"
                continue
            (texts if event_type == "text" else reasoning).append(part["text"])
        elif event_type == "step_finish":
            if not isinstance(part, dict) or part.get("type") != "step-finish":
                subtype = "schema_drift"
                continue
            saw_finish = True
            reason = part.get("reason")
            stop_reason = reason if isinstance(reason, str) else ""
            cost += _nonnegative_float(part.get("cost"))
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                input_tokens += _nonnegative_int(tokens.get("input"))
                output_tokens += _nonnegative_int(tokens.get("output"))
        elif event_type == "error":
            subtype = _error_name(event.get("error"))

    if not subtype and not saw_finish:
        subtype = "incomplete_stream"
    return StreamOutcome(
        answer="\n".join(texts),
        rationale="\n".join(reasoning),
        session_id=session_id,
        is_error=bool(subtype),
        subtype=subtype or "success",
        cost_usd=cost,
        num_turns=1 if saw_finish else 0,
        stop_reason=stop_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def parse_opencode_api_response(payload: Mapping[str, Any]) -> StreamOutcome:
    """Normalize the synchronous ``POST /session/:id/message`` response."""
    info = payload.get("info")
    parts = payload.get("parts")
    if not isinstance(info, dict) or not isinstance(parts, list):
        return _api_error("schema_drift")
    session_id = info.get("sessionID")
    if not isinstance(session_id, str) or not session_id or info.get("role") != "assistant":
        return _api_error("schema_drift")
    texts: list[str] = []
    reasoning: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            return _api_error("schema_drift", session_id=session_id)
        part_type = part.get("type")
        if part_type in {"text", "reasoning"}:
            text = part.get("text")
            if not isinstance(text, str):
                return _api_error("schema_drift", session_id=session_id)
            (texts if part_type == "text" else reasoning).append(text)
    error = info.get("error")
    tokens = info.get("tokens")
    finish = info.get("finish")
    return StreamOutcome(
        answer="\n".join(texts),
        rationale="\n".join(reasoning),
        session_id=session_id,
        is_error=error is not None,
        subtype=_error_name(error) if error is not None else "success",
        cost_usd=_nonnegative_float(info.get("cost")),
        num_turns=1,
        stop_reason=finish if isinstance(finish, str) else "",
        input_tokens=_nonnegative_int(tokens.get("input")) if isinstance(tokens, dict) else 0,
        output_tokens=_nonnegative_int(tokens.get("output")) if isinstance(tokens, dict) else 0,
    )


def _api_error(subtype: str, *, session_id: str = "") -> StreamOutcome:
    return StreamOutcome(
        answer="",
        rationale="",
        session_id=session_id,
        is_error=True,
        subtype=subtype,
        cost_usd=0.0,
        num_turns=0,
        stop_reason="",
        input_tokens=0,
        output_tokens=0,
    )
