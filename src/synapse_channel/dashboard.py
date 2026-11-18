# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only local dashboard snapshot and HTTP serving
"""Serve loopback-first read-side hub snapshots and governed dashboard actions."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import ClassVar, Final, cast
from urllib.parse import parse_qsl, urlencode, urlsplit

from synapse_channel.dashboard_access import DashboardAccessPolicy, DashboardPrincipal
from synapse_channel.dashboard_access_http import (
    APPROVAL_DECIDE_PATH,
    DASHBOARD_ACCESS_PATH,
    MESSAGE_PATH,
    MESSAGE_RESPONSE_PATH,
    TASK_PATH,
    TASK_UPDATE_PATH,
    AccessHttpDecision,
    access_descriptor_decision,
    is_own_origin,
    read_decision,
    write_decision,
)
from synapse_channel.dashboard_approval_decide import ApprovalDecideBindings, decide_approval
from synapse_channel.dashboard_attention import serve_attention
from synapse_channel.dashboard_feed_scope import feed_scope, scoped_feed
from synapse_channel.dashboard_feed_serving import (
    OPTIONAL_SURFACES,
    FeedResponse,
    capability_summary,
    configured_flags,
    json_response,
    plain_response,
    serve_causality,
    serve_cockpit_dist,
    serve_events,
    serve_federation,
    serve_findings,
    serve_health_anomalies,
    serve_merkle_proof,
    serve_metrics_feed,
    serve_operations,
    serve_operator_actions,
    serve_postmortem,
    serve_public_cockpit_asset,
    serve_receipts,
    serve_reliability,
    serve_sessions,
    serve_state_at,
    serve_waits,
)
from synapse_channel.dashboard_fleet_mirror import (
    FLEET_MIRROR_PATHS,
    fleet_mirror_response,
)
from synapse_channel.dashboard_handler_binding import (
    DashboardFeedCache as DashboardFeedCache,
)
from synapse_channel.dashboard_handler_binding import (
    DashboardSnapshotGate as DashboardSnapshotGate,
)
from synapse_channel.dashboard_handler_binding import (
    DashboardSnapshotSource,
    DashboardViewSettings,
    bind_dashboard_readers,
)
from synapse_channel.dashboard_handler_binding import (
    HostSessionMonitor as HostSessionMonitor,
)
from synapse_channel.dashboard_host_guard import (
    allowed_host_authorities,
    host_allowed,
    is_unspecified_host,
)
from synapse_channel.dashboard_host_sessions import (
    HOST_SESSION_PATHS,
    host_session_response,
)
from synapse_channel.dashboard_http_live import (
    LIVE_TRANSPORT_CHANNELS as LIVE_TRANSPORT_CHANNELS,
)
from synapse_channel.dashboard_http_live import (
    LIVE_TRANSPORT_CONTENT_TYPE,
    LIVE_TRANSPORT_PATH,
    READER_LEFT_ERRORS,
    LiveStreamBindings,
    serve_dashboard_live,
)
from synapse_channel.dashboard_http_live import (
    LiveFrameSequence as LiveFrameSequence,
)
from synapse_channel.dashboard_http_live import (
    LiveTransportCursor as LiveTransportCursor,
)
from synapse_channel.dashboard_http_live import (
    decode_feed_response as decode_feed_response,
)
from synapse_channel.dashboard_http_live import (
    parse_diagnostic_cycles as parse_diagnostic_cycles,
)
from synapse_channel.dashboard_http_live import (
    snapshot_fingerprint as snapshot_fingerprint,
)
from synapse_channel.dashboard_http_live import (
    uuid as uuid,
)
from synapse_channel.dashboard_http_pages import (
    COCKPIT_ASSETS as COCKPIT_ASSETS,
)
from synapse_channel.dashboard_http_pages import (
    STUDIO_COMMAND_PATH as STUDIO_COMMAND_PATH,
)
from synapse_channel.dashboard_http_pages import (
    STUDIO_REFERENCE_PATH as STUDIO_REFERENCE_PATH,
)
from synapse_channel.dashboard_http_pages import (
    SnapshotPageBindings,
    serve_page_shell,
    serve_snapshot_page,
)
from synapse_channel.dashboard_http_pages import (
    build_studio_snapshot as build_studio_snapshot,
)
from synapse_channel.dashboard_http_pages import (
    load_cockpit_asset_bytes as load_cockpit_asset_bytes,
)
from synapse_channel.dashboard_http_pages import (
    render_studio_command_html as render_studio_command_html,
)
from synapse_channel.dashboard_http_pages import (
    render_studio_reference_html as render_studio_reference_html,
)
from synapse_channel.dashboard_hub_snapshot import DashboardSnapshot as DashboardSnapshot
from synapse_channel.dashboard_hub_snapshot import DashboardUnavailable as DashboardUnavailable
from synapse_channel.dashboard_hub_snapshot import ManifestCards as ManifestCards
from synapse_channel.dashboard_hub_snapshot import SnapshotMapping as SnapshotMapping
from synapse_channel.dashboard_hub_snapshot import (
    fetch_dashboard_snapshot as fetch_dashboard_snapshot,
)
from synapse_channel.dashboard_operations_feed import OPERATIONS_PATH
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_operator_writes import (
    RelayPlan,
    execute_relay,
    is_json_media_type,
    plan_message,
    plan_message_response,
    plan_task,
    plan_task_update,
    read_operator_body,
)
from synapse_channel.dashboard_postmortem_feed import POSTMORTEM_PATH
from synapse_channel.dashboard_render import render_dashboard_html
from synapse_channel.dashboard_scope_groups import ScopeGroups
from synapse_channel.dashboard_setup_http import (
    SETUP_PATHS,
    UNARMED,
    Inspection,
    SetupServing,
    setup_preflight_response,
)
from synapse_channel.dashboard_setup_writes import (
    SETUP_WRITE_PATHS,
    SetupWriteContext,
    setup_write_response,
)
from synapse_channel.dashboard_start import DashboardServer as DashboardServer
from synapse_channel.dashboard_start import start_dashboard_server as start_dashboard_server
from synapse_channel.dashboard_store_feeds import event_store_key
from synapse_channel.observed_peers import (
    ObservedPeerSpec,
)
from synapse_channel.studio_snapshot import STUDIO_SNAPSHOT_PATH

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

FINDINGS_PATH = "/findings.json"
"""Read-only feed of the findings the hub recorded, newest first."""

SESSIONS_PATH = "/sessions.json"
"""Read-only endpoint reporting opt-in session telemetry left in the durable log."""

WAITS_PATH = "/waits.json"
"""Read-only endpoint listing pending coordination gates — tasks blocked on deps."""

OPERATOR_ACTIONS_PATH = "/operator-actions.json"
"""Read-only endpoint serving governed operator-action audit history."""

RECEIPTS_PATH = "/receipts.json"
"""Read-only endpoint serving universal receipt projections from the event log."""

ATTENTION_PATH = "/attention.json"
"""Authenticated owner-local alert queue, when explicitly configured."""

CAPABILITIES_PATH = "/capabilities.json"
"""What this start serves: every optional surface, whether it is on, and its flag."""

COCKPIT_DIST_PREFIX = "/cockpit/"
"""URL prefix under which an operator-named cockpit build directory is served."""

_QUERY_FEEDS: Final[dict[str, Callable[[Path | None, str], FeedResponse]]] = {
    EVENTS_PATH: serve_events,
    MERKLE_PROOF_PATH: serve_merkle_proof,
    CAUSALITY_PATH: serve_causality,
    FINDINGS_PATH: serve_findings,
    OPERATOR_ACTIONS_PATH: serve_operator_actions,
    RECEIPTS_PATH: serve_receipts,
}
"""Event-store feeds served straight from the store and the request's query."""

_STORE_FEEDS: Final[dict[str, Callable[[Path | None], FeedResponse]]] = {
    METRICS_FEED_PATH: serve_metrics_feed,
    SESSIONS_PATH: serve_sessions,
    WAITS_PATH: serve_waits,
}
"""Event-store feeds that take no query."""

_FEED_PATHS: Final = frozenset(surface.path for surface in OPTIONAL_SURFACES)
"""Every optional feed; each answers a request's ``scope``."""

OPERATOR_RATE_MAX: Final = 30
"""Operator write actions permitted within :data:`OPERATOR_RATE_WINDOW_SECONDS`."""

OPERATOR_RATE_WINDOW_SECONDS: Final = 60.0
"""Sliding-window length for the operator write rate limit."""

# The dashboard and cockpit are self-contained — no CDN, external font, or remote
# script — so a same-origin content policy costs nothing and blocks injected remote
# resources. Runtime configuration is carried in inert ``application/json`` blocks;
# executable JavaScript therefore comes only from same-origin assets, including an
# operator-supplied ``--cockpit-dist``. Inline styles remain allowed until the
# server-rendered and dynamic style attributes have migrated to classes. ``data:``
# images cover embedded favicons and glyphs. The framing and base-uri directives are
# the clickjacking and base-tag-injection guards.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
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
    attention_store: ClassVar[Path | None]
    federation_store: ClassVar[Path | None]
    cockpit_dist: ClassVar[Path | None]
    operator_rate_limiter: ClassVar[WriteRateLimiter]
    observed_peers: ClassVar[tuple[ObservedPeerSpec, ...]]
    observed_token: ClassVar[str | None]
    observed_timeout: ClassVar[float]
    observed_pins: ClassVar[dict[str, str] | None]
    allowed_extra_hosts: ClassVar[tuple[str, ...]]
    heavy_feed_cache: ClassVar[DashboardFeedCache]
    state_feed_cache: ClassVar[DashboardFeedCache]
    snapshot_gate: ClassVar[DashboardSnapshotGate[DashboardSnapshot]]
    host_sessions_access_file: ClassVar[Path | None] = None
    fleet_observed_file: ClassVar[Path | None] = None
    fleet_observed_access_file: ClassVar[Path | None] = None
    scope_groups: ClassVar[ScopeGroups | None] = None
    host_session_monitor: ClassVar[HostSessionMonitor]
    setup_serving: ClassVar[SetupServing] = UNARMED
    setup_inspection: ClassVar[Inspection]

    def _reject_foreign_host(self) -> bool:
        """Refuse a request whose ``Host`` is not an admitted authority.

        The open loopback read path is a DNS-rebinding target, so the transport
        boundary runs before authentication: a request whose ``Host`` header does
        not name the loopback, bind, or operator-approved authority, or that
        repeats the ``Host`` header, is refused with 403 and the caller stops.
        The boundary is Host-only by design; the rebinding threat is
        browser-borne and a browser always sends origin-form with its own
        ``Host``. A wildcard bind is off loopback and mandates a read-protecting
        token, which already defeats rebinding, so the boundary is relaxed there
        unless the operator narrowed the admissible hosts. Returns whether the
        request was rejected.
        """
        # server_address is typed as a loose union on the base server; a TCP HTTP
        # server always binds a concrete (host, port) pair.
        bind_host, port = cast("tuple[str, int]", self.server.server_address)
        if is_unspecified_host(str(bind_host)) and not self.allowed_extra_hosts:
            return False
        allowed = allowed_host_authorities(str(bind_host), int(port), self.allowed_extra_hosts)
        # Exactly one Host: with a repeated header the stdlib parser returns the
        # first value, so the decision would depend on header order.
        host_values = self.headers.get_all("Host") or []
        if len(host_values) == 1 and host_allowed(host_values[0], allowed):
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
        if path == "/" and self.cockpit_dist is not None:
            # The cockpit is the front door. Its build resolves its files against
            # /cockpit/, and a navigation carries no bearer: send the browser to the
            # unlock screen there, keeping any query (such as ?lang=sk).
            query = _encoded_query(urlsplit(self.path).query)
            target = COCKPIT_DIST_PREFIX + (f"?{query}" if query else "")
            self._write(
                HTTPStatus.FOUND,
                b"",
                content_type="text/plain",
                extra_headers=(("Location", target),),
            )
            return
        authorization = self.headers.get("Authorization")
        own_gate = self._own_gate_decision(path, authorization)
        if own_gate is not None:
            self._write_access_decision(own_gate)
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

    def _own_gate_decision(self, path: str, authorization: str | None) -> AccessHttpDecision | None:
        """Answer a route that checks its own access, or return ``None`` for every other route.

        The Fleet mirror and the host sessions check their own grants, the access
        descriptor answers any bearer, and setup needs ``setup_plan``; none of them
        is a plain read.
        """
        if path in FLEET_MIRROR_PATHS:
            return fleet_mirror_response(
                path,
                authorization,
                self.access_policy,
                self.fleet_observed_file,
                self.fleet_observed_access_file,
            )
        if path in HOST_SESSION_PATHS:
            return host_session_response(
                path,
                authorization,
                self.access_policy,
                self.host_sessions_access_file,
                self.host_session_monitor,
            )
        if path == DASHBOARD_ACCESS_PATH:
            return access_descriptor_decision(self.access_policy, authorization)
        if path in SETUP_PATHS:
            return setup_preflight_response(
                authorization, self.access_policy, self.setup_serving, type(self).setup_inspection
            )
        return None

    def _do_get_routed(self) -> None:
        """Route an admitted GET to its shell, feed or bounded snapshot page."""
        path = urlsplit(self.path).path
        if path == LIVE_TRANSPORT_PATH:
            self._serve_live_transport(urlsplit(self.path).query)
            return
        if serve_page_shell(
            path,
            refresh_seconds=self.refresh_seconds,
            snapshot_timeout_seconds=self.ready_timeout
            + self.response_timeout
            + (self.observed_timeout if self.observed_peers else 0)
            + 4.0,
            write=self._write,
        ):
            return
        if path == "/classic":
            # Legacy instrument HTML fetches a live hub snapshot server-side.
            path = "/index.html"
        feed = self._feed_response(path, urlsplit(self.path).query)
        if feed is not None:
            self._write_response(feed)
            return
        if path not in {"/index.html", "/classic", "/snapshot.json", STUDIO_SNAPSHOT_PATH}:
            self._write(HTTPStatus.NOT_FOUND, b"not found\n", content_type="text/plain")
            return
        serve_snapshot_page(
            path,
            SnapshotPageBindings(
                fetch=partial(self.snapshot_gate.fetch, wait_timeout=1.0),
                unavailable_error=DashboardUnavailable,
                html=partial(
                    render_dashboard_html,
                    refresh_seconds=self.refresh_seconds,
                    a2a_state_file=self.a2a_state_file,
                ),
                a2a_state_file=self.a2a_state_file,
                scope_groups=self.scope_groups,
            ),
            write=self._write,
            query=urlsplit(self.path).query,
            if_none_match=self.headers.get("If-None-Match"),
        )

    def do_POST(self) -> None:
        """Re-resolve one principal and relay only its exact route capability.

        Unarmed routes stay undisclosed as 404. Armed writes require a known
        bearer, exactly one ``Origin`` that is this dashboard's own (the check
        ``/setup/plan`` makes too), JSON media type, rate limit, bounded body, and
        a principal-specific relay identity; the hub still authorizes and audits
        the resulting action.
        """
        if self._reject_foreign_host():
            return
        route = urlsplit(self.path).path
        if route in SETUP_WRITE_PATHS:
            self._write_access_decision(self._setup_write_decision(route))
            return
        access = write_decision(
            self.access_policy,
            self.headers.get("Authorization"),
            route,
        )
        if not access.allowed:
            self._write_access_decision(access)
            return
        principal = cast(DashboardPrincipal, access.principal)
        if not is_own_origin(self.headers.get("Host", ""), self.headers.get_all("Origin") or ()):
            self._write(
                HTTPStatus.FORBIDDEN,
                b"dashboard writes require this dashboard's own Origin\n",
                content_type="text/plain",
            )
            return
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
        relay_identity = cast(str, principal.operator_name)
        if route == APPROVAL_DECIDE_PATH:
            self._write_response(decide_approval(body, relay_identity, self._approval_bindings()))
            return
        planners = {
            MESSAGE_PATH: plan_message,
            MESSAGE_RESPONSE_PATH: plan_message_response,
            TASK_PATH: plan_task,
            TASK_UPDATE_PATH: plan_task_update,
        }
        plan = planners[route](body)
        if isinstance(plan, str):
            self._write(HTTPStatus.BAD_REQUEST, f"{plan}\n".encode(), content_type="text/plain")
            return
        self._write_response(self._relay(plan, relay_identity))

    def _setup_write_decision(self, route: str) -> AccessHttpDecision:
        """Answer a setup write from this start's setup decision and the route's checks."""
        context = SetupWriteContext(
            self.access_policy,
            self.setup_serving,
            type(self).setup_inspection,
            self.operator_rate_limiter,
            self.token,
        )
        return setup_write_response(route, self.headers, self.rfile, context)

    def _relay(self, plan: RelayPlan, relay_identity: str) -> FeedResponse:
        """Run one relay plan under ``relay_identity`` against this start's hub."""
        return execute_relay(
            plan,
            uri=self.uri,
            operator_name=relay_identity,
            token=self.token,
            ready_timeout=self.ready_timeout,
            response_timeout=self.response_timeout,
        )

    def _approval_bindings(self) -> ApprovalDecideBindings:
        """Lend the approval route this start's store, a fresh roster read and the relay."""

        def online_agents() -> list[str]:
            # A fresh read through the dashboard's own snapshot identity, never a cache.
            return self.snapshot_gate.fetch(wait_timeout=1.0).online_agents

        return ApprovalDecideBindings(
            feeds_db=self.reliability_db,
            key_file=self.reliability_db_key_file,
            online_agents=online_agents,
            relay=self._relay,
        )

    def _configured_flags(self) -> frozenset[str]:
        """Return the optional-surface flags this start was given."""
        return configured_flags(
            feeds_db=self.reliability_db,
            federation_store=self.federation_store,
            attention_store=self.attention_store,
            scope_groups=self.scope_groups,
        )

    def _feed_response(self, path: str, query: str) -> FeedResponse | None:
        """Compute a feed's answer under the request's ``scope``, or ``None`` for no feed.

        A malformed scope is refused with 400 before the feed is built. The
        scope keeps the rows that belong to it in each feed that places a row
        (``FILTERED_FEEDS``),
        and every other feed says that it was not applied
        (:mod:`synapse_channel.dashboard_feed_scope`).
        """
        if path not in _FEED_PATHS:
            return self._unscoped_feed_response(path, query)
        try:
            scope = feed_scope(query, self.scope_groups)
        except ValueError as exc:
            return plain_response(HTTPStatus.BAD_REQUEST, str(exc))
        return scoped_feed(
            path, self._unscoped_feed_response(path, query), scope, self.reliability_db
        )

    def _unscoped_feed_response(self, path: str, query: str) -> FeedResponse | None:
        """Compute the read-side feed response for ``path``, or ``None``.

        ``None`` means the path is not a feed route and the caller falls
        through to the hub-snapshot pages. Every feed's serving logic —
        including the honest-absence 404 and fail-visible 503 postures —
        lives in :mod:`synapse_channel.dashboard_feed_serving`.
        """
        if path == CAPABILITIES_PATH:
            return json_response(
                capability_summary(self._configured_flags(), setup=self.setup_serving.capability())
            )
        db = self.reliability_db
        query_feed = _QUERY_FEEDS.get(path)
        if query_feed is not None:
            return query_feed(db, query)
        store_feed = _STORE_FEEDS.get(path)
        if store_feed is not None:
            return store_feed(db)
        if path == RELIABILITY_PATH:
            # Served from the durable event store, not the live hub: the
            # reliability report is an offline audit surface, so it stays
            # available when the hub is down and needs no hub round-trip.
            return self.heavy_feed_cache.get_or_build(
                RELIABILITY_PATH,
                partial(serve_reliability, db, self.reliability_db_key_file),
            )
        if path == STATE_AT_PATH:
            return self.state_feed_cache.get_or_build(
                f"{STATE_AT_PATH}?{query}",
                partial(serve_state_at, db, query),
            )
        if path == HEALTH_ANOMALIES_PATH:
            return self.heavy_feed_cache.get_or_build(
                HEALTH_ANOMALIES_PATH,
                partial(serve_health_anomalies, db),
            )
        if path == FEDERATION_PATH:
            return serve_federation(self.federation_store)
        if path == ATTENTION_PATH:
            return serve_attention(self.attention_store)
        if path == POSTMORTEM_PATH:
            return serve_postmortem(db, self.reliability_db_key_file, query)
        if path == OPERATIONS_PATH:
            return serve_operations(db, self.reliability_db_key_file, query)
        if path.startswith(COCKPIT_DIST_PREFIX) or path == COCKPIT_DIST_PREFIX.rstrip("/"):
            return serve_cockpit_dist(self.cockpit_dist, COCKPIT_DIST_PREFIX, path)
        return None

    def _start_live_transport(self) -> None:
        """Write the unchanged live-stream headers before any frame."""
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", f"{LIVE_TRANSPORT_CONTENT_TYPE}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for header, value in _SECURITY_HEADERS:
            self.send_header(header, value)
        self.end_headers()
        self.close_connection = True

    def _serve_live_transport(self, query: str) -> None:
        """Bind this admitted HTTP stream to the existing snapshot and feed owners."""
        serve_dashboard_live(
            query,
            LiveStreamBindings(
                fetch=partial(self.snapshot_gate.fetch, wait_timeout=1.0),
                unavailable_error=DashboardUnavailable,
                feed=self._feed_response,
                feed_specs=lambda: (
                    ("events", EVENTS_PATH),
                    ("receipts", RECEIPTS_PATH),
                    ("operator_actions", OPERATOR_ACTIONS_PATH),
                ),
                a2a_state_file=self.a2a_state_file,
                scope_groups=self.scope_groups,
                start=self._start_live_transport,
                write=self.wfile.write,
                flush=self.wfile.flush,
                refuse=self._write,
            ),
        )

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
        """Write one HTTP response with browser-hardening headers.

        A reader that has left ends the response quietly, as it ends a live stream
        (:data:`~synapse_channel.dashboard_http_live.READER_LEFT_ERRORS`). The
        handler speaks HTTP/1.0, so the connection closes after this response.
        """
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
        with contextlib.suppress(*READER_LEFT_ERRORS):
            self.end_headers()
            self.wfile.write(body)


def _encoded_query(query: str) -> str:
    """Return a request's query decoded and encoded again, for a ``Location`` header.

    The query is request data. Decoding each name and value and encoding them
    again leaves only URL-safe characters, so an encoded line break or any other
    control character stays encoded and cannot end the header.

    Parameters
    ----------
    query : str
        The query part of the request target, without ``?``.

    Returns
    -------
    str
        The same names and values, percent-encoded.
    """
    return urlencode(parse_qsl(query, keep_blank_values=True))


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
    attention_store: Path | None,
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
    source = DashboardSnapshotSource(
        uri=uri,
        name=name,
        token=token,
        ready_timeout=ready_timeout,
        response_timeout=response_timeout,
        observed_peers=observed_peers,
        observed_token=observed_token,
        observed_timeout=observed_timeout,
        observed_pins=observed_pins,
    )
    view = DashboardViewSettings(
        refresh_seconds=refresh_seconds,
        a2a_state_file=a2a_state_file,
        access_policy=access_policy,
        reliability_db=reliability_db,
        reliability_db_key_file=reliability_db_key_file,
        attention_store=attention_store,
        federation_store=federation_store,
        cockpit_dist=cockpit_dist,
        operator_rate_limiter=operator_rate_limiter,
        allow_hosts=allow_hosts,
    )
    readers = bind_dashboard_readers(
        source,
        view,
        fetcher=lambda: fetch_dashboard_snapshot,
        cache_factory=DashboardFeedCache,
        unavailable_error=DashboardUnavailable,
    )

    class BoundDashboardHandler(_DashboardHandler):
        """Dashboard handler bound independently to one hub and local read owners."""

        uri = source.uri
        dashboard_name = source.name
        token = source.token
        ready_timeout = source.ready_timeout
        response_timeout = source.response_timeout
        refresh_seconds = view.refresh_seconds
        a2a_state_file = view.a2a_state_file
        access_policy = view.access_policy
        reliability_db = view.reliability_db
        reliability_db_key_file = view.reliability_db_key_file
        attention_store = view.attention_store
        federation_store = view.federation_store
        cockpit_dist = view.cockpit_dist
        operator_rate_limiter = view.operator_rate_limiter
        observed_peers = source.observed_peers
        observed_token = source.observed_token
        observed_timeout = source.observed_timeout
        observed_pins = source.observed_pins
        allowed_extra_hosts = view.allow_hosts
        heavy_feed_cache = readers.heavy_feed_cache
        state_feed_cache = readers.state_feed_cache
        snapshot_gate = readers.snapshot_gate
        host_session_monitor = readers.host_session_monitor

    return BoundDashboardHandler
