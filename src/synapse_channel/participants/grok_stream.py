# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for Grok CLI streaming-json output
"""Parse the streaming-json stream emitted by headless Grok.

Schema verification
-------------------
Captured on 2026-07-12 from stable Grok 0.2.93 with a read-only, single-turn
request. The live shape is not the Claude Code stream-json convention:

- thought events carry streamed reasoning fragments in data;
- text events carry streamed answer fragments in data;
- end carries stopReason, sessionId, and requestId.

The immutable capture is
tests/fixtures/grok_stream/real_single_pong.ndjson, SHA-256
71ffaeaa567aa59290318afa7284804c3bd7c264a7fec1907edfc15cc0f5e44c.
If a future Grok release changes the wire shape, re-capture and re-verify it
before updating this parser.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE, StreamOutcome

GROK_SCHEMA_VERIFIED = True
"""The parser is pinned to a real stable Grok 0.2.93 capture."""


def parse_grok_stream(lines: Iterable[str]) -> StreamOutcome:
    """Distil native Grok events into one participant stream outcome."""
    thought_parts: list[str] = []
    text_parts: list[str] = []
    session_id = ""
    stop_reason = ""
    saw_end = False

    for line in lines:
        event = _decode(line)
        if event is None:
            continue
        event_type = event.get("type")
        if event_type == "thought":
            fragment = event.get("data")
            if isinstance(fragment, str) and fragment:
                thought_parts.append(fragment)
        elif event_type == "text":
            fragment = event.get("data")
            if isinstance(fragment, str) and fragment:
                text_parts.append(fragment)
        elif event_type == "end":
            saw_end = True
            session_id = _str_field(event, "sessionId", "session_id") or session_id
            stop_reason = _str_field(event, "stopReason", "stop_reason") or stop_reason
            final = event.get("data")
            if isinstance(final, str) and final and not text_parts:
                text_parts.append(final)

    answer = "".join(text_parts)
    rationale = "".join(thought_parts)
    if not saw_end:
        return StreamOutcome(
            answer=answer,
            rationale=rationale,
            session_id=session_id,
            is_error=True,
            subtype=NO_RESULT_SUBTYPE,
            cost_usd=0.0,
            num_turns=0,
            stop_reason=stop_reason,
        )
    return StreamOutcome(
        answer=answer,
        rationale=rationale,
        session_id=session_id,
        is_error=False,
        subtype="success",
        cost_usd=0.0,
        num_turns=1,
        stop_reason=stop_reason or "end_turn",
    )


def _decode(line: str) -> dict[str, Any] | None:
    """Return one JSON-object line, or None for stream noise."""
    text = line.strip()
    if not text:
        return None
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _str_field(event: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string among the candidate keys."""
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
