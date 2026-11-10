# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse hub --message-auth-key-set` rotates keys for real agents
"""A real hub process rotates per-message keys for real agents without a restart."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import CliResult, free_port, isolated_hub, run_cli
from hub_e2e_helpers import Recorder
from key_set_helpers import instant, key_entry, replace_owner_only, write_key_set, write_secret
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.message_auth_key_watch import (
    MESSAGE_KEY_EXPIRED_KIND,
    MESSAGE_KEY_RETIRING_ADMISSION_KIND,
    MESSAGE_KEY_SET_APPLIED_KIND,
    MESSAGE_KEY_SET_REJECTED_KIND,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.token_sessions import WATCH_INTERVAL_SECONDS

_TWO_INTERVALS = 2 * WATCH_INTERVAL_SECONDS
_ANSWERS = (MessageType.CLAIM_GRANTED, MessageType.CLAIM_DENIED, MessageType.ERROR)


async def _claim(uri: str, sender: str, key_id: str, secret: str, task: str) -> str:
    """Claim ``task`` as ``sender`` signing with ``key_id``; return the hub's verdict.

    The verdict is ``granted`` or the per-message verification result of a refusal.
    """
    recorder = Recorder()
    agent = SynapseAgent(
        sender,
        recorder,
        uri=uri,
        verbose=False,
        per_message_auth_key_id=key_id,
        per_message_auth_secret=secret,
        machine_identity=False,
    )
    connection = asyncio.create_task(agent.connect())
    try:
        assert await agent.wait_until_ready(5.0)
        await agent.claim(task, worktree="/repo", paths=[task])
        answer = await recorder.wait_for(lambda m: m.get("type") in _ANSWERS)
    finally:
        agent.running = False
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
    if answer.get("type") == MessageType.CLAIM_GRANTED:
        return "granted"
    return str(answer.get("verification_result") or answer.get("type"))


async def _answer(recorder: Recorder, number: int) -> dict[str, Any]:
    """Wait for the ``number``-th claim answer (from 1) the agent received."""
    for _ in range(500):
        answers = [m for m in recorder.messages if m.get("type") in _ANSWERS]
        if len(answers) >= number:
            return answers[number - 1]
        await asyncio.sleep(0.01)
    raise TimeoutError(f"claim answer {number} did not arrive")


def _rows(db_path: Path, *kinds: str) -> list[tuple[str, dict[str, Any]]]:
    """Read the rows of ``kinds`` that a stopped hub left in its journal."""
    store = EventStore(db_path)
    try:
        return [(event.kind, dict(event.payload)) for event in store.iter_events(kinds=kinds)]
    finally:
        store.close()


def _hub_args(key_set: Path) -> list[str]:
    return ["--require-message-auth", "--message-auth-key-set", str(key_set)]


async def test_one_sender_moves_to_a_new_key_while_another_sender_continues(
    tmp_path: Path,
) -> None:
    """Old and new key for A overlap; A switches; the old key goes; nobody restarts."""
    now = time.time()
    for key_id in ("old-a", "new-a", "key-b"):
        write_secret(tmp_path, key_id, f"secret-{key_id}")
    key_set = tmp_path / "keys.json"
    old_a = key_entry("old-a", ["P/a"], not_before=instant(now - 3 * 3600))
    new_a = key_entry("new-a", ["P/a"], not_before=instant(now - 2 * 3600))
    key_b = key_entry("key-b", ["P/b"], not_before=instant(now - 3600))
    write_key_set(key_set, old_a, new_a, key_b)
    with isolated_hub(tmp_path, extra_args=_hub_args(key_set)) as hub:
        before = [
            await _claim(hub.uri, "P/a", "old-a", "secret-old-a", "T1"),
            await _claim(hub.uri, "P/b", "key-b", "secret-key-b", "T2"),
            await _claim(hub.uri, "P/a", "new-a", "secret-new-a", "T3"),
        ]
        write_key_set(key_set, new_a, key_b)
        after = [
            await _claim(hub.uri, "P/a", "old-a", "secret-old-a", "T4"),
            await _claim(hub.uri, "P/a", "new-a", "secret-new-a", "T5"),
            await _claim(hub.uri, "P/b", "key-b", "secret-key-b", "T6"),
        ]
        db_path = hub.db_path
    assert before == ["granted", "granted", "granted"]
    assert after == ["unknown_key", "granted", "granted"]
    retiring = [
        (row["key_id"], row["sender"], row["newest_key_id"])
        for _, row in _rows(db_path, MESSAGE_KEY_RETIRING_ADMISSION_KIND)
    ]
    assert retiring == [("old-a", "P/a", "new-a")]
    applied = [row["key_ids"] for _, row in _rows(db_path, MESSAGE_KEY_SET_APPLIED_KIND)]
    assert applied == [["old-a", "new-a", "key-b"], ["new-a", "key-b"]]


async def test_replacing_only_a_secret_file_rotates_the_key_in_a_running_hub(
    tmp_path: Path,
) -> None:
    """The set file's bytes never change; secret files alone rotate, fail and recover."""
    write_secret(tmp_path, "key-a", "first-secret")
    key_set = tmp_path / "keys.json"
    write_key_set(key_set, key_entry("key-a", ["P/a"]))
    manifest = key_set.read_bytes()
    with isolated_hub(tmp_path, extra_args=_hub_args(key_set)) as hub:
        first = await _claim(hub.uri, "P/a", "key-a", "first-secret", "T1")
        write_secret(tmp_path, "key-a", "second-secret")
        rotated = [
            await _claim(hub.uri, "P/a", "key-a", "first-secret", "T2"),
            await _claim(hub.uri, "P/a", "key-a", "second-secret", "T3"),
        ]
        replace_owner_only(tmp_path / "key-a.key", "\n")  # empty: not a usable secret
        time.sleep(_TWO_INTERVALS + 0.5)
        kept = await _claim(hub.uri, "P/a", "key-a", "second-secret", "T4")
        write_secret(tmp_path, "key-a", "third-secret")
        repaired = await _claim(hub.uri, "P/a", "key-a", "third-secret", "T5")
        db_path = hub.db_path
    assert first == "granted"
    assert rotated == ["bad_authentication", "granted"]
    assert kept == "granted" and repaired == "granted"
    assert key_set.read_bytes() == manifest
    kinds = [
        kind
        for kind, _ in _rows(db_path, MESSAGE_KEY_SET_APPLIED_KIND, MESSAGE_KEY_SET_REJECTED_KIND)
    ]
    assert kinds == [
        MESSAGE_KEY_SET_APPLIED_KIND,
        MESSAGE_KEY_SET_APPLIED_KIND,
        MESSAGE_KEY_SET_REJECTED_KIND,
        MESSAGE_KEY_SET_APPLIED_KIND,
    ]
    rows = json.dumps(_rows(db_path, MESSAGE_KEY_SET_REJECTED_KIND))
    assert "key-a.key" in rows and "second-secret" not in rows


async def test_a_key_that_expires_while_the_hub_serves_is_refused_and_journaled_once(
    tmp_path: Path,
) -> None:
    write_secret(tmp_path, "key-b", "secret-b")
    key_set = tmp_path / "keys.json"
    write_key_set(key_set, key_entry("key-b", ["P/b"]))
    with isolated_hub(tmp_path, extra_args=_hub_args(key_set)) as hub:
        end = float(int(time.time()) + 4)
        write_key_set(key_set, key_entry("key-b", ["P/b"], not_after=instant(end)))
        valid = await _claim(hub.uri, "P/b", "key-b", "secret-b", "T1")
        time.sleep(max(0.0, end + _TWO_INTERVALS + 0.5 - time.time()))
        expired = await _claim(hub.uri, "P/b", "key-b", "secret-b", "T2")
        db_path = hub.db_path
    assert (valid, expired) == ("granted", "expired")
    rows = [row for _, row in _rows(db_path, MESSAGE_KEY_EXPIRED_KIND)]
    assert rows == [{"key_id": "key-b", "not_after": end, "observed": "watcher"}]


async def test_a_captured_frame_stays_refused_after_its_key_is_removed_and_restored(
    tmp_path: Path,
) -> None:
    write_secret(tmp_path, "key-a", "secret-a")
    key_set = tmp_path / "keys.json"
    write_key_set(key_set, key_entry("key-a", ["P/a"]))
    key = MessageAuthKey(key_id="key-a", secret=b"secret-a", senders=frozenset({"P/a"}))
    frame = sign_frame(
        {
            "sender": "P/a",
            "type": MessageType.CLAIM,
            "target": "System",
            "payload": "",
            "task_id": "T-replay",
            "worktree": "/repo",
            "paths": ["T-replay"],
            "idem_key": "captured",
        },
        key=key,
        nonce="captured-nonce",
        sequence=1,
    )
    with isolated_hub(tmp_path, extra_args=_hub_args(key_set)) as hub:
        recorder = Recorder()
        agent = SynapseAgent("P/a", recorder, uri=hub.uri, verbose=False, machine_identity=False)
        connection = asyncio.create_task(agent.connect())
        verdicts: list[str] = []
        try:
            assert await agent.wait_until_ready(5.0)
            assert agent.connection is not None
            write_secret(tmp_path, "other", "secret-other")
            sets = (
                [key_entry("key-a", ["P/a"])],
                [key_entry("key-a", ["P/a"])],
                [key_entry("other", ["P/x"])],
                [key_entry("key-a", ["P/a"])],
            )
            for number, entries in enumerate(sets, start=1):
                write_key_set(key_set, *entries)
                await agent.connection.send(json.dumps(frame))
                answer = await _answer(recorder, number)
                verdicts.append(str(answer.get("verification_result") or answer["type"]))
        finally:
            agent.running = False
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
    assert verdicts == [MessageType.CLAIM_GRANTED, "replayed", "unknown_key", "replayed"]


def _refused_start(tmp_path: Path, *extra: str) -> CliResult:
    """Start ``synapse hub`` with ``extra``; prove no listener, store or pins remain."""
    port = free_port()
    db_path = tmp_path / "refused.db"
    pins = tmp_path / "refused-pins.json"
    result = run_cli(
        "hub", "--port", str(port), "--db", str(db_path), "--identity-pins", str(pins), *extra
    )
    assert result.returncode != 0, result.output
    assert not db_path.exists() and not pins.exists()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))
    return result


def test_unusable_key_sets_refuse_startup(tmp_path: Path) -> None:
    key_set = tmp_path / "keys.json"
    missing = _refused_start(tmp_path, "--message-auth-key-set", str(key_set))
    assert "keys.json" in missing.stderr
    replace_owner_only(key_set, '{"version": 1}')
    malformed = _refused_start(tmp_path, "--message-auth-key-set", str(key_set))
    assert "exactly version and keys" in malformed.stderr
    write_key_set(key_set, key_entry("key-a", ["P/a"]))
    replace_owner_only(tmp_path / "key-a.key", "\n")
    empty_secret = _refused_start(tmp_path, "--message-auth-key-set", str(key_set))
    assert "key key-a" in empty_secret.stderr and "is empty" in empty_secret.stderr
    write_secret(tmp_path, "key-a", "secret-a")
    combined = _refused_start(
        tmp_path, "--message-auth-key-set", str(key_set), "--message-auth-key", "k:s:P/a"
    )
    assert "cannot be combined with --message-auth-key" in combined.stderr
    key_file = tmp_path / "static.keys"
    replace_owner_only(key_file, "k:s:P/a\n")
    combined = _refused_start(
        tmp_path, "--message-auth-key-set", str(key_set), "--message-auth-key-file", str(key_file)
    )
    assert "cannot be combined with --message-auth-key" in combined.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits define a group-readable file")
def test_a_group_readable_key_set_refuses_startup(tmp_path: Path) -> None:
    write_secret(tmp_path, "key-a", "secret-a")
    key_set = tmp_path / "keys.json"
    write_key_set(key_set, key_entry("key-a", ["P/a"]))
    key_set.chmod(0o640)
    result = _refused_start(tmp_path, "--message-auth-key-set", str(key_set))
    assert "keys.json" in result.stderr


def test_the_hardening_presets_count_a_key_set_as_message_authentication(tmp_path: Path) -> None:
    write_secret(tmp_path, "key-a", "secret-a")
    key_set = tmp_path / "keys.json"
    write_key_set(key_set, key_entry("key-a", ["P/a"]))
    line = "--message-auth-key, --message-auth-key-file or --message-auth-key-set"
    without_set = _refused_start(tmp_path, "--secure")
    with_set = _refused_start(tmp_path, "--secure", "--message-auth-key-set", str(key_set))
    assert line in without_set.stderr and line not in with_set.stderr
    paranoid = ("--paranoid", "--token", "connect-secret")
    without_set = _refused_start(tmp_path, *paranoid)
    with_set = _refused_start(tmp_path, *paranoid, "--message-auth-key-set", str(key_set))
    assert "requires --message-auth-key or --message-auth-key-set" in without_set.stderr
    assert "requires --require-message-auth" in with_set.stderr
