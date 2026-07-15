# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only local dashboard snapshot and HTTP serving
"""Serve loopback-first read-side hub snapshots and governed dashboard actions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, Final, cast
from urllib.parse import urlsplit

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.dashboard_access import (
    DashboardAccessPolicy,
    DashboardPrincipal,
    compatibility_access_policy,
)
from synapse_channel.dashboard_access_http import (
    DASHBOARD_ACCESS_PATH,
    MESSAGE_PATH,
    TASK_PATH,
    AccessHttpDecision,
    access_descriptor_decision,
    read_decision,
    write_decision,
)
from synapse_channel.dashboard_access_store import load_dashboard_access_policy
from synapse_channel.dashboard_bind import (
    _resolve_dashboard_token,
    validate_dashboard_bind,
)
from synapse_channel.dashboard_cockpit import (
    COCKPIT_ASSETS,
    load_cockpit_asset_bytes,
)
from synapse_channel.dashboard_feed_serving import (
    FeedResponse,
    serve_causality,
    serve_cockpit_dist,
    serve_events,
    serve_federation,
    serve_health_anomalies,
    serve_merkle_proof,
    serve_metrics_feed,
    serve_operator_actions,
    serve_postmortem,
    serve_public_cockpit_asset,
    serve_receipts,
    serve_reliability,
    serve_sessions,
    serve_state_at,
    serve_waits,
)
from synapse_channel.dashboard_fleet import build_fleet_visibility
from synapse_channel.dashboard_host_guard import (
    allowed_host_authorities,
    host_allowed,
    is_unspecified_host,
)
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_operator_writes import (
    execute_relay,
    is_json_media_type,
    plan_message,
    plan_task,
    plan_task_update,
    read_operator_body,
)
from synapse_channel.dashboard_postmortem_feed import POSTMORTEM_PATH
from synapse_channel.dashboard_render import render_dashboard_html
from synapse_channel.dashboard_risk import build_risk_view
from synapse_channel.dashboard_risk_guidance import build_risk_guidance
from synapse_channel.dashboard_store_feeds import event_store_key
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
        risk = build_risk_view(fleet).to_dict()
        risk["guidance"] = build_risk_guidance(
            board=self.board,
            manifest=self.manifest,
            state=self.state,
            safe_task_ids=fleet.tasks.ready,
        ).to_dict()
        payload["risk"] = risk
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
    observed_pins: dict[str, str] | None = None,
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
    observed_pins : dict[str, str] or None, optional
        Per-hub ``sha256:<hex>`` certificate pins for self-signed ``wss://``
        observed peers; a pinned pull fails closed on any mismatch.

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
                pins=observed_pins,
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

OPERATOR_RATE_MAX: Final = 30
"""Operator write actions permitted within :data:`OPERATOR_RATE_WINDOW_SECONDS`."""

OPERATOR_RATE_WINDOW_SECONDS: Final = 60.0
"""Sliding-window length for the operator write rate limit."""

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


class _DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler populated by ``start_dashboard_server`` class attributes."""

    uri: ClassVar[str]
    dashboard_name: ClassVar[str]
    token: ClassVar[str | None]
    ready_timeout: ClassVar[float]
    response_timeout: ClassVar[float]
    refresh_seconds: ClassVar[int]
    a2a_state_file: ClassVar[Path | None]
    access_policy: ClassVar[DashboardAccessPolicy]
    reliability_db: ClassVar[Path | None]
    reliability_db_key_file: ClassVar[Path | None]
    federation_store: ClassVar[Path | None]
    cockpit_dist: ClassVar[Path | None]
    operator_rate_limiter: ClassVar[WriteRateLimiter]
    observed_peers: ClassVar[tuple[ObservedPeerSpec, ...]]
    observed_token: ClassVar[str | None]
    observed_timeout: ClassVar[float]
    observed_pins: ClassVar[dict[str, str] | None]
    allowed_extra_hosts: ClassVar[tuple[str, ...]]

    def _reject_foreign_host(self) -> bool:
        """Refuse a request whose ``Host`` is not an admitted authority.

        The open loopback read path is a DNS-rebinding target, so the transport
        boundary runs before authentication: a request whose ``Host`` header does
        not name the loopback, bind, or operator-approved authority is refused
        with 403 and the caller stops. The boundary is Host-only by design; the
        rebinding threat is browser-borne and a browser always sends origin-form
        with its own ``Host``. A wildcard bind is off loopback and mandates a
        read-protecting token, which already defeats rebinding, so the boundary is
        relaxed there unless the operator narrowed the admissible hosts. Returns
        whether the request was rejected.
        """
        # server_address is typed as a loose union on the base server; a TCP HTTP
        # server always binds a concrete (host, port) pair.
        bind_host, port = cast("tuple[str, int]", self.server.server_address)
        if is_unspecified_host(str(bind_host)) and not self.allowed_extra_hosts:
            return False
        allowed = allowed_host_authorities(str(bind_host), int(port), self.allowed_extra_hosts)
        if host_allowed(self.headers.get("Host"), allowed):
            return False
        self._write(
            HTTPStatus.FORBIDDEN,
            b"dashboard host authority not allowed\n",
            content_type="text/plain",
        )
        return True

    def do_GET(self) -> None:
        """Serve the dashboard HTML page, JSON snapshot, or a 404 response."""
        if self._reject_foreign_host():
            return
        path = urlsplit(self.path).path
        authorization = self.headers.get("Authorization")
        if path == DASHBOARD_ACCESS_PATH:
            self._write_access_decision(
                access_descriptor_decision(self.access_policy, authorization)
            )
            return
        access = read_decision(self.access_policy, authorization)
        if not access.allowed:
            # The validated React shell is the narrow exception: navigation cannot
            # carry Authorization, so fixed files load before its unlock veil.
            public_asset = serve_public_cockpit_asset(self.cockpit_dist, COCKPIT_DIST_PREFIX, path)
            if public_asset is not None:
                self._write_response(public_asset)
                return
            self._write_access_decision(access)
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
                load_cockpit_asset_bytes(asset_name),
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
        feed = self._feed_response(path, urlsplit(self.path).query)
        if feed is not None:
            self._write_response(feed)
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
                    observed_pins=self.observed_pins,
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
        """Re-resolve one principal and relay only its exact route capability.

        Unarmed routes stay undisclosed as 404. Armed writes require a known
        bearer, JSON media type, rate limit, bounded body, and a principal-specific
        relay identity; the hub still authorizes and audits the resulting action.
        """
        if self._reject_foreign_host():
            return
        route = urlsplit(self.path).path
        access = write_decision(
            self.access_policy,
            self.headers.get("Authorization"),
            route,
        )
        if not access.allowed:
            self._write_access_decision(access)
            return
        principal = cast(DashboardPrincipal, access.principal)
        if not is_json_media_type(self.headers.get("Content-Type", "")):
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
        body = read_operator_body(self.headers.get("Content-Length"), self.rfile)
        if body is None:
            self._write(
                HTTPStatus.BAD_REQUEST,
                b"request body must be a JSON object within the size limit\n",
                content_type="text/plain",
            )
            return
        planners = {MESSAGE_PATH: plan_message, TASK_PATH: plan_task}
        plan = planners.get(route, plan_task_update)(body)
        if isinstance(plan, str):
            self._write(HTTPStatus.BAD_REQUEST, f"{plan}\n".encode(), content_type="text/plain")
            return
        self._write_response(
            execute_relay(
                plan,
                uri=self.uri,
                operator_name=cast(str, principal.operator_name),
                token=self.token,
                ready_timeout=self.ready_timeout,
                response_timeout=self.response_timeout,
            )
        )

    def _feed_response(self, path: str, query: str) -> FeedResponse | None:
        """Compute the read-side feed response for ``path``, or ``None``.

        ``None`` means the path is not a feed route and the caller falls
        through to the hub-snapshot pages. Every feed's serving logic —
        including the honest-absence 404 and fail-visible 503 postures —
        lives in :mod:`synapse_channel.dashboard_feed_serving`.
        """
        db = self.reliability_db
        if path == RELIABILITY_PATH:
            # Served from the durable event store, not the live hub: the
            # reliability report is an offline audit surface, so it stays
            # available when the hub is down and needs no hub round-trip.
            return serve_reliability(db, self.reliability_db_key_file)
        if path == EVENTS_PATH:
            return serve_events(db, query)
        if path == METRICS_FEED_PATH:
            return serve_metrics_feed(db)
        if path == STATE_AT_PATH:
            return serve_state_at(db, query)
        if path == MERKLE_PROOF_PATH:
            return serve_merkle_proof(db, query)
        if path == HEALTH_ANOMALIES_PATH:
            return serve_health_anomalies(db)
        if path == CAUSALITY_PATH:
            return serve_causality(db, query)
        if path == FEDERATION_PATH:
            return serve_federation(self.federation_store)
        if path == SESSIONS_PATH:
            return serve_sessions(db)
        if path == WAITS_PATH:
            return serve_waits(db)
        if path == OPERATOR_ACTIONS_PATH:
            return serve_operator_actions(db, query)
        if path == RECEIPTS_PATH:
            return serve_receipts(db, query)
        if path == POSTMORTEM_PATH:
            return serve_postmortem(db, self.reliability_db_key_file, query)
        if path.startswith(COCKPIT_DIST_PREFIX) or path == COCKPIT_DIST_PREFIX.rstrip("/"):
            return serve_cockpit_dist(self.cockpit_dist, COCKPIT_DIST_PREFIX, path)
        return None

    def _write_response(self, response: FeedResponse) -> None:
        """Write one computed feed/operator response through ``_write``."""
        self._write(response.status, response.body, content_type=response.content_type)

    def _write_access_decision(self, decision: AccessHttpDecision) -> None:
        """Write one complete access response with its cache-variance contract."""
        status = cast(HTTPStatus, decision.status)
        self._write(
            status,
            decision.body,
            content_type="application/json" if status is HTTPStatus.OK else "text/plain",
            authenticate=decision.authenticate,
            extra_headers=decision.headers,
        )

    def log_message(self, _format: str, *_args: object) -> None:
        """Suppress stdlib access-log noise during CLI and tests."""
        return None

    def _write(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str,
        authenticate: bool = False,
        extra_headers: tuple[tuple[str, str], ...] = (),
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
        for header, value in extra_headers:
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
    access_policy: DashboardAccessPolicy,
    reliability_db: Path | None,
    reliability_db_key_file: Path | None,
    federation_store: Path | None,
    cockpit_dist: Path | None,
    operator_rate_limiter: WriteRateLimiter,
    observed_peers: tuple[ObservedPeerSpec, ...],
    observed_token: str | None,
    observed_timeout: float,
    observed_pins: dict[str, str] | None,
    allow_hosts: tuple[str, ...],
) -> type[_DashboardHandler]:
    """Create an isolated handler class for one dashboard server."""
    bound_uri = uri
    bound_name = name
    bound_token = token
    bound_ready_timeout = ready_timeout
    bound_response_timeout = response_timeout
    bound_refresh_seconds = refresh_seconds
    bound_a2a_state_file = a2a_state_file
    bound_access_policy = access_policy
    bound_reliability_db = reliability_db
    bound_reliability_db_key_file = reliability_db_key_file
    bound_federation_store = federation_store
    bound_cockpit_dist = cockpit_dist
    bound_operator_rate_limiter = operator_rate_limiter
    bound_observed_peers = observed_peers
    bound_observed_token = observed_token
    bound_observed_pins = observed_pins
    bound_observed_timeout = observed_timeout
    bound_allow_hosts = allow_hosts

    class BoundDashboardHandler(_DashboardHandler):
        """Dashboard handler bound to one hub URI and dashboard identity."""

        uri = bound_uri
        dashboard_name = bound_name
        token = bound_token
        ready_timeout = bound_ready_timeout
        response_timeout = bound_response_timeout
        refresh_seconds = bound_refresh_seconds
        a2a_state_file = bound_a2a_state_file
        access_policy = bound_access_policy
        reliability_db = bound_reliability_db
        reliability_db_key_file = bound_reliability_db_key_file
        federation_store = bound_federation_store
        cockpit_dist = bound_cockpit_dist
        operator_rate_limiter = bound_operator_rate_limiter
        observed_peers = bound_observed_peers
        observed_token = bound_observed_token
        observed_timeout = bound_observed_timeout
        observed_pins = bound_observed_pins
        allowed_extra_hosts = bound_allow_hosts

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
    dashboard_access_file: str | Path | None = None,
    reliability_db: str | Path | None = None,
    reliability_db_key_file: str | Path | None = None,
    federation_store: str | Path | None = None,
    cockpit_dist: str | Path | None = None,
    operator: bool = False,
    operator_name: str | None = None,
    observed_peers: tuple[ObservedPeerSpec, ...] = (),
    observed_token: str | None = None,
    observed_timeout: float = 10.0,
    observed_pins: dict[str, str] | None = None,
    allow_hosts: tuple[str, ...] = (),
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
        Optional HTTP bearer token for dashboard browser and JSON requests. When the
        caller provides none, the server generates one — printed by the CLI so the
        cockpit unlock veil can present it — and it gates every live/page read (and
        operator writes when armed), even on loopback, so a same-host process cannot
        read the cockpit's live data unbidden. The validated React shell stays the
        one public exception so its unlock veil can load.
    dashboard_access_file : str, pathlib.Path, or None, optional
        Strict owner-only principal/token-file policy. Mutually exclusive with
        ``dashboard_token`` and the legacy global ``operator_name``.
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
    allow_hosts : tuple of str, optional
        Extra ``Host`` authorities admitted by the always-on DNS-rebinding
        boundary, for a deliberate LAN or reverse-proxy exposure. The loopback
        names and the concrete bind host at the served port are always admitted;
        every other authority — the shape of a rebinding attack — is refused.

    Returns
    -------
    DashboardServer
        Handle with URL helpers and a close method.
    """
    validate_dashboard_bind(host, allow_non_loopback=allow_non_loopback)
    # Surface a malformed operator-approved host at startup, not per request.
    allowed_host_authorities(host, int(port), tuple(allow_hosts))
    if dashboard_access_file is not None:
        if dashboard_token is not None:
            raise ValueError("--dashboard-access-file cannot be combined with --dashboard-token")
        if operator_name is not None:
            raise ValueError("--dashboard-access-file carries each operator identity")
        access_policy = load_dashboard_access_policy(
            dashboard_access_file,
            operator_armed=operator,
        )
        effective_dashboard_token = None
        dashboard_token_generated = False
    else:
        effective_dashboard_token, dashboard_token_generated, token_protects_reads = (
            _resolve_dashboard_token(dashboard_token=dashboard_token)
        )
        access_policy = compatibility_access_policy(
            dashboard_token=effective_dashboard_token,
            token_protects_reads=token_protects_reads,
            operator_armed=operator,
            operator_name=(operator_name if operator_name else f"operator:{name}"),
        )
    handler = _handler_class(
        uri=uri,
        name=name,
        token=token,
        ready_timeout=ready_timeout,
        response_timeout=response_timeout,
        refresh_seconds=max(1, int(refresh_seconds)),
        a2a_state_file=Path(a2a_state_file) if a2a_state_file is not None else None,
        access_policy=access_policy,
        reliability_db=Path(reliability_db) if reliability_db is not None else None,
        reliability_db_key_file=(
            Path(reliability_db_key_file) if reliability_db_key_file is not None else None
        ),
        federation_store=Path(federation_store) if federation_store is not None else None,
        cockpit_dist=Path(cockpit_dist) if cockpit_dist is not None else None,
        operator_rate_limiter=WriteRateLimiter(
            max_calls=OPERATOR_RATE_MAX, window_seconds=OPERATOR_RATE_WINDOW_SECONDS
        ),
        observed_peers=observed_peers,
        observed_token=observed_token,
        observed_timeout=observed_timeout,
        observed_pins=observed_pins,
        allow_hosts=tuple(allow_hosts),
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
