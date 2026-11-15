# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the dashboard's feeds under a scope
"""Narrow the event-backed feeds to a scope, or say that a feed does not.

Five feeds name an identity or a task in each row, and keep only the rows of
the scope: ``/events.json``, ``/findings.json``, ``/receipts.json``,
``/waits.json`` and ``/attention.json``. Every other feed answers a scoped
request with its unscoped body. Each scoped answer carries a ``scope`` block
that says whether the scope was applied.

A row belongs to a scope by the rules of :mod:`synapse_channel.dashboard_scope`:
by the namespace of the identity it names, or by the ``project`` field of the
task it names, as the durable log declared it. Nothing is inferred from a
creator, an owner or a title, and a row that names neither stays in ``all``
only. A scope is a lens, not an access boundary.

A ``peer:<hub id>`` scope shows an observed hub's snapshot; its feeds are not
observed. Every feed answers a peer scope with the local hub's body and says that
the scope was not applied, so no local row is ever shown as the peer's.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from synapse_channel.core.identity_namespace import project_of
from synapse_channel.dashboard_feed_serving import FeedResponse, json_response, plain_response
from synapse_channel.dashboard_scope import WHOLE_HUB, DashboardScope, parse_scope
from synapse_channel.dashboard_scope_groups import ScopeGroups
from synapse_channel.dashboard_store_feeds import store_task_projects

_LOGGER = logging.getLogger(__name__)

PEER_FEED_REASON = "a peer scope narrows the snapshot only; this feed is the local hub's"
"""Why a feed answers a ``peer:<hub id>`` scope with the local hub's rows."""

Place = Callable[[Mapping[str, Any]], tuple[str, str]]
"""Return the identity and the task a feed row names; empty when it names none."""


def _text(value: object) -> str:
    """Return ``value`` when it is text, else an empty string."""
    return value if isinstance(value, str) else ""


def _event_place(row: Mapping[str, Any]) -> tuple[str, str]:
    """Return an event's actor, as the hub words it, and the task its payload names."""
    payload = row.get("payload")
    task = payload.get("task_id") if isinstance(payload, Mapping) else None
    return _text(row.get("actor")), _text(task)


def _finding_place(row: Mapping[str, Any]) -> tuple[str, str]:
    """Return the identity the hub attested for a finding on receipt; a finding names no task."""
    check = row.get("verified_at_source")
    return _text(check.get("by") if isinstance(check, Mapping) else None), ""


def _receipt_place(row: Mapping[str, Any]) -> tuple[str, str]:
    """Return a receipt's actor and subject; the subject counts only when it is a task."""
    return _text(row.get("actor")), _text(row.get("subject"))


def _wait_place(row: Mapping[str, Any]) -> tuple[str, str]:
    """Return a wait's task only: its ``who`` is an owner or a creator, never a scope."""
    return "", _text(row.get("task_id"))


def _alert_place(row: Mapping[str, Any]) -> tuple[str, str]:
    """Return an attention alert's subject, which counts only when it is a task."""
    return "", _text(row.get("subject"))


FILTERED_FEEDS: dict[str, tuple[str, Place, str]] = {
    "/events.json": ("events", _event_place, ""),
    "/findings.json": ("findings", _finding_place, ""),
    "/receipts.json": ("receipts", _receipt_place, "receipt_count"),
    "/waits.json": ("waits", _wait_place, "wait_count"),
    "/attention.json": ("alerts", _alert_place, ""),
}
"""Each filtered feed's rows key, how a row is placed, and the count kept equal to its rows."""


def feed_scope(query: str, groups: ScopeGroups | None = None) -> DashboardScope:
    """Return the scope a feed request asks for; no ``scope`` asks for the whole hub.

    ``groups`` are the groups the dashboard declares, which a ``group:`` scope names.

    Raises
    ------
    ValueError
        For a malformed scope, with the allowed forms in the message.
    """
    return parse_scope(parse_qs(query, keep_blank_values=True).get("scope", []), groups)


def _holds(scope: DashboardScope, who: str, task: str, projects: Mapping[str, str]) -> bool:
    """Return whether a row of identity ``who`` and task ``task`` belongs to ``scope``."""
    if who and scope.holds(project_of(who)):
        return True
    return task in projects and scope.holds(projects[task])


def _filtered(
    document: dict[str, Any],
    feed: tuple[str, Place, str],
    scope: DashboardScope,
    projects: Mapping[str, str],
) -> dict[str, Any]:
    """Return the document with only the rows of ``scope`` and the counts of the cut."""
    key, place, count_key = feed
    rows = [row for row in document.get(key) or [] if isinstance(row, Mapping)]
    kept = [row for row in rows if _holds(scope, *place(row), projects)]
    narrowed = {**document, key: kept}
    if count_key:
        narrowed[count_key] = len(kept)
    narrowed["scope"] = {
        "scope": scope.text,
        "applied": True,
        "rows_in_page": len(rows),
        "rows_in_scope": len(kept),
    }
    return narrowed


def scoped_feed(
    path: str, response: FeedResponse | None, scope: DashboardScope, db: Path | None
) -> FeedResponse | None:
    """Return a feed's answer under ``scope``; any answer but a JSON object passes unchanged.

    Parameters
    ----------
    path : str
        The feed's route.
    response : FeedResponse or None
        The feed's unscoped answer; ``None``, for a path no feed serves, passes through.
    scope : DashboardScope
        The scope the request asked for.
    db : pathlib.Path or None
        The durable event store that places a task by its ``project`` field;
        without one, a filtered feed says that the scope was not applied.
    """
    if (
        response is None
        or scope == WHOLE_HUB
        or response.status != HTTPStatus.OK
        or not response.content_type.startswith("application/json")
    ):
        return response
    document = json.loads(response.body)
    if not isinstance(document, dict):
        return response
    if scope.kind == "peer":
        block = {"scope": scope.text, "applied": False, "reason": PEER_FEED_REASON}
        return json_response({**document, "scope": block})
    feed = FILTERED_FEEDS.get(path)
    if feed is None:
        return json_response({**document, "scope": {"scope": scope.text, "applied": False}})
    if db is None:
        reason = "no event store to place a task by its project"
        block = {"scope": scope.text, "applied": False, "reason": reason}
        return json_response({**document, "scope": block})
    try:
        projects = store_task_projects(db)
    except Exception:
        _LOGGER.exception("A dashboard feed's scope could not be applied")
        return plain_response(HTTPStatus.SERVICE_UNAVAILABLE, "feed scope unavailable")
    return json_response(_filtered(document, feed, scope, projects))
