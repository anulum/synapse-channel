# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard operator write-path: body validation + relay dispatch
"""Operator write-path for the dashboard: validate one body, relay one frame.

The dashboard handler in :mod:`synapse_channel.dashboard` keeps only the HTTP
gate chain (operator mode, bearer token, media type, rate limit); the body
validation and relay execution live here as plain functions returning
:class:`~synapse_channel.dashboard_feed_serving.FeedResponse` values, testable
without a socket. Validation is strict and specific: every malformed body is
answered with one 400 naming the field, never a stack trace, and the relay
outcome maps to an HTTP status in exactly one place (:data:`_OUTCOME_STATUS`).
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final

from synapse_channel.dashboard_feed_serving import (
    FeedResponse,
    json_response,
    plain_response,
)
from synapse_channel.dashboard_operator import (
    DENIED,
    REJECTED,
    UNREACHABLE,
    OperatorRelay,
    RelayOutcome,
)

MAX_OPERATOR_BODY_BYTES: Final = 64 * 1024
"""Upper bound for one operator write body; larger requests answer 400."""

_OUTCOME_STATUS: Final[dict[str, HTTPStatus]] = {
    DENIED: HTTPStatus.FORBIDDEN,
    REJECTED: HTTPStatus.CONFLICT,
    UNREACHABLE: HTTPStatus.SERVICE_UNAVAILABLE,
}
"""Relay-outcome to HTTP-status map; an unlisted (accepted) outcome is ``200``."""


@dataclass(frozen=True)
class RelayPlan:
    """One validated operator write, ready to run against the hub.

    Parameters
    ----------
    action : str
        Verb echoed into the response document (``message``, ``task``,
        ``task_update``).
    extra : dict[str, str]
        Action-specific fields echoed into the response document.
    run : Callable
        Coroutine factory the executor awaits with a connected relay.
    """

    action: str
    extra: dict[str, str]
    run: Callable[[OperatorRelay], Coroutine[Any, Any, RelayOutcome]]


def is_json_media_type(content_type: str) -> bool:
    """Return whether a ``Content-Type`` header declares ``application/json``.

    Operator writes require this media type. A browser can send a request to
    another origin without a CORS preflight only when its content type is one
    of the three "simple" types (``text/plain``, form-encoded, or multipart);
    ``application/json`` forces a preflight, which this surface never answers
    with cross-origin allow headers, so the browser blocks the real write.
    Requiring JSON therefore turns away a cross-origin page trying to drive an
    operator action it cannot read the response of — a local CSRF. The check
    ignores any charset or boundary parameter after the media type.
    """
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json"


def read_operator_body(
    content_length: str | None, rfile: io.BufferedIOBase
) -> dict[str, Any] | None:
    """Return the request body as a JSON object, or ``None`` when unusable.

    ``None`` covers a missing, over-large, non-JSON, or non-object body — every
    case the caller answers with one 400, never a stack trace.
    """
    try:
        length = int(content_length or "0")
    except (TypeError, ValueError):
        return None
    if length <= 0 or length > MAX_OPERATOR_BODY_BYTES:
        return None
    raw = rfile.read(length)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _is_string_list(value: object) -> bool:
    """Return whether ``value`` is a list whose every element is a string."""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def plan_message(body: dict[str, Any]) -> RelayPlan | str:
    """Validate a chat write; return its plan, or the 400 reason."""
    to = body.get("to")
    text = body.get("text")
    if not isinstance(to, str) or not to.strip():
        return "'to' must be a non-empty string"
    if not isinstance(text, str) or not text.strip():
        return "'text' must be a non-empty string"
    target = to.strip()
    message_text = text
    return RelayPlan(
        "message", {"to": target}, lambda relay: relay.relay_message(target, message_text)
    )


def plan_task(body: dict[str, Any]) -> RelayPlan | str:
    """Validate a task declaration; return its plan, or the 400 reason."""
    task_id = body.get("id")
    title = body.get("title")
    depends_on = body.get("depends_on", [])
    if not isinstance(task_id, str) or not task_id.strip():
        return "'id' must be a non-empty string"
    if not isinstance(title, str) or not title.strip():
        return "'title' must be a non-empty string"
    if not _is_string_list(depends_on):
        return "'depends_on' must be a list of strings"
    task = task_id.strip()
    task_title = title
    deps = tuple(dep.strip() for dep in depends_on if dep.strip())
    return RelayPlan(
        "task", {"id": task}, lambda relay: relay.relay_task(task, task_title, depends_on=deps)
    )


def plan_task_update(body: dict[str, Any]) -> RelayPlan | str:
    """Validate a task update; return its plan, or the 400 reason.

    At least one of ``status`` or ``note`` must be present; each, when present,
    must be a non-empty string.
    """
    task_id = body.get("id")
    status_value = body.get("status")
    note = body.get("note")
    if not isinstance(task_id, str) or not task_id.strip():
        return "'id' must be a non-empty string"
    if status_value is not None and (not isinstance(status_value, str) or not status_value.strip()):
        return "'status' must be a non-empty string when present"
    if note is not None and (not isinstance(note, str) or not note.strip()):
        return "'note' must be a non-empty string when present"
    if status_value is None and note is None:
        return "a task update needs at least one of 'status' or 'note'"
    task = task_id.strip()
    new_status = status_value.strip() if isinstance(status_value, str) else None
    update_note = note
    return RelayPlan(
        "task_update",
        {"id": task},
        lambda relay: relay.relay_task_update(task, status=new_status, note=update_note),
    )


def execute_relay(
    plan: RelayPlan,
    *,
    uri: str,
    operator_name: str,
    token: str | None,
    ready_timeout: float,
    response_timeout: float,
) -> FeedResponse:
    """Run one relay plan and map its outcome to an HTTP response."""
    relay = OperatorRelay(
        uri=uri,
        operator_name=operator_name,
        token=token,
        ready_timeout=ready_timeout,
        response_timeout=response_timeout,
    )
    try:
        outcome = asyncio.run(plan.run(relay))
    except (OSError, RuntimeError) as exc:
        return plain_response(HTTPStatus.SERVICE_UNAVAILABLE, f"operator relay failed: {exc}")
    document: dict[str, object] = {
        "action": plan.action,
        **plan.extra,
        "status": outcome.status,
        "detail": outcome.detail,
        "ok": outcome.ok,
    }
    response = json_response(document)
    status = _OUTCOME_STATUS.get(outcome.status, HTTPStatus.OK)
    return FeedResponse(status, response.body, response.content_type)
