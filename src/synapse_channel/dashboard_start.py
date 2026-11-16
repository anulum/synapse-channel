# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — assembling and starting one dashboard server
"""Assemble one dashboard server from its options and start it on a daemon thread.

The start checks the bind and the approved hosts, resolves the access policy,
builds an isolated handler class, binds the start's owner-only local files and
its host monitor, and serves. :mod:`synapse_channel.dashboard` re-exports
:func:`start_dashboard_server` and :class:`DashboardServer`, so callers keep
importing them from there.

The handler and the class that binds it to one server stay with the handler in
:mod:`synapse_channel.dashboard`. The start imports that binder and the
operator rate limits from there when it runs, so a caller that replaces one of
them on that module is still honoured, as before.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from synapse_channel.dashboard_access import DashboardAccessPolicy, compatibility_access_policy
from synapse_channel.dashboard_access_store import load_dashboard_access_policy
from synapse_channel.dashboard_bind import _resolve_dashboard_token, validate_dashboard_bind
from synapse_channel.dashboard_fleet_mirror import load_mirror_grants
from synapse_channel.dashboard_handler_binding import DashboardFeedCache
from synapse_channel.dashboard_host_guard import allowed_host_authorities
from synapse_channel.dashboard_host_sessions import load_host_grants
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_scope_groups import load_scope_groups
from synapse_channel.dashboard_setup_http import SetupServing, hub_inspection, setup_serving
from synapse_channel.observed_peers import ObservedPeerSpec
from synapse_channel.prompt_bind_http import PromptBindThreadingHTTPServer

if TYPE_CHECKING:
    from synapse_channel.dashboard import _DashboardHandler


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
    heavy_feed_cache: DashboardFeedCache | None = None
    state_feed_cache: DashboardFeedCache | None = None

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
        if self.heavy_feed_cache is not None:
            self.heavy_feed_cache.close()
        if self.state_feed_cache is not None:
            self.state_feed_cache.close()


def _bind_local_files(
    handler: type[_DashboardHandler],
    *,
    fleet_observed_file: str | Path | None,
    fleet_observed_access_file: str | Path | None,
    host_sessions_access_file: str | Path | None,
    scope_groups_file: str | Path | None,
) -> None:
    """Bind the start's owner-only local files to its handler, refusing any the start cannot use.

    Raises
    ------
    ValueError
        When the Fleet export and its access file are not given together, or a
        policy or groups file is unavailable, unsafe or malformed.
    """
    if bool(fleet_observed_file) != bool(fleet_observed_access_file):
        raise ValueError("Fleet export and access file must be supplied together")
    handler.fleet_observed_file = Path(fleet_observed_file) if fleet_observed_file else None
    handler.fleet_observed_access_file = (
        Path(fleet_observed_access_file) if fleet_observed_access_file else None
    )
    if handler.fleet_observed_access_file is not None:
        load_mirror_grants(handler.fleet_observed_access_file)
    handler.host_sessions_access_file = (
        Path(host_sessions_access_file) if host_sessions_access_file else None
    )
    if handler.host_sessions_access_file is not None:
        load_host_grants(handler.host_sessions_access_file)
    handler.scope_groups = load_scope_groups(scope_groups_file) if scope_groups_file else None


def _optional_path(value: str | Path | None) -> Path | None:
    """Return ``value`` as a path, or ``None`` when the start did not name one."""
    return Path(value) if value is not None else None


@dataclass(frozen=True)
class _StartAccess:
    """Who may use this start: its access policy, its effective bearer, and its setup decision."""

    policy: DashboardAccessPolicy
    token: str | None
    generated: bool
    setup: SetupServing


def _resolve_access(
    *,
    name: str,
    host: str,
    dashboard_token: str | None,
    dashboard_access_file: str | Path | None,
    operator: bool,
    operator_name: str | None,
    setup: tuple[bool, str | Path | None],
) -> _StartAccess:
    """Return the start's access policy, its effective bearer, whether it was generated, and setup.

    An access file carries each principal, so it stands alone: no bearer and no
    global operator name beside it, and no bearer of the start's own. Without
    one, a single bearer, given or generated, guards the reads and, when armed,
    the operator's writes, which are attributed to ``operator:<name>`` unless an
    operator name is given. ``setup`` is ``(setup_apply, setup_receipts)``: an
    access file's admins hold the setup capabilities only when setup is armed,
    and :func:`~synapse_channel.dashboard_setup_http.setup_serving` decides
    whether setup is served at all.

    Raises
    ------
    ValueError
        When an access file is combined with a bearer or an operator name, or
        cannot be used, or when the setup options cannot be used.
    """
    setup_apply, setup_receipts = setup
    if dashboard_access_file is not None:
        if dashboard_token is not None:
            raise ValueError("--dashboard-access-file cannot be combined with --dashboard-token")
        if operator_name is not None:
            raise ValueError("--dashboard-access-file carries each operator identity")
        policy = load_dashboard_access_policy(
            dashboard_access_file, operator_armed=operator, setup_armed=setup_apply
        )
        effective, generated = None, False
    else:
        effective, generated, protects_reads = _resolve_dashboard_token(
            dashboard_token=dashboard_token
        )
        policy = compatibility_access_policy(
            dashboard_token=effective,
            token_protects_reads=protects_reads,
            operator_armed=operator,
            operator_name=(operator_name if operator_name else f"operator:{name}"),
        )
    serving = setup_serving(
        host=host,
        operator=operator,
        setup_apply=setup_apply,
        setup_receipts=setup_receipts,
        policy=policy,
    )
    return _StartAccess(policy, effective, generated, serving)


def _bind_host_monitor(
    handler: type[_DashboardHandler],
    *,
    pids: tuple[int, ...],
    tmux_socket: str | None,
    context_root: str | Path | None,
) -> None:
    """Scope the handler's host observation to the start's processes, tmux socket and context root.

    Raises
    ------
    ValueError
        When more than 256 process ids are given, or one is not a positive integer.
    """
    if len(pids) > 256 or any(type(pid) is not int or pid <= 0 for pid in pids):
        raise ValueError("host monitor accepts at most 256 positive PIDs")
    handler.host_session_monitor.pids = pids
    handler.host_session_monitor.tmux_socket = tmux_socket
    handler.host_session_monitor.context_root = (
        Path(context_root).resolve() if context_root is not None else None
    )


def _serve(
    handler: type[_DashboardHandler], host: str, port: int, access: _StartAccess
) -> DashboardServer:
    """Bind the HTTP server, serve it on a daemon thread and return the handle to it."""
    server = PromptBindThreadingHTTPServer((host, int(port)), handler)
    thread = threading.Thread(target=server.serve_forever, name="synapse-dashboard", daemon=True)
    thread.start()
    return DashboardServer(
        server=server,
        thread=thread,
        dashboard_token=access.token,
        dashboard_token_generated=access.generated,
        heavy_feed_cache=handler.heavy_feed_cache,
        state_feed_cache=handler.state_feed_cache,
    )


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
    host_sessions_access_file: str | Path | None = None,
    fleet_observed_file: str | Path | None = None,
    fleet_observed_access_file: str | Path | None = None,
    scope_groups_file: str | Path | None = None,
    host_session_pids: tuple[int, ...] = (),
    host_session_tmux_socket: str | None = None,
    host_session_context_root: str | Path | None = None,
    reliability_db: str | Path | None = None,
    reliability_db_key_file: str | Path | None = None,
    attention_store: str | Path | None = None,
    federation_store: str | Path | None = None,
    cockpit_dist: str | Path | None = None,
    operator: bool = False,
    operator_name: str | None = None,
    setup_apply: bool = False,
    setup_receipts: str | Path | None = None,
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
    host_sessions_access_file : str, pathlib.Path, or None, optional
        Owner-only explicit host observation grants, reloaded per request.
    fleet_observed_file, fleet_observed_access_file : str, pathlib.Path, or None, optional
        Optional versioned Fleet export and explicit observer policy; both are
        required together. No Fleet package or journal reader is imported.
    scope_groups_file : str, pathlib.Path, or None, optional
        Owner-only groups of namespaces a ``group:<name>`` scope may name
        (:mod:`synapse_channel.dashboard_scope_groups`); read and checked once here.
    host_session_pids : tuple of int, optional
        Explicit process scope for host observation; empty discovers candidates.
    host_session_context_root : str, pathlib.Path, or None, optional
        Allowed context pathname root for a non-default installation. Context
        disclosure still requires an explicit grant; no transcript body is read.
    host_session_tmux_socket : str or None, optional
        Explicit local tmux socket for host observation.
    reliability_db : str, pathlib.Path, or None, optional
        Hub event store powering the store-backed feeds —
        ``/reliability.json``, ``/events.json``, ``/causality.json``,
        ``/receipts.json``, ``/operator-actions.json``, and ``/sessions.json``;
        without it each endpoint reports its absence with 404.
    federation_store : str, pathlib.Path, or None, optional
        Operator federation store powering ``/federation.json``.
    attention_store : str, pathlib.Path, or None, optional
        Explicit owner-local queue powering authenticated ``/attention.json``.
    cockpit_dist : str, pathlib.Path, or None, optional
        Built cockpit directory served under ``/cockpit/``.
    operator : bool, optional
        Arm the operator write-path (``POST /message``). Off by default; when off,
        every write route is a 404 and the server stays a read-only observer.
    operator_name : str or None, optional
        Sender identity for relayed operator actions; ``operator:<name>`` when
        omitted, so operator writes are attributed and never impersonate an agent.
    setup_apply : bool, optional
        Arm setup from the dashboard (``--setup-apply``); it needs ``operator``,
        and only an access file's ``admin`` principals then hold the setup
        capabilities.
    setup_receipts : str, pathlib.Path, or None, optional
        Existing owner-only directory of the setup receipt chain
        (``--setup-receipts``); without it, setup is not served.
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
    from synapse_channel.dashboard import (
        OPERATOR_RATE_MAX,
        OPERATOR_RATE_WINDOW_SECONDS,
        _handler_class,
    )

    validate_dashboard_bind(host, allow_non_loopback=allow_non_loopback)
    # Surface a malformed operator-approved host at startup, not per request.
    allowed_host_authorities(host, int(port), tuple(allow_hosts))
    access = _resolve_access(
        name=name,
        host=host,
        dashboard_token=dashboard_token,
        dashboard_access_file=dashboard_access_file,
        operator=operator,
        operator_name=operator_name,
        setup=(setup_apply, setup_receipts),
    )
    handler = _handler_class(
        uri=uri,
        name=name,
        token=token,
        ready_timeout=ready_timeout,
        response_timeout=response_timeout,
        refresh_seconds=max(1, int(refresh_seconds)),
        a2a_state_file=_optional_path(a2a_state_file),
        access_policy=access.policy,
        reliability_db=_optional_path(reliability_db),
        reliability_db_key_file=_optional_path(reliability_db_key_file),
        attention_store=_optional_path(attention_store),
        federation_store=_optional_path(federation_store),
        cockpit_dist=_optional_path(cockpit_dist),
        operator_rate_limiter=WriteRateLimiter(
            max_calls=OPERATOR_RATE_MAX, window_seconds=OPERATOR_RATE_WINDOW_SECONDS
        ),
        observed_peers=observed_peers,
        observed_token=observed_token,
        observed_timeout=observed_timeout,
        observed_pins=observed_pins,
        allow_hosts=tuple(allow_hosts),
    )
    _bind_local_files(
        handler,
        fleet_observed_file=fleet_observed_file,
        fleet_observed_access_file=fleet_observed_access_file,
        host_sessions_access_file=host_sessions_access_file,
        scope_groups_file=scope_groups_file,
    )
    _bind_host_monitor(
        handler,
        pids=host_session_pids,
        tmux_socket=host_session_tmux_socket,
        context_root=host_session_context_root,
    )
    handler.setup_serving = access.setup
    handler.setup_inspection = hub_inspection(uri, token)
    return _serve(handler, host, port, access)
