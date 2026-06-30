# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for Codex CLI `exec --json` output
"""Parse the JSONL event stream emitted by ``codex exec --json``.

``codex exec --json`` writes one JSON object per line. The schema this parser targets was
captured from a real invocation (Codex CLI 0.142.4):

- ``{"type": "thread.started", "thread_id": "<uuid>"}`` — first event; ``thread_id`` is the
  session id used to resume the conversation later.
- ``{"type": "turn.started"}`` — start marker, ignored.
- ``{"type": "item.completed", "item": {"type": "agent_message", "text": "<reply>"}}`` — the
  model's reply; an agentic run may emit several, and the last is the final answer. An item of
  type ``reasoning`` carries disclosed rationale when present.
- ``{"type": "turn.completed", "usage": {...}}`` — the terminal success event. Codex reports
  token usage but **no monetary cost**, so the outcome's ``cost_usd`` is ``0.0`` (a cost
  budget therefore cannot bound a Codex turn — only the round cap can).

The result is normalised into the same
:class:`~synapse_channel.participants.stream_json.StreamOutcome` the Claude parser produces,
so both providers feed one envelope builder. A stream that ends without a terminal
``turn.completed`` and without any agent message is reported as an error. Error and failure
events (any ``type`` containing ``error`` or ending in ``.failed``) are handled defensively;
the exact failure schema was not available to capture at build time.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE, StreamOutcome


def parse_codex_stream(lines: Iterable[str]) -> StreamOutcome:
    """Parse Codex ``exec --json`` JSONL lines into a :class:`StreamOutcome`.

    Parameters
    ----------
    lines : Iterable[str]
        The provider's stdout split into lines. Blank lines and non-object JSON lines are
        skipped rather than raising.

    Returns
    -------
    StreamOutcome
        ``answer`` is the last ``agent_message`` text; ``session_id`` is the ``thread_id``;
        ``cost_usd`` is always ``0.0`` (Codex does not report cost). A stream with neither a
        terminal ``turn.completed`` nor any agent message is an error carrying
        :data:`~synapse_channel.participants.stream_json.NO_RESULT_SUBTYPE`.
    """
    session_id = ""
    answers: list[str] = []
    rationale_parts: list[str] = []
    saw_completed = False
    error_subtype = ""

    for line in lines:
        event = _decode(line)
        if event is None:
            continue
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str):
                session_id = thread_id
        elif event_type == "item.completed":
            _collect_item(event.get("item"), answers, rationale_parts)
        elif event_type == "turn.completed":
            saw_completed = True
        elif _is_failure(event_type):
            error_subtype = event_type if isinstance(event_type, str) else "error"

    answer = answers[-1] if answers else ""
    is_error = bool(error_subtype) or (not saw_completed and not answers)
    subtype = error_subtype or ("success" if saw_completed else NO_RESULT_SUBTYPE)
    return StreamOutcome(
        answer=answer,
        rationale="\n".join(rationale_parts),
        session_id=session_id,
        is_error=is_error,
        subtype=subtype,
        cost_usd=0.0,
        num_turns=0,
        stop_reason="completed" if saw_completed else "",
    )


def _decode(line: str) -> dict[str, Any] | None:
    """Decode one JSONL line to a JSON object, or ``None`` when it is not one."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        value: Any = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _collect_item(item: Any, answers: list[str], rationale_parts: list[str]) -> None:
    """Record an ``item.completed`` payload's agent message or reasoning text."""
    if not isinstance(item, dict):
        return
    item_type = item.get("type")
    text = item.get("text")
    if not isinstance(text, str):
        return
    if item_type == "agent_message":
        answers.append(text)
    elif item_type == "reasoning":
        rationale_parts.append(text)


def _is_failure(event_type: Any) -> bool:
    """Return whether an event type denotes an error or a failed turn."""
    return isinstance(event_type, str) and ("error" in event_type or event_type.endswith(".failed"))
