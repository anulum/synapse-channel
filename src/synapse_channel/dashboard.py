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
import json
import threading
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, Final
from urllib.parse import parse_qs, urlsplit

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.federation_store import FederationStoreError
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.reliability import reliability_to_json, run_reliability_report
from synapse_channel.dashboard_bind import (
    _resolve_dashboard_token,
    validate_dashboard_bind,
)
from synapse_channel.dashboard_cockpit import (
    COCKPIT_ASSETS,
    load_cockpit_asset,
)
from synapse_channel.dashboard_fleet import build_fleet_visibility
from synapse_channel.dashboard_operator import (
    DENIED,
    REJECTED,
    UNREACHABLE,
    OperatorRelay,
    RelayOutcome,
    WriteRateLimiter,
)
from synapse_channel.dashboard_render import render_dashboard_html
from synapse_channel.dashboard_risk import build_risk_view
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
    event_store_key,
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
from synapse_channel.observed_peers import (
    ObservedPeerSnapshot,
    ObservedPeerSpec,
    fetch_observed_peers,
    network_observed_fetcher_factory,
    observed_peers_to_dict,
)
from synapse_channel.studio_snapshot import STUDIO_SNAPSHOT_PATH, build_studio_snapshot

SnapshotMapping = dict[str, Any]
"""Mutable mapping shape used for hub snapshot payloads."""

ManifestCards = list[dict[str, Any]]
"""List shape used by the hub capability manifest snapshot."""


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
    hub_version : str
        Package version the live hub reports, for a cockpit pinning indicator.
        Empty when the hub predates the field.
    hub_id : str
        Hub identifier reported by the welcome frame. Empty only if the client
        connects to a hub old enough not to send the field.
    config_epoch : str
        Fingerprint of the hub's configuration posture, for the same indicator.
        Empty when the hub predates the field or was built without a config.
    agent_roles : dict[str, list[str]]
        The ``<project>/<role>`` roles each online agent answers to, mirrored from
        the hub's ``who`` snapshot so the cockpit can show role bindings alongside
        the roster. Empty for an agent that declared none, or for a hub that
        predates role addressing.
    observed_peers : tuple[ObservedPeerSnapshot, ...]
        Optional advisory peer snapshots fetched through the multi-hub log
        request path. Empty unless the dashboard was started with observed peers.
    """

    online_agents: list[str]
    state: SnapshotMapping
    board: SnapshotMapping
    manifest: ManifestCards
    hub_version: str = ""
    hub_id: str = ""
    config_epoch: str = ""
    agent_roles: dict[str, list[str]] = field(default_factory=dict)
    observed_peers: tuple[ObservedPeerSnapshot, ...] = ()

    def to_dict(self, *, a2a_state_file: str | Path | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary for HTTP responses.

        Parameters
        ----------
        a2a_state_file : str, pathlib.Path, or None, optional
            Optional persisted A2A bridge state file used to populate the
            derived ``fleet.a2a`` summary.
        """
        payload = asdict(self)
        payload["observed_peers"] = observed_peers_to_dict(self.observed_peers)
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


def _agent_roles_from_who(who: Mapping[str, Any]) -> dict[str, list[str]]:
    """Return the roster's role bindings from a ``who`` snapshot, defensively.

    Each agent name maps to the ``<project>/<role>`` names it answers to. A
    non-mapping ``agent_roles`` field yields an empty map, a non-list binding for
    one agent is dropped, and names and roles are string-coerced — so a malformed
    or version-skewed hub snapshot degrades to an empty or partial map rather than
    propagating bad types into the served dashboard document.
    """
    raw = who.get("agent_roles", {})
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(name): [str(role) for role in roles]
        for name, roles in raw.items()
        if isinstance(roles, list)
    }


async def fetch_dashboard_snapshot(
    *,
    uri: str,
    name: str,
    token: str | None,
    ready_timeout: float = 5.0,
    response_timeout: float = 2.0,
    observed_peers: tuple[ObservedPeerSpec, ...] = (),
    observed_token: str | None = None,
    observed_timeout: float = 10.0,
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
    observed_peers : tuple[ObservedPeerSpec, ...], optional
        Peer hubs to fetch through the multi-hub log path and render as
        advisory observed state.

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
        who = messages[MessageType.WHO_SNAPSHOT]
        observed = await fetch_observed_peers(
            observed_peers,
            fetcher_factory=network_observed_fetcher_factory(
                local_id=f"{name}-observed",
                token=observed_token,
                timeout=observed_timeout,
            ),
        )
        return DashboardSnapshot(
            online_agents=[str(agent_name) for agent_name in who.get("online_agents", [])],
            state=dict(messages[MessageType.STATE_SNAPSHOT].get("snapshot", {})),
            board=dict(messages[MessageType.BOARD_SNAPSHOT].get("board", {})),
            manifest=[
                dict(card)
                for card in messages[MessageType.MANIFEST_SNAPSHOT].get("manifest", [])
                if isinstance(card, Mapping)
            ],
            hub_version=str(who.get("hub_version", "")),
            hub_id=agent.hub_id if agent.hub_id != "unknown" else "",
            config_epoch=str(who.get("config_epoch", "")),
            agent_roles=_agent_roles_from_who(who),
            observed_peers=observed,
        )
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


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

STATE_AT_PATH = "/state-at.json"
"""Read-only endpoint reconstructing coordination state as of an event seq."""

MERKLE_PROOF_PATH = "/merkle-proof.json"
"""Read-only endpoint proving one event's inclusion in the attested log."""

HEALTH_ANOMALIES_PATH = "/health-anomalies.json"
"""Read-only endpoint flagging coordination anomalies — the hub-side alert view."""

CAUSALITY_PATH = "/causality.json"
"""Read-only endpoint answering one causality query in the CLI's JSON shape."""

FEDERATION_PATH = "/federation.json"
"""Read-only endpoint serving the imported peerings from the federation store."""

SESSIONS_PATH = "/sessions.json"
"""Read-only endpoint reporting opt-in session telemetry left in the durable log."""

WAITS_PATH = "/waits.json"
"""Read-only endpoint listing pending coordination gates — tasks blocked on deps."""

OPERATOR_ACTIONS_PATH = "/operator-actions.json"
"""Read-only endpoint serving governed operator-action audit history."""

RECEIPTS_PATH = "/receipts.json"
"""Read-only endpoint serving universal receipt projections from the event log."""

COCKPIT_DIST_PREFIX = "/cockpit/"
"""URL prefix under which an operator-named cockpit build directory is served."""

MESSAGE_PATH = "/message"
"""Operator write endpoint (POST) relaying one chat message to the fleet."""

TASK_PATH = "/task"
"""Operator write endpoint (POST) declaring one board task for the fleet."""

TASK_UPDATE_PATH = "/task/update"
"""Operator write endpoint (POST) updating a board task's status or progress note."""

MAX_OPERATOR_BODY_BYTES: Final = 64 * 1024
"""Largest operator write body accepted; anything larger is a 400."""

OPERATOR_RATE_MAX: Final = 30
"""Operator write actions permitted within :data:`OPERATOR_RATE_WINDOW_SECONDS`."""

OPERATOR_RATE_WINDOW_SECONDS: Final = 60.0
"""Sliding-window length for the operator write rate limit."""

_OUTCOME_STATUS: Final[dict[str, HTTPStatus]] = {
    DENIED: HTTPStatus.FORBIDDEN,
    REJECTED: HTTPStatus.CONFLICT,
    UNREACHABLE: HTTPStatus.SERVICE_UNAVAILABLE,
}
"""Relay-outcome to HTTP-status map; an unlisted (accepted) outcome is ``200``."""

# The dashboard and cockpit are self-contained — no CDN, external font, or remote
# script — so a same-origin content policy costs nothing and blocks injected remote
# resources. `'unsafe-inline'` for script/style is retained deliberately: the
# server-rendered pages carry inline `<script>`/`<style>` and there is no nonce
# pipeline; `data:` images cover embedded favicons and glyphs. The framing and
# base-uri directives are the clickjacking and base-tag-injection guards.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)
_SECURITY_HEADERS: Final[tuple[tuple[str, str], ...]] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", _CONTENT_SECURITY_POLICY),
)
"""Browser-hardening headers sent on every dashboard response."""


def _is_string_list(value: object) -> bool:
    """Return whether ``value`` is a list whose every element is a string."""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


# SQLite stores integers as signed 64-bit. A query integer beyond that range parses
# fine as an unbounded Python int, then raises OverflowError deep inside a store
# query — a 500. The feed handlers bound it here so an out-of-range value is a 400.
_SQLITE_INT_MIN: Final = -(2**63)
_SQLITE_INT_MAX: Final = 2**63 - 1


def _bounded_query_int(raw: str) -> int:
    """Parse a query integer, rejecting values outside SQLite's 64-bit range.

    Raises
    ------
    ValueError
        For a non-numeric value, or one too large to reach the durable store
        safely. Every feed handler already maps this to a ``400``.
    """
    value = int(raw)
    if not _SQLITE_INT_MIN <= value <= _SQLITE_INT_MAX:
        raise ValueError("integer out of range")
    return value


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
    token_protects_reads: ClassVar[bool]
    reliability_db: ClassVar[Path | None]
    reliability_db_key_file: ClassVar[Path | None]
    federation_store: ClassVar[Path | None]
    cockpit_dist: ClassVar[Path | None]
    operator_enabled: ClassVar[bool]
    operator_name: ClassVar[str]
    operator_rate_limiter: ClassVar[WriteRateLimiter]
    observed_peers: ClassVar[tuple[ObservedPeerSpec, ...]]
    observed_token: ClassVar[str | None]
    observed_timeout: ClassVar[float]

    def do_GET(self) -> None:
        """Serve the dashboard HTML page, JSON snapshot, or a 404 response."""
        # Reads are gated only when the token protects reads — a caller-supplied token
        # or one generated for an exposed bind. A token generated solely to gate
        # operator writes on loopback leaves reads open, so the read-only browser
        # cockpit still loads (a browser cannot send an Authorization header on
        # navigation, and the write-path is protected by do_POST regardless).
        reads_gated = self.dashboard_token is not None and self.token_protects_reads
        if reads_gated and not self._authorized():
            self._write(
                HTTPStatus.UNAUTHORIZED,
                b"dashboard authorization required\n",
                content_type="text/plain",
                authenticate=True,
            )
            return
        # Bind optional SQLCipher key for every store-backed feed on this request.
        with event_store_key(self.reliability_db_key_file):
            self._do_get_routed()

    def _do_get_routed(self) -> None:
        """Route a GET after auth and SQLCipher key binding."""
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
        if path in {STUDIO_COMMAND_PATH, "/"}:
            # Studio command centre is the dashboard front door: hub-independent shell
            # that fills in from /studio.json. Classic hub HTML remains at /classic.
            self._write(
                HTTPStatus.OK,
                render_studio_command_html(poll_seconds=self.refresh_seconds).encode("utf-8"),
                content_type="text/html",
            )
            return
        if path == "/classic":
            # Legacy instrument HTML that fetches a live hub snapshot server-side.
            path = "/index.html"
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
        if path == STATE_AT_PATH:
            self._serve_state_at(urlsplit(self.path).query)
            return
        if path == MERKLE_PROOF_PATH:
            self._serve_merkle_proof(urlsplit(self.path).query)
            return
        if path == HEALTH_ANOMALIES_PATH:
            self._serve_health_anomalies()
            return
        if path == CAUSALITY_PATH:
            self._serve_causality(urlsplit(self.path).query)
            return
        if path == FEDERATION_PATH:
            self._serve_federation()
            return
        if path == SESSIONS_PATH:
            self._serve_sessions()
            return
        if path == WAITS_PATH:
            self._serve_waits()
            return
        if path == OPERATOR_ACTIONS_PATH:
            self._serve_operator_actions(urlsplit(self.path).query)
            return
        if path == RECEIPTS_PATH:
            self._serve_receipts(urlsplit(self.path).query)
            return
        if path.startswith(COCKPIT_DIST_PREFIX) or path == COCKPIT_DIST_PREFIX.rstrip("/"):
            self._serve_cockpit_dist(path)
            return
        if path not in {"/index.html", "/classic", "/snapshot.json", STUDIO_SNAPSHOT_PATH}:
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
                    observed_peers=self.observed_peers,
                    observed_token=self.observed_token,
                    observed_timeout=self.observed_timeout,
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
        # /classic and /index.html — server-side hub snapshot HTML (pre-Studio cockpit).
        html_body = render_dashboard_html(
            snapshot,
            refresh_seconds=self.refresh_seconds,
            a2a_state_file=self.a2a_state_file,
        ).encode("utf-8")
        self._write(HTTPStatus.OK, html_body, content_type="text/html")

    def do_POST(self) -> None:
        """Relay one operator write action, or refuse it.

        Off by default: without operator mode every write route is a 404,
        indistinguishable from an unknown path, so a read-only dashboard reveals
        no write surface at all. When armed, a write must be an
        ``application/json`` request — a cross-origin web page can only send a
        CORS "simple" content type without a preflight, and this surface answers
        no preflight, so requiring JSON blocks a browser on another origin from
        driving an operator write (a local CSRF). A write also carries the
        dashboard bearer token, which is always present under operator mode (a
        token is generated for the write-path even on loopback, so a same-host
        non-browser process cannot write unauthenticated), is rate-limited, and is
        authorised and audited by the hub — this handler only validates the body
        and relays the frame.
        """
        if not self.operator_enabled:
            self._write(HTTPStatus.NOT_FOUND, b"not found\n", content_type="text/plain")
            return
        if self.dashboard_token is not None and not self._authorized():
            self._write(
                HTTPStatus.UNAUTHORIZED,
                b"dashboard authorization required\n",
                content_type="text/plain",
                authenticate=True,
            )
            return
        route = urlsplit(self.path).path
        if route not in (MESSAGE_PATH, TASK_PATH, TASK_UPDATE_PATH):
            self._write(HTTPStatus.NOT_FOUND, b"not found\n", content_type="text/plain")
            return
        if not self._is_json_request():
            self._write(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                b"operator writes require Content-Type: application/json\n",
                content_type="text/plain",
            )
            return
        if not self.operator_rate_limiter.allow():
            self._write(
                HTTPStatus.TOO_MANY_REQUESTS,
                b"operator write rate limit exceeded\n",
                content_type="text/plain",
            )
            return
        body = self._read_json_body()
        if body is None:
            self._write(
                HTTPStatus.BAD_REQUEST,
                b"request body must be a JSON object within the size limit\n",
                content_type="text/plain",
            )
            return
        if route == MESSAGE_PATH:
            self._handle_message(body)
        elif route == TASK_PATH:
            self._handle_task(body)
        else:
            self._handle_task_update(body)

    def _handle_message(self, body: dict[str, Any]) -> None:
        """Validate a chat write and relay it, or answer 400 on a bad body."""
        to = body.get("to")
        text = body.get("text")
        if not isinstance(to, str) or not to.strip():
            self._bad_request("'to' must be a non-empty string")
            return
        if not isinstance(text, str) or not text.strip():
            self._bad_request("'text' must be a non-empty string")
            return
        target = to.strip()
        self._dispatch_relay(
            "message", {"to": target}, lambda relay: relay.relay_message(target, text)
        )

    def _handle_task(self, body: dict[str, Any]) -> None:
        """Validate a task declaration and relay it, or answer 400 on a bad body."""
        task_id = body.get("id")
        title = body.get("title")
        depends_on = body.get("depends_on", [])
        if not isinstance(task_id, str) or not task_id.strip():
            self._bad_request("'id' must be a non-empty string")
            return
        if not isinstance(title, str) or not title.strip():
            self._bad_request("'title' must be a non-empty string")
            return
        if not _is_string_list(depends_on):
            self._bad_request("'depends_on' must be a list of strings")
            return
        task = task_id.strip()
        deps = tuple(dep.strip() for dep in depends_on if dep.strip())
        self._dispatch_relay(
            "task", {"id": task}, lambda relay: relay.relay_task(task, title, depends_on=deps)
        )

    def _handle_task_update(self, body: dict[str, Any]) -> None:
        """Validate a task update and relay it, or answer 400 on a bad body.

        At least one of ``status`` or ``note`` must be present; each, when present,
        must be a non-empty string.
        """
        task_id = body.get("id")
        status_value = body.get("status")
        note = body.get("note")
        if not isinstance(task_id, str) or not task_id.strip():
            self._bad_request("'id' must be a non-empty string")
            return
        if status_value is not None and (
            not isinstance(status_value, str) or not status_value.strip()
        ):
            self._bad_request("'status' must be a non-empty string when present")
            return
        if note is not None and (not isinstance(note, str) or not note.strip()):
            self._bad_request("'note' must be a non-empty string when present")
            return
        if status_value is None and note is None:
            self._bad_request("a task update needs at least one of 'status' or 'note'")
            return
        task = task_id.strip()
        new_status = status_value.strip() if isinstance(status_value, str) else None
        self._dispatch_relay(
            "task_update",
            {"id": task},
            lambda relay: relay.relay_task_update(task, status=new_status, note=note),
        )

    def _dispatch_relay(
        self,
        action: str,
        extra: dict[str, str],
        make_coro: Callable[[OperatorRelay], Coroutine[Any, Any, RelayOutcome]],
    ) -> None:
        """Run one relay coroutine and map its outcome to an HTTP response."""
        relay = OperatorRelay(
            uri=self.uri,
            operator_name=self.operator_name,
            token=self.token,
            ready_timeout=self.ready_timeout,
            response_timeout=self.response_timeout,
        )
        try:
            outcome = asyncio.run(make_coro(relay))
        except (OSError, RuntimeError) as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"operator relay failed: {exc}\n".encode(),
                content_type="text/plain",
            )
            return
        document: dict[str, object] = {
            "action": action,
            **extra,
            "status": outcome.status,
            "detail": outcome.detail,
            "ok": outcome.ok,
        }
        self._write(
            _OUTCOME_STATUS.get(outcome.status, HTTPStatus.OK),
            json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
        )

    def _bad_request(self, message: str) -> None:
        """Answer a malformed operator body with a specific, stack-free 400."""
        self._write(
            HTTPStatus.BAD_REQUEST,
            f"{message}\n".encode(),
            content_type="text/plain",
        )

    def _is_json_request(self) -> bool:
        """Return whether the request declares an ``application/json`` body.

        Operator writes require this media type. A browser can send a request to
        another origin without a CORS preflight only when its content type is one
        of the three "simple" types (``text/plain``, form-encoded, or multipart);
        ``application/json`` forces a preflight, which this surface never answers
        with cross-origin allow headers, so the browser blocks the real write.
        Requiring JSON therefore turns away a cross-origin page trying to drive an
        operator action it cannot read the response of — a local CSRF. The check
        ignores any charset or boundary parameter after the media type.
        """
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        return media_type == "application/json"

    def _read_json_body(self) -> dict[str, Any] | None:
        """Return the request body as a JSON object, or ``None`` when unusable.

        ``None`` covers a missing, over-large, non-JSON, or non-object body — every
        case the caller answers with one 400, never a stack trace.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > MAX_OPERATOR_BODY_BYTES:
            return None
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

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
            report = run_reliability_report(
                self.reliability_db, key_file=self.reliability_db_key_file
            )
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

    def _serve_state_at(self, query: str) -> None:
        """Serve the coordination state reconstructed as of ``?seq=N``.

        Store-derived time-travel: bounded replay of the durable log to ``seq``,
        the state and board in the live-snapshot shape plus ``as_of_seq`` and
        ``log_end_seq``. Same posture as the other store feeds — 404 without
        ``--feeds-db``, 503 on an unreadable store, 400 on a malformed ``seq``.
        Presence/roster is not journalled and is omitted (the document says so).
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"state-at feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        raw = parse_qs(query).get("seq", ["0"])[0]
        try:
            seq = _bounded_query_int(raw)
        except ValueError:
            self._write(
                HTTPStatus.BAD_REQUEST,
                b"seq must be an integer\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_state_at_feed(self.reliability_db, seq=seq)
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

    def _serve_merkle_proof(self, query: str) -> None:
        """Serve an inclusion proof for the event named by ``?seq=N``.

        Store-derived tamper-evidence: an RFC 6962 Merkle inclusion proof a
        cockpit row's *verify* button checks against the tree root. Same posture
        as the other store feeds — 404 without ``--feeds-db``, 503 on an
        unreadable store, 400 on a malformed ``seq``. A ``seq`` the committed
        log does not hold yields ``{"present": false}`` with a note, never a
        fabricated proof.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"merkle-proof feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        raw = parse_qs(query).get("seq", ["0"])[0]
        try:
            seq = _bounded_query_int(raw)
        except ValueError:
            self._write(
                HTTPStatus.BAD_REQUEST,
                b"seq must be an integer\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_merkle_proof_feed(self.reliability_db, seq=seq)
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

    def _serve_health_anomalies(self) -> None:
        """Serve the coordination-anomaly report, or its honest absence.

        The hub-side alert surface: orphaned, dangling, and stale coordination
        signals the causality graph makes visible, with an ``anomaly_count`` for
        a cockpit badge. Same posture as the other store feeds — 404 without
        ``--feeds-db``, 503 on an unreadable store (or one past the graph node
        ceiling). Fired alerts stay collector-side off ``/metrics``; this is
        only what the durable log can prove.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"health-anomalies feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_health_anomalies_feed(self.reliability_db)
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

    def _serve_sessions(self) -> None:
        """Serve the opt-in session-telemetry report, or its honest absence.

        Aggregates the ``session_metric`` notes the fleet left in the durable
        log — per-session token counts, cost, latency, and error/abstention
        rates, each record carrying the ``seq`` a cockpit joins back to the
        causality feed. Same posture as the other store feeds: 404 without
        ``--feeds-db``, 503 on an unreadable store. Opt-in operational
        telemetry, never hub-core collected — a log with no notes reports empty
        sessions and zeroed totals, not a fabricated cost.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"sessions feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_sessions_feed(self.reliability_db)
        except ValueError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"{exc}\n".encode(),
                content_type="text/plain",
            )
            return
        self._write_json(document)

    def _serve_waits(self) -> None:
        """Serve the pending coordination gates, or their honest absence.

        Lists the non-terminal tasks blocked on dependencies that have not
        completed — who is waiting, on which dependency ids, and since when —
        reconstructed from the durable log. Same posture as the other store
        feeds: 404 without ``--feeds-db``, 503 on an unreadable store. Transient
        socket waiters are not journalled and are omitted; this is the
        coordination gates the plan can prove.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"waits feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_waits_feed(self.reliability_db)
        except ValueError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"{exc}\n".encode(),
                content_type="text/plain",
            )
            return
        self._write_json(document)

    def _serve_operator_actions(self, query: str) -> None:
        """Serve governed operator-action history from the durable log.

        The feed is store-derived and audit-only like the rest of the cockpit
        feeds: 404 without ``--feeds-db``, 503 on an unreadable store, 400 on a
        malformed cursor or limit, and no inferred actions beyond journalled
        ``operator_relay`` events.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"operator-actions feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        params = parse_qs(query)
        try:
            since = _bounded_query_int(params.get("since", ["0"])[0])
            limit = _bounded_query_int(params.get("limit", ["50"])[0])
        except ValueError:
            self._write(
                HTTPStatus.BAD_REQUEST,
                b"since and limit must be integers\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_operator_actions_feed(self.reliability_db, since=since, limit=limit)
        except ValueError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"{exc}\n".encode(),
                content_type="text/plain",
            )
            return
        self._write_json(document)

    def _serve_receipts(self, query: str) -> None:
        """Serve the universal receipt feed from the durable log.

        The feed is store-derived like the other cockpit feeds: 404 without
        ``--feeds-db``, 503 on an unreadable store, 400 on malformed ``since``
        or ``limit``, and no inferred receipts beyond event families that carry
        receipt semantics.
        """
        if self.reliability_db is None:
            self._write(
                HTTPStatus.NOT_FOUND,
                b"receipts feed not configured; start the dashboard with --feeds-db\n",
                content_type="text/plain",
            )
            return
        params = parse_qs(query)
        try:
            since = _bounded_query_int(params.get("since", ["0"])[0])
            limit = _bounded_query_int(params.get("limit", ["100"])[0])
        except ValueError:
            self._write(
                HTTPStatus.BAD_REQUEST,
                b"since and limit must be integers\n",
                content_type="text/plain",
            )
            return
        try:
            document = build_receipts_feed(self.reliability_db, since=since, limit=limit)
        except ValueError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"{exc}\n".encode(),
                content_type="text/plain",
            )
            return
        self._write_json(document)

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
            limit = _bounded_query_int(params.get("limit", [str(DEFAULT_EVENTS_LIMIT)])[0])
            since = None if since_raw == "latest" else _bounded_query_int(since_raw)
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
            seq = _bounded_query_int(seq_raw) if seq_raw is not None else None
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
        """Write one HTTP response with browser-hardening headers."""
        self.send_response(status.value)
        if authenticate:
            self.send_header("WWW-Authenticate", 'Bearer realm="synapse-dashboard"')
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for header, value in _SECURITY_HEADERS:
            self.send_header(header, value)
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
    token_protects_reads: bool,
    reliability_db: Path | None,
    reliability_db_key_file: Path | None,
    federation_store: Path | None,
    cockpit_dist: Path | None,
    operator_enabled: bool,
    operator_name: str,
    operator_rate_limiter: WriteRateLimiter,
    observed_peers: tuple[ObservedPeerSpec, ...],
    observed_token: str | None,
    observed_timeout: float,
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
    bound_token_protects_reads = token_protects_reads
    bound_reliability_db = reliability_db
    bound_reliability_db_key_file = reliability_db_key_file
    bound_federation_store = federation_store
    bound_cockpit_dist = cockpit_dist
    bound_operator_enabled = operator_enabled
    bound_operator_name = operator_name
    bound_operator_rate_limiter = operator_rate_limiter
    bound_observed_peers = observed_peers
    bound_observed_token = observed_token
    bound_observed_timeout = observed_timeout

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
        token_protects_reads = bound_token_protects_reads
        reliability_db = bound_reliability_db
        reliability_db_key_file = bound_reliability_db_key_file
        federation_store = bound_federation_store
        cockpit_dist = bound_cockpit_dist
        operator_enabled = bound_operator_enabled
        operator_name = bound_operator_name
        operator_rate_limiter = bound_operator_rate_limiter
        observed_peers = bound_observed_peers
        observed_token = bound_observed_token
        observed_timeout = bound_observed_timeout

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
    reliability_db_key_file: str | Path | None = None,
    federation_store: str | Path | None = None,
    cockpit_dist: str | Path | None = None,
    operator: bool = False,
    operator_name: str | None = None,
    observed_peers: tuple[ObservedPeerSpec, ...] = (),
    observed_token: str | None = None,
    observed_timeout: float = 10.0,
) -> DashboardServer:
    """Start a background dashboard HTTP server (read-only unless armed).

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
        token is generated automatically when the caller does not provide one and
        either the bind is non-loopback or ``operator`` writes are armed — so the
        operator write-path is never reachable unauthenticated, even on loopback.
    reliability_db : str, pathlib.Path, or None, optional
        Hub event store powering the store-backed feeds —
        ``/reliability.json``, ``/events.json``, ``/causality.json``,
        ``/receipts.json``, ``/operator-actions.json``, and ``/sessions.json``;
        without it each endpoint reports its absence with 404.
    federation_store : str, pathlib.Path, or None, optional
        Operator federation store powering ``/federation.json``.
    cockpit_dist : str, pathlib.Path, or None, optional
        Built cockpit directory served under ``/cockpit/``.
    operator : bool, optional
        Arm the operator write-path (``POST /message``). Off by default; when off,
        every write route is a 404 and the server stays a read-only observer.
    operator_name : str or None, optional
        Sender identity for relayed operator actions; ``operator:<name>`` when
        omitted, so operator writes are attributed and never impersonate an agent.

    Returns
    -------
    DashboardServer
        Handle with URL helpers and a close method.
    """
    validate_dashboard_bind(host, allow_non_loopback=allow_non_loopback)
    effective_dashboard_token, dashboard_token_generated, token_protects_reads = (
        _resolve_dashboard_token(
            host,
            allow_non_loopback=allow_non_loopback,
            dashboard_token=dashboard_token,
            operator=operator,
        )
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
        token_protects_reads=token_protects_reads,
        reliability_db=Path(reliability_db) if reliability_db is not None else None,
        reliability_db_key_file=(
            Path(reliability_db_key_file) if reliability_db_key_file is not None else None
        ),
        federation_store=Path(federation_store) if federation_store is not None else None,
        cockpit_dist=Path(cockpit_dist) if cockpit_dist is not None else None,
        operator_enabled=operator,
        operator_name=(operator_name if operator_name else f"operator:{name}"),
        operator_rate_limiter=WriteRateLimiter(
            max_calls=OPERATOR_RATE_MAX, window_seconds=OPERATOR_RATE_WINDOW_SECONDS
        ),
        observed_peers=observed_peers,
        observed_token=observed_token,
        observed_timeout=observed_timeout,
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
