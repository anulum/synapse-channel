# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the seat register, the hook mapping and the spool
"""The local half of native-message recording: register, mapping, spool, hook run.

The hook input used here has the keys a real ``PostToolUse`` call of the
session-send tool carried when it was captured (session_id, transcript_path,
cwd, hook_event_name, tool_name, tool_input with to/summary/message,
tool_response with success/message/display/msg_id, tool_use_id); the values
are invented. The hub side is exercised in ``test_cli_native_hook.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.native_message import (
    MAX_NATIVE_TEXT_BYTES,
    native_idempotency_key,
    own_side_seat,
    parse_native_message_record,
)
from synapse_channel.native_hook_claude import recipient_name, record_from_hook
from synapse_channel.native_record_hook import completing_sender, run_hook
from synapse_channel.native_seat_register import (
    Registration,
    SeatRegisterError,
    default_register_dir,
    load,
    register,
    seat_of_name,
    seat_of_session,
)
from synapse_channel.native_spool import (
    SendResult,
    counts,
    default_spool_dir,
    entry_stem,
    find_entry,
    flush,
    pending_entries,
    retry_failed,
    set_aside,
    spool,
)

_T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
_SESSION = "0f3c9a52-6d1e-4b7a-9c35-2f1d8e7a6b40"
_CALL = "toolu_01ExampleCall0000000000001"
_MESSAGE_ID = "7a1c2b3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


@pytest.fixture(autouse=True)
def open_umask() -> Iterator[None]:
    """Run with umask 0, so that a file mode in a test is the mode the code asked for."""
    previous = os.umask(0)
    try:
        yield
    finally:
        os.umask(previous)


def _hook(**changes: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "session_id": _SESSION,
        "transcript_path": "/home/user/.agent/projects/p/0f3c.jsonl",
        "cwd": "/home/user",
        "hook_event_name": "PostToolUse",
        "tool_name": "SendMessage",
        "tool_input": {"to": "night-shift [a1b2c3]", "summary": "status", "message": "héllo #1"},
        "tool_response": {
            "success": True,
            "message": "queued",
            "display": "sent",
            "msg_id": _MESSAGE_ID,
        },
        "tool_use_id": _CALL,
    }
    call.update(changes)
    return call


def _registered(tmp_path: Path) -> Path:
    directory = tmp_path / "seats"
    register(directory, native_session=_SESSION, seat="P/sender", session_name="day", now=_T0)
    register(
        directory,
        native_session="peer-session",
        seat="P/receiver",
        session_name="night-shift",
        now=_T0 + timedelta(seconds=1),
    )
    return directory


# ---------------------------------------------------------------------------
# Seat register
# ---------------------------------------------------------------------------


def test_each_registration_is_a_new_owner_only_file_and_the_newest_wins(tmp_path: Path) -> None:
    directory = tmp_path / "seats"
    first = register(directory, native_session="s1", seat="P/a", session_name="one", now=_T0)
    second = register(directory, native_session="s1", seat="P/b", now=_T0 + timedelta(seconds=5))
    same_instant = register(directory, native_session="s1", seat="P/c", now=_T0)

    assert len({first, second, same_instant}) == 3
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    registrations = load(directory)
    # Two registrations of the same microsecond are ordered by file name, the newest is last.
    assert sorted(item.seat for item in registrations[:2]) == ["P/a", "P/c"]
    assert [item.seat for item in registrations[:2]] == [
        json.loads(path.read_text(encoding="utf-8"))["seat"]
        for path in sorted((first, same_instant), key=lambda path: path.name)
    ]
    assert registrations[2].seat == "P/b"
    assert seat_of_session(registrations, "s1") == Registration(
        "s1", "P/b", None, "2026-10-11T12:00:05.000000Z"
    )
    named = seat_of_name(registrations, "one")
    assert named is not None and named.seat == "P/a"
    assert seat_of_session(registrations, "s2") is None and seat_of_name(registrations, "x") is None
    assert json.loads(first.read_text(encoding="utf-8"))["schema"] == "native-seat-registration-v1"


def test_unusable_registrations_are_refused_and_foreign_files_ignored(tmp_path: Path) -> None:
    directory = tmp_path / "seats"
    for bad in (
        {"native_session": " "},
        {"seat": "a\nb"},
        {"seat": "x" * 257},
        {"session_name": "\x7f"},
    ):
        values = {"native_session": "s", "seat": "P/a", **bad}
        with pytest.raises(SeatRegisterError, match="1 to 256 characters"):
            register(
                directory,
                native_session=values["native_session"],
                seat=values["seat"],
                session_name=values.get("session_name"),
            )
    assert load(directory) == () and load(tmp_path / "absent") == ()

    register(directory, native_session="s", seat="P/a", now=_T0)
    (directory / "broken.json").write_text("{", encoding="utf-8")
    (directory / "list.json").write_text("[]", encoding="utf-8")
    (directory / "other.json").write_text('{"schema": "x"}', encoding="utf-8")
    (directory / "partial.json").write_text(
        '{"schema": "native-seat-registration-v1", "native_session": "s", "seat": ""}',
        encoding="utf-8",
    )
    assert [item.seat for item in load(directory)] == ["P/a"]


def test_the_register_lives_under_the_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_register_dir() == tmp_path / "synapse" / "native-seats"
    assert default_spool_dir() == tmp_path / "synapse" / "native-spool"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert (
        default_register_dir() == tmp_path / "h" / ".local" / "share" / "synapse" / "native-seats"
    )
    assert default_spool_dir() == tmp_path / "h" / ".local" / "share" / "synapse" / "native-spool"


# ---------------------------------------------------------------------------
# Hook mapping
# ---------------------------------------------------------------------------


def test_a_send_call_becomes_the_senders_outcome_record_the_hub_accepts(tmp_path: Path) -> None:
    mapped = record_from_hook(_hook(), load(_registered(tmp_path)), captured_at=_T0)

    assert (
        mapped.skip_reason == "" and mapped.call_id == _CALL and mapped.native_session == _SESSION
    )
    record = mapped.record
    assert record is not None
    encoded = "héllo #1".encode()
    assert record == {
        "channel": "claude_cross_session",
        "direction": "sent",
        "phase": "outcome",
        "outcome": "queued",
        "sender_seat": "P/sender",
        "recipient_seat": "P/receiver",
        "sender_native_session": _SESSION,
        "recipient_native_session": "peer-session",
        "recipient_address": "night-shift [a1b2c3]",
        "execution_host": None,
        "native_message_id": _MESSAGE_ID,
        "native_call_id": _CALL,
        "source_msg_seq": None,
        "sent_at": "2026-10-11T12:00:00.000000Z",
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "text_bytes": len(encoded),
        "text": "héllo #1",
        "tool_result": _hook()["tool_response"],
    }
    # The hub's own parser takes it unchanged, and the sender is the own side.
    assert parse_native_message_record(record) == record
    assert own_side_seat(record) == "P/sender"
    assert native_idempotency_key(record).startswith("nm-")


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        ({"success": True}, "queued"),
        ({"success": False, "message": "no such session"}, "refused"),
        ({"message": "?"}, "uncertain"),
        ({"success": "true"}, "uncertain"),
        (None, "uncertain"),
        ("sent", "uncertain"),
    ],
)
def test_only_a_reported_success_is_queued(response: object, outcome: str, tmp_path: Path) -> None:
    mapped = record_from_hook(_hook(tool_response=response), (), captured_at=_T0)

    assert mapped.record is not None and mapped.record["outcome"] == outcome
    assert mapped.record["native_message_id"] is None
    assert parse_native_message_record(mapped.record)["outcome"] == outcome


def test_an_unregistered_sender_and_recipient_stay_unresolved_with_the_raw_address() -> None:
    mapped = record_from_hook(_hook(), (), captured_at=_T0)

    assert mapped.record is not None
    assert mapped.record["sender_seat"] is None and mapped.record["recipient_seat"] is None
    assert mapped.record["recipient_address"] == "night-shift [a1b2c3]"
    assert own_side_seat(mapped.record) is None


@pytest.mark.parametrize(
    ("hook_input", "reason"),
    [
        ([], "hook input is not an object"),
        (_hook(tool_name="Bash"), "not a session-send tool call"),
        (_hook(tool_input={"to": "x"}), "the call carries no message text"),
        (_hook(tool_input={"to": "x", "message": ""}), "the call carries no message text"),
        (_hook(tool_input="text"), "the call carries no message text"),
    ],
)
def test_a_call_with_nothing_to_record_says_why(hook_input: object, reason: str) -> None:
    mapped = record_from_hook(hook_input, (), captured_at=_T0)

    assert mapped.record is None and mapped.skip_reason == reason


def test_long_text_is_recorded_by_hash_and_a_large_answer_is_cut(tmp_path: Path) -> None:
    text = "x" * (MAX_NATIVE_TEXT_BYTES + 1)
    response = {"success": True, "message": "m" * 9000}
    hook = _hook(tool_input={"to": "night-shift", "message": text}, tool_response=response)

    record = record_from_hook(hook, (), captured_at=_T0).record

    assert record is not None and record["text"] is None
    assert record["text_bytes"] == len(text)
    assert record["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert set(record["tool_result"]) == {"truncated_json_prefix"}
    assert len(record["tool_result"]["truncated_json_prefix"]) == 1024
    assert parse_native_message_record(record)["text"] is None
    exact = record_from_hook(
        _hook(tool_input={"to": "n", "message": "x" * MAX_NATIVE_TEXT_BYTES}), (), captured_at=_T0
    ).record
    assert exact is not None and exact["text"] is not None


def test_odd_values_are_bounded_and_never_break_the_mapping() -> None:
    hook = _hook(
        tool_use_id="a\nb" + "c" * 400,
        session_id=7,
        tool_input={"to": 5, "message": "m"},
        tool_response={"success": True, "msg_id": ["x"], "nan": float("nan")},
    )

    mapped = record_from_hook(hook, (), captured_at=_T0.astimezone(timezone(timedelta(hours=2))))

    assert mapped.record is not None
    assert mapped.call_id == ("a b" + "c" * 400)[:256] and mapped.native_session == ""
    assert mapped.record["sender_native_session"] is None
    assert mapped.record["recipient_address"] is None and mapped.record["native_message_id"] is None
    assert mapped.record["tool_result"] == {"unserialisable": True}
    assert mapped.record["sent_at"] == "2026-10-11T12:00:00.000000Z"
    assert parse_native_message_record(mapped.record)["native_call_id"] == mapped.call_id
    assert recipient_name("  night-shift [a1b2c3] ") == "night-shift"
    assert recipient_name("night shift") == "night shift"


# ---------------------------------------------------------------------------
# Spool
# ---------------------------------------------------------------------------


def _record(tmp_path: Path) -> dict[str, Any]:
    record = record_from_hook(_hook(), load(_registered(tmp_path)), captured_at=_T0).record
    assert record is not None
    return record


class Hub:
    """A stand-in for the hub at the sender boundary: scripted answers, kept calls."""

    def __init__(self, *answers: SendResult) -> None:
        self.answers = list(answers)
        self.sent: list[dict[str, Any]] = []

    def __call__(self, record: dict[str, Any]) -> SendResult:
        self.sent.append(record)
        return self.answers.pop(0) if self.answers else SendResult(True, False, "recorded", 7)


_DOWN = SendResult(False, False, "the hub did not answer")
_REFUSED = SendResult(False, True, "refused by the hub (native_record_not_own_side)")


def test_the_record_is_on_disk_before_the_hub_is_asked_and_moves_only_after_it_answers(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "spool"
    record = _record(tmp_path)
    path, created = spool(directory, native_session=_SESSION, call_id=_CALL, record=record, now=_T0)

    assert created and path.parent.name == "pending"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["record"] == record and stored["attempts"] == [] and stored["state"] == "pending"

    seen: list[bool] = []

    def hub(sent: dict[str, Any]) -> SendResult:
        # While the hub is being asked the entry is still pending, not recorded.
        seen.append(path.is_file() and counts(directory)["recorded"] == 0)
        return SendResult(True, False, "recorded", 41)

    report = flush(directory, hub, now=lambda: _T0 + timedelta(seconds=3))

    assert seen == [True]
    assert (report.recorded, report.failed, report.left_pending, report.stopped_early) == (
        1,
        0,
        0,
        False,
    )
    moved = directory / "recorded" / "2026-10-11" / path.name
    assert not path.exists() and moved.is_file()
    final = json.loads(moved.read_text(encoding="utf-8"))
    assert final["state"] == "recorded" and final["audit_seq"] == 41
    assert final["attempts"] == [
        {
            "at": "2026-10-11T12:00:03.000000Z",
            "recorded": True,
            "final": False,
            "detail": "recorded",
        }
    ]


def test_the_same_call_delivered_twice_makes_one_entry_in_any_state(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    record = _record(tmp_path)
    first, created = spool(directory, native_session=_SESSION, call_id=_CALL, record=record)
    again, created_again = spool(directory, native_session=_SESSION, call_id=_CALL, record=record)

    assert created and not created_again and again == first
    flush(directory, Hub())
    recorded, created_after = spool(
        directory, native_session=_SESSION, call_id=_CALL, record=record
    )
    assert not created_after and recorded.parent.parent.name == "recorded"
    assert counts(directory) == {
        "pending": 0,
        "recorded": 1,
        "failed": 0,
        "skipped": 0,
        "unparsed": 0,
    }

    # Another call, another session, or no call identifier are different entries.
    other, made = spool(directory, native_session=_SESSION, call_id="toolu_2", record=record)
    assert made and other != first
    assert entry_stem(_SESSION, _CALL) != entry_stem("other-session", _CALL)
    # Call identifiers that look alike once made safe for a file name stay distinct.
    assert entry_stem(_SESSION, "a/b") != entry_stem(_SESSION, "a:b")
    assert entry_stem(_SESSION, "a/b").startswith("a-b__")
    assert entry_stem(_SESSION, "x" * 60) != entry_stem(_SESSION, "x" * 61)
    one, _ = spool(directory, native_session=_SESSION, call_id="", record=record)
    two, _ = spool(directory, native_session=_SESSION, call_id="", record=record)
    assert one != two
    assert find_entry(directory, "absent") is None


def test_a_hub_that_is_down_leaves_the_entry_pending_and_stops_the_flush(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    record = _record(tmp_path)
    for index in range(4):
        spool(directory, native_session=_SESSION, call_id=f"call-{index}", record=record)
    hub = Hub(_DOWN, _DOWN)

    report = flush(directory, hub)

    # Two transport failures in a row end the flush; the other two were not tried.
    assert len(hub.sent) == 2 and report.stopped_early
    assert (report.recorded, report.failed, report.left_pending) == (0, 0, 4)
    attempts = [
        len(json.loads(path.read_text(encoding="utf-8"))["attempts"])
        for path in pending_entries(directory)
    ]
    assert sorted(attempts) == [0, 0, 1, 1]

    later = flush(directory, Hub())
    assert (later.recorded, later.left_pending, later.stopped_early) == (4, 0, False)


def test_retry_is_bounded_and_a_refusal_by_the_hub_is_final_at_once(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    record = _record(tmp_path)
    spool(directory, native_session=_SESSION, call_id="slow", record=record)

    for expected_left in (1, 1):
        report = flush(directory, Hub(_DOWN), max_attempts=3)
        assert (report.failed, report.left_pending) == (0, expected_left)
    report = flush(directory, Hub(_DOWN), max_attempts=3)
    assert (report.failed, report.left_pending) == (1, 0)
    failed = json.loads((directory / "failed" / pending_name(directory, "slow")).read_text("utf-8"))
    assert failed["state"] == "failed" and len(failed["attempts"]) == 3

    spool(directory, native_session=_SESSION, call_id="refused", record=record)
    hub = Hub(_REFUSED)
    report = flush(directory, hub, max_attempts=3)
    assert (report.failed, report.left_pending, report.stopped_early) == (1, 0, False)
    assert len(hub.sent) == 1

    # Nothing failed is retried unless asked; asking keeps the earlier attempts.
    assert flush(directory, Hub()).recorded == 0
    assert retry_failed(directory) == 2 and retry_failed(tmp_path / "absent") == 0
    again = flush(directory, Hub())
    assert again.recorded == 2
    stored = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (directory / "recorded").rglob("*.json")
    ]
    assert sorted(len(entry["earlier_attempts"]) for entry in stored) == [1, 3]


def pending_name(directory: Path, call_id: str) -> str:
    return f"{entry_stem(_SESSION, call_id)}.json"


def test_a_refusal_between_two_outages_does_not_count_as_an_outage(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    record = _record(tmp_path)
    for index in range(4):
        path, _ = spool(directory, native_session=_SESSION, call_id=f"c{index}", record=record)
        os.utime(path, ns=(index * 10**9, index * 10**9))
    hub = Hub(_DOWN, _REFUSED, _DOWN, SendResult(True, False, "recorded", 9))

    report = flush(directory, hub)

    assert len(hub.sent) == 4 and not report.stopped_early
    assert (report.recorded, report.failed, report.left_pending) == (1, 1, 2)


def test_the_flush_limit_and_unreadable_entries(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    record = _record(tmp_path)
    for index in range(3):
        spool(directory, native_session=_SESSION, call_id=f"c{index}", record=record)
    (directory / "pending" / "broken.json").write_text("{", encoding="utf-8")
    (directory / "pending" / "foreign.json").write_text('{"schema": "x"}', encoding="utf-8")
    (directory / "pending" / "shape.json").write_text(
        '{"schema": "native-spool-entry-v1", "record": {}, "attempts": 3}', encoding="utf-8"
    )
    (directory / "failed").mkdir()
    (directory / "failed" / "broken.json").write_text("{", encoding="utf-8")

    assert flush(directory, Hub(), limit=2).recorded <= 2
    assert flush(directory, Hub()).left_pending == 3
    assert retry_failed(directory) == 0
    assert (
        pending_entries(tmp_path / "absent") == []
        and flush(tmp_path / "absent", Hub()).recorded == 0
    )


def test_input_without_a_record_is_kept_aside(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    skipped = set_aside(directory, "skipped", "toolu/1", b"{}", _T0)
    unparsed = set_aside(directory, "unparsed", "", b"\xff", _T0)

    assert skipped == directory / "skipped" / "2026-10-11" / "20261011T120000000000Z_toolu-1.json"
    assert unparsed.parent.name == "unparsed" and unparsed.name.startswith(
        "20261011T120000000000Z_input_"
    )
    assert unparsed.read_bytes() == b"\xff"
    assert counts(directory)["skipped"] == 1 and counts(directory)["unparsed"] == 1


# ---------------------------------------------------------------------------
# Hook run
# ---------------------------------------------------------------------------


def _run(tmp_path: Path, raw: bytes, hub: Hub, seats: Path | None = None) -> Any:
    return run_hook(
        raw,
        spool_dir=tmp_path / "spool",
        register_dir=seats or tmp_path / "seats",
        send=hub,
        now=lambda: _T0,
    )


def test_a_hook_call_is_spooled_then_recorded_once_even_when_delivered_twice(
    tmp_path: Path,
) -> None:
    seats = _registered(tmp_path)
    hub = Hub()
    raw = json.dumps(_hook()).encode()

    first = _run(tmp_path, raw, hub, seats)
    second = _run(tmp_path, raw, hub, seats)

    assert (first.recorded, second.recorded) == (1, 0)
    assert len(hub.sent) == 1 and hub.sent[0]["sender_seat"] == "P/sender"
    assert counts(tmp_path / "spool")["recorded"] == 1


def test_an_unregistered_session_waits_in_the_spool_until_it_is_registered(tmp_path: Path) -> None:
    hub = Hub()
    raw = json.dumps(_hook()).encode()

    report = _run(tmp_path, raw, hub)

    assert hub.sent == [] and (report.recorded, report.left_pending) == (0, 1)
    pending = pending_entries(tmp_path / "spool")[0]
    attempt = json.loads(pending.read_text(encoding="utf-8"))["attempts"][0]
    assert attempt["detail"] == "the sending session is not registered as a seat"
    assert not attempt["final"]

    seats = _registered(tmp_path)
    later = _run(tmp_path, json.dumps(_hook(tool_name="Bash")).encode(), hub, seats)

    assert later.recorded == 1 and len(hub.sent) == 1
    assert (
        hub.sent[0]["sender_seat"] == "P/sender" and hub.sent[0]["recipient_seat"] == "P/receiver"
    )
    assert hub.sent[0]["recipient_native_session"] == "peer-session"
    stored = next((tmp_path / "spool" / "recorded").rglob("*.json"))
    assert json.loads(stored.read_text(encoding="utf-8"))["record"]["sender_seat"] == "P/sender"


def test_entries_of_an_unregistered_session_do_not_hold_the_ones_behind_them(
    tmp_path: Path,
) -> None:
    seats = _registered(tmp_path)
    hub = Hub()
    for number in range(3):
        raw = json.dumps(_hook(session_id="stranger", tool_use_id=f"toolu_s{number}")).encode()
        report = _run(tmp_path, raw, hub, seats)
        assert (report.left_pending, report.stopped_early) == (number + 1, False)

    report = _run(tmp_path, json.dumps(_hook()).encode(), hub, seats)

    assert (report.recorded, report.left_pending, report.stopped_early) == (1, 3, False)
    assert [record["sender_seat"] for record in hub.sent] == ["P/sender"]
    stranger = json.loads(pending_entries(tmp_path / "spool")[0].read_text(encoding="utf-8"))
    assert len(stranger["attempts"]) == 4


def test_a_sender_that_never_reached_the_hub_is_passed_on_as_such(tmp_path: Path) -> None:
    seats = _registered(tmp_path)
    unread = SendResult(False, False, "cannot read token file", hub_asked=False)

    result = completing_sender(seats, Hub(unread))(_record(tmp_path))

    assert (result.hub_asked, result.detail) == (False, "cannot read token file")
    assert completing_sender(seats, Hub(_DOWN))(_record(tmp_path)).hub_asked


def test_the_completing_sender_keeps_what_is_already_resolved(tmp_path: Path) -> None:
    seats = _registered(tmp_path)
    hub = Hub()
    record = dict(_record(tmp_path), sender_seat="P/declared", recipient_seat="P/known")

    result = completing_sender(seats, hub)(record)

    assert result.recorded and result.sent_record == record
    bare = dict(
        record,
        sender_seat=None,
        sender_native_session=None,
        recipient_seat=None,
        recipient_address=None,
    )
    refused = completing_sender(seats, hub)(bare)
    assert not refused.recorded and not refused.final and refused.sent_record is None
    unknown = dict(record, sender_seat=None, recipient_seat=None, recipient_address="nobody")
    sent = completing_sender(seats, hub)(unknown)
    assert sent.sent_record is not None and sent.sent_record["recipient_seat"] is None


def test_input_that_is_not_a_send_with_text_is_never_sent(tmp_path: Path) -> None:
    hub = Hub()

    assert _run(tmp_path, b"\xff not json", hub) is None
    assert _run(tmp_path, b"{ broken", hub) is None
    other_tool = _hook(tool_name="Bash", tool_use_id="toolu_other_tool")
    assert _run(tmp_path, json.dumps(other_tool).encode(), hub).recorded == 0
    _run(tmp_path, json.dumps(_hook(tool_input={"to": "x"})).encode(), hub)

    assert hub.sent == []
    assert counts(tmp_path / "spool") == {
        "pending": 0,
        "recorded": 0,
        "failed": 0,
        "skipped": 1,
        "unparsed": 2,
    }


def test_no_exception_leaves_the_hook(tmp_path: Path) -> None:
    def broken(record: dict[str, Any]) -> SendResult:
        raise RuntimeError("the sender itself is broken")

    blocked = tmp_path / "file-not-directory"
    blocked.write_text("x", encoding="utf-8")
    raw = json.dumps(_hook()).encode()

    assert (
        run_hook(raw, spool_dir=tmp_path / "s", register_dir=_registered(tmp_path), send=broken)
        is None
    )
    assert run_hook(raw, spool_dir=blocked, register_dir=tmp_path / "seats", send=Hub()) is None
    # The record of the first call was written before the sender broke.
    assert counts(tmp_path / "s")["pending"] == 1


def test_a_failed_write_leaves_no_temporary_file_and_no_entry(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    record = _record(tmp_path)
    # A directory sits where the entry file must go, so the final rename fails.
    (directory / "pending" / f"{entry_stem(_SESSION, _CALL)}.json").mkdir(parents=True)

    with pytest.raises(OSError):
        spool(directory, native_session=_SESSION, call_id=_CALL, record=record)

    assert [path.name for path in (directory / "pending").iterdir()] == [
        f"{entry_stem(_SESSION, _CALL)}.json"
    ]
    assert pending_entries(directory) == []
