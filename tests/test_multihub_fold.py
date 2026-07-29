# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-hub observed-state fold regressions

from __future__ import annotations

from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_fold import (
    BOARD_DISPLAY_WARNING,
    ObservedBoardConflict,
    ObservedBoardContender,
    ObservedBoardProvenance,
    ObservedClaim,
    ObservedState,
    asserting_owners,
    asserting_owners_from_events,
    fold_observed_state,
)
from synapse_channel.core.multihub_merge import HubEvent, merge_event_logs


def _ev(hub: str, seq: int, ts: float, kind: str, **payload: Any) -> HubEvent:
    return HubEvent(hub_id=hub, seq=seq, ts=ts, kind=kind, payload=payload)


def _project_of(agent: str) -> str:
    """Mimic the ownership gate's namespace derivation: the prefix before the first slash."""
    return agent.split("/", 1)[0] if "/" in agent else ""


def test_board_is_explicitly_non_authoritative_display_lww_with_provenance() -> None:
    events = [
        _ev("hub-a", 1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="first", status="open"),
        _ev("hub-b", 1, 2.0, EventKind.LEDGER_TASK, task_id="T", title="second", status="done"),
    ]
    state = fold_observed_state(events)
    assert state.board["T"]["title"] == "second"
    assert state.board["T"]["status"] == "done"
    provenance = state.board_provenance["T"]
    assert provenance.order_key == (2.0, "hub-b", 1)
    assert provenance.to_dict() == {
        "task_id": "T",
        "hub_id": "hub-b",
        "seq": 1,
        "timestamp": 2.0,
        "order_key": [2.0, "hub-b", 1],
        "display_winner": True,
        "authoritative": False,
        "causal": False,
    }


def test_clock_ahead_older_state_can_win_but_is_never_presented_as_causal_truth() -> None:
    real_newer = _ev("hub-a", 2, 10.0, EventKind.LEDGER_TASK, task_id="T", title="real newer")
    clock_ahead_older = _ev(
        "hub-b", 1, 1000.0, EventKind.LEDGER_TASK, task_id="T", title="older but ahead"
    )

    state = fold_observed_state(merge_event_logs([real_newer], [clock_ahead_older]))
    payload = state.to_dict()

    assert state.board["T"]["title"] == "older but ahead"
    assert payload["board_policy"] == {
        "mode": "display-only-lww",
        "order": ["timestamp", "hub_id", "seq"],
        "authoritative": False,
        "causal": False,
        "warning": BOARD_DISPLAY_WARNING,
    }
    assert payload["board_provenance"]["T"]["hub_id"] == "hub-b"
    assert payload["board_provenance"]["T"]["causal"] is False
    assert "NTP" in payload["board_policy"]["warning"]


def test_divergent_cross_hub_task_snapshots_emit_payload_free_conflict() -> None:
    events = [
        _ev("hub-b", 4, 2.0, EventKind.LEDGER_TASK, task_id="T", title="secret-second"),
        _ev("hub-a", 1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="secret-first"),
    ]

    state = fold_observed_state(events)
    conflict = state.board_conflicts["T"]
    payload = conflict.to_dict()

    assert conflict.task_id == "T"
    assert [item.hub_id for item in conflict.contenders] == ["hub-a", "hub-b"]
    assert payload["status"] == "unresolved"
    assert payload["kind"] == "cross-hub-snapshot-divergence"
    assert payload["causal"] is False
    assert payload["resolution"] == "operator-required"
    assert {item["record_fingerprint"] for item in payload["contenders"]} == {
        item.record_fingerprint for item in conflict.contenders
    }
    assert "title" not in str(payload)
    assert "secret" not in str(payload)


def test_equal_cross_hub_snapshots_ignore_mapping_insertion_order() -> None:
    east_task = {"task_id": "T", "title": "same", "status": "open"}
    west_task = {"status": "open", "title": "same", "task_id": "T"}
    events = [
        HubEvent(hub_id="hub-a", seq=1, ts=1.0, kind=EventKind.LEDGER_TASK, payload=east_task),
        HubEvent(hub_id="hub-b", seq=7, ts=2.0, kind=EventKind.LEDGER_TASK, payload=west_task),
    ]

    assert fold_observed_state(events).board_conflicts == {}


def test_conflict_compares_each_hubs_latest_sequence_not_display_timestamp() -> None:
    events = [
        _ev("hub-a", 2, 1.0, EventKind.LEDGER_TASK, task_id="T", title="converged"),
        _ev("hub-b", 2, 2.0, EventKind.LEDGER_TASK, task_id="T", title="converged"),
        _ev("hub-a", 1, 100.0, EventKind.LEDGER_TASK, task_id="T", title="old divergent"),
    ]

    state = fold_observed_state(events)

    assert state.board["T"]["title"] == "old divergent"
    assert state.board_conflicts == {}


def test_conflict_objects_round_trip_with_stable_order() -> None:
    contender_b = ObservedBoardContender("hub-b", 2, 3.0, "b" * 64)
    contender_a = ObservedBoardContender("hub-a", 7, 4.0, "a" * 64)
    state = ObservedState(
        board_conflicts={
            "T": ObservedBoardConflict("T", (contender_a, contender_b)),
        }
    )

    assert state.to_dict()["board_conflicts"] == {
        "T": {
            "task_id": "T",
            "status": "unresolved",
            "kind": "cross-hub-snapshot-divergence",
            "causal": False,
            "resolution": "operator-required",
            "contenders": [
                {
                    "hub_id": "hub-a",
                    "seq": 7,
                    "timestamp": 4.0,
                    "record_fingerprint": "a" * 64,
                },
                {
                    "hub_id": "hub-b",
                    "seq": 2,
                    "timestamp": 3.0,
                    "record_fingerprint": "b" * 64,
                },
            ],
        }
    }


def test_observed_state_preserves_legacy_positional_constructor_order() -> None:
    provenance = ObservedBoardProvenance("T", "hub-a", 1, 1.0)
    claim = ObservedClaim("T", "hub-a", {"task_id": "T", "owner": "alpha"})

    state = ObservedState(
        {"T": {"task_id": "T"}},
        {"T": provenance},
        ({"task_id": "T", "text": "progress"},),
        {"T": claim},
    )

    assert state.progress[0]["text"] == "progress"
    assert state.observed_claims["T"] == claim
    assert state.board_conflicts == {}


def test_progress_is_grow_only_in_order() -> None:
    events = [
        _ev("hub-a", 1, 1.0, EventKind.LEDGER_PROGRESS, task_id="T", text="one"),
        _ev("hub-b", 1, 2.0, EventKind.LEDGER_PROGRESS, task_id="T", text="two"),
    ]
    state = fold_observed_state(events)
    assert [note["text"] for note in state.progress] == ["one", "two"]


def test_claims_are_observed_and_tagged_with_their_hub_never_granted() -> None:
    events = [
        _ev("hub-a", 1, 1.0, EventKind.CLAIM, task_id="T", owner="alpha", paths=["src/x.py"]),
    ]
    state = fold_observed_state(events)
    observed = state.observed_claims["T"]
    assert observed.hub_id == "hub-a"
    assert observed.claim["owner"] == "alpha"
    # the view is explicitly advisory: it carries an observed marker, not a grant
    assert observed.to_dict()["observed"] is True


def test_task_update_refreshes_and_release_clears_the_observed_claim() -> None:
    events = [
        _ev("hub-a", 1, 1.0, EventKind.CLAIM, task_id="T", owner="alpha"),
        _ev("hub-a", 2, 2.0, EventKind.TASK_UPDATE, task_id="T", owner="alpha", status="active"),
    ]
    assert fold_observed_state(events).observed_claims["T"].claim["status"] == "active"
    released = [*events, _ev("hub-a", 3, 3.0, EventKind.RELEASE, task_id="T")]
    assert "T" not in fold_observed_state(released).observed_claims


def test_events_without_a_task_id_are_ignored() -> None:
    events = [
        _ev("hub-a", 1, 1.0, EventKind.LEDGER_TASK, title="no id"),
        _ev("hub-a", 2, 2.0, EventKind.CLAIM, owner="alpha"),
        _ev("hub-a", 3, 3.0, EventKind.CHAT, text="chatter"),  # an unrelated kind
    ]
    state = fold_observed_state(events)
    assert state.board == {}
    assert state.observed_claims == {}


def test_fold_consumes_the_merged_order_from_two_logs() -> None:
    # a's later-timestamped task declaration must win even though b's log is passed first
    a = [_ev("hub-a", 1, 5.0, EventKind.LEDGER_TASK, task_id="T", title="late")]
    b = [_ev("hub-b", 1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="early")]
    merged = merge_event_logs(b, a)
    state = fold_observed_state(merged)
    assert state.board["T"]["title"] == "late"


def test_empty_fold_and_to_dict_round_trip() -> None:
    empty = fold_observed_state([])
    assert empty == ObservedState()
    assert empty.to_dict() == {
        "board": {},
        "board_policy": {
            "mode": "display-only-lww",
            "order": ["timestamp", "hub_id", "seq"],
            "authoritative": False,
            "causal": False,
            "warning": BOARD_DISPLAY_WARNING,
        },
        "board_provenance": {},
        "board_conflicts": {},
        "progress": [],
        "observed_claims": {},
    }

    populated = fold_observed_state(
        [
            _ev("hub-a", 1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="t"),
            _ev("hub-a", 2, 2.0, EventKind.LEDGER_PROGRESS, task_id="T", text="p"),
            _ev("hub-a", 3, 3.0, EventKind.CLAIM, task_id="T", owner="alpha"),
        ]
    )
    payload = populated.to_dict()
    assert payload["board"]["T"]["title"] == "t"
    assert payload["board_provenance"]["T"]["order_key"] == [1.0, "hub-a", 1]
    assert payload["progress"][0]["text"] == "p"
    assert payload["observed_claims"]["T"]["observed"] is True


# --- asserting_owners: the runtime partition feed --------------------------------------------


def test_asserting_owners_maps_a_namespace_to_the_hub_that_claimed_in_it() -> None:
    events = [_ev("hub-a", 1, 1.0, EventKind.CLAIM, task_id="T", owner="OWNED/alice")]
    owners = asserting_owners(fold_observed_state(events), project_of=_project_of)
    assert owners == {"OWNED": frozenset({"hub-a"})}


def test_asserting_owners_collects_every_hub_seen_in_a_namespace() -> None:
    events = [
        _ev("hub-a", 1, 1.0, EventKind.CLAIM, task_id="T1", owner="OWNED/alice"),
        _ev("hub-b", 1, 2.0, EventKind.CLAIM, task_id="T2", owner="OWNED/bob"),
    ]
    owners = asserting_owners(fold_observed_state(events), project_of=_project_of)
    assert owners == {"OWNED": frozenset({"hub-a", "hub-b"})}


def test_asserting_owners_skips_a_claim_without_an_owner() -> None:
    events = [_ev("hub-a", 1, 1.0, EventKind.CLAIM, task_id="T")]
    owners = asserting_owners(fold_observed_state(events), project_of=_project_of)
    assert owners == {}


def test_asserting_owners_skips_an_owner_with_no_namespace() -> None:
    events = [_ev("hub-a", 1, 1.0, EventKind.CLAIM, task_id="T", owner="bare-agent")]
    owners = asserting_owners(fold_observed_state(events), project_of=_project_of)
    assert owners == {}


def test_asserting_owners_of_an_empty_view_is_empty() -> None:
    assert asserting_owners(ObservedState(), project_of=_project_of) == {}


def test_asserting_owners_from_events_keeps_equal_task_ids_isolated_per_hub() -> None:
    events = [
        _ev("hub-b", 1, 1.0, EventKind.CLAIM, task_id="T", owner="OWNED/alice"),
        _ev("hub-c", 1, 2.0, EventKind.CLAIM, task_id="T", owner="OWNED/bob"),
        _ev("hub-c", 2, 3.0, EventKind.RELEASE, task_id="T"),
    ]

    assert asserting_owners_from_events(events, project_of=_project_of) == {
        "OWNED": frozenset({"hub-b"})
    }


def test_asserting_owners_from_events_ignores_non_claim_lifecycle_noise() -> None:
    events = [
        _ev("hub-a", 1, 1.0, EventKind.CLAIM, owner="OWNED/no-task"),
        _ev("hub-a", 2, 2.0, EventKind.CHAT, task_id="T", owner="OWNED/alice"),
        _ev("hub-a", 3, 3.0, EventKind.CLAIM, task_id="T", owner="bare-agent"),
    ]

    assert asserting_owners_from_events(events, project_of=_project_of) == {}
