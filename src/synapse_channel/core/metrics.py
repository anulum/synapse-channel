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
        presence/claims/resources/history/board counts, and a monotonic message
        counter.
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
        ``status`` (``ok`` whenever the hub answers), the ``hub_id``, and the
        current online-agent and active-claim counts.
    """
    return {
        "status": "ok",
        "hub_id": hub.hub_id,
        "online_agents": len(hub.agent_sockets),
        "active_claims": len(hub.state.claims),
    }
