# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only local dashboard snapshot and HTTP serving
"""Read-only local dashboard over the live Synapse hub.

The dashboard is deliberately small: it opens a normal Synapse client, asks the
hub for roster, state, board, and manifest snapshots, then renders those
read-side values as HTML or JSON. It does not mutate hub state, does not store
snapshots, and binds to loopback by default because the rendered page can expose
task names, claim scopes, and agent identities.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import html
import ipaddress
import json
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, Final
from urllib.parse import parse_qs, urlsplit

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.federation_store import FederationStoreError
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.reliability import reliability_to_json, run_reliability_report
from synapse_channel.dashboard_cockpit import (
    COCKPIT_ASSETS,
    load_cockpit_asset,
    render_cockpit_html,
)
from synapse_channel.dashboard_fleet import build_fleet_visibility, render_fleet_visibility_html
from synapse_channel.dashboard_risk import build_risk_view
from synapse_channel.dashboard_store_feeds import (
    DEFAULT_EVENTS_LIMIT,
    build_causality_feed,
    build_events_tail,
    build_federation_feed,
    build_metrics_feed,
    latest_cursor,
)
from synapse_channel.dashboard_studio import (
    STUDIO_REFERENCE_PATH,
    render_studio_reference_html,
)
from synapse_channel.dashboard_studio_command import (
    STUDIO_COMMAND_PATH,
    render_studio_command_html,
)
from synapse_channel.studio_snapshot import STUDIO_SNAPSHOT_PATH, build_studio_snapshot

SnapshotMapping = dict[str, Any]
"""Mutable mapping shape used for hub snapshot payloads."""

ManifestCards = list[dict[str, Any]]
"""List shape used by the hub capability manifest snapshot."""

LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "::1"})
"""Host names and literals treated as local-only dashboard binds."""


class DashboardUnavailable(RuntimeError):
    """Raised when the dashboard cannot fetch the live hub snapshot."""


@dataclass(frozen=True)
class DashboardSnapshot:
    """Read-side data rendered by the local dashboard.

    Parameters
    ----------
    online_agents : list[str]
        Agent names currently present in the hub roster.
    state : dict[str, Any]
        Raw state snapshot returned by the hub.
    board : dict[str, Any]
        Raw shared-board snapshot returned by the hub.
    manifest : list[dict[str, Any]]
        Capability cards returned by the hub.
    """

    online_agents: list[str]
    state: SnapshotMapping
    board: SnapshotMapping
    manifest: ManifestCards

    def to_dict(self, *, a2a_state_file: str | Path | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary for HTTP responses.

        Parameters
        ----------
        a2a_state_file : str, pathlib.Path, or None, optional
            Optional persisted A2A bridge state file used to populate the
            derived ``fleet.a2a`` summary.
        """
        payload = asdict(self)
        fleet = build_fleet_visibility(self, a2a_state_file=a2a_state_file)
        payload["fleet"] = fleet.to_dict()
        payload["risk"] = build_risk_view(fleet).to_dict()
        return payload


@dataclass(frozen=True)
class DashboardServer:
    """Running local dashboard server handle.

    Parameters
    ----------
    server : ThreadingHTTPServer
        Bound stdlib HTTP server.
    thread : threading.Thread
        Background thread running ``serve_forever``.
    dashboard_token : str or None
        Bearer token required by the dashboard HTTP surface. ``None`` means the
        HTTP surface is unauthenticated, which is allowed only for loopback
        binds.
    dashboard_token_generated : bool
        Whether ``dashboard_token`` was generated at startup because the
        dashboard was explicitly exposed without a caller-provided token.
    """

    server: ThreadingHTTPServer
    thread: threading.Thread
    dashboard_token: str | None = None
    dashboard_token_generated: bool = False

    @property
    def host(self) -> str:
        """Return the concrete host bound by the HTTP server."""
        return str(self.server.server_address[0])

    @property
    def port(self) -> int:
        """Return the concrete TCP port bound by the HTTP server."""
        return int(self.server.server_address[1])

    def url(self, path: str = "/") -> str:
        """Build an HTTP URL for ``path`` on this server."""
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}{path}"

    def close(self) -> None:
        """Stop the dashboard server and wait briefly for its thread."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


def _is_loopback_host(host: str) -> bool:
    """Return whether ``host`` names a loopback-only bind target."""
    candidate = host.strip().lower()
    if candidate in LOOPBACK_HOSTS:
        return True
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(candidate).is_loopback
    return False


def validate_dashboard_bind(host: str, *, allow_non_loopback: bool) -> None:
    """Refuse non-loopback dashboard binds unless explicitly allowed.

    Parameters
    ----------
    host : str
        Host literal or name passed to the dashboard HTTP server.
    allow_non_loopback : bool
        Whether the caller explicitly accepts exposing the read-only dashboard
        beyond loopback.

    Raises
    ------
    ValueError
        If ``host`` is not a loopback target and ``allow_non_loopback`` is false.
    """
    if allow_non_loopback or _is_loopback_host(host):
        return
    raise ValueError(
        "dashboard binds to loopback by default; pass --allow-non-loopback "
        "only behind trusted local network controls"
    )


def _resolve_dashboard_token(
    host: str,
    *,
    allow_non_loopback: bool,
    dashboard_token: str | None,
) -> tuple[str | None, bool]:
    """Return the effective dashboard HTTP bearer token.

    Parameters
    ----------
    host : str
        HTTP bind host supplied by the operator.
    allow_non_loopback : bool
        Whether the operator explicitly allowed an exposed dashboard bind.
    dashboard_token : str or None
        Caller-provided dashboard HTTP bearer token.

    Returns
    -------
    tuple[str or None, bool]
        Effective bearer token and whether it was generated by the server.

    Raises
    ------
    ValueError
        If the caller supplied an empty dashboard token.
    """
    if dashboard_token is not None and len(dashboard_token) == 0:
        raise ValueError("dashboard token must not be empty")
    if dashboard_token is not None:
        return dashboard_token, False
    if allow_non_loopback and not _is_loopback_host(host):
        return secrets.token_urlsafe(32), True
    return None, False


async def fetch_dashboard_snapshot(
    *,
    uri: str,
    name: str,
    token: str | None,
    ready_timeout: float = 5.0,
    response_timeout: float = 2.0,
) -> DashboardSnapshot:
    """Fetch roster, state, board, and manifest snapshots from a live hub.

    Parameters
    ----------
    uri, name : str
        Hub URI and dashboard client identity.
    token : str or None
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the hub welcome handshake.
    response_timeout : float, optional
        Seconds to wait for every read-side snapshot response.

    Returns
    -------
    DashboardSnapshot
        Snapshot values gathered through public hub request messages.

    Raises
    ------
    DashboardUnavailable
        If the hub is unreachable or does not return every requested snapshot.
    """
    messages: dict[str, dict[str, Any]] = {}
    expected = {
        MessageType.WHO_SNAPSHOT,
        MessageType.STATE_SNAPSHOT,
        MessageType.BOARD_SNAPSHOT,
        MessageType.MANIFEST_SNAPSHOT,
    }

    async def collect(data: dict[str, Any]) -> None:
        message_type = str(data.get("type", ""))
        if message_type in expected:
            messages[message_type] = data

    agent = SynapseAgent(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            raise DashboardUnavailable(f"could not reach hub at {uri}")
        await agent.request_who()
        await agent.request_state()
        await agent.request_board()
        await agent.request_manifest()
        deadline = time.monotonic() + max(0.0, response_timeout)
        while set(messages) != expected and time.monotonic() < deadline:
            await asyncio.sleep(0.025)
        missing = sorted(expected.difference(messages))
        if missing:
            joined = ", ".join(missing)
            raise DashboardUnavailable(f"hub did not return dashboard snapshot(s): {joined}")
        return DashboardSnapshot(
            online_agents=[
                str(agent_name)
                for agent_name in messages[MessageType.WHO_SNAPSHOT].get("online_agents", [])
            ],
            state=dict(messages[MessageType.STATE_SNAPSHOT].get("snapshot", {})),
            board=dict(messages[MessageType.BOARD_SNAPSHOT].get("board", {})),
            manifest=[
                dict(card)
                for card in messages[MessageType.MANIFEST_SNAPSHOT].get("manifest", [])
                if isinstance(card, Mapping)
            ],
        )
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


def _escape(value: object) -> str:
    """Return ``value`` escaped for HTML text nodes."""
    return html.escape(str(value), quote=True)


def _render_list(items: list[str]) -> str:
    """Render escaped list items or a single empty marker."""
    if not items:
        return '<li class="muted">None</li>'
    return "".join(f"<li>{_escape(item)}</li>" for item in items)


def _render_claims(state: SnapshotMapping) -> str:
    """Render active claims from a state snapshot."""
    claims = state.get("active_claims", [])
    if not isinstance(claims, list) or not claims:
        return '<li class="muted">No active claims</li>'
    rows: list[str] = []
    sorted_claims = sorted(
        (claim for claim in claims if isinstance(claim, Mapping)),
        key=lambda claim: str(claim.get("task_id", "")),
    )
    for claim in sorted_claims:
        owner = _escape(claim.get("owner", "-"))
        paths = claim.get("paths", [])
        rendered_paths = (
            ", ".join(_escape(path) for path in paths) if isinstance(paths, list) else "-"
        )
        rows.append(
            f"<li><strong>{_escape(claim.get('task_id', '-'))}</strong> — "
            f"{owner}<br><small>{rendered_paths}</small></li>"
        )
    return "".join(rows)


def _render_tasks(board: SnapshotMapping) -> str:
    """Render task cards from a board snapshot."""
    tasks = board.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        return '<li class="muted">No board tasks</li>'
    rows: list[str] = []
    for raw_task in tasks:
        if not isinstance(raw_task, Mapping):
            continue
        rows.append(
            "<li>"
            f"<strong>{_escape(raw_task.get('task_id', '-'))}</strong> "
            f"<span>{_escape(raw_task.get('status', '-'))}</span><br>"
            f"{_escape(raw_task.get('title', ''))}"
            "</li>"
        )
    return "".join(rows) if rows else '<li class="muted">No board tasks</li>'


def _render_progress(board: SnapshotMapping) -> str:
    """Render recent board progress notes."""
    progress = board.get("progress", [])
    if not isinstance(progress, list) or not progress:
        return '<li class="muted">No progress notes</li>'
    rows: list[str] = []
    for raw_note in progress[-10:]:
        if not isinstance(raw_note, Mapping):
            continue
        rows.append(
            "<li>"
            f"{_escape(raw_note.get('author', '-'))} "
            f"[{_escape(raw_note.get('kind', '-'))}] "
            f"{_escape(raw_note.get('task_id', '-'))}: "
            f"{_escape(raw_note.get('text', ''))}"
            "</li>"
        )
    return "".join(rows) if rows else '<li class="muted">No progress notes</li>'


def _render_manifest(manifest: ManifestCards) -> str:
    """Render advertised capability cards."""
    if not manifest:
        return '<li class="muted">No advertised capabilities</li>'
    rows: list[str] = []
    for card in manifest:
        classes = card.get("task_classes", [])
        class_text = (
            ", ".join(_escape(item) for item in classes) if isinstance(classes, list) else "-"
        )
        contracts = card.get("contracts", [])
        contract_text = (
            f" · contracts: {len(contracts)}" if isinstance(contracts, list) and contracts else ""
        )
        rows.append(
            "<li>"
            f"<strong>{_escape(card.get('agent', '-'))}</strong> "
            f"<small>{class_text}{contract_text}</small><br>"
            f"{_escape(card.get('description', ''))}"
            "</li>"
        )
    return "".join(rows)


def render_dashboard_html(
    snapshot: DashboardSnapshot,
    *,
    refresh_seconds: int = 5,
    a2a_state_file: str | Path | None = None,
) -> str:
    """Render a complete read-only HTML dashboard page.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        Read-side snapshot fetched from the hub.
    refresh_seconds : int, optional
        Browser refresh interval. Values below one are coerced to one second.
    a2a_state_file : str, pathlib.Path, or None, optional
        Optional persisted A2A bridge state file used to populate the fleet
        visibility section.

    Returns
    -------
    str
        Escaped HTML page.
    """
    refresh = max(1, int(refresh_seconds))
    ready = snapshot.board.get("ready", [])
    ready_items = [str(item) for item in ready] if isinstance(ready, list) else []
    fleet_html = render_fleet_visibility_html(snapshot, a2a_state_file=a2a_state_file)
    fallback_html = f"""<h1>SYNAPSE CHANNEL dashboard</h1>
  <div class="grid">
    <section>
      <h2>Online agents ({len(snapshot.online_agents)})</h2>
      <ul>{_render_list(snapshot.online_agents)}</ul>
    </section>
    <section><h2>Ready tasks</h2><ul>{_render_list(ready_items)}</ul></section>
    <section><h2>Active claims</h2><ul>{_render_claims(snapshot.state)}</ul></section>
    <section><h2>Board tasks</h2><ul>{_render_tasks(snapshot.board)}</ul></section>
    <section><h2>Recent progress</h2><ul>{_render_progress(snapshot.board)}</ul></section>
    <section><h2>Capability manifest</h2><ul>{_render_manifest(snapshot.manifest)}</ul></section>
    {fleet_html}
  </div>"""
    return render_cockpit_html(refresh_seconds=refresh, fallback_html=fallback_html)


def _json_bytes(snapshot: DashboardSnapshot, *, a2a_state_file: str | Path | None) -> bytes:
    """Return stable UTF-8 JSON bytes for ``snapshot``."""
    return json.dumps(
        snapshot.to_dict(a2a_state_file=a2a_state_file),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")


RELIABILITY_PATH = "/reliability.json"
"""Read-only endpoint serving the reliability audit-signal report."""

EVENTS_PATH = "/events.json"
"""Read-only endpoint serving the raw event-log tail past a cursor."""

METRICS_FEED_PATH = "/metrics.json"
"""Read-only endpoint serving store-attested log metrics for the cockpit."""

CAUSALITY_PATH = "/causality.json"
"""Read-only endpoint answering one causality query in the CLI's JSON shape."""

FEDERATION_PATH = "/federation.json"
"""Read-only endpoint serving the imported peerings from the federation store."""

COCKPIT_DIST_PREFIX = "/cockpit/"
"""URL prefix under which an operator-named cockpit build directory is served."""

_DIST_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".map": "application/json",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain",
}
"""Suffixes the cockpit dist serving recognises; anything else is refused."""


class _DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler populated by ``start_dashboard_server`` class attributes."""

    uri: ClassVar[str]
    dashboard_name: ClassVar[str]
    token: ClassVar[str | None]
    ready_timeout: ClassVar[float]
    response_timeout: ClassVar[float]
    refresh_seconds: ClassVar[int]
    a2a_state_file: ClassVar[Path | None]
    dashboard_token: ClassVar[str | None]
    reliability_db: ClassVar[Path | None]
    federation_store: ClassVar[Path | None]
    cockpit_dist: ClassVar[Path | None]

    def do_GET(self) -> None:
        """Serve the dashboard HTML page, JSON snapshot, or a 404 response."""
        if self.dashboard_token is not None and not self._authorized():
            self._write(
                HTTPStatus.UNAUTHORIZED,
                b"dashboard authorization required\n",
                content_type="text/plain",
                authenticate=True,
            )
            return
        path = urlsplit(self.path).path
        asset_name = path.lstrip("/")
        if asset_name in COCKPIT_ASSETS:
            self._write(
                HTTPStatus.OK,
                load_cockpit_asset(asset_name).encode("utf-8"),
                content_type=COCKPIT_ASSETS[asset_name],
            )
            return
        if path == STUDIO_REFERENCE_PATH:
            # The Studio design-system reference renders no live data, so it serves
            # without reaching the hub — a stable visual reference even when offline.
            self._write(
                HTTPStatus.OK,
                render_studio_reference_html().encode("utf-8"),
                content_type="text/html",
            )
            return
        if path == STUDIO_COMMAND_PATH:
            # The command-centre shell is hub-independent: it serves without reaching the
            # hub and fetches the live /studio.json projection from the browser, so the
            # page loads (and shows an offline state) even when the hub is down.
            self._write(
                HTTPStatus.OK,
                render_studio_command_html(poll_seconds=self.refresh_seconds).encode("utf-8"),
                content_type="text/html",
            )
            return
        if path == RELIABILITY_PATH:
            # Served from the durable event store, not the live hub: the
            # reliability report is an offline audit surface, so it stays
            # available when the hub is down and needs no hub round-trip.
            self._serve_reliability()
            return
        if path == EVENTS_PATH:
            self._serve_events(urlsplit(self.path).query)
            return
        if path == METRICS_FEED_PATH:
            self._serve_metrics_feed()
            return
        if path == CAUSALITY_PATH:
            self._serve_causality(urlsplit(self.path).query)
            return
        if path == FEDERATION_PATH:
            self._serve_federation()
            return
        if path.startswith(COCKPIT_DIST_PREFIX) or path == COCKPIT_DIST_PREFIX.rstrip("/"):
            self._serve_cockpit_dist(path)
            return
        if path not in {"/", "/index.html", "/snapshot.json", STUDIO_SNAPSHOT_PATH}:
            self._write(HTTPStatus.NOT_FOUND, b"not found\n", content_type="text/plain")
            return
        try:
            snapshot = asyncio.run(
                fetch_dashboard_snapshot(
                    uri=self.uri,
                    name=self.dashboard_name,
                    token=self.token,
                    ready_timeout=self.ready_timeout,
                    response_timeout=self.response_timeout,
                )
            )
        except DashboardUnavailable as exc:
            body = f"{exc}\n".encode()
            self._write(HTTPStatus.SERVICE_UNAVAILABLE, body, content_type="text/plain")
            return
        if path == "/snapshot.json":
            self._write(
                HTTPStatus.OK,
                _json_bytes(snapshot, a2a_state_file=self.a2a_state_file),
                content_type="application/json",
            )
            return
        if path == STUDIO_SNAPSHOT_PATH:
            studio = build_studio_snapshot(snapshot.to_dict(a2a_state_file=self.a2a_state_file))
            self._write(
                HTTPStatus.OK,
                json.dumps(studio, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                content_type="application/json",
            )
            return
        html_body = render_dashboard_html(
            snapshot,
            refresh_seconds=self.refresh_seconds,
            a2a_state_file=self.a2a_state_file,
        ).encode("utf-8")
        self._write(HTTPStatus.OK, html_body, content_type="text/html")

    def _serve_reliability(self) -> None:
        """Serve the reliability audit-signal report, or its honest absence.

        Without a configured store the endpoint is 404 — the cockpit panel
        treats that as the feed being absent, states so, and activates the
        moment the operator starts the dashboard with ``--reliability-db``.
        An unreadable store is 503, fail-visible rather than an empty
        report pretending the log is clean.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"reliability feed not configured; start the dashboard with --reliability-db\n",
                content_type="text/plain",
            )
            return
        try:
            report = run_reliability_report(self.reliability_db)
        except ValueError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"{exc}\n".encode(),
                content_type="text/plain",
            )
            return
        self._write(
            HTTPStatus.OK,
            json.dumps(reliability_to_json(report), ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            ),
            content_type="application/json",
        )

    def _serve_metrics_feed(self) -> None:
        """Serve store-attested log metrics, or their honest absence.

        Same posture as the other store feeds: 404 without ``--feeds-db``
        (the panel states the feed is absent), 503 on an unreadable store
        (fail-visible, never an empty document pretending quiet), and the
        document itself explains that the live process registry lives on
        the hub's own ``/metrics`` endpoint, not here.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"metrics feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_metrics_feed(self.reliability_db)
        except ValueError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"{exc}\n".encode(),
                content_type="text/plain",
            )
            return
        self._write(
            HTTPStatus.OK,
            json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
        )

    def _serve_events(self, query: str) -> None:
        """Serve the raw event-log tail past a cursor, or its honest absence.

        Rides the same durable store as the reliability feed; parameters are
        ``since`` (exclusive sequence cursor) and ``limit``. Malformed numbers
        are a 400 naming the parameter, not a silent default.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"events feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        params = parse_qs(query)
        since_raw = params.get("since", ["0"])[0]
        try:
            limit = int(params.get("limit", [str(DEFAULT_EVENTS_LIMIT)])[0])
            since = None if since_raw == "latest" else int(since_raw)
        except ValueError:
            self._write(
                HTTPStatus.BAD_REQUEST,
                b"since must be an integer or 'latest'; limit must be an integer\n",
                content_type="text/plain",
            )
            return
        try:
            if since is None:
                # the tail shortcut: start at the log's end instead of
                # walking a large history just to catch up to now
                since = latest_cursor(self.reliability_db)
            document = build_events_tail(self.reliability_db, since=since, limit=limit)
        except ValueError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE, f"{exc}\n".encode(), content_type="text/plain"
            )
            return
        self._write_json(document)

    def _serve_causality(self, query: str) -> None:
        """Answer one causality query in the CLI's exact JSON shape.

        ``seq=N`` or ``task=ID`` anchors the query; ``direction`` defaults to
        ``causes``. A bad anchor or direction is a 400 with the reason; a task
        the log never recorded is a 404 — absent, not invented.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"causality feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        params = parse_qs(query)
        direction = params.get("direction", ["causes"])[0]
        task = params.get("task", [None])[0]
        seq_raw = params.get("seq", [None])[0]
        try:
            seq = int(seq_raw) if seq_raw is not None else None
        except ValueError:
            self._write(
                HTTPStatus.BAD_REQUEST, b"seq must be an integer\n", content_type="text/plain"
            )
            return
        try:
            document = build_causality_feed(
                self.reliability_db, direction=direction, seq=seq, task=task
            )
        except ValueError as exc:
            reason = str(exc)
            status = (
                HTTPStatus.NOT_FOUND
                if reason.startswith("no recorded event for task")
                else HTTPStatus.BAD_REQUEST
            )
            if reason.startswith("missing event store"):
                status = HTTPStatus.SERVICE_UNAVAILABLE
            self._write(status, f"{reason}\n".encode(), content_type="text/plain")
            return
        self._write_json(document)

    def _serve_federation(self) -> None:
        """Serve the imported peerings, or the feed's honest absence."""
        if self.federation_store is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"federation feed not configured; start the dashboard with --federation-store\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_federation_feed(self.federation_store)
        except FederationStoreError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE, f"{exc}\n".encode(), content_type="text/plain"
            )
            return
        self._write_json(document)

    def _serve_cockpit_dist(self, path: str) -> None:
        """Serve one file from the operator-named cockpit build directory.

        ``/cockpit/`` maps to ``index.html``; every other path resolves inside
        the named directory and is refused when it escapes it (path
        traversal), carries an unrecognised suffix, or does not exist.
        """
        if self.cockpit_dist is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"cockpit build not configured; start the dashboard with --cockpit-dist\n",
                content_type="text/plain",
            )
            return
        relative = path[len(COCKPIT_DIST_PREFIX) :] if path.startswith(COCKPIT_DIST_PREFIX) else ""
        if relative == "":
            relative = "index.html"
        root = self.cockpit_dist.resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            self._write(HTTPStatus.NOT_FOUND, b"not found\n", content_type="text/plain")
            return
        content_type = _DIST_CONTENT_TYPES.get(target.suffix.lower())
        if content_type is None or not target.is_file():
            self._write(HTTPStatus.NOT_FOUND, b"not found\n", content_type="text/plain")
            return
        self._write(HTTPStatus.OK, target.read_bytes(), content_type=content_type)

    def _write_json(self, document: dict[str, object]) -> None:
        """Write one JSON feed response with the shared headers."""
        self._write(
            HTTPStatus.OK,
            json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
        )

    def log_message(self, _format: str, *_args: object) -> None:
        """Suppress stdlib access-log noise during CLI and tests."""
        return None

    def _authorized(self) -> bool:
        """Return whether the request supplies the configured bearer token."""
        if self.dashboard_token is None:
            return True
        authorization = self.headers.get("Authorization", "") or ""
        return hmac.compare_digest(authorization, f"Bearer {self.dashboard_token}")

    def _write(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str,
        authenticate: bool = False,
    ) -> None:
        """Write one HTTP response."""
        self.send_response(status.value)
        if authenticate:
            self.send_header("WWW-Authenticate", 'Bearer realm="synapse-dashboard"')
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _handler_class(
    *,
    uri: str,
    name: str,
    token: str | None,
    ready_timeout: float,
    response_timeout: float,
    refresh_seconds: int,
    a2a_state_file: Path | None,
    dashboard_token: str | None,
    reliability_db: Path | None,
    federation_store: Path | None,
    cockpit_dist: Path | None,
) -> type[_DashboardHandler]:
    """Create an isolated handler class for one dashboard server."""
    bound_uri = uri
    bound_name = name
    bound_token = token
    bound_ready_timeout = ready_timeout
    bound_response_timeout = response_timeout
    bound_refresh_seconds = refresh_seconds
    bound_a2a_state_file = a2a_state_file
    bound_dashboard_token = dashboard_token
    bound_reliability_db = reliability_db
    bound_federation_store = federation_store
    bound_cockpit_dist = cockpit_dist

    class BoundDashboardHandler(_DashboardHandler):
        """Dashboard handler bound to one hub URI and dashboard identity."""

        uri = bound_uri
        dashboard_name = bound_name
        token = bound_token
        ready_timeout = bound_ready_timeout
        response_timeout = bound_response_timeout
        refresh_seconds = bound_refresh_seconds
        a2a_state_file = bound_a2a_state_file
        dashboard_token = bound_dashboard_token
        reliability_db = bound_reliability_db
        federation_store = bound_federation_store
        cockpit_dist = bound_cockpit_dist

    return BoundDashboardHandler


def start_dashboard_server(
    *,
    host: str,
    port: int,
    uri: str,
    name: str,
    token: str | None,
    ready_timeout: float,
    response_timeout: float,
    refresh_seconds: int,
    allow_non_loopback: bool,
    a2a_state_file: str | Path | None = None,
    dashboard_token: str | None = None,
    reliability_db: str | Path | None = None,
    federation_store: str | Path | None = None,
    cockpit_dist: str | Path | None = None,
) -> DashboardServer:
    """Start a background read-only dashboard HTTP server.

    Parameters
    ----------
    host, port : str, int
        HTTP bind address. Port ``0`` asks the OS for a free local port.
    uri, name : str
        Hub URI and dashboard client identity used for read-side queries.
    token : str or None
        Shared-secret token for a secured hub.
    ready_timeout, response_timeout : float
        Hub connection and snapshot wait bounds.
    refresh_seconds : int
        HTML browser refresh interval.
    allow_non_loopback : bool
        Whether to permit non-loopback HTTP binds.
    a2a_state_file : str, pathlib.Path, or None, optional
        Optional persisted A2A bridge state file to summarise in the dashboard
        fleet section.
    dashboard_token : str or None, optional
        Optional HTTP bearer token for dashboard browser and JSON requests. A
        token is generated automatically for non-loopback binds when the caller
        does not provide one.
    reliability_db : str, pathlib.Path, or None, optional
        Hub event store powering the store-backed feeds —
        ``/reliability.json``, ``/events.json``, and ``/causality.json``;
        without it each endpoint reports its absence with 404.
    federation_store : str, pathlib.Path, or None, optional
        Operator federation store powering ``/federation.json``.
    cockpit_dist : str, pathlib.Path, or None, optional
        Built cockpit directory served under ``/cockpit/``.

    Returns
    -------
    DashboardServer
        Handle with URL helpers and a close method.
    """
    validate_dashboard_bind(host, allow_non_loopback=allow_non_loopback)
    effective_dashboard_token, dashboard_token_generated = _resolve_dashboard_token(
        host,
        allow_non_loopback=allow_non_loopback,
        dashboard_token=dashboard_token,
    )
    handler = _handler_class(
        uri=uri,
        name=name,
        token=token,
        ready_timeout=ready_timeout,
        response_timeout=response_timeout,
        refresh_seconds=max(1, int(refresh_seconds)),
        a2a_state_file=Path(a2a_state_file) if a2a_state_file is not None else None,
        dashboard_token=effective_dashboard_token,
        reliability_db=Path(reliability_db) if reliability_db is not None else None,
        federation_store=Path(federation_store) if federation_store is not None else None,
        cockpit_dist=Path(cockpit_dist) if cockpit_dist is not None else None,
    )
    server = ThreadingHTTPServer((host, int(port)), handler)
    thread = threading.Thread(target=server.serve_forever, name="synapse-dashboard", daemon=True)
    thread.start()
    return DashboardServer(
        server=server,
        thread=thread,
        dashboard_token=effective_dashboard_token,
        dashboard_token_generated=dashboard_token_generated,
    )
