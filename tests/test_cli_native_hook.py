# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse native-hook` and `synapse native-seat` against real hubs
"""Run the real entry point with a hook call on standard input against real hubs.

Every hub here is a real one on its own socket with its own store in the
test's directory; the spool and the seat register are in the test's directory
too. The two peers that answer with a scripted frame are real WebSocket
servers: a hub cannot be brought to refuse a sender's own record, and what
the command does with such a refusal still has to be shown.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from hub_e2e_helpers import collect_available, read_until_type, running_hub, send_json
from synapse_channel.cli import main
from synapse_channel.cli_native_hook import hub_sender
from synapse_channel.core import hub_connection
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.native_hook_claude import record_from_hook
from synapse_channel.native_seat_register import Registration
from synapse_channel.native_spool import counts

SENDER = "GROUP-A/seat-aaaa"
RECEIVER = "GROUP-B/seat-bbbb"
SESSION = "0f3c9a52-6d1e-4b7a-9c35-2f1d8e7a6b40"
PEER_SESSION = "9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
CALL = "toolu_01ExampleCall0000000000001"
MESSAGE_ID = "7a1c2b3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
TEXT = "Správa č. 1 — héllo #1"


def _hook(call_id: str = CALL, text: str = TEXT, message_id: str = MESSAGE_ID) -> bytes:
    return json.dumps(
        {
            "session_id": SESSION,
            "transcript_path": "/home/user/.agent/projects/p/0f3c.jsonl",
            "cwd": "/home/user",
            "hook_event_name": "PostToolUse",
            "tool_name": "SendMessage",
            "tool_input": {"to": "night-shift [a1b2c3]", "summary": "status", "message": text},
            "tool_response": {
                "success": True,
                "message": "queued",
                "display": "sent",
                "msg_id": message_id,
            },
            "tool_use_id": call_id,
        }
    ).encode("utf-8")


class _Stdin:
    """Standard input holding one hook call."""

    def __init__(self, raw: bytes) -> None:
        self.buffer = io.BytesIO(raw)


class _BrokenStdin:
    """Standard input whose read fails."""

    class _Buffer:
        def read(self, size: int = -1) -> bytes:
            raise OSError("closed")

    buffer = _Buffer()


def _dirs(tmp_path: Path) -> list[str]:
    return ["--spool-dir", str(tmp_path / "spool"), "--register-dir", str(tmp_path / "seats")]


async def _run(argv: list[str]) -> int:
    return await asyncio.to_thread(main, argv)


async def _call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uri: str, raw: bytes, *extra: str
) -> int:
    monkeypatch.setattr(sys, "stdin", _Stdin(raw))
    return await _run(
        ["native-hook", "claude", "--uri", uri, "--ready-timeout", "1", *_dirs(tmp_path), *extra]
    )


async def _flush(tmp_path: Path, uri: str, *extra: str) -> int:
    return await _run(
        ["native-hook", "flush", "--uri", uri, "--ready-timeout", "1", *_dirs(tmp_path), *extra]
    )


def _register(tmp_path: Path, *, sender: bool = True) -> None:
    seats = ["--register-dir", str(tmp_path / "seats")]
    if sender:
        code = main(["native-seat", "register", "--session-id", SESSION, "--seat", SENDER, *seats])
        assert code == 0
    code = main(
        [
            "native-seat",
            "register",
            "--session-id",
            PEER_SESSION,
            "--seat",
            RECEIVER,
            "--name",
            "night-shift",
            *seats,
        ]
    )
    assert code == 0


def _events(store: EventStore) -> list[dict[str, Any]]:
    return [e.payload for e in store.read_all() if e.kind == EventKind.NATIVE_MESSAGE]


def _state(tmp_path: Path) -> dict[str, int]:
    return {state: count for state, count in counts(tmp_path / "spool").items() if count}


@pytest.fixture(autouse=True)
def no_token_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the workstation's own hub token out of every test."""
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)


async def test_a_hook_call_becomes_one_stored_record_and_a_repeat_adds_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    capsys.readouterr()
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(hub_id="hook", journal=store)) as (_, uri):
        first = await _call(tmp_path, monkeypatch, uri, _hook())
        second = await _call(tmp_path, monkeypatch, uri, _hook())
        other = await _call(
            tmp_path, monkeypatch, uri, _hook("toolu_02", "second message", "msg-2")
        )
    captured = capsys.readouterr()

    assert (first, second, other) == (0, 0, 0)
    assert (captured.out, captured.err) == ("", "")
    event, later = _events(store)
    assert (event["sender_seat"], event["recipient_seat"]) == (SENDER, RECEIVER)
    assert (event["recorder"], event["direction"], event["phase"]) == (SENDER, "sent", "outcome")
    assert (event["outcome"], event["channel"]) == ("queued", "claude_cross_session")
    assert (event["text"], event["native_call_id"]) == (TEXT, CALL)
    assert (event["native_message_id"], event["sender_native_session"]) == (MESSAGE_ID, SESSION)
    assert event["recipient_native_session"] == PEER_SESSION
    assert (later["text"], later["native_call_id"]) == ("second message", "toolu_02")
    assert _state(tmp_path) == {"recorded": 2}
    (entry,) = (tmp_path / "spool" / "recorded").glob(f"*/{CALL}__*.json")
    assert json.loads(entry.read_text(encoding="utf-8"))["audit_seq"] > 0
    store.close()


async def test_a_hub_that_is_down_costs_nothing_and_a_later_flush_stores_the_record_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(hub_id="hook", journal=store)) as (_, gone):
        pass
    capsys.readouterr()

    down = await _call(tmp_path, monkeypatch, gone, _hook())
    silent = capsys.readouterr()
    assert (down, silent.out, silent.err) == (0, "", "")
    assert (_state(tmp_path), _events(store)) == ({"pending": 1}, [])
    still_down = await _flush(tmp_path, gone)
    assert still_down == 1
    assert capsys.readouterr().out == "recorded 0, failed 0, pending 1\n"

    async with running_hub(SynapseHub(hub_id="hook", journal=store)) as (_, uri):
        flushed = await _flush(tmp_path, uri)
        flushed_out = capsys.readouterr().out
        again = await _flush(tmp_path, uri)
        repeat = await _call(tmp_path, monkeypatch, uri, _hook())
        status = await _run(["native-hook", "status", *_dirs(tmp_path)])

    assert (flushed, again, repeat, status) == (0, 0, 0, 0)
    assert flushed_out == "recorded 1, failed 0, pending 0\n"
    assert capsys.readouterr().out == (
        "recorded 0, failed 0, pending 0\n"
        "pending: 0\nrecorded: 1\nfailed: 0\nskipped: 0\nunparsed: 0\n"
    )
    (event,) = _events(store)
    assert (event["text"], event["native_call_id"]) == (TEXT, CALL)
    entry = next((tmp_path / "spool" / "recorded").glob("*/*.json"))
    attempts = json.loads(entry.read_text(encoding="utf-8"))["attempts"]
    assert [a["recorded"] for a in attempts] == [False, False, True]
    store.close()


async def test_two_outages_in_a_row_stop_the_flush_and_say_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    async with running_hub(SynapseHub(hub_id="hook")) as (_, gone):
        pass
    for number in range(3):
        monkeypatch.setattr(
            sys, "stdin", _Stdin(_hook(f"toolu_{number}", f"m{number}", f"msg-{number}"))
        )
        # A flush limit of zero is not offered; the three entries are spooled by
        # three hook calls, each of which also tries the hub and finds it down.
        assert await _run(["native-hook", "claude", "--uri", gone, *_dirs(tmp_path)]) == 0
    capsys.readouterr()

    code = await _flush(tmp_path, gone)

    assert code == 1
    assert capsys.readouterr().out == (
        "recorded 0, failed 0, pending 3; stopped early, the hub did not answer\n"
    )


async def test_an_unregistered_session_waits_and_is_stored_after_registration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path, sender=False)
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(hub_id="hook", journal=store)) as (_, uri):
        assert await _call(tmp_path, monkeypatch, uri, _hook()) == 0
        assert (_state(tmp_path), _events(store)) == ({"pending": 1}, [])
        _register(tmp_path)
        capsys.readouterr()
        assert await _flush(tmp_path, uri) == 0

    assert capsys.readouterr().out == "recorded 1, failed 0, pending 0\n"
    (event,) = _events(store)
    assert (event["sender_seat"], event["recipient_seat"]) == (SENDER, RECEIVER)
    store.close()


async def test_a_hub_that_cannot_record_exhausts_the_attempts_and_a_retry_brings_it_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    async with running_hub(SynapseHub(hub_id="no-journal")) as (_, uri):
        first = await _call(tmp_path, monkeypatch, uri, _hook(), "--max-attempts", "2")
        assert _state(tmp_path) == {"pending": 1}
        second = await _flush(tmp_path, uri, "--max-attempts", "2")
        at_once = await _call(
            tmp_path, monkeypatch, uri, _hook("toolu_now", "now", "msg-now"), "--max-attempts", "1"
        )
    out = capsys.readouterr().out.splitlines()[-1]
    assert (first, second, at_once) == (0, 1, 0)
    assert (out, _state(tmp_path)) == ("recorded 0, failed 1, pending 0", {"failed": 2})
    (failed,) = (tmp_path / "spool" / "failed").glob(f"{CALL}__*.json")
    details = [a["detail"] for a in json.loads(failed.read_text(encoding="utf-8"))["attempts"]]
    assert details == ["refused by the hub (native_record_unavailable)"] * 2

    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(hub_id="hook", journal=store)) as (_, uri):
        untouched = await _flush(tmp_path, uri)
        assert _events(store) == []
        revived = await _flush(tmp_path, uri, "--retry-failed")
    lines = capsys.readouterr().out.splitlines()

    # A flush reports and judges what it did itself; the standing failed entry
    # is what ``native-hook status`` shows.
    assert (untouched, revived) == (0, 0)
    assert lines == [
        "recorded 0, failed 0, pending 0",
        "recorded 2, failed 0, pending 0, revived 2",
    ]
    assert sorted(event["text"] for event in _events(store)) == [TEXT, "now"]
    store.close()


def _token_access(path: Path, *, owner_only: bool) -> None:
    """Set real platform permissions on the owned token fixture."""
    if os.name == "nt":
        tool = Path(os.environ["SYSTEMROOT"]) / "System32" / "icacls.exe"
        operation = ["/remove:g", "*S-1-1-0"] if owner_only else ["/grant:r", "*S-1-1-0:R"]
        subprocess.run(  # noqa: S603 - system ACL tool and an owned fixture path
            [str(tool), str(path), *operation],
            check=True,
            capture_output=True,
            timeout=10,
        )
        if owner_only:
            apply_owner_only_file(path)
    else:
        path.chmod(0o600 if owner_only else 0o644)


async def test_a_secured_hub_takes_the_token_from_a_file_the_flag_or_the_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    secret = tmp_path / "token"
    secret.write_text("hub-secret\n", encoding="utf-8")
    _token_access(secret, owner_only=False)
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(
        hub_id="secured", journal=store, authenticator=TokenAuthenticator(["hub-secret"])
    )
    async with running_hub(hub) as (_, uri):
        without = await _call(tmp_path, monkeypatch, uri, _hook("toolu_a", "a", "msg-a"))
        readable_by_others = await _call(
            tmp_path, monkeypatch, uri, _hook("toolu_b", "b", "msg-b"), "--token-file", str(secret)
        )
        assert (_state(tmp_path), _events(store)) == ({"pending": 2}, [])
        pending = sorted((tmp_path / "spool" / "pending").glob("toolu_b__*.json"))
        last = json.loads(pending[0].read_text(encoding="utf-8"))["attempts"][-1]
        assert last["detail"].startswith("cannot read token file: ")
        # Reading the token failed before the hub was contacted: not an outage.
        blocked = await _flush(tmp_path, uri, "--token-file", str(secret))
        assert capsys.readouterr().out.endswith("recorded 0, failed 0, pending 2\n")

        _token_access(secret, owner_only=True)
        by_file = await _flush(tmp_path, uri, "--token-file", str(secret))
        by_flag = await _call(
            tmp_path, monkeypatch, uri, _hook("toolu_c", "c", "msg-c"), "--token", "hub-secret"
        )
        unusable = tmp_path / "other-token"
        unusable.write_text("wrong\n", encoding="utf-8")
        _token_access(unusable, owner_only=False)
        flag_first = await _call(
            tmp_path,
            monkeypatch,
            uri,
            _hook("toolu_e", "e", "msg-e"),
            *("--token", "hub-secret", "--token-file", str(unusable)),
        )
        # Recorded by this very call: the flag is used and the file is not read.
        assert _state(tmp_path) == {"recorded": 4}
        monkeypatch.setenv("SYNAPSE_TOKEN", "hub-secret")
        by_environment = await _call(tmp_path, monkeypatch, uri, _hook("toolu_d", "d", "msg-d"))

    assert (without, readable_by_others, by_file, by_flag, by_environment) == (0, 0, 0, 0, 0)
    assert (blocked, flag_first) == (1, 0)
    assert sorted(event["text"] for event in _events(store)) == ["a", "b", "c", "d", "e"]
    assert _state(tmp_path) == {"recorded": 5}
    assert "hub-secret" not in capsys.readouterr().out
    store.close()


async def test_an_older_hub_and_a_held_seat_name_leave_the_record_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(hub_id="hook", journal=store)) as (_, uri):
        async with connect(uri) as holder:
            await read_until_type(holder, "welcome")
            await send_json(holder, sender=SENDER, type="heartbeat", payload="online")
            await collect_available(holder)
            assert await _call(tmp_path, monkeypatch, uri, _hook()) == 0
        assert (_state(tmp_path), _events(store)) == ({"pending": 1}, [])
        with monkeypatch.context() as older:
            older.setattr(hub_connection, "WIRE_PROTOCOL_VERSION", 6)
            assert await _flush(tmp_path, uri) == 1
        assert (_state(tmp_path), _events(store)) == ({"pending": 1}, [])
        assert await _flush(tmp_path, uri) == 0

    entry = next((tmp_path / "spool" / "recorded").glob("*/*.json"))
    attempts = json.loads(entry.read_text(encoding="utf-8"))["attempts"]
    assert [(a["recorded"], a["final"]) for a in attempts] == [
        (False, False),
        (False, False),
        (True, False),
    ]
    assert "record path unavailable" in attempts[1]["detail"]
    assert len(_events(store)) == 1
    store.close()


async def test_a_flush_beside_a_running_flush_does_nothing_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real flush is held open by a peer that welcomes and then stays silent."""

    async def quiet(websocket: Any) -> None:
        await websocket.send(
            json.dumps({"sender": "SynapseHub", "type": "welcome", "protocol_version": 7})
        )
        await websocket.wait_closed()

    _register(tmp_path)
    async with serve(quiet, "localhost", 0) as server:
        uri = f"ws://localhost:{server.sockets[0].getsockname()[1]}"
        monkeypatch.setattr(sys, "stdin", _Stdin(_hook()))
        first = asyncio.create_task(
            _run(["native-hook", "claude", "--uri", uri, "--reply-timeout", "3", *_dirs(tmp_path)])
        )
        lock = tmp_path / "spool" / ".flush.lock"
        for _ in range(200):
            if lock.exists() and _state(tmp_path) == {"pending": 1}:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.3)
        capsys.readouterr()
        beside = await _flush(tmp_path, uri)
        retried = await _flush(tmp_path, uri, "--retry-failed")
        monkeypatch.setattr(sys, "stdin", _Stdin(_hook("toolu_2", "second", "msg-2")))
        hook_beside = await _run(["native-hook", "claude", "--uri", uri, *_dirs(tmp_path)])
        out = capsys.readouterr().out
        assert await first == 0

    assert (beside, retried, hook_beside) == (1, 1, 0)
    assert out == (
        "nothing flushed: another flush is running; pending 1\n"
        "nothing flushed: another flush is running; pending 1\n"
    )
    assert _state(tmp_path) == {"pending": 2}
    entries = sorted((tmp_path / "spool" / "pending").glob("*.json"))
    attempts = [len(json.loads(e.read_text(encoding="utf-8"))["attempts"]) for e in entries]
    assert sorted(attempts) == [0, 1]


@pytest.mark.parametrize("welcomes", [False, True])
async def test_the_waits_given_on_the_command_line_bound_a_hook_call(
    welcomes: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def quiet(websocket: Any) -> None:
        if welcomes:
            await websocket.send(
                json.dumps({"sender": "SynapseHub", "type": "welcome", "protocol_version": 7})
            )
        await websocket.wait_closed()

    _register(tmp_path)
    monkeypatch.setattr(sys, "stdin", _Stdin(_hook()))
    async with serve(quiet, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        waits = ["--ready-timeout", "0.5", "--reply-timeout", "0.5"]
        started = time.monotonic()
        code = await _run(
            [
                *("native-hook", "claude", "--uri", f"ws://localhost:{port}"),
                *waits,
                *_dirs(tmp_path),
            ]
        )
        elapsed = time.monotonic() - started

    # The defaults are 3 s and 5 s; a wait that ignored the options would show here.
    assert (code, _state(tmp_path)) == (0, {"pending": 1})
    assert elapsed < 2.5


def _record() -> dict[str, Any]:
    registrations = (Registration(SESSION, SENDER, None, "2026-10-11T12:00:00.000000Z"),)
    moment = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    record = record_from_hook(json.loads(_hook()), registrations, captured_at=moment).record
    assert record is not None
    return record


@pytest.mark.parametrize(
    ("answer", "final", "detail"),
    [
        (
            {"type": "native_message_rejected", "error_code": "native_record_not_own_side"},
            True,
            "refused by the hub (native_record_not_own_side)",
        ),
        (
            {"type": "native_message_rejected", "error_code": "native_record_rate_limited"},
            False,
            "refused by the hub (native_record_rate_limited)",
        ),
        (
            {"type": "native_message_rejected"},
            True,
            "refused by the hub (refused)",
        ),
        (
            {"type": "error", "error_code": "idempotency_conflict"},
            True,
            "refused by the hub (idempotency_conflict)",
        ),
        (
            {"type": "error", "error_code": "internal"},
            False,
            "refused by the hub (internal)",
        ),
    ],
)
async def test_which_answers_of_a_hub_end_the_retrying(
    answer: dict[str, str], final: bool, detail: str
) -> None:
    async def scripted(websocket: Any) -> None:
        await websocket.send(
            json.dumps({"sender": "SynapseHub", "type": "welcome", "protocol_version": 7})
        )
        async for _ in websocket:
            await websocket.send(json.dumps({"sender": "SynapseHub", "target": SENDER, **answer}))

    async with serve(scripted, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        result = await asyncio.to_thread(
            hub_sender, _record(), uri=f"ws://localhost:{port}", token=None, reply_timeout=3
        )

    assert (result.recorded, result.final, result.detail) == (False, final, detail)
    assert result.audit_seq is None


async def test_a_record_the_hub_would_not_parse_is_final_without_a_connection() -> None:
    broken = {**_record(), "direction": "sideways"}

    result = await asyncio.to_thread(hub_sender, broken, uri="ws://127.0.0.1:1", token=None)

    assert (result.recorded, result.final) == (False, True)
    assert result.detail.startswith("record refused before sending: ")


async def test_a_confirmation_without_a_sequence_number_is_still_a_confirmation() -> None:
    async def confirms(websocket: Any) -> None:
        await websocket.send(
            json.dumps({"sender": "SynapseHub", "type": "welcome", "protocol_version": 7})
        )
        async for _ in websocket:
            await websocket.send(
                json.dumps(
                    {"sender": "SynapseHub", "target": SENDER, "type": "native_message_recorded"}
                )
            )

    async with serve(confirms, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        result = await asyncio.to_thread(
            hub_sender, _record(), uri=f"ws://localhost:{port}", token=None, reply_timeout=3
        )

    assert (result.recorded, result.final, result.audit_seq) == (True, False, None)


def test_input_that_cannot_be_read_or_is_not_a_call_never_fails_the_hook(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    base = ["native-hook", "claude", "--uri", "ws://127.0.0.1:1", *_dirs(tmp_path)]
    monkeypatch.setattr(sys, "stdin", _BrokenStdin())
    unreadable = main(base)
    monkeypatch.setattr(sys, "stdin", _Stdin(b"\xff not json"))
    not_json = main(base)
    monkeypatch.setattr(sys, "stdin", _Stdin(b'{"tool_name": "Read"}'))
    other_tool = main(base)
    captured = capsys.readouterr()

    assert (unreadable, not_json, other_tool) == (0, 0, 0)
    assert (captured.out, captured.err) == ("", "")
    assert _state(tmp_path) == {"unparsed": 1}


def test_the_seat_register_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seats = ["--register-dir", str(tmp_path / "seats")]
    assert main(["native-seat", "list", *seats]) == 0
    assert capsys.readouterr().out == ""
    _register(tmp_path)
    registered = capsys.readouterr().out
    assert f"registered {SESSION} as {SENDER}: {tmp_path / 'seats'}" in registered

    assert main(["native-seat", "list", *seats]) == 0
    first, second = capsys.readouterr().out.splitlines()
    assert first.endswith(f" {SESSION} -> {SENDER}")
    assert second.endswith(f" {PEER_SESSION} -> {RECEIVER} name night-shift")

    codes = [
        main(["native-seat", "resolve", "--session-id", SESSION, *seats]),
        main(["native-seat", "resolve", "--name", "night-shift", *seats]),
        main(["native-seat", "resolve", "--name", "nobody", *seats]),
        main(["native-seat", "resolve", "--session-id", "unknown", *seats]),
        main(["native-seat", "register", "--session-id", SESSION, "--seat", "bad\x07seat", *seats]),
    ]
    lines = capsys.readouterr().out.splitlines()
    assert codes == [0, 0, 1, 1, 1]
    assert lines[:4] == [SENDER, RECEIVER, "not registered", "not registered"]
    assert lines[4].startswith("synapse native-seat: ")

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert main(["native-seat", "register", "--session-id", "s", "--seat", SENDER]) == 0
    assert main(["native-hook", "status"]) == 0
    assert (tmp_path / "data" / "synapse" / "native-seats").is_dir()
    assert capsys.readouterr().out.splitlines()[1:] == [
        "pending: 0",
        "recorded: 0",
        "failed: 0",
        "skipped: 0",
        "unparsed: 0",
    ]
