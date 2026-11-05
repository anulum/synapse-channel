# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sessions that outlive their connect token
"""Exercise what a real hub does when an admitted session's token expires."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel import cli
from synapse_channel.cli_hub_transport import _connect_authenticator
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.token_audit import TokenAudit
from synapse_channel.core.token_sessions import (
    EXPIRY_CLOSE_CODE,
    EXPIRY_CLOSE_REASON,
    RETIRED_CLOSE_REASON,
    TokenSessions,
    token_sessions_for,
)
from synapse_channel.core.token_set import TokenSetAuthenticator, connect_token_digest


def _instant(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, *entries: tuple[Any, ...]) -> tuple[Path, list[float]]:
    """Write a set of (id, secret, seconds[, agents]); return each whole-second end."""
    tokens: list[dict[str, Any]] = []
    ends: list[float] = []
    for token_id, secret, seconds, *agents in entries:
        entry: dict[str, Any] = {"id": token_id, "sha256": connect_token_digest(secret)}
        if agents:
            entry["agents"] = agents[0]
        if seconds is not None:
            end = float(int(time.time()) + seconds)
            entry["not_after"] = _instant(end)
            ends.append(end)
        tokens.append(entry)
    staging = path.with_name(path.name + ".new")
    staging.write_text(json.dumps({"version": 1, "tokens": tokens}))
    os.chmod(staging, 0o600)
    os.replace(staging, path)
    return path, ends


def _expired_rows(journal: EventStore) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in journal.read_all()
        if event.kind == EventKind.CONNECT_TOKEN_SESSION_ENDED
    ]


async def _until(condition: Any, seconds: float = 8.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.05)


async def test_by_default_a_session_outlives_its_token_and_is_recorded(tmp_path: Path) -> None:
    path, (end,) = _write(tmp_path / "tokens.json", ("short", "s-short", 2))
    journal = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=journal, authenticator=TokenSetAuthenticator(path))
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as websocket:
                await send_json(websocket, sender="A", type="heartbeat", token="s-short")
                await read_until_type(websocket, "welcome")
                await _until(lambda: time.time() >= end + 0.3)
                await asyncio.wait_for(await websocket.ping(), timeout=5)
            await _until(lambda: bool(_expired_rows(journal)))
            async with connect(uri) as websocket:
                await send_json(websocket, sender="B", type="heartbeat", token="s-short")
                denied = await read_until_type(websocket, "auth_denied")
        rows = _expired_rows(journal)
    finally:
        journal.close()
    assert rows == [{"token_id": "short", "agent": "A", "closed": False, "reason": "expired"}]
    assert "has expired" in denied["payload"]


async def test_the_hub_can_close_a_session_when_its_token_expires(tmp_path: Path) -> None:
    path, (end,) = _write(tmp_path / "tokens.json", ("short", "s-short", 2))
    journal = EventStore(tmp_path / "hub.db")
    try:
        authenticator = TokenSetAuthenticator(path, expiry_closes_sessions=True)
        hub = SynapseHub(journal=journal, authenticator=authenticator)
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as websocket:
                await send_json(websocket, sender="A", type="heartbeat", token="s-short")
                await read_until_type(websocket, "welcome")
                await asyncio.wait_for(websocket.wait_closed(), timeout=10)
                closed_at = time.time()
                code, reason = websocket.close_code, websocket.close_reason
            await _until(lambda: bool(_expired_rows(journal)))
        rows = _expired_rows(journal)
    finally:
        journal.close()
    assert (code, reason) == (EXPIRY_CLOSE_CODE, EXPIRY_CLOSE_REASON)
    assert closed_at >= end
    assert rows == [{"token_id": "short", "agent": "A", "closed": True, "reason": "expired"}]


async def test_a_session_that_ends_first_leaves_no_timer_and_no_row(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path, (end,) = _write(
        tmp_path / "tokens.json", ("short", "s-short", 2), ("open", "s-open", None)
    )
    journal = EventStore(tmp_path / "hub.db")
    try:
        authenticator = TokenSetAuthenticator(path, expiry_closes_sessions=True)
        hub = SynapseHub(journal=journal, authenticator=authenticator)
        sessions = hub._ingress._token_sessions
        assert sessions is not None
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as websocket:
                await send_json(websocket, sender="A", type="heartbeat", token="s-short")
                await read_until_type(websocket, "welcome")
                assert len(sessions) == 1
            async with connect(uri) as websocket:
                await send_json(websocket, sender="B", type="heartbeat", token="s-open")
                await read_until_type(websocket, "welcome")
                assert len(sessions) == 1
            await _until(lambda: len(sessions) == 0)
            await _until(lambda: time.time() >= end + 0.5)
        rows = _expired_rows(journal)
    finally:
        journal.close()
    assert rows == []
    # A timer left running would fire into a forgotten session and be logged.
    assert not [record for record in caplog.records if record.name == "asyncio"]


async def test_closing_without_a_journal_still_closes(tmp_path: Path) -> None:
    path, _ = _write(tmp_path / "tokens.json", ("short", "s-short", 2))
    authenticator = TokenSetAuthenticator(path, expiry_closes_sessions=True)
    async with running_hub(SynapseHub(authenticator=authenticator)) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="A", type="heartbeat", token="s-short")
            await read_until_type(websocket, "welcome")
            await asyncio.wait_for(websocket.wait_closed(), timeout=10)
            assert websocket.close_code == EXPIRY_CLOSE_CODE


def test_a_tracker_exists_only_where_it_has_work(tmp_path: Path) -> None:
    path, _ = _write(tmp_path / "tokens.json", ("a", "s-a", None))
    journal = EventStore(tmp_path / "hub.db")
    try:
        audit = TokenAudit(journal)
        quiet = TokenSetAuthenticator(path)
        closing = TokenSetAuthenticator(path, expiry_closes_sessions=True)
        assert token_sessions_for(None, audit) is None
        assert token_sessions_for(TokenAuthenticator(["s"]), audit) is None
        assert token_sessions_for(quiet, None) is None
        assert isinstance(token_sessions_for(quiet, audit), TokenSessions)
        assert isinstance(token_sessions_for(closing, None), TokenSessions)
    finally:
        journal.close()


def test_the_cli_flag_reaches_the_authenticator_and_needs_a_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = _write(tmp_path / "tokens.json", ("a", "s-a", None))
    built = _connect_authenticator(
        argparse.Namespace(token_set=str(path), token=None, token_expiry_closes_sessions=True)
    )
    assert isinstance(built, TokenSetAuthenticator) and built.expiry_closes_sessions
    plain = _connect_authenticator(argparse.Namespace(token_set=str(path), token=None))
    assert isinstance(plain, TokenSetAuthenticator) and not plain.expiry_closes_sessions
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    assert cli.main(["hub", "--token-expiry-closes-sessions"]) == 2
    assert "--token-expiry-closes-sessions requires --token-set" in capsys.readouterr().err


async def _session(uri: str, agent: str, secret: str) -> Any:
    websocket = await connect(uri)
    await send_json(websocket, sender=agent, type="heartbeat", token=secret)
    await read_until_type(websocket, "welcome")
    return websocket


async def test_closing_mode_ends_sessions_of_a_retired_token_without_a_new_connection(
    tmp_path: Path,
) -> None:
    # The retired token has no end of validity, so only the retire can end its session.
    path, _ = _write(tmp_path / "tokens.json", ("old", "s-old", None), ("new", "s-new", 600))
    journal = EventStore(tmp_path / "hub.db")
    try:
        authenticator = TokenSetAuthenticator(path, expiry_closes_sessions=True)
        hub = SynapseHub(journal=journal, authenticator=authenticator)
        async with running_hub(hub) as (_, uri):
            retired = await _session(uri, "A", "s-old")
            kept = await _session(uri, "B", "s-new")
            try:
                _write(path, ("new", "s-new", 600))
                await asyncio.wait_for(retired.wait_closed(), timeout=10)
                assert (retired.close_code, retired.close_reason) == (
                    EXPIRY_CLOSE_CODE,
                    RETIRED_CLOSE_REASON,
                )
                await asyncio.wait_for(await kept.ping(), timeout=5)
            finally:
                await retired.close()
                await kept.close()
            await _until(lambda: bool(_expired_rows(journal)))
        rows = _expired_rows(journal)
    finally:
        journal.close()
    assert rows == [{"token_id": "old", "agent": "A", "closed": True, "reason": "retired"}]


async def test_closing_mode_follows_a_moved_end_and_an_unbound_agent(tmp_path: Path) -> None:
    path, _ = _write(
        tmp_path / "tokens.json", ("move", "s-move", 600), ("seat", "s-seat", 600, ["A", "B"])
    )
    authenticator = TokenSetAuthenticator(path, expiry_closes_sessions=True)
    async with running_hub(SynapseHub(authenticator=authenticator)) as (_, uri):
        moved = await _session(uri, "M", "s-move")
        unbound = await _session(uri, "B", "s-seat")
        bound = await _session(uri, "A", "s-seat")
        try:
            _, (end, _seat_end) = _write(
                path, ("move", "s-move", 2), ("seat", "s-seat", 600, ["A"])
            )
            await asyncio.wait_for(unbound.wait_closed(), timeout=10)
            assert unbound.close_reason == RETIRED_CLOSE_REASON
            await asyncio.wait_for(moved.wait_closed(), timeout=10)
            assert moved.close_reason == EXPIRY_CLOSE_REASON
            assert time.time() >= end
            await asyncio.wait_for(await bound.ping(), timeout=5)
        finally:
            for websocket in (moved, unbound, bound):
                await websocket.close()


async def test_a_later_end_and_a_rejected_file_keep_the_session(tmp_path: Path) -> None:
    path, _ = _write(tmp_path / "tokens.json", ("tok", "s-tok", 2))
    authenticator = TokenSetAuthenticator(path, expiry_closes_sessions=True)
    async with running_hub(SynapseHub(authenticator=authenticator)) as (_, uri):
        websocket = await _session(uri, "A", "s-tok")
        try:
            _, (end,) = _write(path, ("tok", "s-tok", 600))
            await _until(lambda: authenticator.entries[0].not_after == end)
            path.write_text("{broken")
            await _until(lambda: authenticator.stale_reason is not None)
            await asyncio.sleep(3)
            await asyncio.wait_for(await websocket.ping(), timeout=5)
        finally:
            await websocket.close()


async def test_by_default_a_retired_token_keeps_its_session_and_is_recorded(
    tmp_path: Path,
) -> None:
    path, _ = _write(tmp_path / "tokens.json", ("old", "s-old", None), ("new", "s-new", None))
    journal = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=journal, authenticator=TokenSetAuthenticator(path))
        async with running_hub(hub) as (_, uri):
            websocket = await _session(uri, "A", "s-old")
            try:
                _write(path, ("new", "s-new", None))
                kept = await _session(uri, "B", "s-new")
                await kept.close()
                await asyncio.wait_for(await websocket.ping(), timeout=5)
            finally:
                await websocket.close()
            await _until(lambda: bool(_expired_rows(journal)))
        rows = _expired_rows(journal)
    finally:
        journal.close()
    assert rows == [{"token_id": "old", "agent": "A", "closed": False, "reason": "retired"}]


async def test_a_failed_close_and_a_failed_check_are_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path, _ = _write(tmp_path / "tokens.json", ("gone", "s-gone", 600))
    authenticator = TokenSetAuthenticator(path, expiry_closes_sessions=True)

    async def failing_close(_websocket: Any, _reason: str) -> None:
        raise OSError("socket already torn down")

    class FailingObserver:
        armed = False

        def set_applied(self, _path: Path, _ids: tuple[str, ...]) -> None:
            if self.armed:
                raise RuntimeError("observer failed")

        def set_rejected(self, _path: Path, _reason: str) -> None:
            """Accept every rejection."""

    sessions = TokenSessions(authenticator, None, failing_close)
    authenticator.observe(sessions)
    observer = FailingObserver()
    authenticator.observe(observer)
    socket = object()
    sessions.admitted(socket, authenticator.entries[0], "A")
    watcher = asyncio.create_task(sessions.watch(0.01))
    try:
        observer.armed = True
        _write(path, ("other", "s-other", 600))
        await _until(lambda: "cannot check the token-set file" in caplog.text)
        await _until(lambda: "cannot close a connect-token session" in caplog.text)
        # A set applied while a close is still pending leaves that session alone.
        sessions.set_applied(path, ("other",))
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    sessions.closed(socket)
    assert len(sessions) == 0
    assert sessions.watches
