# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for pairing sender and receiver records
"""Which native messages have a record on both sides."""

from __future__ import annotations

from typing import Any

from synapse_channel.native_pairing import PairingReport, pair_records, render

_A, _B, _C = "session-a", "session-b", "session-c"


def _record(direction: str, digest: str, at: str, **fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "channel": "claude_cross_session",
        "direction": direction,
        "phase": "outcome",
        "outcome": "queued" if direction == "sent" else None,
        "sender_seat": "P/a",
        "recipient_seat": "P/b",
        "sender_native_session": _A,
        "recipient_native_session": _B,
        "recipient_address": "night [a1]",
        "native_call_id": None,
        "sent_at": at,
        "text_sha256": digest * 64,
    }
    record.update(fields)
    return record


def test_both_sides_of_one_message_pair_and_the_rest_is_named() -> None:
    sent = _record("sent", "1", "2026-10-11T12:00:00Z")
    got = _record("received", "1", "2026-10-11T12:00:01Z")
    lost = _record("sent", "2", "2026-10-11T12:01:00Z")
    stray = _record("received", "3", "2026-10-11T12:02:00Z", sender_seat=None)

    report = pair_records([stray, lost, got, sent, "noise", {"phase": "other"}])

    assert report == PairingReport(((sent, got),), (lost,), (stray,), ())
    assert report.counts() == {
        "paired": 1,
        "sender_only": 1,
        "receiver_only": 1,
        "attempts_without_outcome": 0,
    }
    assert render(report) == [
        "paired: 1, sender only: 1, receiver only: 1, attempts without outcome: 0",
        "sender only (no receiver's record):",
        "  2026-10-11T12:01:00Z claude_cross_session P/a -> P/b sha256 222222222222 outcome queued",
        "receiver only (no sender's record):",
        "  2026-10-11T12:02:00Z claude_cross_session session-a -> P/b "
        "sha256 333333333333 outcome -",
    ]


def test_the_same_text_between_other_sessions_is_not_the_same_message() -> None:
    sent = _record("sent", "1", "2026-10-11T12:00:00Z")
    elsewhere = _record("received", "1", "2026-10-11T12:00:01Z", sender_native_session=_C)
    other_recipient = _record("received", "1", "2026-10-11T12:00:02Z", recipient_native_session=_C)
    other_channel = _record("received", "1", "2026-10-11T12:00:03Z", channel="codex_queue")

    report = pair_records([sent, elsewhere, other_recipient, other_channel])

    assert report.paired == () and report.sender_only == (sent,)
    assert len(report.receiver_only) == 3


def test_a_side_that_does_not_know_one_session_still_pairs_on_the_other() -> None:
    sent = _record("sent", "1", "2026-10-11T12:00:00Z", recipient_native_session=None)
    got = _record("received", "1", "2026-10-11T12:00:01Z")

    assert pair_records([sent, got]).paired == ((sent, got),)

    blind = _record(
        "sent",
        "1",
        "2026-10-11T12:00:00Z",
        sender_native_session=None,
        recipient_native_session=None,
    )
    # With no session stated by both sides the digest alone proves nothing.
    report = pair_records([blind, got])
    assert report.paired == () and report.sender_only == (blind,) and report.receiver_only == (got,)


def test_a_repeated_text_pairs_one_to_one_in_order_of_time() -> None:
    first = _record("sent", "1", "2026-10-11T12:00:00Z")
    second = _record("sent", "1", "2026-10-11T12:05:00Z")
    third = _record("sent", "1", "2026-10-11T12:09:00Z")
    got_first = _record("received", "1", "2026-10-11T12:00:02Z")
    got_second = _record("received", "1", "2026-10-11T12:05:02Z")

    report = pair_records([third, got_second, second, got_first, first])

    assert report.paired == ((first, got_first), (second, got_second))
    assert report.sender_only == (third,) and report.receiver_only == ()


def test_an_attempt_is_listed_only_while_its_call_has_no_outcome() -> None:
    open_attempt = _record(
        "sent", "1", "2026-10-11T12:00:00Z", phase="attempt", outcome=None, native_call_id="call-1"
    )
    done_attempt = _record(
        "sent", "2", "2026-10-11T12:01:00Z", phase="attempt", outcome=None, native_call_id="call-2"
    )
    done = _record("sent", "2", "2026-10-11T12:01:01Z", native_call_id="call-2")
    other_session = _record(
        "sent",
        "3",
        "2026-10-11T11:00:00Z",
        phase="attempt",
        outcome=None,
        native_call_id="call-2",
        sender_native_session=_C,
        recorded_at="2026-10-11T11:00:05Z",
        sent_at=None,
    )
    odd = _record("sideways", "4", "2026-10-11T12:03:00Z", phase="attempt", native_call_id="x")

    report = pair_records([done, open_attempt, done_attempt, other_session, odd])

    assert report.attempts_without_outcome == (other_session, open_attempt)
    assert report.sender_only == (done,)
    assert render(report)[-3:] == [
        "attempts without outcome:",
        "  2026-10-11T11:00:05Z claude_cross_session P/a -> P/b sha256 333333333333 outcome -",
        "  2026-10-11T12:00:00Z claude_cross_session P/a -> P/b sha256 111111111111 outcome -",
    ]


def test_nothing_stored_gives_an_empty_report_and_unknown_names_show_a_question_mark() -> None:
    assert render(pair_records([])) == [
        "paired: 0, sender only: 0, receiver only: 0, attempts without outcome: 0"
    ]
    bare = {
        "channel": "acp",
        "direction": "sent",
        "phase": "outcome",
        "text_sha256": "ab" * 32,
        "recipient_address": "raw address",
    }
    lines = render(pair_records([bare]))
    assert lines[2] == "   acp ? -> raw address sha256 abababababab outcome -"
    assert render(pair_records([dict(bare, recipient_address=None)]))[2].endswith(
        "? -> ? sha256 abababababab outcome -"
    )
