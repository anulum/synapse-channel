# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dead-letter ledger and hub wiring regressions

from __future__ import annotations

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.dead_letters import (
    DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS,
    DEFAULT_DEAD_LETTER_TARGETS,
    DeadLetterLedger,
    is_directed_target,
)
from synapse_channel.core.hub import SynapseHub


class TestDirectedTarget:
    def test_names_are_directed(self) -> None:
        assert is_directed_target("ACME/coordinator") is True
        assert is_directed_target("ACME") is True

    def test_audiences_are_not(self) -> None:
        assert is_directed_target("all") is False
        assert is_directed_target("ACME/*") is False
        assert is_directed_target("") is False
        assert is_directed_target("  ") is False


class TestLedger:
    def test_record_counts_and_keeps_the_latest_context(self) -> None:
        ledger = DeadLetterLedger()
        ledger.record("A/coord", sender="X", ts=1.0)
        ledger.record("A/coord", sender="Y", ts=2.0)

        snapshot = ledger.snapshot()

        assert snapshot == [{"target": "A/coord", "count": 2, "last_ts": 2.0, "last_sender": "Y"}]

    def test_snapshot_orders_the_biggest_blackhole_first(self) -> None:
        ledger = DeadLetterLedger()
        ledger.record("quiet", sender="X", ts=1.0)
        ledger.record("loud", sender="X", ts=2.0)
        ledger.record("loud", sender="X", ts=3.0)

        targets = [entry["target"] for entry in ledger.snapshot()]

        assert targets == ["loud", "quiet"]

    def test_clear_forgets_only_the_arrived_reader(self) -> None:
        ledger = DeadLetterLedger()
        ledger.record("A", sender="X", ts=1.0)
        ledger.record("B", sender="X", ts=2.0)

        ledger.clear("A")
        ledger.clear("never-recorded")

        assert [entry["target"] for entry in ledger.snapshot()] == ["B"]

    def test_bound_evicts_the_stalest_target(self) -> None:
        ledger = DeadLetterLedger(max_targets=2)
        ledger.record("old", sender="X", ts=1.0)
        ledger.record("mid", sender="X", ts=2.0)
        ledger.record("new", sender="X", ts=3.0)

        targets = {entry["target"] for entry in ledger.snapshot()}

        assert targets == {"mid", "new"}

    def test_default_bound_is_the_documented_value(self) -> None:
        assert DeadLetterLedger().max_targets == DEFAULT_DEAD_LETTER_TARGETS
        assert DeadLetterLedger(max_targets=0).max_targets == 1


class TestLedgerAgeBound:
    def test_no_age_bound_by_default_keeps_ancient_entries(self) -> None:
        ledger = DeadLetterLedger()
        ledger.record("ghost", sender="X", ts=1.0)

        # A snapshot judged far in the future still shows it — no age bound.
        assert [e["target"] for e in ledger.snapshot(now=1_000_000.0)] == ["ghost"]
        assert ledger.max_age_seconds is None

    def test_snapshot_expires_a_target_older_than_the_age_bound(self) -> None:
        ledger = DeadLetterLedger(max_age_seconds=10.0)
        ledger.record("ghost", sender="X", ts=100.0)

        assert [e["target"] for e in ledger.snapshot(now=109.0)] == ["ghost"]  # within window
        assert ledger.snapshot(now=111.0) == []  # aged out

    def test_a_refreshed_target_never_ages_out(self) -> None:
        ledger = DeadLetterLedger(max_age_seconds=10.0)
        ledger.record("busy", sender="X", ts=100.0)
        ledger.record("busy", sender="Y", ts=200.0)  # fresh traffic keeps it alive

        snapshot = ledger.snapshot(now=205.0)

        assert snapshot == [{"target": "busy", "count": 2, "last_ts": 200.0, "last_sender": "Y"}]

    def test_recording_a_fresh_target_prunes_a_gone_quiet_one(self) -> None:
        ledger = DeadLetterLedger(max_age_seconds=10.0)
        ledger.record("old", sender="X", ts=100.0)
        ledger.record("new", sender="X", ts=200.0)  # 'old' is now 100s stale, age bound 10s

        assert [e["target"] for e in ledger.snapshot(now=200.0)] == ["new"]

    def test_age_bound_and_capacity_bound_compose(self) -> None:
        ledger = DeadLetterLedger(max_targets=5, max_age_seconds=10.0)
        ledger.record("a", sender="X", ts=1.0)
        ledger.record("b", sender="X", ts=2.0)
        ledger.record("c", sender="X", ts=100.0)  # a, b are now stale and pruned on record

        assert [e["target"] for e in ledger.snapshot(now=100.0)] == ["c"]

    def test_hub_applies_the_recommended_age_default(self) -> None:
        assert DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS == 7 * 24 * 60 * 60
        assert SynapseHub().dead_letters.max_age_seconds == DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS


async def test_hub_reports_and_clears_dead_letters_end_to_end() -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        sender = await connect_agent("SPEAKER", uri)
        try:
            await sender.agent.send_message("chat", target="GHOST/coordinator", payload="anyone?")
            await sender.agent.send_message("chat", target="all", payload="broadcast is fine")
            await sender.agent.request_state()
            snap = await sender.recorder.wait_for(lambda m: m.get("type") == "state_snapshot")

            letters = snap["snapshot"]["dead_letters"]
            assert [entry["target"] for entry in letters] == ["GHOST/coordinator"]
            assert letters[0]["count"] == 1
            assert letters[0]["last_sender"] == "SPEAKER"

            reader = await connect_agent("GHOST/coordinator", uri)
            try:
                await sender.agent.request_state()
                cleared = await sender.recorder.wait_for(
                    lambda m: (
                        m.get("type") == "state_snapshot" and m["snapshot"]["dead_letters"] == []
                    )
                )
                assert cleared["snapshot"]["dead_letters"] == []
            finally:
                await close_agents(reader)
        finally:
            await close_agents(sender)
        assert hub.dead_letters.snapshot() == []
