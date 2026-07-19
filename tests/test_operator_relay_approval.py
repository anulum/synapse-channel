# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — two-person operator-relay approval ledger regressions

from __future__ import annotations

import pytest

from synapse_channel.core.operator_relay import RELAY_RELEASE
from synapse_channel.core.operator_relay_approval import (
    ApprovalStatus,
    RelayApprovalKey,
    RelayApprovalLedger,
)
from synapse_channel.core.operator_relay_wire import RelayActionRequest


def _request(
    operator: str, *, task: str = "MY-NS/build", namespace: str = "MY-NS"
) -> RelayActionRequest:
    return RelayActionRequest(
        action=RELAY_RELEASE,
        namespace=namespace,
        task_id=task,
        operator=operator,
        origin_hub_id="syn-origin",
    )


def _principal(name: str) -> str:
    return f"federation-peer:{name}"


def test_first_request_is_recorded_pending() -> None:
    ledger = RelayApprovalLedger()

    outcome = ledger.submit(_request("alice"), principal=_principal("peer-a"))

    assert outcome.status is ApprovalStatus.RECORDED
    assert outcome.requester == "alice"
    assert outcome.approver == ""
    assert outcome.requester_principal == _principal("peer-a")
    assert outcome.approver_principal == ""
    assert ledger.pending_count == 1


def test_second_distinct_operator_approves() -> None:
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"), principal=_principal("peer-a"))

    outcome = ledger.submit(_request("bob"), principal=_principal("peer-b"))

    assert outcome.status is ApprovalStatus.APPROVED
    assert outcome.requester == "alice"
    assert outcome.approver == "bob"
    assert outcome.requester_principal == _principal("peer-a")
    assert outcome.approver_principal == _principal("peer-b")
    assert ledger.pending_count == 0  # the pending record is cleared on approval


def test_same_verified_principal_alias_stays_pending() -> None:
    # Human-readable aliases do not create a second identity boundary.
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"), principal=_principal("peer-a"))

    outcome = ledger.submit(_request("bob"), principal=_principal("peer-a"))

    assert outcome.status is ApprovalStatus.AWAITING
    assert outcome.requester == "alice"
    assert outcome.approver == ""
    assert outcome.requester_principal == _principal("peer-a")
    assert outcome.approver_principal == ""
    assert ledger.pending_count == 1


def test_different_task_is_a_separate_quorum() -> None:
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice", task="MY-NS/build"), principal=_principal("peer-a"))

    # A second operator on a *different* task does not approve the first task's request.
    outcome = ledger.submit(_request("bob", task="MY-NS/lint"), principal=_principal("peer-b"))

    assert outcome.status is ApprovalStatus.RECORDED
    assert ledger.pending_count == 2


def test_approval_key_strips_the_task_id() -> None:
    # The key matches the stripped task id the apply path uses, so surrounding space cannot
    # split one action into two separate quorums.
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice", task="  MY-NS/build  "), principal=_principal("peer-a"))

    outcome = ledger.submit(_request("bob", task="MY-NS/build"), principal=_principal("peer-b"))

    assert outcome.status is ApprovalStatus.APPROVED
    assert outcome.approver == "bob"


def test_key_from_request_uses_action_namespace_and_stripped_task() -> None:
    key = RelayApprovalKey.from_request(_request("alice", task=" MY-NS/build "))

    assert key == RelayApprovalKey(action=RELAY_RELEASE, namespace="MY-NS", task_id="MY-NS/build")


def test_full_cycle_returns_to_empty_and_can_repeat() -> None:
    # After an approval the same target can be requested again (a fresh quorum), not auto-approved.
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"), principal=_principal("peer-a"))
    ledger.submit(_request("bob"), principal=_principal("peer-b"))  # approved, cleared

    again = ledger.submit(_request("carol"), principal=_principal("peer-c"))

    assert again.status is ApprovalStatus.RECORDED
    assert ledger.pending_count == 1


def test_withdraw_drops_a_pending_record() -> None:
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"), principal=_principal("peer-a"))
    key = RelayApprovalKey.from_request(_request("alice"))

    assert ledger.withdraw(key) is True
    assert ledger.pending_count == 0
    # A subsequent operator now starts a fresh quorum rather than approving the withdrawn one.
    assert (
        ledger.submit(_request("bob"), principal=_principal("peer-b")).status
        is ApprovalStatus.RECORDED
    )


def test_withdraw_of_unknown_key_is_false() -> None:
    ledger = RelayApprovalLedger()

    assert ledger.withdraw(RelayApprovalKey(RELAY_RELEASE, "MY-NS", "absent")) is False


def test_capacity_evicts_the_oldest_pending() -> None:
    ledger = RelayApprovalLedger(capacity=2)
    ledger.submit(_request("alice", task="MY-NS/a"), principal=_principal("peer-a"))
    ledger.submit(_request("alice", task="MY-NS/b"), principal=_principal("peer-a"))

    ledger.submit(
        _request("alice", task="MY-NS/c"), principal=_principal("peer-a")
    )  # at capacity → evicts the oldest (task a)

    assert ledger.pending_count == 2
    # The evicted request is gone: a second operator on task a starts a fresh quorum (which in
    # turn evicts the now-oldest pending, task b).
    assert (
        ledger.submit(_request("bob", task="MY-NS/a"), principal=_principal("peer-b")).status
        is ApprovalStatus.RECORDED
    )
    # Task c was never evicted, so a second, different operator still approves it.
    assert (
        ledger.submit(_request("bob", task="MY-NS/c"), principal=_principal("peer-b")).status
        is ApprovalStatus.APPROVED
    )


def test_capacity_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RelayApprovalLedger(capacity=0)


def test_pending_lists_records_oldest_first() -> None:
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice", task="MY-NS/a"), principal=_principal("peer-a"))
    ledger.submit(_request("carol", task="MY-NS/b"), principal=_principal("peer-c"))

    assert ledger.pending() == [
        {"action": RELAY_RELEASE, "namespace": "MY-NS", "task_id": "MY-NS/a", "requester": "alice"},
        {"action": RELAY_RELEASE, "namespace": "MY-NS", "task_id": "MY-NS/b", "requester": "carol"},
    ]


def test_pending_is_empty_without_any_awaiting() -> None:
    assert RelayApprovalLedger().pending() == []


def test_pending_records_the_stripped_task_id() -> None:
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice", task="  MY-NS/build  "), principal=_principal("peer-a"))

    assert ledger.pending() == [
        {
            "action": RELAY_RELEASE,
            "namespace": "MY-NS",
            "task_id": "MY-NS/build",
            "requester": "alice",
        }
    ]


def test_pending_drops_an_approved_record() -> None:
    # A completed quorum clears its pending record, so it no longer appears in the pending view.
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"), principal=_principal("peer-a"))
    ledger.submit(
        _request("bob"), principal=_principal("peer-b")
    )  # a second verified principal approves → cleared

    assert ledger.pending() == []


def test_blank_verified_principal_is_rejected_fail_closed() -> None:
    ledger = RelayApprovalLedger()

    with pytest.raises(ValueError, match="verified relay principal is required"):
        ledger.submit(_request("alice"), principal="")
