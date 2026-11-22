# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the approval note's optional addressee, batch and summary tokens
"""A request may name its addressee, batch and summary digest; readers stay compatible."""

from __future__ import annotations

import pytest

from synapse_channel.core.approvals import (
    ApprovalAddressing,
    ApprovalEvent,
    ApprovalReport,
    ApprovalStatus,
    approval_addressing_problem,
    approval_text_sha256,
    format_approval_note,
    parse_approval_addressing,
    parse_approval_note,
    render_human,
)

BATCH = "b" * 64
SUMMARY = "5" * 64


def test_a_request_carries_its_tokens_in_a_fixed_order_and_old_readers_ignore_them() -> None:
    text = format_approval_note(
        subject="S-1",
        state="requested",
        reason="please",
        summary=SUMMARY,
        batch=BATCH,
        approver_role="P/person",
        approver="P/alice",
    )
    assert text == (
        f"approval subject=S-1 state=requested approver=P/alice approver_role=P/person "
        f"batch={BATCH} summary={SUMMARY} :: please"
    )
    assert parse_approval_note(text) == {"subject": "S-1", "state": "requested", "reason": "please"}
    addressing = parse_approval_addressing(text)
    assert addressing == ApprovalAddressing("P/alice", "P/person", BATCH, SUMMARY)
    assert addressing.is_addressed
    assert approval_addressing_problem(addressing) is None


def test_a_note_without_tokens_is_unchanged() -> None:
    text = format_approval_note(subject="S-1", state="approved")
    assert text == "approval subject=S-1 state=approved"
    assert parse_approval_addressing(text) == ApprovalAddressing()
    assert not ApprovalAddressing().is_addressed
    assert ApprovalAddressing().present() == {}


def test_a_decision_may_not_carry_addressing_tokens() -> None:
    with pytest.raises(ValueError, match="belong only to a request"):
        format_approval_note(subject="S-1", state="approved", approver="P/alice")


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        ({"approver": "alice"}, "approver must be"),
        ({"approver": "/alice"}, "approver must be"),
        ({"approver_role": "P/"}, "approver_role must be"),
        ({"batch": "B" * 64}, "batch must be 64"),
        ({"summary": "5" * 63}, "summary must be 64"),
    ],
)
def test_a_malformed_token_is_refused_when_formatted(tokens: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        format_approval_note(subject="S-1", state="requested", **tokens)
    assert approval_addressing_problem(ApprovalAddressing(**tokens)) is not None


def test_parsing_is_lenient_and_the_last_repeat_wins() -> None:
    text = "approval subject=S-1 state=requested approver=bad approver=P/bob batch=xyz other=1"
    addressing = parse_approval_addressing(text)
    assert addressing == ApprovalAddressing(approver="P/bob", batch="xyz")
    assert approval_addressing_problem(addressing) == "batch must be 64 lower-case hex characters"
    assert parse_approval_addressing("note subject=S-1 approver=P/a") == ApprovalAddressing()
    assert parse_approval_addressing("") == ApprovalAddressing()
    assert parse_approval_addressing("approval subject=S :: approver=P/a") == ApprovalAddressing()


def test_the_text_digest_survives_a_hostile_surrogate() -> None:
    assert approval_text_sha256("approval subject=S state=requested") == approval_text_sha256(
        "approval subject=S state=requested"
    )
    assert len(approval_text_sha256("\ud800")) == 64


def test_the_human_report_names_a_pending_requests_addressee_and_batch() -> None:
    request = ApprovalEvent(
        subject="S-1",
        state="requested",
        actor="P/req",
        reason="",
        seq=1,
        ts=1.0,
        addressing=ApprovalAddressing(approver="P/alice", batch=BATCH),
    )
    status = ApprovalStatus(
        subject="S-1",
        current_state="awaiting_approval",
        requested_by="P/req",
        requested_at=1.0,
        decided_by="",
        decided_at=0.0,
        decision_reason="",
        history=(request,),
    )
    text = render_human(ApprovalReport(generated_from_seq=1, as_of=1.0, statuses=(status,)))
    assert f"requested by P/req approver=P/alice batch={BATCH}" in text
