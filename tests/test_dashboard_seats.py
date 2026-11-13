# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what the hub knows about each seat, and nothing more
"""Seats are listening, active and not listening, or silent; never offline."""

from __future__ import annotations

from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.dashboard import DashboardSnapshot, fetch_dashboard_snapshot
from synapse_channel.dashboard_bounds import bound_snapshot
from synapse_channel.dashboard_live_transport import snapshot_fingerprint
from synapse_channel.dashboard_seats import SEAT_ACTIVE_WINDOW_SECONDS, build_seats

NOW = 1_783_000_000.0


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
