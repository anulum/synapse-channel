# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what the hub knows about each seat, and nothing more
"""Seats are listening, active and not listening, or silent; never offline."""

from __future__ import annotations

import asyncio
from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.agent_liveness import deaf_mark, format_reaction_age
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import DashboardSnapshot, fetch_dashboard_snapshot
from synapse_channel.dashboard_bounds import bound_snapshot
from synapse_channel.dashboard_fleet import render_fleet_visibility_html
from synapse_channel.dashboard_live_transport import snapshot_fingerprint
from synapse_channel.dashboard_seats import SEAT_ACTIVE_WINDOW_SECONDS, build_seats

NOW = 1_783_000_000.0
NO_JUDGEMENT = {"proven": None, "live_waiter": None, "last_reaction_age": None, "mark": ""}


def _states(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {row["identity"]: row["state"] for row in rows}


def test_a_seat_holding_a_claim_with_nobody_listening_is_active_not_listening() -> None:
    """The plan's "Done when": never called offline."""
    rows = build_seats(
        [],
        {
            "active_claims": [
                {"owner": "S/claimer", "claimed_at": NOW - 86_400, "lease_expires_at": NOW + 60}
            ]
        },
        {},
        now=NOW,
    )

    assert rows == [
        {
            "identity": "S/claimer",
            "listening": False,
            "active": True,
            "evidence": "claim",
            "evidence_at": NOW - 86_400,  # a held claim counts at any age
            "state": "active, not listening",
            "reaction": NO_JUDGEMENT,
        }
    ]


def test_who_is_listening() -> None:
    rows = build_seats(
        ["S/library", "S/terminal-rx", "S/pane-pane-rx", "S/both", "S/both-rx"], {}, {}, now=NOW
    )

    assert _states(rows) == {
        "S/library": "listening",  # connected itself, no sidecar: not "waker missing"
        "S/terminal": "listening",  # its waiter receives for it
        "S/pane": "listening",  # its pane bridge receives for it
        "S/both": "listening",
    }
    assert all(row["active"] is False and row["evidence"] is None for row in rows)


def test_the_window_edge_is_exact_for_notes_and_claim_stamps() -> None:
    edge = NOW - SEAT_ACTIVE_WINDOW_SECONDS
    rows = build_seats(
        [],
        {
            "active_claims": [
                {"owner": "S/fresh-stamp", "claimed_at": edge, "lease_expires_at": NOW},
                {"owner": "S/old-stamp", "claimed_at": edge - 0.5, "lease_expires_at": NOW},
                {"owner": "S/no-stamp", "lease_expires_at": NOW + 1},
                {"owner": "", "claimed_at": NOW, "lease_expires_at": NOW + 1},
            ]
        },
        {
            "progress": [
                {"author": "S/on-the-edge", "posted_at": edge},
                {"author": "S/just-past", "posted_at": edge - 0.001},
                {"author": "S/no-time", "posted_at": "late"},
                None,
            ]
        },
        now=NOW,
    )
    seats = {row["identity"]: row for row in rows}

    # A note past the window, or without a time, names no seat: the board keeps
    # every author it ever had, and an old approval note is not a seat.
    assert _states(rows) == {
        "S/fresh-stamp": "active, not listening",
        "S/old-stamp": "silent",  # its lease ended at NOW and its stamp is past the window
        "S/no-stamp": "active, not listening",
        "S/on-the-edge": "active, not listening",
    }
    assert seats["S/no-stamp"]["evidence_at"] is None
    assert seats["S/on-the-edge"]["evidence"] == "note"


def test_a_time_that_is_not_a_finite_number_is_no_evidence() -> None:
    """``True`` is not the epoch's first second, and an infinite stamp is never recent."""
    rows = build_seats(
        [],
        {"active_claims": [{"owner": "S/endless", "lease_expires_at": float("inf")}]},
        {
            "progress": [
                {"author": "S/flag", "posted_at": True},
                {"author": "S/future", "posted_at": float("inf")},
            ]
        },
        now=1.0,
    )

    assert rows == [
        {
            "identity": "S/endless",
            "listening": False,
            "active": False,
            "evidence": None,
            "evidence_at": None,
            "state": "silent",
            "reaction": NO_JUDGEMENT,
        }
    ]


def test_the_newest_evidence_names_the_seat_s_work() -> None:
    rows = build_seats(
        ["S/worker"],
        {"active_claims": [{"owner": "S/worker", "lease_expires_at": NOW + 1}]},
        {
            "progress": [
                {"author": "S/worker", "posted_at": NOW - 30},
                {"author": "S/worker", "posted_at": NOW - 10},
            ]
        },
        now=NOW,
    )

    assert rows[0]["state"] == "listening" and rows[0]["active"] is True
    assert (rows[0]["evidence"], rows[0]["evidence_at"]) == ("note", NOW - 10)


def test_a_stamped_claim_outranks_an_unstamped_one_in_either_order() -> None:
    held = NOW + 60
    rows = build_seats(
        [],
        {
            "active_claims": [
                {"owner": "S/first-stamped", "claimed_at": NOW - 50, "lease_expires_at": held},
                {"owner": "S/first-stamped", "lease_expires_at": held},
                {"owner": "S/first-bare", "lease_expires_at": held},
                {"owner": "S/first-bare", "claimed_at": NOW - 40, "lease_expires_at": held},
            ]
        },
        {},
        now=NOW,
    )

    assert {row["identity"]: row["evidence_at"] for row in rows} == {
        "S/first-bare": NOW - 40,
        "S/first-stamped": NOW - 50,
    }


def _snapshot(hub_time: float) -> DashboardSnapshot:
    # The claim's lease ran out at NOW; only its stamp keeps the claimer active.
    claim = {"owner": "S/claimer", "claimed_at": NOW, "lease_expires_at": NOW}
    return DashboardSnapshot(
        online_agents=["S/observer", "S/reader-rx"],
        state={"active_claims": [claim], "generated_at": hub_time},
        board={"tasks": [], "progress": [{"author": "S/writer", "posted_at": NOW}]},
        manifest=[],
    )


def test_a_seat_state_change_moves_the_weak_tag_once() -> None:
    """A1's unchanged rule still holds with the projection inside the document."""
    window = SEAT_ACTIVE_WINDOW_SECONDS
    tags = [
        snapshot_fingerprint(bound_snapshot(_snapshot(NOW + offset).to_dict()))
        for offset in (window - 2, window - 1, window + 1, window + 2)
    ]
    before, after = (
        _states(_snapshot(NOW + offset).to_dict()["fleet"]["seats"])
        for offset in (window - 1, window + 1)
    )

    assert before == {
        "S/claimer": "active, not listening",
        "S/observer": "listening",
        "S/reader": "listening",  # through its waiter
        "S/writer": "active, not listening",
    }
    # The claimer falls silent; the writer's note is past the window and names no seat.
    assert after == {"S/claimer": "silent", "S/observer": "listening", "S/reader": "listening"}
    assert tags[0] == tags[1]  # nothing changed but the hub's clock
    assert tags[1] != tags[2]  # the seats changed
    assert tags[2] == tags[3]
    counts = ("seats_listening", "seats_active_not_listening", "seats_silent")
    headlines = [
        _snapshot(NOW + offset).to_dict()["headline"] for offset in (window - 1, window + 1)
    ]
    assert [tuple(headline[key] for key in counts) for headline in headlines] == [
        (2, 2, 0),
        (2, 0, 1),
    ]


async def test_a_real_seat_that_claimed_and_left_reads_active_not_listening() -> None:
    async with running_hub() as (_, uri):
        claimer = await connect_agent("SEATS/claimer", uri)
        await claimer.agent.claim("SEAT-TASK", paths=["src/seat.py"], ttl_seconds=600.0)
        await claimer.recorder.wait_for(lambda message: message.get("type") == "claim_granted")
        await close_agents(claimer)
        snapshot = await fetch_dashboard_snapshot(
            uri=uri, name="SEATS/observer", token=None, ready_timeout=2.0, response_timeout=2.0
        )

    seats = {row["identity"]: row for row in snapshot.to_dict()["fleet"]["seats"]}
    assert seats["SEATS/claimer"]["state"] == "active, not listening"
    assert seats["SEATS/claimer"]["evidence"] == "claim"
    assert seats["SEATS/observer"]["state"] == "listening"


def test_the_hub_s_word_for_a_seat_that_does_not_react() -> None:
    """The dashboard says "deaf" exactly as ``synapse who`` does."""
    assert [format_reaction_age(age) for age in (0, 59.9, 60, 3599, 3600)] == [
        "~0s",
        "~59s",
        "~1m",
        "~59m",
        "~1h",
    ]
    assert deaf_mark({"proven_live": True, "last_reaction_age": 900}) == ""
    assert deaf_mark({"proven_live": False, "last_reaction_age": 240}) == "deaf ~4m"
    assert deaf_mark({"proven_live": False, "last_reaction_age": None}) == "deaf — no reaction seen"


def test_each_seat_carries_the_hub_s_judgement_and_none_where_it_made_none() -> None:
    liveness = {
        "S/quiet": {"proven_live": False, "has_live_waiter": False, "last_reaction_age": 240.5},
        "S/never": {"proven_live": False, "has_live_waiter": False, "last_reaction_age": None},
        "S/heard": {"proven_live": True, "has_live_waiter": True, "last_reaction_age": 3.0},
        "S/talker": {"proven_live": True, "has_live_waiter": False, "last_reaction_age": 2.0},
        "S/odd": {"proven_live": "yes", "has_live_waiter": 1, "last_reaction_age": float("nan")},
        "S/junk": "not a record",
    }
    online = ["S/quiet", "S/never", "S/heard", "S/talker", "S/odd", "S/junk", "S/unjudged"]
    rows = {
        row["identity"]: row["reaction"]
        for row in build_seats(online, {}, {}, now=NOW, liveness=liveness)
    }

    assert rows == {
        "S/quiet": {
            "proven": False,
            "live_waiter": False,
            "last_reaction_age": 240.5,
            "mark": "deaf ~4m",
        },
        "S/never": {
            "proven": False,
            "live_waiter": False,
            "last_reaction_age": None,
            "mark": "deaf — no reaction seen",
        },
        "S/heard": {"proven": True, "live_waiter": True, "last_reaction_age": 3.0, "mark": ""},
        # Proven by its own reaction, without a waiter.
        "S/talker": {"proven": True, "live_waiter": False, "last_reaction_age": 2.0, "mark": ""},
        "S/odd": NO_JUDGEMENT,
        "S/junk": NO_JUDGEMENT,
        "S/unjudged": NO_JUDGEMENT,  # the hub judged it not: nothing is invented
    }
    # A hub that tracks no reactions sends none, and every seat has no judgement.
    assert all(row["reaction"] == NO_JUDGEMENT for row in build_seats(online, {}, {}, now=NOW))


def _judged(age: float, *, proven: bool) -> DashboardSnapshot:
    return DashboardSnapshot(
        online_agents=["S/quiet"],
        state={"active_claims": [], "generated_at": NOW},
        board={"tasks": []},
        manifest=[],
        agent_liveness={
            "S/quiet": {"proven_live": proven, "has_live_waiter": False, "last_reaction_age": age}
        },
    )


def test_only_a_proven_flip_moves_the_weak_tag_and_the_document_keeps_no_raw_ages() -> None:
    tags = [
        snapshot_fingerprint(bound_snapshot(_judged(age, proven=proven).to_dict()))
        for age, proven in ((100.0, False), (4000.0, False), (4001.0, True), (9000.0, True))
    ]
    document = _judged(240.0, proven=False).to_dict()

    assert tags[0] == tags[1]  # only the age and its mark grew
    assert tags[1] != tags[2]  # the hub now proves the seat live
    assert tags[2] == tags[3]
    assert "agent_liveness" not in document
    assert document["headline"]["seats_deaf"] == 1
    assert _judged(1.0, proven=True).to_dict()["headline"]["seats_deaf"] == 0


def test_the_classic_page_shows_the_hub_s_mark() -> None:
    page = render_fleet_visibility_html(_judged(7300.0, proven=False))

    assert "<li>S/quiet: listening · deaf ~2h</li>" in page


async def test_a_real_hub_s_judgement_reaches_each_seat() -> None:
    hub = SynapseHub(
        hub_id="seats-reaction", warn_stale_recipients=True, recipient_liveness_window=1.0
    )
    async with running_hub(hub) as (_, uri):
        quiet = await connect_agent("SEATS/quiet", uri)
        await asyncio.sleep(1.5)  # past the hub's window, without a reaction
        deaf = await fetch_dashboard_snapshot(
            uri=uri, name="SEATS/observer", token=None, ready_timeout=2.0, response_timeout=2.0
        )
        waiter = await connect_agent("SEATS/quiet-rx", uri)
        armed = await fetch_dashboard_snapshot(
            uri=uri, name="SEATS/observer", token=None, ready_timeout=2.0, response_timeout=2.0
        )
        await close_agents(quiet, waiter)

    before = {row["identity"]: row for row in deaf.to_dict()["fleet"]["seats"]}
    after = {row["identity"]: row for row in armed.to_dict()["fleet"]["seats"]}
    assert before["SEATS/quiet"]["state"] == "listening"
    assert before["SEATS/quiet"]["reaction"]["proven"] is False
    assert before["SEATS/quiet"]["reaction"]["mark"].startswith("deaf ~")
    assert before["SEATS/observer"]["reaction"]["proven"] is True  # it asked: a reaction
    assert after["SEATS/quiet"]["reaction"]["proven"] is True
    assert after["SEATS/quiet"]["reaction"]["live_waiter"] is True
    assert (
        deaf.to_dict()["headline"]["seats_deaf"],
        armed.to_dict()["headline"]["seats_deaf"],
    ) == (1, 0)


def test_a_seat_row_without_a_reaction_is_fingerprinted_whole() -> None:
    """A hand-written document may carry seat rows the projection never makes."""
    odd = {"fleet": {"seats": ["odd", {"identity": "a/one"}]}}
    other = {"fleet": {"seats": ["odd", {"identity": "a/two"}]}}

    assert snapshot_fingerprint(odd) == snapshot_fingerprint(odd)
    assert snapshot_fingerprint(odd) != snapshot_fingerprint(other)


def test_a_changed_judgement_moves_the_weak_tag_even_when_no_count_moves() -> None:
    """Proven, then no judgement: the same waiter flag and age, and no deaf seat either way."""

    def tag(info: dict[str, Any]) -> str:
        snapshot = DashboardSnapshot(
            online_agents=["S/seat"],
            state={"active_claims": [], "generated_at": NOW},
            board={"tasks": []},
            manifest=[],
            agent_liveness={"S/seat": info},
        )
        return snapshot_fingerprint(bound_snapshot(snapshot.to_dict()))

    proven = {"proven_live": True, "has_live_waiter": False, "last_reaction_age": 5.0}
    unjudged = {"has_live_waiter": False, "last_reaction_age": 5.0}

    assert tag(proven) != tag(unjudged)
