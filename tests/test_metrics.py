# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Prometheus metrics and health snapshot

from __future__ import annotations

import time
from pathlib import Path

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.metrics import (
    Metric,
    collect_hub_metrics,
    health_snapshot,
    render_prometheus,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION

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
    assert metrics["synapse_journal_corrupt_rows"] == 0
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
        "journal_corrupt_rows": 0,
        "version": __version__,
        "protocol_version": WIRE_PROTOCOL_VERSION,
        "hub_id": "syn-health",
        "config_epoch": "",  # an ad-hoc hub was built without a fingerprinted config
        "uptime_seconds": 5.0,
        "online_agents": 1,
        "active_claims": 1,
    }


def test_corrupt_journal_rows_degrade_health_and_metric(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    seq = store.append("claim", {"task_id": "T1"})
    store._conn.execute("UPDATE events SET payload = 'bad' WHERE seq = ?", (seq,))
    store._conn.commit()
    hub = SynapseHub(journal=store)

    snapshot = health_snapshot(hub)
    metrics = {metric.name: metric.value for metric in collect_hub_metrics(hub)}

    assert snapshot["status"] == "degraded"
    assert snapshot["journal_corrupt_rows"] == 1
    assert metrics["synapse_journal_corrupt_rows"] == 1
    store.close()


def test_health_snapshot_reports_the_config_epoch_of_a_configured_hub() -> None:
    from synapse_channel.core.hub_config import HubConfig, HubLimits, config_fingerprint

    config = HubConfig(limits=HubLimits(max_clients=7))
    hub = SynapseHub.from_config(config)

    snapshot = health_snapshot(hub)

    # A hub built from a config carries its posture fingerprint, so a cockpit can
    # pin it; the same config always yields the same epoch.
    assert snapshot["config_epoch"] == config_fingerprint(config)
    assert snapshot["config_epoch"] != ""


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


# -- decision counters ---------------------------------------------------------


def test_fresh_hub_reports_zero_decision_counters() -> None:
    metrics = {m.name: m.value for m in collect_hub_metrics(SynapseHub())}
    for name in (
        "synapse_claims_granted_total",
        "synapse_claims_denied_total",
        "synapse_releases_granted_total",
        "synapse_chat_directed_total",
        "synapse_chat_broadcast_total",
        "synapse_auth_failures_total",
        "synapse_rate_limited_total",
        "synapse_durable_ingress_refused_total",
        "synapse_federation_denied_total",
        "synapse_forwarded_claims_total",
        "synapse_forwarded_claims_granted_total",
        "synapse_forwarded_claims_denied_total",
        "synapse_forwarded_claim_timeouts_total",
        "synapse_takeovers_total",
        "synapse_takeover_quarantines_total",
        "synapse_live_waiters",
        "synapse_dead_letter_targets",
        "synapse_dead_letters",
    ):
        assert metrics[name] == 0, name


def test_counters_and_gauges_surface_hub_decisions() -> None:
    hub = SynapseHub()
    hub.counters.claims_granted = 3
    hub.counters.claims_denied = 2
    hub.counters.releases_granted = 1
    hub.counters.chat_directed = 7
    hub.counters.chat_broadcast = 4
    hub.counters.auth_failures = 5
    hub.counters.rate_limited = 6
    hub.counters.durable_ingress_refused = 15
    hub.counters.federation_denied = 8
    hub.counters.forwarded_claims = 11
    hub.counters.forwarded_claims_granted = 12
    hub.counters.forwarded_claims_denied = 13
    hub.counters.forwarded_claim_timeouts = 14
    hub.counters.takeovers = 9
    hub.counters.takeover_quarantines = 10
    hub.agent_sockets["repo/agent"] = object()
    hub.agent_sockets["repo/agent-rx"] = object()
    # Fresh timestamps: the hub's ledger ages out stale targets, so the gauge
    # reflects live blackholes measured against the wall clock, not epoch zero.
    now = time.time()
    hub.dead_letters.record("ghost/one", sender="a", ts=now - 2.0)
    hub.dead_letters.record("ghost/one", sender="b", ts=now - 1.0)
    hub.dead_letters.record("ghost/two", sender="a", ts=now)

    by_name = {m.name: m.value for m in collect_hub_metrics(hub)}

    assert by_name["synapse_claims_granted_total"] == 3
    assert by_name["synapse_claims_denied_total"] == 2
    assert by_name["synapse_releases_granted_total"] == 1
    assert by_name["synapse_chat_directed_total"] == 7
    assert by_name["synapse_chat_broadcast_total"] == 4
    assert by_name["synapse_auth_failures_total"] == 5
    assert by_name["synapse_rate_limited_total"] == 6
    assert by_name["synapse_durable_ingress_refused_total"] == 15
    assert by_name["synapse_federation_denied_total"] == 8
    assert by_name["synapse_forwarded_claims_total"] == 11
    assert by_name["synapse_forwarded_claims_granted_total"] == 12
    assert by_name["synapse_forwarded_claims_denied_total"] == 13
    assert by_name["synapse_forwarded_claim_timeouts_total"] == 14
    assert by_name["synapse_takeovers_total"] == 9
    assert by_name["synapse_takeover_quarantines_total"] == 10
    assert by_name["synapse_live_waiters"] == 1
    assert by_name["synapse_dead_letter_targets"] == 2
    assert by_name["synapse_dead_letters"] == 3


def test_registry_shares_the_hub_counters_object() -> None:
    hub = SynapseHub()
    assert hub.clients.counters is hub.counters


# -- live-path counter increments ----------------------------------------------


async def test_decision_counters_increment_through_the_live_path() -> None:
    hub = SynapseHub()
    async with running_hub(hub) as (_, uri):
        alice = await connect_agent("alice", uri)
        bob = await connect_agent("bob", uri)
        try:
            await alice.agent.send_message("chat", payload="hello everyone")  # broadcast
            await alice.agent.send_message(
                "chat", target="ghost/nobody", payload="psst"
            )  # directed blackhole
            await alice.agent.claim("T1")
            await alice.recorder.wait_for(
                lambda m: m.get("type") == "claim_granted" and m.get("owner") == "alice"
            )
            await bob.agent.claim("T1")  # denied: alice holds it
            await bob.recorder.wait_for(lambda m: m.get("type") == "claim_denied")
            await alice.agent.release("T1")
            await alice.recorder.wait_for(lambda m: m.get("type") == "release_granted")
        finally:
            await close_agents(bob, alice)

    by_name = {m.name: m.value for m in collect_hub_metrics(hub)}
    assert by_name["synapse_chat_broadcast_total"] == 1
    assert by_name["synapse_chat_directed_total"] == 1
    assert by_name["synapse_claims_granted_total"] == 1
    assert by_name["synapse_claims_denied_total"] == 1
    assert by_name["synapse_releases_granted_total"] == 1
    assert by_name["synapse_dead_letter_targets"] == 1  # ghost/nobody heard nothing
    rendered = render_prometheus(collect_hub_metrics(hub))
    assert "synapse_claims_denied_total 1" in rendered
