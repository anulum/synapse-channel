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


def test_first_request_is_recorded_pending() -> None:
    ledger = RelayApprovalLedger()

    outcome = ledger.submit(_request("alice"))

    assert outcome.status is ApprovalStatus.RECORDED
    assert outcome.requester == "alice"
    assert outcome.approver == ""
    assert ledger.pending_count == 1


def test_second_distinct_operator_approves() -> None:
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"))

    outcome = ledger.submit(_request("bob"))

    assert outcome.status is ApprovalStatus.APPROVED
    assert outcome.requester == "alice"
    assert outcome.approver == "bob"
    assert ledger.pending_count == 0  # the pending record is cleared on approval


def test_same_operator_repeat_stays_pending() -> None:
    # An operator cannot approve their own request; a repeat leaves it awaiting a different one.
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"))

    outcome = ledger.submit(_request("alice"))

    assert outcome.status is ApprovalStatus.AWAITING
    assert outcome.requester == "alice"
    assert outcome.approver == ""
    assert ledger.pending_count == 1


def test_different_task_is_a_separate_quorum() -> None:
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice", task="MY-NS/build"))

    # A second operator on a *different* task does not approve the first task's request.
    outcome = ledger.submit(_request("bob", task="MY-NS/lint"))

    assert outcome.status is ApprovalStatus.RECORDED
    assert ledger.pending_count == 2


def test_approval_key_strips_the_task_id() -> None:
    # The key matches the stripped task id the apply path uses, so surrounding space cannot
    # split one action into two separate quorums.
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice", task="  MY-NS/build  "))

    outcome = ledger.submit(_request("bob", task="MY-NS/build"))

    assert outcome.status is ApprovalStatus.APPROVED
    assert outcome.approver == "bob"


def test_key_from_request_uses_action_namespace_and_stripped_task() -> None:
    key = RelayApprovalKey.from_request(_request("alice", task=" MY-NS/build "))

    assert key == RelayApprovalKey(action=RELAY_RELEASE, namespace="MY-NS", task_id="MY-NS/build")


def test_full_cycle_returns_to_empty_and_can_repeat() -> None:
    # After an approval the same target can be requested again (a fresh quorum), not auto-approved.
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"))
    ledger.submit(_request("bob"))  # approved, cleared

    again = ledger.submit(_request("carol"))

    assert again.status is ApprovalStatus.RECORDED
    assert ledger.pending_count == 1


def test_withdraw_drops_a_pending_record() -> None:
    ledger = RelayApprovalLedger()
    ledger.submit(_request("alice"))
    key = RelayApprovalKey.from_request(_request("alice"))

    assert ledger.withdraw(key) is True
    assert ledger.pending_count == 0
    # A subsequent operator now starts a fresh quorum rather than approving the withdrawn one.
    assert ledger.submit(_request("bob")).status is ApprovalStatus.RECORDED


def test_withdraw_of_unknown_key_is_false() -> None:
    ledger = RelayApprovalLedger()

    assert ledger.withdraw(RelayApprovalKey(RELAY_RELEASE, "MY-NS", "absent")) is False


def test_capacity_evicts_the_oldest_pending() -> None:
    ledger = RelayApprovalLedger(capacity=2)
    ledger.submit(_request("alice", task="MY-NS/a"))
    ledger.submit(_request("alice", task="MY-NS/b"))

    ledger.submit(_request("alice", task="MY-NS/c"))  # at capacity → evicts the oldest (task a)

    assert ledger.pending_count == 2
    # The evicted request is gone: a second operator on task a starts a fresh quorum (which in
    # turn evicts the now-oldest pending, task b).
    assert ledger.submit(_request("bob", task="MY-NS/a")).status is ApprovalStatus.RECORDED
    # Task c was never evicted, so a second, different operator still approves it.
    assert ledger.submit(_request("bob", task="MY-NS/c")).status is ApprovalStatus.APPROVED


def test_capacity_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RelayApprovalLedger(capacity=0)
