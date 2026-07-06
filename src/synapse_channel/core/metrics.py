# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Prometheus metrics and the health snapshot for the hub
"""Prometheus-format metrics and a health snapshot, rendered from hub counters.

The hub can expose an optional HTTP ``/metrics`` and ``/health`` endpoint (wired
in :mod:`synapse_channel.core.hub`); this module is the transport-free half that
turns the hub's live in-memory counters into the Prometheus text exposition
format and a small JSON health document. It depends on no WebSocket transport and
no third-party metrics client — the exposition text is built by hand — so every
metric and its formatting is unit-testable against a plain hub instance.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
"""Content-Type for the Prometheus text exposition format (version 0.0.4)."""

HEALTH_CONTENT_TYPE = "application/json"
"""Content-Type for the JSON health document."""


@dataclass(frozen=True)
class Metric:
    """One Prometheus sample: a named gauge or counter with help text.

    Attributes
    ----------
    name : str
        Metric name, e.g. ``synapse_active_claims``.
    documentation : str
        Human-readable ``HELP`` text.
    metric_type : str
        ``gauge`` for a value that goes up and down, ``counter`` for a monotonic
        total.
    value : float
        The current sample value.
    """

    name: str
    documentation: str
    metric_type: str
    value: float


def _format_value(value: float) -> str:
    """Render a metric value, dropping the decimal point for an integral number."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return repr(number)


def render_prometheus(metrics: Iterable[Metric]) -> str:
    """Render metrics in the Prometheus text exposition format.

    Each metric emits a ``# HELP`` line, a ``# TYPE`` line, and one sample line.

    Parameters
    ----------
    metrics : Iterable[Metric]
        The samples to render, in order.

    Returns
    -------
    str
        The exposition text, terminated by a trailing newline.
    """
    lines: list[str] = []
    for metric in metrics:
        lines.append(f"# HELP {metric.name} {metric.documentation}")
        lines.append(f"# TYPE {metric.name} {metric.metric_type}")
        lines.append(f"{metric.name} {_format_value(metric.value)}")
    return "\n".join(lines) + "\n"


def collect_hub_metrics(hub: SynapseHub) -> list[Metric]:
    """Read the hub's live counters into a list of metrics.

    Only the hub's in-memory state is inspected — no I/O — so this is safe to call
    from the event loop on every scrape.

    Parameters
    ----------
    hub : SynapseHub
        The hub to read.

    Returns
    -------
    list[Metric]
        A constant ``synapse_up`` liveness gauge, gauges for the live
        presence/claims/resources/history/board counts (waiter sidecars and
        dead-letter targets included), the monotonic message counter, and the
        hub's decision counters — claims granted/denied, releases, directed and
        broadcast chat, auth failures, rate-limit rejections, federation
        denials, takeovers and their quarantines — everything a Grafana panel
        or an alert rule needs to see the hub deciding, not just existing.
    """
    return [
        Metric("synapse_up", "Whether the hub is serving (always 1).", "gauge", 1),
        Metric(
            "synapse_connected_clients",
            "Open WebSocket connections.",
            "gauge",
            len(hub.connected_clients),
        ),
        Metric(
            "synapse_online_agents",
            "Registered named agents.",
            "gauge",
            len(hub.agent_sockets),
        ),
        Metric(
            "synapse_active_claims",
            "Live task leases.",
            "gauge",
            len(hub.state.claims),
        ),
        Metric(
            "synapse_resource_offers",
            "Advertised resource offers.",
            "gauge",
            len(hub.state.resources),
        ),
        Metric(
            "synapse_chat_history_messages",
            "Chat messages retained in memory.",
            "gauge",
            len(hub.chat_history),
        ),
        Metric(
            "synapse_blackboard_tasks",
            "Tasks on the shared blackboard.",
            "gauge",
            len(hub.blackboard.tasks),
        ),
        Metric(
            "synapse_messages_total",
            "Messages assigned a sequence id since start (resumed from the journal).",
            "counter",
            hub._message_seq,
        ),
        Metric(
            "synapse_live_waiters",
            "Connected -rx waiter sidecars.",
            "gauge",
            sum(1 for name in hub.agent_sockets if name.endswith("-rx")),
        ),
        Metric(
            "synapse_dead_letter_targets",
            "Directed-chat targets nobody was listening for (cleared on connect).",
            "gauge",
            len(hub.dead_letters.snapshot()),
        ),
        Metric(
            "synapse_dead_letters",
            "Directed chats that reached no live connection, across all targets.",
            "gauge",
            sum(
                count
                for letter in hub.dead_letters.snapshot()
                if isinstance(count := letter["count"], int)
            ),
        ),
        Metric(
            "synapse_claims_granted_total",
            "Claim requests granted since start, forwarded claims included.",
            "counter",
            hub.counters.claims_granted,
        ),
        Metric(
            "synapse_claims_denied_total",
            "Claim requests denied since start.",
            "counter",
            hub.counters.claims_denied,
        ),
        Metric(
            "synapse_releases_granted_total",
            "Releases granted since start.",
            "counter",
            hub.counters.releases_granted,
        ),
        Metric(
            "synapse_chat_directed_total",
            "Chat frames addressed to a name, list, or glob since start.",
            "counter",
            hub.counters.chat_directed,
        ),
        Metric(
            "synapse_chat_broadcast_total",
            "Chat frames addressed to everyone since start.",
            "counter",
            hub.counters.chat_broadcast,
        ),
        Metric(
            "synapse_auth_failures_total",
            "Frames refused by required per-message authentication since start.",
            "counter",
            hub.counters.auth_failures,
        ),
        Metric(
            "synapse_rate_limited_total",
            "Frames refused by the per-sender rate limiter since start.",
            "counter",
            hub.counters.rate_limited,
        ),
        Metric(
            "synapse_federation_denied_total",
            "Frames refused by the federation gate since start.",
            "counter",
            hub.counters.federation_denied,
        ),
        Metric(
            "synapse_takeovers_total",
            "Waiter takeover requests accepted since start.",
            "counter",
            hub.counters.takeovers,
        ),
        Metric(
            "synapse_takeover_quarantines_total",
            "Takeover oscillations that entered quarantine since start.",
            "counter",
            hub.counters.takeover_quarantines,
        ),
    ]


def health_snapshot(hub: SynapseHub) -> dict[str, Any]:
    """Return a small JSON-serialisable health document for the hub.

    Parameters
    ----------
    hub : SynapseHub
        The hub to summarise.

    Returns
    -------
    dict[str, Any]
        ``status`` (``ok`` whenever the hub answers), the package ``version``, the
        ``protocol_version`` (the wire-protocol version, decoupled from the package
        version), the ``hub_id``, the ``config_epoch`` (a fingerprint of the
        configuration posture the hub was built from — empty for an ad-hoc
        construction), the ``uptime_seconds`` since start, and the current
        online-agent and active-claim counts. ``version`` and ``config_epoch``
        together are the hub's pinning indicator: a change in either is a deploy or
        a config drift; ``protocol_version`` changes only on a wire-incompatible one.
    """
    # Imported lazily: the package __init__ imports this module, so a top-level
    # import would be circular; by call time the package is fully initialised.
    from synapse_channel import __version__
    from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION

    return {
        "status": "ok",
        "version": __version__,
        "protocol_version": WIRE_PROTOCOL_VERSION,
        "hub_id": hub.hub_id,
        "config_epoch": hub.config_epoch,
        "uptime_seconds": round(hub.uptime_seconds(), 3),
        "online_agents": len(hub.agent_sockets),
        "active_claims": len(hub.state.claims),
    }
