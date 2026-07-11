# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-side dashboard feed serving (pure responses)
"""Read-side dashboard feeds computed as plain response values.

Every function here answers one dashboard GET route and returns a
:class:`FeedResponse` instead of writing to a socket, so the feed logic is
testable without an HTTP server and the handler in
:mod:`synapse_channel.dashboard` stays a thin shell. The uniform store-feed
posture lives in one place (:func:`_store_feed`): 404 when the backing store
is not configured (the cockpit panel states the feed is absent), 503 when
the store exists but cannot be read (fail-visible, never an empty document
pretending quiet), and 400 for a malformed query parameter.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs

from synapse_channel.core.federation_store import FederationStoreError
from synapse_channel.core.reliability import reliability_to_json, run_reliability_report
from synapse_channel.dashboard_postmortem_feed import (
    MAX_POSTMORTEM_TASK_ID_LENGTH,
    build_postmortem_feed,
)
from synapse_channel.dashboard_store_feeds import (
    DEFAULT_EVENTS_LIMIT,
    build_causality_feed,
    build_events_tail,
    build_federation_feed,
    build_health_anomalies_feed,
    build_merkle_proof_feed,
    build_metrics_feed,
    build_operator_actions_feed,
    build_receipts_feed,
    build_sessions_feed,
    build_state_at_feed,
    build_waits_feed,
    latest_cursor,
)


@dataclass(frozen=True)
class FeedResponse:
    """One computed HTTP response: status, body, and bare content type.

    Parameters
    ----------
    status : HTTPStatus
        Status code the handler writes.
    body : bytes
        Response body, already encoded.
    content_type : str
        Bare media type (the handler appends the charset parameter).
    """

    status: HTTPStatus
    body: bytes
    content_type: str


def plain_response(status: HTTPStatus, message: str) -> FeedResponse:
    """Return a one-line ``text/plain`` response ending in a newline."""
    return FeedResponse(status, f"{message}\n".encode(), "text/plain")


def json_response(document: dict[str, object]) -> FeedResponse:
    """Return a deterministic (sorted-keys) ``application/json`` 200."""
    return FeedResponse(
        HTTPStatus.OK,
        json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        "application/json",
    )


# SQLite stores integers as signed 64-bit. A query integer beyond that range parses
# fine as an unbounded Python int, then raises OverflowError deep inside a store
# query — a 500. The feed serving bounds it here so an out-of-range value is a 400.
_SQLITE_INT_MIN: Final = -(2**63)
_SQLITE_INT_MAX: Final = 2**63 - 1


def bounded_query_int(raw: str) -> int:
    """Parse a query integer, rejecting values outside SQLite's 64-bit range.

    Raises
    ------
    ValueError
        For a non-numeric value, or one too large to reach the durable store
        safely. Every feed route maps this to a ``400``.
    """
    value = int(raw)
    if not _SQLITE_INT_MIN <= value <= _SQLITE_INT_MAX:
        raise ValueError("integer out of range")
    return value


def _absent(feed: str, flag: str) -> FeedResponse:
    """Return the honest-absence 404 for an unconfigured store feed."""
    return plain_response(
        HTTPStatus.NOT_FOUND,
        f"{feed} feed not configured; start the dashboard with {flag}",
    )


def _store_feed(build: Callable[[], dict[str, Any]]) -> FeedResponse:
    """Run one store-feed builder with the shared unreadable-store posture."""
    try:
        document = build()
    except ValueError as exc:
        return plain_response(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
    return json_response(document)


def serve_reliability(db: Path | None, key_file: Path | None) -> FeedResponse:
    """Serve the reliability audit-signal report, or its honest absence."""
    if db is None:
        return _absent("reliability", "--reliability-db")
    return _store_feed(
        lambda: dict(reliability_to_json(run_reliability_report(db, key_file=key_file)))
    )


def serve_metrics_feed(db: Path | None) -> FeedResponse:
    """Serve store-attested log metrics, or their honest absence.

    The document itself explains that the live process registry lives on the
    hub's own ``/metrics`` endpoint, not here.
    """
    if db is None:
        return _absent("metrics", "--feeds-db")
    return _store_feed(lambda: build_metrics_feed(db))


def serve_state_at(db: Path | None, query: str) -> FeedResponse:
    """Serve the coordination state reconstructed as of ``?seq=N``.

    Store-derived time-travel: bounded replay of the durable log to ``seq``,
    the state and board in the live-snapshot shape plus ``as_of_seq`` and
    ``log_end_seq``. Presence/roster is not journalled and is omitted (the
    document says so).
    """
    if db is None:
        return _absent("state-at", "--feeds-db")
    raw = parse_qs(query).get("seq", ["0"])[0]
    try:
        seq = bounded_query_int(raw)
    except ValueError:
        return plain_response(HTTPStatus.BAD_REQUEST, "seq must be an integer")
    return _store_feed(lambda: build_state_at_feed(db, seq=seq))


def serve_merkle_proof(db: Path | None, query: str) -> FeedResponse:
    """Serve an inclusion proof for the event named by ``?seq=N``.

    Store-derived tamper-evidence: an RFC 6962 Merkle inclusion proof a
    cockpit row's *verify* button checks against the tree root. A ``seq`` the
    committed log does not hold yields ``{"present": false}`` with a note,
    never a fabricated proof.
    """
    if db is None:
        return _absent("merkle-proof", "--feeds-db")
    raw = parse_qs(query).get("seq", ["0"])[0]
    try:
        seq = bounded_query_int(raw)
    except ValueError:
        return plain_response(HTTPStatus.BAD_REQUEST, "seq must be an integer")
    return _store_feed(lambda: build_merkle_proof_feed(db, seq=seq))


def serve_health_anomalies(db: Path | None) -> FeedResponse:
    """Serve the coordination-anomaly report, or its honest absence.

    Orphaned, dangling, and stale coordination signals the causality graph
    makes visible, with an ``anomaly_count`` for a cockpit badge. Fired
    alerts stay collector-side off ``/metrics``; this is only what the
    durable log can prove.
    """
    if db is None:
        return _absent("health-anomalies", "--feeds-db")
    return _store_feed(lambda: build_health_anomalies_feed(db))


def serve_sessions(db: Path | None) -> FeedResponse:
    """Serve the opt-in session-telemetry report, or its honest absence.

    Aggregates the ``session_metric`` notes the fleet left in the durable
    log. Opt-in operational telemetry, never hub-core collected — a log with
    no notes reports empty sessions and zeroed totals, not a fabricated cost.
    """
    if db is None:
        return _absent("sessions", "--feeds-db")
    return _store_feed(lambda: build_sessions_feed(db))


def serve_waits(db: Path | None) -> FeedResponse:
    """Serve the pending coordination gates, or their honest absence.

    Non-terminal tasks blocked on incomplete dependencies — who is waiting,
    on which dependency ids, and since when — reconstructed from the durable
    log. Transient socket waiters are not journalled and are omitted.
    """
    if db is None:
        return _absent("waits", "--feeds-db")
    return _store_feed(lambda: build_waits_feed(db))


def serve_operator_actions(db: Path | None, query: str) -> FeedResponse:
    """Serve governed operator-action history from the durable log.

    Audit-only: no inferred actions beyond journalled ``operator_relay``
    events; 400 on a malformed cursor or limit.
    """
    if db is None:
        return _absent("operator-actions", "--feeds-db")
    params = parse_qs(query)
    try:
        since = bounded_query_int(params.get("since", ["0"])[0])
        limit = bounded_query_int(params.get("limit", ["50"])[0])
    except ValueError:
        return plain_response(HTTPStatus.BAD_REQUEST, "since and limit must be integers")
    return _store_feed(lambda: build_operator_actions_feed(db, since=since, limit=limit))


def serve_receipts(db: Path | None, query: str) -> FeedResponse:
    """Serve the universal receipt feed from the durable log.

    No inferred receipts beyond event families that carry receipt semantics;
    400 on malformed ``since`` or ``limit``.
    """
    if db is None:
        return _absent("receipts", "--feeds-db")
    params = parse_qs(query)
    try:
        since = bounded_query_int(params.get("since", ["0"])[0])
        limit = bounded_query_int(params.get("limit", ["100"])[0])
    except ValueError:
        return plain_response(HTTPStatus.BAD_REQUEST, "since and limit must be integers")
    return _store_feed(lambda: build_receipts_feed(db, since=since, limit=limit))


def serve_postmortem(db: Path | None, key_file: Path | None, query: str) -> FeedResponse:
    """Serve one replayable task postmortem from ``?task=ID``.

    The task id is required, singular, and bounded before the durable store is
    touched. An unknown task returns a successful document with
    ``present=false``; only missing configuration or unreadable storage fail.
    """
    if db is None:
        return _absent("postmortem", "--feeds-db")
    values = parse_qs(query, keep_blank_values=True).get("task", [])
    task_id = values[0].strip() if len(values) == 1 else ""
    if not task_id or len(task_id) > MAX_POSTMORTEM_TASK_ID_LENGTH:
        return plain_response(
            HTTPStatus.BAD_REQUEST,
            "task must be one non-empty identifier of at most "
            f"{MAX_POSTMORTEM_TASK_ID_LENGTH} characters",
        )
    return _store_feed(lambda: build_postmortem_feed(db, task_id, key_file=key_file))


def serve_events(db: Path | None, query: str) -> FeedResponse:
    """Serve the raw event-log tail past a cursor, or its honest absence.

    Parameters are ``since`` (exclusive sequence cursor, or ``latest`` for
    the tail shortcut) and ``limit``. Malformed numbers are a 400 naming the
    parameter, not a silent default.
    """
    if db is None:
        return _absent("events", "--feeds-db")
    params = parse_qs(query)
    since_raw = params.get("since", ["0"])[0]
    try:
        limit = bounded_query_int(params.get("limit", [str(DEFAULT_EVENTS_LIMIT)])[0])
        since = None if since_raw == "latest" else bounded_query_int(since_raw)
    except ValueError:
        return plain_response(
            HTTPStatus.BAD_REQUEST,
            "since must be an integer or 'latest'; limit must be an integer",
        )

    def build() -> dict[str, Any]:
        # the tail shortcut: start at the log's end instead of walking a
        # large history just to catch up to now
        cursor = latest_cursor(db) if since is None else since
        return build_events_tail(db, since=cursor, limit=limit)

    return _store_feed(build)


def serve_causality(db: Path | None, query: str) -> FeedResponse:
    """Answer one causality query in the CLI's exact JSON shape.

    ``seq=N`` or ``task=ID`` anchors the query; ``direction`` defaults to
    ``causes``. A bad anchor or direction is a 400 with the reason; a task
    the log never recorded is a 404 — absent, not invented.
    """
    if db is None:
        return _absent("causality", "--feeds-db")
    params = parse_qs(query)
    direction = params.get("direction", ["causes"])[0]
    task = params.get("task", [None])[0]
    seq_raw = params.get("seq", [None])[0]
    try:
        seq = bounded_query_int(seq_raw) if seq_raw is not None else None
    except ValueError:
        return plain_response(HTTPStatus.BAD_REQUEST, "seq must be an integer")
    try:
        document = build_causality_feed(db, direction=direction, seq=seq, task=task)
    except ValueError as exc:
        reason = str(exc)
        status = (
            HTTPStatus.NOT_FOUND
            if reason.startswith("no recorded event for task")
            else HTTPStatus.BAD_REQUEST
        )
        if reason.startswith("missing event store"):
            status = HTTPStatus.SERVICE_UNAVAILABLE
        return plain_response(status, reason)
    return json_response(document)


def serve_federation(store: Path | None) -> FeedResponse:
    """Serve the imported peerings, or the feed's honest absence."""
    if store is None:
        return _absent("federation", "--federation-store")
    try:
        document = build_federation_feed(store)
    except FederationStoreError as exc:
        return plain_response(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
    return json_response(document)


_DIST_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".map": "application/json",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain",
}
"""Suffixes the cockpit dist serving recognises; anything else is refused."""

_NOT_FOUND: Final = FeedResponse(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")


def serve_cockpit_dist(dist_dir: Path | None, prefix: str, path: str) -> FeedResponse:
    """Serve one file from the operator-named cockpit build directory.

    ``prefix`` alone maps to ``index.html``; every other path resolves inside
    the named directory and is refused when it escapes it (path traversal),
    carries an unrecognised suffix, or does not exist.
    """
    if dist_dir is None:
        return plain_response(
            HTTPStatus.NOT_FOUND,
            "cockpit build not configured; start the dashboard with --cockpit-dist",
        )
    relative = path[len(prefix) :] if path.startswith(prefix) else ""
    if relative == "":
        relative = "index.html"
    root = dist_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        return _NOT_FOUND
    content_type = _DIST_CONTENT_TYPES.get(target.suffix.lower())
    if content_type is None or not target.is_file():
        return _NOT_FOUND
    return FeedResponse(HTTPStatus.OK, target.read_bytes(), content_type)


def serve_public_cockpit_asset(
    dist_dir: Path | None, prefix: str, path: str
) -> FeedResponse | None:
    """Return one validated token-free cockpit shell asset, if ``path`` names one.

    The React shell must load before it can ask for a dashboard bearer. This is
    deliberately narrower than a prefix bypass: only an existing file accepted
    by :func:`serve_cockpit_dist` is public; unknown, escaping, and odd-suffix
    paths remain behind normal dashboard authentication.
    """
    if path != prefix.rstrip("/") and not path.startswith(prefix):
        return None
    response = serve_cockpit_dist(dist_dir, prefix, path)
    return response if response.status == HTTPStatus.OK else None
