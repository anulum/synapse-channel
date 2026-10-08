# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse native-record` against real hubs
"""Run the real command line entry point against real hubs and read their stores."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from hub_e2e_helpers import collect_available, read_until_type, running_hub, send_json
from synapse_channel.cli import main
from synapse_channel.core import hub_connection
from synapse_channel.core.handlers.native_message import native_message_quota
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.native_message import MAX_NATIVE_TEXT_BYTES
from synapse_channel.core.persistence import EventStore

SEAT = "GROUP-A/claude-aaaa"
PEER = "GROUP-B/claude-bbbb"
TEXT = "Správa č. 1 — first line."
SENT_AT = "2026-10-05T09:50:22.130036Z"


def _argv(uri: str, *extra: str, text: str | None = TEXT) -> list[str]:
    argv = [
        "native-record",
        "--channel",
        "claude_cross_session",
        "--direction",
        "sent",
        "--phase",
        "outcome",
        "--outcome",
        "queued",
        "--seat",
        SEAT,
        "--peer-seat",
        PEER,
        "--sent-at",
        SENT_AT,
        "--uri",
        uri,
    ]
    if text is not None:
        argv += ["--text", text]
    return [*argv, *extra]


def _replace(argv: list[str], flag: str, value: str) -> list[str]:
    changed = list(argv)
    changed[changed.index(flag) + 1] = value
    return changed


def _events(store: EventStore) -> list[dict[str, Any]]:
    return [e.payload for e in store.read_all() if e.kind == EventKind.NATIVE_MESSAGE]


async def _run(argv: list[str]) -> int:
    return await asyncio.to_thread(main, argv)


async def test_sent_message_is_recorded_and_a_repeat_is_applied_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "cli.db")
    async with running_hub(SynapseHub(hub_id="cli-native", journal=store)) as (_, uri):
        first = await _run(_argv(uri, "--native-message-id", "msg-1", "--native-call-id", "c1"))
        first_out = capsys.readouterr().out
        second = await _run(_argv(uri, "--native-message-id", "msg-1", "--native-call-id", "c1"))
        second_out = capsys.readouterr().out

    digest = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert (first, second) == (0, 0)
    assert first_out == second_out
    assert f"sha256 {digest}, binding socket_name" in first_out
    (event,) = _events(store)
    assert first_out.startswith("recorded native message: seq ")
    assert (event["sender_seat"], event["recipient_seat"]) == (SEAT, PEER)
    assert (event["recorder"], event["direction"], event["outcome"]) == (SEAT, "sent", "queued")
    assert (event["text"], event["sent_at"]) == (TEXT, SENT_AT)
    assert (event["native_message_id"], event["native_call_id"]) == ("msg-1", "c1")
    store.close()


async def test_received_side_file_text_optional_fields_and_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "cli.db")
    source = tmp_path / "message.txt"
    source.write_bytes(TEXT.encode("utf-8"))
    argv = _replace(_argv("", text=None), "--direction", "received")
    argv = _replace(argv, "--phase", "attempt")
    argv = [a for i, a in enumerate(argv) if a != "--outcome" and argv[i - 1] != "--outcome"]
    async with running_hub(SynapseHub(hub_id="cli-native", journal=store)) as (_, uri):
        code = await _run(
            [
                *_replace(_replace(argv, "--uri", uri), "--sent-at", SENT_AT),
                "--text-file",
                str(source),
                "--sender-native-session",
                "session-a",
                "--recipient-native-session",
                "session-b",
                "--recipient-address",
                "uds:/run/cc/1.sock",
                "--execution-host",
                "workstation",
                "--source-msg-seq",
                "42",
                "--tool-result-json",
                '{"success": true}',
                "--idem-key",
                "explicit-key",
                "--json",
            ]
        )
    reply = json.loads(capsys.readouterr().out)

    assert code == 0
    assert reply["type"] == "native_message_recorded"
    assert reply["phase"] == "attempt"
    (event,) = _events(store)
    assert (event["sender_seat"], event["recipient_seat"]) == (PEER, SEAT)
    assert (event["recorder"], event["direction"], event["outcome"]) == (SEAT, "received", None)
    assert event["text"] == TEXT
    assert event["recipient_address"] == "uds:/run/cc/1.sock"
    assert (event["execution_host"], event["source_msg_seq"]) == ("workstation", 42)
    assert event["tool_result"] == {"success": True}
    store.close()


async def test_hash_only_by_flag_and_by_size(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "cli.db")
    large = "ž" * (MAX_NATIVE_TEXT_BYTES // 2 + 1)
    async with running_hub(SynapseHub(hub_id="cli-native", journal=store)) as (_, uri):
        by_flag = await _run(_argv(uri, "--hash-only"))
        flag_out = capsys.readouterr().out
        by_size = await _run(_argv(uri, "--native-call-id", "large", text=large))
        size_out = capsys.readouterr().out

    assert (by_flag, by_size) == (0, 0)
    assert "recorded by hash only" not in flag_out
    assert f"text is above {MAX_NATIVE_TEXT_BYTES} bytes; recorded by hash only" in size_out
    small, big = _events(store)
    assert (small["text"], small["text_bytes"]) == (None, len(TEXT.encode("utf-8")))
    assert (big["text"], big["text_bytes"]) == (None, len(large.encode("utf-8")))
    assert big["text_sha256"] == hashlib.sha256(large.encode("utf-8")).hexdigest()
    store.close()


async def test_refusals_exit_one_and_store_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "cli.db")
    hub = SynapseHub(hub_id="cli-native", journal=store)
    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"\xff\xfe not utf-8")
    async with running_hub(hub) as (_, uri):
        no_outcome = [a for a in _argv(uri) if a not in {"--outcome", "queued"}]
        results = {
            "schema": await _run(no_outcome),
            "file": await _run(_argv(uri, "--text-file", str(binary), text=None)),
            "json": await _run(_argv(uri, "--tool-result-json", "{not json")),
        }
        local_out = capsys.readouterr().out
        assert _events(store) == []

        assert await _run(_argv(uri, "--idem-key", "k")) == 0
        capsys.readouterr()
        results["conflict"] = await _run(_argv(uri, "--idem-key", "k", "--hash-only"))
        conflict_out = capsys.readouterr().out
        native_message_quota(hub).max_events = 1
        results["quota"] = await _run(_argv(uri, "--idem-key", "k2"))
        quota_out = capsys.readouterr().out
        results["quota_json"] = await _run(_argv(uri, "--idem-key", "k3", "--json"))
        quota_json = json.loads(capsys.readouterr().out)

    assert set(results.values()) == {1}
    assert local_out.count("record refused before sending:") == 3
    assert "record refused by the hub (idempotency_conflict)" in conflict_out
    assert "record refused by the hub (native_record_rate_limited)" in quota_out
    assert quota_json["error_code"] == "native_record_rate_limited"
    assert len(_events(store)) == 1
    store.close()


async def test_hub_without_a_journal_and_unreachable_hub_exit_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub(hub_id="cli-memory")) as (_, uri):
        no_journal = await _run(_argv(uri))
        no_journal_out = capsys.readouterr().out
    unreachable = await _run([*_argv(uri), "--ready-timeout", "0.5"])
    unreachable_out = capsys.readouterr().out

    assert (no_journal, unreachable) == (1, 1)
    assert "record refused by the hub (native_record_unavailable)" in no_journal_out
    assert "recorded native message" not in unreachable_out
    assert unreachable_out.strip()


async def test_older_hub_is_refused_locally_and_receives_no_record_frame(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub_connection, "WIRE_PROTOCOL_VERSION", 6)
    store = EventStore(tmp_path / "cli.db")
    async with running_hub(SynapseHub(hub_id="cli-old", journal=store)) as (_, uri):
        code = await _run(_argv(uri))
    out = capsys.readouterr().out

    assert code == 1
    assert "record path unavailable: hub does not advertise native message records" in out
    assert _events(store) == []
    store.close()


async def test_silent_hub_is_never_reported_as_stored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "cli.db")
    hub = SynapseHub(hub_id="cli-small", journal=store, max_msg_bytes=700)
    async with running_hub(hub) as (_, uri):
        code = await _run(_argv(uri, "--reply-timeout", "3", text="x" * 2_000))
    out = capsys.readouterr().out

    assert code == 1
    assert "The record is not confirmed as stored." in out
    assert "recorded native message" not in out
    assert _events(store) == []
    store.close()


async def test_peer_that_welcomes_and_then_stays_silent_times_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def welcome_only(websocket: Any) -> None:
        await websocket.send(
            json.dumps({"sender": "SynapseHub", "type": "welcome", "protocol_version": 7})
        )
        await websocket.wait_closed()

    async with serve(welcome_only, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        code = await _run(_argv(f"ws://localhost:{port}", "--reply-timeout", "0.6"))
    out = capsys.readouterr().out

    assert code == 1
    assert "the hub did not answer the record; it is not confirmed as stored" in out


async def test_seat_name_held_by_another_connection_is_retried_then_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "cli.db")
    async with running_hub(SynapseHub(hub_id="cli-native", journal=store)) as (_, uri):
        async with connect(uri) as holder:
            await read_until_type(holder, "welcome")
            await send_json(holder, sender=SEAT, type="heartbeat", payload="online")
            await collect_available(holder)
            started = time.monotonic()
            code = await _run(_argv(uri))
            elapsed = time.monotonic() - started
    out = capsys.readouterr().out

    assert code == 1
    assert "The record is not confirmed as stored." in out
    assert elapsed >= 4 * 0.3
    assert _events(store) == []
    store.close()


async def test_seat_name_released_during_the_retries_lets_the_record_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "cli.db")
    async with running_hub(SynapseHub(hub_id="cli-native", journal=store)) as (_, uri):
        holder = await connect(uri)
        await read_until_type(holder, "welcome")
        await send_json(holder, sender=SEAT, type="heartbeat", payload="online")
        await collect_available(holder)

        async def release_soon() -> None:
            await asyncio.sleep(0.5)
            await holder.close()

        releasing = asyncio.create_task(release_soon())
        code = await _run(_argv(uri))
        await releasing
    out = capsys.readouterr().out

    assert code == 0
    assert out.startswith("recorded native message: seq ")
    assert len(_events(store)) == 1
    store.close()
