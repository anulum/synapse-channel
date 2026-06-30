# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parser for Kimi CLI `--print --output-format stream-json` output
"""Parse the JSONL message stream emitted by ``kimi --print --output-format stream-json``.

Kimi is a Claude-Code-family CLI, but its headless ``stream-json`` shape differs from both
Claude and Codex; the schema this parser targets was captured from a real invocation
(Kimi CLI 1.47.0):

- Each stdout line is one assistant message: ``{"role": "assistant", "content": …}``.
- ``content`` is **either a plain string** (the reply, when thinking is off) **or a list of
  blocks** when thinking is on — ``{"type": "think", "think": "<reasoning>", "encrypted": …}``
  for disclosed reasoning and ``{"type": "text", "text": "<reply>"}`` for the reply. Any other
  block type (a tool call, for instance) is ignored for the answer.
- A print-mode turn observed in practice emits a single final assistant message, but an
  agentic run may emit several; the last message that carries reply text is the final answer
  and every ``think`` block across the turn is collected as the disclosed rationale.

Unlike Claude and Codex, Kimi reports the **resume token on stderr**, not in the stdout
stream — a fixed ``To resume this session: kimi -r <id>`` line — so the session id is
extracted from the captured stderr text (:func:`extract_kimi_session`) rather than an event.
Kimi reports **no monetary cost**, so the outcome's ``cost_usd`` is ``0.0`` (a cost budget
therefore cannot bound a Kimi turn — only the round cap can). A stream with no assistant
message at all is reported as an error so a crashed turn never reads as a silent empty
answer. The exact error-line schema was not available to capture at build time and is
handled defensively.

The result is normalised into the same
:class:`~synapse_channel.participants.stream_json.StreamOutcome` the Claude and Codex parsers
produce, so every provider feeds one envelope builder.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE, StreamOutcome

_SESSION_RE = re.compile(r"kimi\s+-r\s+(\S+)")
"""Match the stderr resume hint ``kimi -r <id>`` and capture the session id."""


def extract_kimi_session(stderr: str) -> str:
    """Return the resume session id from a Kimi run's stderr, or ``""`` when absent.

    Kimi prints ``To resume this session: kimi -r <id>`` to stderr after a headless turn.
    The id is captured from the last such line so a turn that logs more than one keeps the
    most recent token.

    Parameters
    ----------
    stderr : str
        The captured stderr text of the Kimi invocation.

    Returns
    -------
    str
        The session id, or an empty string when no resume hint is present.
    """
    matches = _SESSION_RE.findall(stderr)
    return matches[-1] if matches else ""


def parse_kimi_stream(lines: Iterable[str], *, stderr: str = "") -> StreamOutcome:
    """Parse Kimi ``--print --output-format stream-json`` output into a :class:`StreamOutcome`.

    Parameters
    ----------
    lines : Iterable[str]
        The provider's stdout split into lines. Blank lines and non-object JSON lines are
        skipped rather than raising.
    stderr : str, optional
        The provider's captured stderr, mined for the resume session id (Kimi reports it
        there, not in the stdout stream).

    Returns
    -------
    StreamOutcome
        ``answer`` is the last assistant message's reply text; ``rationale`` is every
        ``think`` block joined; ``session_id`` comes from ``stderr``; ``cost_usd`` is always
        ``0.0`` (Kimi reports no cost). A stream with no assistant message is an error
        carrying :data:`~synapse_channel.participants.stream_json.NO_RESULT_SUBTYPE`.
    """
    answer = ""
    rationale_parts: list[str] = []
    saw_assistant = False
    error_subtype = ""

    for line in lines:
        event = _decode(line)
        if event is None:
            continue
        role = event.get("role")
        if role == "assistant":
            saw_assistant = True
            text, thinks = _content_parts(event.get("content"))
            rationale_parts.extend(thinks)
            if text:
                answer = text
        elif _is_error_event(role, event):
            error_subtype = _error_subtype(event)

    is_error = bool(error_subtype) or not saw_assistant
    subtype = error_subtype or ("success" if saw_assistant else NO_RESULT_SUBTYPE)
    return StreamOutcome(
        answer=answer,
        rationale="\n".join(rationale_parts),
        session_id=extract_kimi_session(stderr),
        is_error=is_error,
        subtype=subtype,
        cost_usd=0.0,
        num_turns=0,
        stop_reason="",
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


def _content_parts(content: Any) -> tuple[str, list[str]]:
    """Split a Kimi assistant ``content`` field into reply text and reasoning blocks.

    ``content`` is either a plain reply string or a list of typed blocks. Returns the joined
    reply text (empty when the message carries no reply) and every disclosed ``think`` block.
    """
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []
    text_parts: list[str] = []
    thinks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block_type == "think" and isinstance(block.get("think"), str):
            thinks.append(block["think"])
    return "".join(text_parts), thinks


def _is_error_event(role: Any, event: dict[str, Any]) -> bool:
    """Return whether a non-assistant line denotes a Kimi failure (best-effort schema)."""
    if role == "error":
        return True
    event_type = event.get("type")
    return isinstance(event_type, str) and "error" in event_type


def _error_subtype(event: dict[str, Any]) -> str:
    """Return a short error subtype for a failure line, never an empty string.

    A failure line reaches here only via :func:`_is_error_event`, i.e. it either names an
    error ``type`` or carries ``role == "error"``; the typed name is preferred and the
    role-based failure falls back to ``"error"``.
    """
    event_type = event.get("type")
    if isinstance(event_type, str) and event_type:
        return event_type
    return "error"
