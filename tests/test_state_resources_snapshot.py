# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exhaustive tests for the coordination state registry

from __future__ import annotations

from typing import cast

from synapse_channel.core.state import (
    SynapseState,
)


def test_offer_resource_returns_key_and_clamps_capacity() -> None:
    state = SynapseState()
    key = state.offer_resource("A", kind="llm", name="m", capacity=0, now=1000.0)
    assert key == "A:llm:m"
    assert state.resources[key].capacity == 1
    assert state.resources[key].meta == {}


def test_offer_resource_non_finite_capacity_clamps_to_one() -> None:
    state = SynapseState()
    key = state.offer_resource(
        "A",
        kind="llm",
        name="m",
        capacity=cast(int, float("inf")),
        now=1000.0,
    )

    assert key == "A:llm:m"
    assert state.resources[key].capacity == 1


def test_offer_resource_keeps_meta_and_refreshes() -> None:
    state = SynapseState()
    state.offer_resource("A", kind="llm", name="m", meta={"vram": "8G"}, now=1000.0)
    state.offer_resource("A", kind="llm", name="m", meta={"vram": "16G"}, now=1100.0)
    offer = state.resources["A:llm:m"]
    assert offer.meta == {"vram": "16G"}
    assert offer.offered_at == 1100.0


def test_query_resources_filters_and_sorts() -> None:
    state = SynapseState()
    state.offer_resource("B", kind="llm", name="z", now=1000.0)
    state.offer_resource("A", kind="compute", name="gpu", now=1000.0)
    state.offer_resource("A", kind="llm", name="a", now=1000.0)

    everything = state.query_resources()
    assert [(r["agent"], r["kind"], r["name"]) for r in everything] == [
        ("A", "compute", "gpu"),
        ("A", "llm", "a"),
        ("B", "llm", "z"),
    ]

    only_llm = state.query_resources(kind="llm")
    assert {r["name"] for r in only_llm} == {"a", "z"}


def test_resource_offer_expires_after_ttl() -> None:
    state = SynapseState()
    state.offer_resource("A", kind="llm", name="m", now=1000.0)
    # A heartbeat far in the future triggers the soft-TTL sweep.
    state.heartbeat("A", now=1000.0 + 301.0)
    assert state.resources == {}


# --- snapshot ----------------------------------------------------------------


def test_snapshot_reports_claims_agents_and_resources() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.heartbeat("A", now=1000.0)
    state.heartbeat("B", now=1000.0)
    state.claim("A", "TASK-12", note="sync", now=1000.0)
    state.offer_resource("B", kind="llm", name="m", now=1000.0)

    snap = state.snapshot(now=1001.0)
    assert snap["generated_at"] == 1001.0
    assert len(snap["active_claims"]) == 1
    assert snap["active_claims"][0]["task_id"] == "TASK-12"
    assert {item["agent"] for item in snap["agents"]} == {"A", "B"}
    assert snap["resources"][0]["name"] == "m"


def test_snapshot_drops_expired_claim() -> None:
    state = SynapseState(default_ttl_seconds=60)
    state.claim("A", "TASK-13", now=1000.0)
    snap = state.snapshot(now=1000.0 + 61.0)
    assert snap["active_claims"] == []
