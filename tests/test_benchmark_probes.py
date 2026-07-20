# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark probe regressions over real surfaces

from __future__ import annotations

import asyncio

import pytest

from synapse_channel.benchmark.probes import (
    PROBES,
    _connect_ready_agent,
    _free_port,
    _MessageWaiter,
    _percentiles_ms,
    probe_claim_grant,
    probe_durable_claim_grant,
    probe_encode_lite,
    probe_event_store_append,
    probe_event_store_replay,
    probe_hub_roundtrip,
    run_probes,
)


def test_event_store_append_measures_durable_writes() -> None:
    result = probe_event_store_append(5)
    assert result.name == "event-store-append"
    assert result.iterations == 5
    assert result.duration_seconds > 0
    assert result.metrics["events_per_second"] > 0
    assert result.metrics["p95_ms"] >= result.metrics["p50_ms"] > 0


def test_event_store_replay_rebuilds_every_live_claim() -> None:
    result = probe_event_store_replay(7)
    assert result.metrics["live_claims_rebuilt"] == 7.0
    assert result.metrics["events_per_second"] > 0


def test_encode_lite_reports_a_real_byte_reduction() -> None:
    result = probe_encode_lite(10)
    assert 0 < result.metrics["lite_bytes"] < result.metrics["raw_bytes"]
    assert 0 < result.metrics["lite_to_raw_ratio"] < 1
    assert result.metrics["extension_fields_preserved"] == 10.0
    assert result.metrics["messages_per_second"] > 0


def test_hub_roundtrip_exercises_a_real_websocket() -> None:
    result = probe_hub_roundtrip(3)
    assert result.iterations == 3
    assert result.metrics["roundtrips_per_second"] > 0
    assert result.metrics["p50_ms"] > 0


def test_claim_grant_round_trips_the_coordination_core() -> None:
    result = probe_claim_grant(3)
    assert result.iterations == 3
    assert result.metrics["claims_per_second"] > 0
    assert result.metrics["p95_ms"] >= result.metrics["p50_ms"] > 0


def test_durable_claim_grant_measures_scheduler_lag() -> None:
    result = probe_durable_claim_grant(3)
    assert result.name == "durable-claim-grant"
    assert result.iterations == 3
    assert result.metrics["claims_per_second"] > 0
    assert result.metrics["p95_ms"] >= result.metrics["p50_ms"] > 0
    assert result.metrics["event_loop_lag_p95_ms"] >= 0
    assert result.metrics["event_loop_lag_max_ms"] >= result.metrics["event_loop_lag_p95_ms"]


def test_run_probes_preserves_order_and_applies_overrides() -> None:
    results = run_probes(["encode-lite", "event-store-append"], iterations=4)
    assert [result.name for result in results] == ["encode-lite", "event-store-append"]
    assert all(result.iterations == 4 for result in results)


def test_run_probes_uses_registry_defaults_without_override() -> None:
    (result,) = run_probes(["encode-lite"])
    assert result.iterations == PROBES["encode-lite"][0]


def test_run_probes_refuses_unknown_names_and_bad_iterations() -> None:
    with pytest.raises(ValueError, match="unknown probe"):
        run_probes(["nonesuch"])
    with pytest.raises(ValueError, match="iterations must be positive"):
        run_probes(["encode-lite"], iterations=0)


def test_percentiles_of_a_single_sample_collapse() -> None:
    metrics = _percentiles_ms([0.002])
    assert metrics["p50_ms"] == pytest.approx(2.0)
    assert metrics["p95_ms"] == pytest.approx(2.0)


def test_connect_ready_agent_times_out_without_a_hub() -> None:
    async def attempt() -> None:
        await _connect_ready_agent(_MessageWaiter(), _free_port(), attempts=1)

    with pytest.raises(TimeoutError, match="did not receive the hub welcome"):
        asyncio.run(attempt())
