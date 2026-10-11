# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the query of stored native-message records
"""Records put into a real hub's store by the real commands, then queried.

The sender's records come from ``native-hook claude`` with hook input on
standard input, the receiver's from ``native-record --direction received``;
both go through a real hub with its store in the test directory.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.cli import main
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.native_query import (
    capped,
    render_human,
    result_to_json,
    run_native_query,
    selector_of,
)

SENDER = "GROUP-A/seat-aaaa"
RECEIVER = "GROUP-B/seat-bbbb"
OTHER = "GROUP-C/seat-cccc"
SESSION = "0f3c9a52-6d1e-4b7a-9c35-2f1d8e7a6b40"
PEER_SESSION = "9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


class _Stdin:
    def __init__(self, raw: bytes) -> None:
        self.buffer = io.BytesIO(raw)


async def _run(argv: list[str]) -> int:
    return await asyncio.to_thread(main, argv)


async def _send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    uri: str,
    number: int,
    text: str,
    to: str = "night-shift [a1b2c3]",
) -> None:
    """Record the sender's side of message ``number`` through the hook command."""
    call = {
        "session_id": SESSION,
        "hook_event_name": "PostToolUse",
        "tool_name": "SendMessage",
        "tool_input": {"to": to, "summary": "s", "message": text},
        "tool_response": {"success": True, "message": "queued", "msg_id": f"msg-{number}"},
        "tool_use_id": f"toolu_{number}",
    }
    monkeypatch.setattr(sys, "stdin", _Stdin(json.dumps(call).encode("utf-8")))
    code = await _run(
        [
            *("native-hook", "claude", "--uri", uri),
            *("--spool-dir", str(tmp_path / "spool")),
            *("--register-dir", str(tmp_path / "seats")),
        ]
    )
    assert code == 0


async def _receive(uri: str, text: str, *, seat: str = RECEIVER, session: str = SESSION) -> None:
    """Record the receiver's side through the explicit record command."""
    code = await _run(
        [
            *("native-record", "--channel", "claude_cross_session"),
            *("--direction", "received", "--phase", "outcome", "--outcome", "queued"),
            *("--seat", seat, "--peer-seat", SENDER),
            *("--sender-native-session", session, "--text", text, "--uri", uri),
        ]
    )
    assert code == 0


@pytest.fixture
async def store_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Path:
    """A store with one paired message, one sent only, one received only."""
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    seats = ["--register-dir", str(tmp_path / "seats")]
    assert main(["native-seat", "register", "--session-id", SESSION, "--seat", SENDER, *seats]) == 0
    path = tmp_path / "hub.db"
    store = EventStore(path)
    async with running_hub(SynapseHub(hub_id="query", journal=store)) as (_, uri):
        await _send(tmp_path, monkeypatch, uri, 1, "both sides have this")
        await _receive(uri, "both sides have this")
        await _send(tmp_path, monkeypatch, uri, 2, "only the sender has this \x1b[31m")
        await _receive(uri, "only the receiver has this")
        await _receive(uri, "another session sent this", seat=OTHER, session="other-session")
    store.close()
    capsys.readouterr()
    return path


def _group(data: dict[str, object], name: str) -> list[dict[str, Any]]:
    group = data[name]
    assert isinstance(group, list)
    return group


def test_the_query_word_and_its_selector() -> None:
    assert selector_of("native-messages all") == "all"
    assert selector_of("  native-messages   GROUP-A/seat-aaaa ") == SENDER
    assert selector_of("native-messages") is None
    assert selector_of("native-messages a b") is None
    assert selector_of("receipts all") is None


def test_stored_records_are_paired_and_the_one_sided_ones_are_named(store_path: Path) -> None:
    result = run_native_query(store_path, "native-messages all")

    assert result.report.counts() == {
        "paired": 1,
        "sender_only": 1,
        "receiver_only": 2,
        "attempts_without_outcome": 0,
    }
    ((sent, received),) = result.report.paired
    assert (sent["recorder"], received["recorder"]) == (SENDER, RECEIVER)
    assert sent["text_sha256"] == received["text_sha256"]
    assert sent["event_seq"] < received["event_seq"]


def test_a_seat_selects_whole_pairs_even_when_the_sender_named_only_an_address(
    store_path: Path,
) -> None:
    # The sender's records carry the recipient as a raw address: the receiving
    # session was never registered. The pair is still the receiver's.
    receiver = run_native_query(store_path, f"native-messages {RECEIVER}")
    other = run_native_query(store_path, f"native-messages {OTHER}")
    nobody = run_native_query(store_path, "native-messages GROUP-X/none")

    ((sent, _),) = receiver.report.paired
    assert sent["recipient_seat"] is None and sent["recipient_address"]
    assert receiver.report.counts() == {
        "paired": 1,
        "sender_only": 0,
        "receiver_only": 1,
        "attempts_without_outcome": 0,
    }
    assert other.report.counts()["receiver_only"] == 1 and not other.report.paired
    sender = run_native_query(store_path, f"native-messages {SENDER}")
    assert sender.report.counts() == {
        "paired": 1,
        "sender_only": 1,
        "receiver_only": 2,
        "attempts_without_outcome": 0,
    }
    assert set(nobody.report.counts().values()) == {0}


def test_the_output_withholds_the_text_and_cannot_move_the_cursor(store_path: Path) -> None:
    result = run_native_query(store_path, "native-messages all")
    data = result_to_json(result)
    human = render_human(result)

    assert data["kind"] == "native_messages" and data["selector"] == "all"
    assert data["counts"] == result.report.counts()
    listed = [*_group(data, "sender_only"), *_group(data, "receiver_only")]
    (pair,) = _group(data, "paired")
    assert all("text" not in record and "tool_result" not in record for record in listed)
    assert "text" not in pair["sent"] and "text" not in pair["received"]
    assert all(record["text_sha256"] and record["text_bytes"] for record in listed)
    assert "has this" not in json.dumps(data) and "has this" not in human
    assert "\x1b" not in human
    hostile = render_human(run_native_query(store_path, "native-messages seat\x1b[2J"))
    assert "\x1b" not in hostile and hostile.startswith("native messages seat")
    assert human.splitlines()[0] == (
        "native messages all: paired 1, sender only 1, receiver only 2, attempts without outcome 0"
    )
    assert "sender only (no receiver's record):" in human
    assert human.count(f"{SENDER} -> ") == 3


def test_only_native_message_events_are_read(store_path: Path) -> None:
    before = run_native_query(store_path, "native-messages all").report.counts()
    store = EventStore(store_path)
    # An event of another kind whose body looks like a receiver's record.
    store.append(
        EventKind.CHAT,
        {
            "channel": "claude_cross_session",
            "direction": "received",
            "phase": "outcome",
            "outcome": "queued",
            "recipient_seat": RECEIVER,
            "sender_native_session": SESSION,
            "text_sha256": "ab" * 32,
        },
    )
    store.close()

    assert run_native_query(store_path, "native-messages all").report.counts() == before


def test_a_limit_cuts_the_listing_and_never_the_counts(store_path: Path) -> None:
    result = run_native_query(store_path, "native-messages all")
    newest = result.report.receiver_only[-1]

    assert capped(result, None) == result
    assert capped(result, 1).report.receiver_only == (newest,)
    assert capped(result, 0).report.counts() == dict.fromkeys(result.report.counts(), 0)
    data = result_to_json(result, limit=1)
    assert data["counts"] == result.report.counts()
    assert [r["event_seq"] for r in _group(data, "receiver_only")] == [newest["event_seq"]]
    assert render_human(result, limit=0).splitlines() == [render_human(result).splitlines()[0]]


def test_a_missing_store_and_another_query_are_refused(tmp_path: Path, store_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_native_query(tmp_path / "absent.db", "native-messages all")
    with pytest.raises(ValueError, match="unsupported event query"):
        run_native_query(store_path, "receipts all")
    assert not (tmp_path / "absent.db").exists()


def test_the_event_query_command_answers_the_native_message_query(
    tmp_path: Path, store_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    human = main(["event-query", str(store_path), "native-messages all"])
    human_out = capsys.readouterr().out
    as_json = main(["event-query", str(store_path), f"native-messages {RECEIVER}", "--json"])
    data = json.loads(capsys.readouterr().out)
    cut = main(["event-query", str(store_path), "native-messages all", "--limit", "1", "--json"])
    cut_data = json.loads(capsys.readouterr().out)
    absent = main(["event-query", str(tmp_path / "absent.db"), "native-messages all"])
    refused = capsys.readouterr()
    # Every other query still goes to the temporal reader.
    other = main(["event-query", str(store_path), "receipts all"])
    other_out = capsys.readouterr().out

    assert (human, as_json, cut, absent, other) == (0, 0, 0, 2, 0)
    assert human_out.splitlines()[0].startswith("native messages all: paired 1, sender only 1")
    assert human_out.endswith("\n") and "\x1b" not in human_out
    assert (data["kind"], data["selector"]) == ("native_messages", RECEIVER)
    assert data["counts"]["paired"] == 1 and len(_group(data, "paired")) == 1
    assert cut_data["counts"]["receiver_only"] == 2 and len(_group(cut_data, "receiver_only")) == 1
    assert (refused.out, refused.err.startswith("missing event store: ")) == ("", True)
    assert other_out.startswith("delivery receipts all: ")
