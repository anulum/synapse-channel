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
import html
import ipaddress
import json
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar, Final
from urllib.parse import urlsplit

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType

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

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary for HTTP responses."""
        return asdict(self)


@dataclass(frozen=True)
class DashboardServer:
    """Running local dashboard server handle.

    Parameters
    ----------
    server : ThreadingHTTPServer
        Bound stdlib HTTP server.
    thread : threading.Thread
        Background thread running ``serve_forever``.
    """

    server: ThreadingHTTPServer
    thread: threading.Thread

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
        rows.append(
            "<li>"
            f"<strong>{_escape(card.get('agent', '-'))}</strong> "
            f"<small>{class_text}</small><br>"
            f"{_escape(card.get('description', ''))}"
            "</li>"
        )
    return "".join(rows)


def render_dashboard_html(snapshot: DashboardSnapshot, *, refresh_seconds: int = 5) -> str:
    """Render a complete read-only HTML dashboard page.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        Read-side snapshot fetched from the hub.
    refresh_seconds : int, optional
        Browser refresh interval. Values below one are coerced to one second.

    Returns
    -------
    str
        Escaped HTML page.
    """
    refresh = max(1, int(refresh_seconds))
    ready = snapshot.board.get("ready", [])
    ready_items = [str(item) for item in ready] if isinstance(ready, list) else []
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{refresh}">
  <title>SYNAPSE CHANNEL dashboard</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; background: #f7f7f4; color: #1d1d1b; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.5rem; margin: 0 0 16px; }}
    h2 {{ font-size: 1rem; margin: 0 0 10px; }}
    section {{ border: 1px solid #d8d4c8; border-radius: 8px; padding: 16px; background: #ffffff; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
    }}
    ul {{ list-style: none; margin: 0; padding: 0; }}
    li {{ border-top: 1px solid #ece8dd; padding: 8px 0; }}
    li:first-child {{ border-top: 0; }}
    small, .muted {{ color: #5f6468; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #191a1a; color: #f2f0e8; }}
      section {{ background: #222424; border-color: #3a3d3d; }}
      li {{ border-color: #343838; }}
      small, .muted {{ color: #b4b8ba; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>SYNAPSE CHANNEL dashboard</h1>
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
  </div>
</main>
</body>
</html>
"""


def _json_bytes(snapshot: DashboardSnapshot) -> bytes:
    """Return stable UTF-8 JSON bytes for ``snapshot``."""
    return json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")


class _DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler populated by ``start_dashboard_server`` class attributes."""

    uri: ClassVar[str]
    dashboard_name: ClassVar[str]
    token: ClassVar[str | None]
    ready_timeout: ClassVar[float]
    response_timeout: ClassVar[float]
    refresh_seconds: ClassVar[int]

    def do_GET(self) -> None:
        """Serve the dashboard HTML page, JSON snapshot, or a 404 response."""
        path = urlsplit(self.path).path
        if path not in {"/", "/index.html", "/snapshot.json"}:
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
            self._write(HTTPStatus.OK, _json_bytes(snapshot), content_type="application/json")
            return
        html_body = render_dashboard_html(snapshot, refresh_seconds=self.refresh_seconds).encode(
            "utf-8"
        )
        self._write(HTTPStatus.OK, html_body, content_type="text/html")

    def log_message(self, _format: str, *_args: object) -> None:
        """Suppress stdlib access-log noise during CLI and tests."""
        return None

    def _write(self, status: HTTPStatus, body: bytes, *, content_type: str) -> None:
        """Write one HTTP response."""
        self.send_response(status.value)
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
) -> type[_DashboardHandler]:
    """Create an isolated handler class for one dashboard server."""
    bound_uri = uri
    bound_name = name
    bound_token = token
    bound_ready_timeout = ready_timeout
    bound_response_timeout = response_timeout
    bound_refresh_seconds = refresh_seconds

    class BoundDashboardHandler(_DashboardHandler):
        """Dashboard handler bound to one hub URI and dashboard identity."""

        uri = bound_uri
        dashboard_name = bound_name
        token = bound_token
        ready_timeout = bound_ready_timeout
        response_timeout = bound_response_timeout
        refresh_seconds = bound_refresh_seconds

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

    Returns
    -------
    DashboardServer
        Handle with URL helpers and a close method.
    """
    validate_dashboard_bind(host, allow_non_loopback=allow_non_loopback)
    handler = _handler_class(
        uri=uri,
        name=name,
        token=token,
        ready_timeout=ready_timeout,
        response_timeout=response_timeout,
        refresh_seconds=max(1, int(refresh_seconds)),
    )
    server = ThreadingHTTPServer((host, int(port)), handler)
    thread = threading.Thread(target=server.serve_forever, name="synapse-dashboard", daemon=True)
    thread.start()
    return DashboardServer(server=server, thread=thread)
