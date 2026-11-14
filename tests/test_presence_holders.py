# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a presence holder says it is passive, and every reader believes it
"""A ``synapse listen`` connection declares ``passive``; ``who``, ``doctor`` and the dashboard
read that fact the same way: a presence holder is counted, and it is neither an agent nor a seat."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, cast

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli_doctor
from synapse_channel.cli_doctor_mailbox import DoctorRoster, doctor_roster_from_snapshot
from synapse_channel.cli_messaging_listen import _listen
from synapse_channel.cli_query_rendering import _render_who
from synapse_channel.cli_query_who import _who
from synapse_channel.client.diagnostics import check_deaf_agents
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.waiter_identity import presence_holders
from synapse_channel.dashboard import DashboardSnapshot, fetch_dashboard_snapshot
from synapse_channel.dashboard_seats import build_seats
from synapse_channel.waiter_identity import presence_holders as facade_presence_holders

if TYPE_CHECKING:
    from synapse_channel.cli_messaging_types import AgentFactory

NOW = 1_000.0


def test_a_holder_is_a_passive_name_that_is_not_a_waiter() -> None:
    online = ["P-presence", "S/a", "S/a-rx", "S/b-pane-rx", "S/c", "LOUD-presence", "S/d"]
    declared = {
        "P-presence": " Passive ",
        "S/a": "direct",
        "S/a-rx": "passive",
        "S/b-pane-rx": "passive",
        "S/c": "pane_bridge",
        "LOUD-presence": "direct",
        "S/d": "nonsense",
    }

    assert presence_holders(online, declared) == {"P-presence"}
    assert presence_holders(online, None) == frozenset()
    assert presence_holders(online, {}) == frozenset()
    assert facade_presence_holders is presence_holders


async def test_listen_declares_that_it_never_acts() -> None:
    declared: dict[str, Any] = {}

    class _Agent:
        def __init__(self, name: str, callback: Any, **kwargs: Any) -> None:
            declared.update(kwargs, name=name)
            self.connection = None
            self.running = True
            self.last_close_code = None
            self.last_close_reason = None

        async def connect(self) -> None:
            return None

        async def wait_until_ready(self, timeout: float) -> bool:
            return False

    rc = await _listen(
        uri="ws://127.0.0.1:1", name="P-presence", agent_factory=cast("AgentFactory", _Agent)
    )

    assert rc == 1
    assert (declared["name"], declared["wake_capability"]) == ("P-presence", "passive")


def _snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        online_agents=["P-presence", "S/quiet", "S/away-rx", "OLD-presence"],
        state={"active_claims": [], "generated_at": NOW},
        board={"tasks": []},
        manifest=[],
        agent_liveness={
            "S/quiet": {"proven_live": False, "has_live_waiter": False, "last_reaction_age": 9.0},
            "OLD-presence": {"proven_live": False, "has_live_waiter": False},
            "P-presence": {"proven_live": False, "has_live_waiter": False},
        },
        wake_capabilities={
            "P-presence": "passive",
            "S/quiet": "direct",
            "S/away-rx": "passive",
            "OLD-presence": "direct",
        },
    )


def test_the_dashboard_counts_a_holder_and_names_no_seat_or_agent_for_it() -> None:
    document = _snapshot().to_dict()

    agents = document["fleet"]["agents"]
    seats = {row["identity"]: row for row in document["fleet"]["seats"]}
    assert agents["live"] == ["OLD-presence", "S/quiet"]
    assert agents["missing_waiters"] == ["OLD-presence-rx", "S/quiet-rx"]
    assert agents["presence_holders"] == 1
    assert sorted(seats) == ["OLD-presence", "S/away", "S/quiet"]
    assert seats["S/away"]["state"] == "listening"  # its waiter stands for it
    headline = document["headline"]
    assert (headline["agents_live"], headline["waiters_missing"]) == (2, 2)
    assert (headline["presence_holders"], headline["seats_deaf"]) == (1, 2)
    assert "wake_capabilities" not in document


def test_without_declared_capabilities_the_dashboard_reads_as_before() -> None:
    snapshot = _snapshot()
    plain = DashboardSnapshot(
        online_agents=snapshot.online_agents,
        state=snapshot.state,
        board=snapshot.board,
        manifest=[],
        agent_liveness=snapshot.agent_liveness,
    ).to_dict()

    assert plain["headline"]["presence_holders"] == 0
    assert "P-presence" in {row["identity"] for row in plain["fleet"]["seats"]}
    assert "P-presence" in plain["fleet"]["agents"]["live"]


def test_a_holder_that_holds_a_claim_is_a_seat_by_its_claim() -> None:
    rows = build_seats(
        ["P-presence"],
        {"active_claims": [{"owner": "P-presence", "claimed_at": NOW - 10.0}]},
        {"progress": []},
        now=NOW,
        wake_capabilities={"P-presence": "passive"},
    )

    assert [(row["identity"], row["evidence"]) for row in rows] == [("P-presence", "claim")]


def test_who_lists_holders_apart_without_a_deaf_mark(capsys: pytest.CaptureFixture[str]) -> None:
    _render_who(
        ["P-presence", "S/quiet", "S/away-rx"],
        liveness={
            "P-presence": {"proven_live": False, "has_live_waiter": False},
            "S/quiet": {"proven_live": False, "has_live_waiter": False, "last_reaction_age": 9.0},
        },
        wake_capabilities={"P-presence": "passive", "S/quiet": "direct", "S/away-rx": "passive"},
    )
    lines = capsys.readouterr().out.splitlines()

    assert lines[0] == "Online (1 agents · 1 waiters · 1 presence holders):"
    assert lines[-3:] == [
        "Presence holders (1):",
        "  P-presence",
        "Unarmed (present, no live waiter): S/quiet",
    ]
    assert not any("P-presence" in line and "deaf" in line for line in lines)


def test_who_without_capabilities_renders_as_before(capsys: pytest.CaptureFixture[str]) -> None:
    _render_who(["P-presence", "S/quiet"])

    assert capsys.readouterr().out.splitlines() == [
        "Online (2 agents · 0 waiters):",
        "  P-presence",
        "  S/quiet",
    ]


def test_doctor_reads_the_declared_fact_and_states_its_fallback() -> None:
    roster = ["QUIET-presence", "S/a", "S/a-rx", "LOUD-presence"]
    declared = {"QUIET-presence": "passive", "S/a": "direct", "LOUD-presence": "direct"}

    by_fact = check_deaf_agents(roster, declared)
    by_name = check_deaf_agents(roster)

    assert by_fact.status == "warn"
    assert by_fact.detail == "agents present without a wake waiter: LOUD-presence"
    assert by_name.status == "pass"
    assert by_name.detail == (
        "every live agent that is wakeable has a -rx waiter (1 agent(s)); the hub reported no "
        "wake capabilities, so -presence names count as presence"
    )
    unnamed = check_deaf_agents(["holder", "S/a", "S/a-rx"], {"holder": "passive"})
    assert unnamed.detail == "every live agent that is wakeable has a -rx waiter (1 agent(s))"


def test_doctor_keeps_the_who_answer_s_capabilities() -> None:
    with_capabilities = doctor_roster_from_snapshot(
        {"online_agents": ["P-presence"], "wake_capabilities": {"P-presence": "passive"}}
    )
    without = doctor_roster_from_snapshot({"online_agents": ["P-presence"]})

    assert with_capabilities.wake_capabilities == {"P-presence": "passive"}
    assert without.wake_capabilities is None


async def test_doctor_passes_the_who_answer_s_capabilities_to_its_deaf_agents_check() -> None:
    async def roster(**_: Any) -> DoctorRoster:
        return DoctorRoster(
            agents=("HOLDER", "LOUD-presence"),
            mailbox_pending=None,
            wake_capabilities={"HOLDER": "passive", "LOUD-presence": "direct"},
        )

    _, _, diagnoses = await cli_doctor._diagnose(
        feed_tail_reader=lambda _env: [],
        cursor_names_reader=lambda _env: [],
        uri="ws://127.0.0.1:1",
        project="demorepo",
        agent_id=None,
        token=None,
        roster_probe=roster,
    )

    (deaf,) = [diagnosis for diagnosis in diagnoses if diagnosis.check == "deaf-agents"]
    assert deaf.detail == "agents present without a wake waiter: LOUD-presence"


async def test_a_real_holder_started_by_listen_is_presence_on_who_and_the_dashboard(
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = SynapseHub(
        hub_id="presence-holders", warn_stale_recipients=True, recipient_liveness_window=1.0
    )
    async with running_hub(hub) as (_, uri):
        holder = asyncio.create_task(_listen(uri=uri, name="SEATS-presence", ready_timeout=2.0))
        quiet = await connect_agent("SEATS/quiet", uri)
        away = await connect_agent("SEATS/away-rx", uri, wake_capability="passive")
        older = await connect_agent("OLD-presence", uri)  # an older package's holder: direct
        await asyncio.sleep(1.5)  # past the hub's window, without a reaction
        snapshot = await fetch_dashboard_snapshot(
            uri=uri, name="SEATS/observer", token=None, ready_timeout=2.0, response_timeout=2.0
        )
        capsys.readouterr()
        assert await _who(uri=uri, name="SEATS/who-probe", ready_timeout=2.0) == 0
        roster = capsys.readouterr().out.splitlines()
        await close_agents(quiet, away, older)
        holder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await holder

    assert snapshot.wake_capabilities["SEATS-presence"] == "passive"
    document = snapshot.to_dict()
    seats = {row["identity"]: row for row in document["fleet"]["seats"]}
    assert "SEATS-presence" not in seats
    assert seats["SEATS/away"]["state"] == "listening"
    assert seats["SEATS/quiet"]["reaction"]["proven"] is False
    assert seats["OLD-presence"]["reaction"]["proven"] is False
    assert document["headline"]["presence_holders"] == 1
    assert document["headline"]["seats_deaf"] == sum(
        1 for row in seats.values() if row["reaction"]["proven"] is False
    )
    assert "Presence holders (1):" in roster
    assert "  SEATS-presence" in roster
    assert any(line.startswith("  SEATS/quiet") and "deaf" in line for line in roster)
    assert not any("SEATS-presence" in line and "deaf" in line for line in roster)
