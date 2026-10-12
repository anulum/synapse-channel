# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard snapshot and host-monitor binding
"""Bind bounded snapshot collection and host coordination for one dashboard."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from synapse_channel.dashboard_feed_cache import DashboardFeedCache as DashboardFeedCache
from synapse_channel.dashboard_snapshot_gate import DashboardSnapshotGate as DashboardSnapshotGate
from synapse_channel.host_sessions import HostSessionMonitor as HostSessionMonitor

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path
    from typing import Protocol

    from synapse_channel.dashboard import DashboardSnapshot
    from synapse_channel.dashboard_access import DashboardAccessPolicy
    from synapse_channel.dashboard_operator import WriteRateLimiter
    from synapse_channel.observed_peers import ObservedPeerSpec

    class SnapshotFetcher(Protocol):
        """Existing asynchronous snapshot entrypoint, bound without changing it."""

        def __call__(
            self,
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
        ) -> Coroutine[object, object, DashboardSnapshot]:
            """Collect the owner's exact live-hub snapshot under its deadlines."""
            ...


@dataclass(frozen=True)
class DashboardSnapshotSource:
    """Existing live and observed-hub inputs for one independent server.

    Parameters
    ----------
    uri, name, token
        Exact hub and dashboard identity inputs.
    ready_timeout, response_timeout
        Existing readiness and response bounds.
    observed_peers, observed_token, observed_timeout, observed_pins
        Existing optional observed-hub configuration; no authority is derived from it.
    """

    uri: str
    name: str
    token: str | None
    ready_timeout: float
    response_timeout: float
    observed_peers: tuple[ObservedPeerSpec, ...]
    observed_token: str | None
    observed_timeout: float
    observed_pins: dict[str, str] | None


def bind_snapshot_gate(
    source: DashboardSnapshotSource, fetcher: Callable[[], SnapshotFetcher]
) -> DashboardSnapshotGate[DashboardSnapshot]:
    """Bind the same asynchronous fetch and aggregate deadline to a server gate.

    Parameters
    ----------
    source
        Exact per-server live and observed-hub inputs.
    fetcher
        Owner snapshot callable, preserving existing facade substitutions.

    Returns
    -------
    DashboardSnapshotGate
        Existing bounded gate with its original default policy.
    """

    def fetch() -> DashboardSnapshot:
        return asyncio.run(
            asyncio.wait_for(
                fetcher()(
                    uri=source.uri,
                    name=source.name,
                    token=source.token,
                    ready_timeout=source.ready_timeout,
                    response_timeout=source.response_timeout,
                    observed_peers=source.observed_peers,
                    observed_token=source.observed_token,
                    observed_timeout=source.observed_timeout,
                    observed_pins=source.observed_pins,
                ),
                timeout=source.ready_timeout
                + source.response_timeout
                + (source.observed_timeout if source.observed_peers else 0)
                + 1.0,
            )
        )

    return DashboardSnapshotGate(fetch)


def bind_host_monitor(
    source: DashboardSnapshotSource,
    gate: DashboardSnapshotGate[DashboardSnapshot],
    fetcher: Callable[[], SnapshotFetcher],
    unavailable_error: type[Exception],
) -> HostSessionMonitor:
    """Bind the existing bounded roster/claim observation to a host monitor.

    Parameters
    ----------
    source, gate, fetcher
        Same server snapshot source, shared gate and owning async fetcher.
    unavailable_error
        Existing authored refusal type for oversized host observations.

    Returns
    -------
    HostSessionMonitor
        Existing monitor with its current default scope and platform adapters.
    """

    def coordination() -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
        snapshot = gate.fetch(
            wait_timeout=0.05,
            fetcher=lambda: asyncio.run(
                fetcher()(
                    uri=source.uri,
                    name=source.name,
                    token=source.token,
                    ready_timeout=0.5,
                    response_timeout=0.5,
                )
            ),
        )
        claims = snapshot.state.get("active_claims", [])
        active: list[tuple[str, str]] = []
        if len(snapshot.online_agents) > 1024 or (isinstance(claims, list) and len(claims) > 1024):
            raise unavailable_error("host coordination observation exceeds limit")
        if isinstance(claims, list):
            for claim in claims[:1024]:
                if not isinstance(claim, dict):
                    continue
                owner, task, expiry = (
                    claim.get("owner"),
                    claim.get("task_id"),
                    claim.get("lease_expires_at"),
                )
                if (
                    isinstance(owner, str)
                    and isinstance(task, str)
                    and isinstance(expiry, (int, float))
                    and expiry > time.time()
                ):
                    active.append((owner, task))
        return tuple(snapshot.online_agents[:1024]), tuple(active)

    return HostSessionMonitor(coordination=coordination)


@dataclass(frozen=True)
class DashboardViewSettings:
    """Existing page, feed and operator settings of one bound server.

    Parameters
    ----------
    refresh_seconds, a2a_state_file
        Existing page refresh and optional read-side A2A input.
    access_policy, operator_rate_limiter, allow_hosts
        Existing request admission inputs; this record grants nothing.
    reliability_db, reliability_db_key_file, attention_store, federation_store, cockpit_dist
        Existing local read-side sources and static build directory.
    """

    refresh_seconds: int
    a2a_state_file: Path | None
    access_policy: DashboardAccessPolicy
    reliability_db: Path | None
    reliability_db_key_file: Path | None
    attention_store: Path | None
    federation_store: Path | None
    cockpit_dist: Path | None
    operator_rate_limiter: WriteRateLimiter
    allow_hosts: tuple[str, ...]


@dataclass(frozen=True)
class DashboardReaders:
    """Independently owned caches, snapshot gate and host monitor for one server.

    Parameters
    ----------
    heavy_feed_cache, state_feed_cache
        Existing cache configurations and process-isolation choices.
    snapshot_gate, host_session_monitor
        Existing bounded snapshot and host-coordination owners.
    """

    heavy_feed_cache: DashboardFeedCache
    state_feed_cache: DashboardFeedCache
    snapshot_gate: DashboardSnapshotGate[DashboardSnapshot]
    host_session_monitor: HostSessionMonitor


def bind_dashboard_readers(
    source: DashboardSnapshotSource,
    view: DashboardViewSettings,
    *,
    fetcher: Callable[[], SnapshotFetcher],
    cache_factory: type[DashboardFeedCache],
    unavailable_error: type[Exception],
) -> DashboardReaders:
    """Construct the original per-server read owners in their existing order.

    Parameters
    ----------
    source, view
        Exact independent per-server inputs; no global or shared cache state.
    fetcher
        Getter for the owner callable, preserving its request-time late lookup.
    cache_factory, unavailable_error
        Existing owner cache constructor and precise authored refusal type.

    Returns
    -------
    DashboardReaders
        Original heavy-cache, state-cache, gate and monitor owners.
    """
    heavy = cache_factory(process_isolation=view.reliability_db is not None)
    state = cache_factory(
        ttl_seconds=15.0, max_entries=64, process_isolation=view.reliability_db is not None
    )
    gate = bind_snapshot_gate(source, fetcher)
    monitor = bind_host_monitor(source, gate, fetcher, unavailable_error)
    return DashboardReaders(heavy, state, gate, monitor)
