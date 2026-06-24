# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Prometheus metrics and health snapshot

from __future__ import annotations

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.metrics import (
    Metric,
    collect_hub_metrics,
    health_snapshot,
    render_prometheus,
)

# -- render_prometheus --------------------------------------------------------


def test_render_emits_help_type_and_sample_lines() -> None:
    text = render_prometheus([Metric("synapse_x", "An x.", "gauge", 3)])
    assert "# HELP synapse_x An x." in text
    assert "# TYPE synapse_x gauge" in text
    assert "synapse_x 3" in text
    assert text.endswith("\n")


def test_render_drops_the_decimal_for_an_integral_value() -> None:
    text = render_prometheus([Metric("synapse_x", "x", "counter", 5.0)])
    assert "synapse_x 5\n" in text


def test_render_keeps_a_fractional_value() -> None:
    text = render_prometheus([Metric("synapse_ratio", "r", "gauge", 2.5)])
    assert "synapse_ratio 2.5" in text


def test_render_of_no_metrics_is_a_lone_newline() -> None:
    assert render_prometheus([]) == "\n"


def test_render_preserves_metric_order() -> None:
    text = render_prometheus(
        [Metric("a_first", "first", "gauge", 1), Metric("z_second", "second", "gauge", 2)]
    )
    assert text.index("a_first") < text.index("z_second")


# -- collect_hub_metrics ------------------------------------------------------


def test_fresh_hub_reports_up_and_zeroes() -> None:
    metrics = {m.name: m.value for m in collect_hub_metrics(SynapseHub())}
    assert metrics["synapse_up"] == 1
    assert metrics["synapse_connected_clients"] == 0
    assert metrics["synapse_online_agents"] == 0
    assert metrics["synapse_active_claims"] == 0
    assert metrics["synapse_messages_total"] == 0


def test_metrics_track_live_state() -> None:
    hub = SynapseHub()
    hub.connected_clients.add(object())
    hub.agent_sockets["A"] = object()
    hub.state.claim("A", "T1", now=0.0)
    hub.blackboard.post_task(task_id="PLAN-1", title="plan", author="A", now=0.0)
    by_name = {m.name: m.value for m in collect_hub_metrics(hub)}
    assert by_name["synapse_connected_clients"] == 1
    assert by_name["synapse_online_agents"] == 1
    assert by_name["synapse_active_claims"] == 1
    assert by_name["synapse_blackboard_tasks"] == 1


def test_every_metric_declares_a_known_type() -> None:
    for metric in collect_hub_metrics(SynapseHub()):
        assert metric.metric_type in {"gauge", "counter"}
        assert metric.documentation  # never blank


def test_messages_total_is_a_counter() -> None:
    by_name = {m.name: m for m in collect_hub_metrics(SynapseHub())}
    assert by_name["synapse_messages_total"].metric_type == "counter"


# -- health_snapshot ----------------------------------------------------------


def test_health_snapshot_reports_ok_version_uptime_and_counts() -> None:
    from synapse_channel import __version__

    ticks = iter([100.0, 105.0])  # construction, then the snapshot read
    hub = SynapseHub(hub_id="syn-health", clock=lambda: next(ticks))
    hub.agent_sockets["A"] = object()
    hub.state.claim("A", "T1", now=0.0)
    snapshot = health_snapshot(hub)
    assert snapshot == {
        "status": "ok",
        "version": __version__,
        "hub_id": "syn-health",
        "uptime_seconds": 5.0,
        "online_agents": 1,
        "active_claims": 1,
    }


def test_rendered_metrics_parse_with_prometheus_client() -> None:
    # Validate the hand-rendered exposition against the real Prometheus parser, so
    # a format drift is caught without taking a runtime dependency on the client.
    from prometheus_client.parser import text_string_to_metric_families

    text = render_prometheus(collect_hub_metrics(SynapseHub()))
    families = list(text_string_to_metric_families(text))
    names = {family.name for family in families}
    assert "synapse_up" in names
    assert "synapse_active_claims" in names
    assert all(family.samples for family in families)  # every family carries a sample
