# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the live approval-request window: limits, reservations and recount
"""The rate window reserves before append, gives places back, and recounts from the journal."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from synapse_channel.core.approval_load import (
    LIMIT_APPROVER,
    LIMIT_ROLE,
    RECOUNT_SLACK_SECONDS,
    ApprovalLoad,
)
from synapse_channel.core.approval_load_policy import parse_approval_load_policy
from synapse_channel.core.approvals import ApprovalAddressing
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.role_grants import RoleGrants


def _policy(**sections: object) -> Any:
    return parse_approval_load_policy({"schema": "synapse-approval-load-policy.v1", **sections})


def _rule(
    rate: int = 2, window: float = 100.0, review: float = 10.0, batch: int = 5
) -> dict[str, Any]:
    return {
        "request_rate": rate,
        "window_seconds": window,
        "min_review_seconds": review,
        "max_batch": batch,
    }


class Clock:
    """A settable wall clock."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


GRANTS = RoleGrants({"P/person": frozenset({"P/alice", "P/bob"}), "P/lead": frozenset({"P/alice"})})


def test_an_identity_counts_against_its_own_rule_and_every_ruled_role_it_holds() -> None:
    load = ApprovalLoad(
        _policy(
            roles={"P/person": _rule(review=10), "P/lead": _rule(batch=3), "P/other": _rule()},
            approvers={"P/alice": _rule(review=30)},
        )
    )
    matched = load.matched_rules(ApprovalAddressing(approver="P/alice"), GRANTS)
    assert [key for key, _ in matched] == [
        (LIMIT_APPROVER, "P/alice"),
        (LIMIT_ROLE, "P/lead"),
        (LIMIT_ROLE, "P/person"),
    ]
    assert load.matched_rules(ApprovalAddressing(approver="P/alice"), None) == (
        ((LIMIT_APPROVER, "P/alice"), load.policy.approvers["P/alice"]),
    )
    both = load.matched_rules(
        ApprovalAddressing(approver="P/bob", approver_role="P/person"), GRANTS
    )
    assert [key for key, _ in both] == [(LIMIT_ROLE, "P/person")]
    assert load.matched_rules(ApprovalAddressing(approver_role="P/none"), GRANTS) == ()


def test_the_rate_admits_up_to_its_limit_then_names_the_full_limit() -> None:
    clock = Clock(1000.0)
    load = ApprovalLoad(_policy(roles={"P/person": _rule(rate=2, window=100)}), clock=clock)
    matched = load.matched_rules(ApprovalAddressing(approver_role="P/person"), GRANTS)
    first, none = load.reserve(matched)
    assert first is not None and none is None
    clock.now += 1
    second, _ = load.reserve(matched)
    assert second is not None
    third, reason = load.reserve(matched)
    assert third is None
    assert reason == "approval_rate_exceeded: role P/person allows 2 requests per 100 s"
    clock.now = first.at + 100
    later, reason = load.reserve(matched)
    assert later is not None and reason is None


def test_spreading_requests_across_a_roles_members_does_not_evade_the_role() -> None:
    load = ApprovalLoad(_policy(roles={"P/person": _rule(rate=2)}))
    for member in ("P/alice", "P/bob"):
        reservation, _ = load.reserve(
            load.matched_rules(ApprovalAddressing(approver=member), GRANTS)
        )
        assert reservation is not None
    _, reason = load.reserve(load.matched_rules(ApprovalAddressing(approver="P/alice"), GRANTS))
    assert reason is not None and "role P/person" in reason


def test_a_released_reservation_gives_its_place_back_everywhere() -> None:
    load = ApprovalLoad(
        _policy(roles={"P/person": _rule(rate=1)}, approvers={"P/alice": _rule(rate=1)})
    )
    matched = load.matched_rules(ApprovalAddressing(approver="P/alice"), GRANTS)
    reservation, _ = load.reserve(matched)
    assert reservation is not None
    assert load.reserve(matched)[0] is None
    load.release(reservation)
    assert load.reserve(matched)[0] is not None
    other = ApprovalLoad(_policy(roles={"P/person": _rule()}))
    other.release(reservation)


def test_a_reservation_takes_the_strictest_thresholds_and_no_rule_reserves_nothing() -> None:
    load = ApprovalLoad(
        _policy(
            roles={"P/person": _rule(review=10, batch=9)},
            approvers={"P/alice": _rule(review=40, batch=4)},
        )
    )
    reservation, _ = load.reserve(
        load.matched_rules(ApprovalAddressing(approver="P/alice"), GRANTS)
    )
    assert reservation is not None
    assert (reservation.min_review_seconds, reservation.max_batch) == (40.0, 4)
    assert load.reserve(()) == (None, None)


def _admitted(store: EventStore, ts: float, limits: object) -> None:
    store.append(EventKind.APPROVAL_LOAD_ADMITTED, {"limits": limits}, ts=ts, durable=True)


def test_start_journals_the_policy_once_and_recounts_the_window(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        now = 10_000.0
        _admitted(store, now - 500, [[LIMIT_ROLE, "P/person"]])
        _admitted(store, now - 50, [[LIMIT_ROLE, "P/person"]])
        _admitted(store, now - 40, [[LIMIT_ROLE, "P/gone"], "bad", [LIMIT_ROLE]])
        _admitted(store, now - 30, "not a list")
        load = ApprovalLoad(
            _policy(roles={"P/person": _rule(rate=2, window=100)}), clock=Clock(now)
        )
        load.start(store)
        load.start(store)
        policies = store.read_since(0, kinds=(EventKind.APPROVAL_LOAD_POLICY,))
        assert len(policies) == 1
        assert policies[0].payload["policy_sha256"] == load.policy.sha256
        assert policies[0].payload["policy"] == load.policy.to_json_obj()
        matched = load.matched_rules(ApprovalAddressing(approver_role="P/person"), GRANTS)
        first, _ = load.reserve(matched)
        assert first is not None
        assert load.reserve(matched)[1] is not None
    finally:
        store.close()


def test_start_on_an_empty_journal_recounts_nothing(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        load = ApprovalLoad(_policy(roles={"P/person": _rule(rate=1)}), clock=Clock(1e12))
        load.start(store)
        assert load.reserve(load.matched_rules(ApprovalAddressing(approver_role="P/person"), None))[
            0
        ]
    finally:
        store.close()


class CountingStore(EventStore):
    """A real event store, opened on a real journal, that counts the rows each read returns."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.rows = 0
        self.scan_floors: list[int] = []

    def read_since(
        self, after_seq: int, *, kinds: Iterable[str] | None = None, limit: int | None = None
    ) -> list[StoredEvent]:
        rows = super().read_since(after_seq, kinds=kinds, limit=limit)
        self.rows += len(rows)
        return rows

    def iter_events(
        self,
        *,
        through_seq: int | None = None,
        kinds: Iterable[str] | None = None,
        after_seq: int | None = None,
        apply_row_quarantine: bool = True,
    ) -> Iterator[StoredEvent]:
        self.scan_floors.append(after_seq or 0)
        for row in super().iter_events(
            through_seq=through_seq,
            kinds=kinds,
            after_seq=after_seq,
            apply_row_quarantine=apply_row_quarantine,
        ):
            self.rows += 1
            yield row


def test_the_recount_reads_only_rows_inside_the_window(tmp_path: Path) -> None:
    now = 1_000_000.0
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append_batch(
            [("note", {"n": index}) for index in range(2000)], ts=now - 100_000, durable=True
        )
        for offset in (90, 60, 30):
            _admitted(store, now - offset, [[LIMIT_ROLE, "P/person"]])
    finally:
        store.close()
    counting = CountingStore(tmp_path / "hub.db")
    try:
        load = ApprovalLoad(
            _policy(roles={"P/person": _rule(rate=3, window=100)}), clock=Clock(now)
        )
        load.start(counting)
        # About log2(2003) single-row probes, then the three rows of the window. The
        # window's read starts after the 2000 old rows, so SQLite never scans them.
        assert counting.rows <= 3 + 12
        assert counting.scan_floors == [2000]
        assert load.reserve(load.matched_rules(ApprovalAddressing(approver_role="P/person"), None))[
            1
        ]
    finally:
        counting.close()


def test_the_recount_slack_reaches_back_before_the_window(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        now = 50_000.0
        _admitted(store, now - 100 - RECOUNT_SLACK_SECONDS + 1, [[LIMIT_ROLE, "P/person"]])
        _admitted(store, now - 10, [[LIMIT_ROLE, "P/person"]])
        load = ApprovalLoad(
            _policy(roles={"P/person": _rule(rate=1, window=100)}), clock=Clock(now)
        )
        load.start(store)
        # The old row is read under the slack but is outside the window, so one place is taken.
        assert load.reserve(load.matched_rules(ApprovalAddressing(approver_role="P/person"), None))[
            1
        ]
    finally:
        store.close()
