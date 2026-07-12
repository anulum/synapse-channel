# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for Gemini CLI `--output-format stream-json` output
"""Parse the ``stream-json`` stream emitted by headless ``gemini -p``.

Schema verification
-------------------
The event shape below was first read on 2026-07-12 directly from the **installed**
``gemini`` 0.47.0 bundle source (``StreamJsonFormatter.emitEvent`` call sites in
``/usr/lib/node_modules/@google/gemini-cli/bundle``) and then **behaviourally
captured the same day from a real run of that installed binary**:

``gemini -p "Reply with exactly one word: pong" -o stream-json
--fake-responses-non-strict <ndjson>``

The hidden ``--fake-responses-non-strict`` harness replaces only the model API
client (``FakeContentGenerator``), so the whole real CLI pipeline — turn loop,
``StreamJsonFormatter``, stdout framing — emitted the captured envelope; no API
account is required and the OAuth-personal ``IneligibleTierError`` path is never
reached. One JSON object per line:

- ``{"type": "init", "timestamp": …, "session_id": …, "model": …}`` — first event.
- ``{"type": "message", "timestamp": …, "role": "user", "content": …}`` — prompt echo.
- ``{"type": "message", "timestamp": …, "role": "assistant", "content": …,
  "delta": true}`` — streamed answer tokens.
- ``{"type": "tool_use", …}`` / ``{"type": "tool_result", …}`` — tool telemetry, ignored.
- ``{"type": "error", "timestamp": …, "severity": …, "message": …}`` — non-fatal noise.
- ``{"type": "result", "timestamp": …, "status": "success" | "error", "stats": …,
  "error"?: {"type": …, "message": …}}`` — the single terminal event.

Fixture: ``tests/fixtures/gemini_stream/real_emitter_single_pong.ndjson``
(SHA-256 ``7340a9925e74df070cab9a83947f01b155f2bdbcd3429b540dd782c1f8e2dd84``).

:data:`GEMINI_SCHEMA_VERIFIED` is therefore ``True``. The capture's model *content*
was synthetic by necessity; the envelope — the only thing this parser reads — came
from the real emitter. If a future Gemini release changes the wire shape,
re-capture, update this parser, and re-verify at source.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE, StreamOutcome

GEMINI_SCHEMA_VERIFIED = True
"""Whether the Gemini stream schema has been captured from a real run of a stable CLI.

``True``: the envelope was captured from the installed 0.47.0 binary's real emitter
via the ``--fake-responses-non-strict`` harness (see the module docstring for the
exact command and the pinned fixture). Only the model API client was substituted;
the stream framing this parser reads came from the shipped code path.
"""


def parse_gemini_stream(lines: Iterable[str]) -> StreamOutcome:
    """Parse Gemini ``--output-format stream-json`` lines into a :class:`StreamOutcome`.

    Parameters
    ----------
    lines : Iterable[str]
        The provider's stdout split into lines. Blank lines and non-JSON lines are
        skipped so a partial flush cannot abort an otherwise complete stream.

    Returns
    -------
    StreamOutcome
        Distilled from concatenated assistant ``message`` tokens and the terminal
        ``result`` event. A stream without ``result`` is an error carrying
        :data:`NO_RESULT_SUBTYPE`; a ``result`` with ``status`` other than
        ``"success"`` is an error carrying that status and its error message.
    """
    answer_parts: list[str] = []
    session_id = ""
    saw_result = False
    result_status = ""
    error_message = ""

    for line in lines:
        event = _decode(line)
        if event is None:
            continue
        event_type = event.get("type")
        if event_type == "init":
            session_id = _str_field(event, "session_id") or session_id
        elif event_type == "message":
            if event.get("role") != "assistant":
                continue
            fragment = event.get("content")
            if isinstance(fragment, str) and fragment:
                answer_parts.append(fragment)
        elif event_type == "error":
            message = _str_field(event, "message")
            if message:
                error_message = message
        elif event_type == "result":
            saw_result = True
            result_status = _str_field(event, "status")
            detail = event.get("error")
            if isinstance(detail, dict):
                error_message = _str_field(detail, "message") or error_message

    answer = "".join(answer_parts)
    if not saw_result:
        return StreamOutcome(
            answer=answer,
            rationale="",
            session_id=session_id,
            is_error=True,
            subtype=NO_RESULT_SUBTYPE,
            cost_usd=0.0,
            num_turns=0,
            stop_reason=error_message,
        )
    if result_status != "success":
        return StreamOutcome(
            answer=answer,
            rationale="",
            session_id=session_id,
            is_error=True,
            subtype=result_status or "error",
            cost_usd=0.0,
            num_turns=0,
            stop_reason=error_message,
        )
    return StreamOutcome(
        answer=answer,
        rationale="",
        session_id=session_id,
        is_error=False,
        subtype="success",
        cost_usd=0.0,
        num_turns=1,
        stop_reason="end_turn",
    )


def _decode(line: str) -> dict[str, Any] | None:
    """Return one JSON object line, or ``None`` when the line is noise."""
    text = line.strip()
    if not text:
        return None
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _str_field(event: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string among ``keys``."""
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
