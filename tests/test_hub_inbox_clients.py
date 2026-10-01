# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real transport durable inbox tests
"""Exercise journal inbox reads through admitted WebSocket connections."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.role_grants import RoleGrants
from synapse_channel.hub_inbox import read_hub_inbox
from synapse_channel.hub_inbox_cursor import (
    HubInboxCursor,
    hub_inbox_cursor_path,
    load_hub_inbox_cursor,
    save_hub_inbox_cursor,
)
from synapse_channel.mcp.bridge import SynapseHubBridge
from test_cli_lock_legacy_runtime import legacy_release_profile as legacy_release_profile
from test_hub_inbox import fence


async def test_cli_reader_persists_independent_cursor_and_does_not_replay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            publisher = await connect_agent("P/publisher", uri)
            try:
                for number in range(3):
                    await publisher.agent.chat(f"message {number}", target="P/reader")
                await fence(publisher)
                home = tmp_path / "reader"
                for expected in [2, 1, 0]:
                    assert (
                        await read_hub_inbox(
                            uri=uri, identity="P/reader", home=home, limit=2, timeout=2
                        )
                        == 0
                    )
                    page = json.loads(capsys.readouterr().out)
                    assert page["available"] is True
                    assert len(page["messages"]) == expected
                    assert page["source"] == uri
                path = hub_inbox_cursor_path(home, uri, "P/reader")
                cursor = load_hub_inbox_cursor(path)
                assert cursor.hub_id and cursor.seq > 0
                assert path.stat().st_mode & 0o777 == 0o600
                assert not (home / "inbox-cursor").exists()
                save_hub_inbox_cursor(path, HubInboxCursor("foreign-hub", cursor.seq))
                before = path.read_bytes()
                assert await read_hub_inbox(uri=uri, identity="P/reader", home=home, timeout=2) == 1
                assert json.loads(capsys.readouterr().out)["available"] is False
                assert path.read_bytes() == before
            finally:
                await close_agents(publisher)
    finally:
        journal.close()


async def test_mcp_reads_remote_hub_using_existing_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SYN_INBOX_SOURCE", "hub")
    monkeypatch.setenv("SYN_HOME", str(tmp_path / "reader"))
    journal = EventStore(tmp_path / "hub.db")
    try:
        grants = RoleGrants({"P/reviewer": frozenset({"P/reader"})})
        async with running_hub(SynapseHub(journal=journal, role_grants=grants)) as (_, uri):
            bridge = SynapseHubBridge(
                uri=uri, name="P/reader", request_timeout=2, roles=("P/reviewer",)
            )
            task = asyncio.create_task(bridge.agent.connect())
            publisher = await connect_agent("P/publisher", uri)
            try:
                assert await bridge.agent.wait_until_ready(2)
                await publisher.agent.chat("remote MCP body", target="P/reader")
                await publisher.agent.chat("admitted role body", target="P/reviewer")
                await fence(publisher)
                page = json.loads(await bridge.inbox())
                assert page["available"] is True
                assert [m["payload"] for m in page["messages"]] == [
                    "remote MCP body",
                    "admitted role body",
                ]
                assert json.loads(await bridge.inbox())["messages"] == []
                assert await bridge.agent.wait_until_ready(0.1)
            finally:
                await close_agents(publisher)
                bridge.agent.running = False
                if bridge.agent.connection is not None:
                    await bridge.agent.connection.close()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    finally:
        journal.close()


async def test_remote_cli_options_use_real_hub_and_leave_stale_feed_untouched(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from synapse_channel import ergonomics

    journal = EventStore(tmp_path / "hub.db")
    home = tmp_path / "reader"
    home.mkdir()
    stale = home / "feed.ndjson"
    stale.write_text("old local data\n")
    env = {"SYN_HOME": str(home), "SYN_IDENTITY": "P/reader", "SYN_PROJECT": "P"}
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            publisher = await connect_agent("P/publisher", uri)
            try:
                await publisher.agent.chat("actual remote body", target="P/reader")
                await fence(publisher)
                assert (
                    await asyncio.to_thread(
                        ergonomics.main,
                        ["inbox", "--source=hub", f"--uri={uri}", "--limit=1"],
                        env=env,
                        cwd_basename="P",
                    )
                    == 0
                )
                assert (
                    json.loads(capsys.readouterr().out)["messages"][0]["payload"]
                    == "actual remote body"
                )
                assert stale.read_text() == "old local data\n"
            finally:
                await close_agents(publisher)
    finally:
        journal.close()


async def test_unreachable_hub_and_invalid_read_bounds_do_not_create_cursor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bounds_list: list[dict[str, Any]] = [
        {"timeout": 0.03},
        {"limit": True},
        {"timeout": float("nan")},
    ]
    for bounds in bounds_list:
        assert (
            await read_hub_inbox(
                uri="ws://127.0.0.1:0", identity="P/reader", home=tmp_path, **bounds
            )
            == 1
        )
        assert json.loads(capsys.readouterr().out)["available"] is False
    assert not (tmp_path / "hub-inbox-cursor").exists()


async def test_legacy_published_hub_is_unavailable_without_consuming_local_feed(
    tmp_path: Path,
    legacy_release_profile: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import sys

    from cli_e2e_helpers import free_port

    port = free_port()
    uri = f"ws://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PYTHONPATH": str(legacy_release_profile),
        "SYN_HOME": str(tmp_path / "legacy-home"),
    }
    with (tmp_path / "legacy.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-m",
            "synapse_channel.cli",
            "hub",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--db",
            str(tmp_path / "legacy.db"),
            env=env,
            stdout=log,
            stderr=log,
            cwd=tmp_path,
        )
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                assert process.returncode is None
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.02)
                    continue
                writer.close()
                await writer.wait_closed()
                break
            home = tmp_path / "reader"
            home.mkdir()
            feed = home / "feed.ndjson"
            feed.write_text("unrelated local data\n")
            assert await read_hub_inbox(uri=uri, identity="P/reader", home=home, timeout=2) == 1
            assert json.loads(capsys.readouterr().out)["available"] is False
            assert feed.read_text() == "unrelated local data\n"
            assert not (home / "hub-inbox-cursor").exists()
            monkeypatch.setenv("SYN_INBOX_SOURCE", "hub")
            monkeypatch.setenv("SYN_HOME", str(home))
            bridge = SynapseHubBridge(uri=uri, name="P/mcp", request_timeout=0.1)
            task = asyncio.create_task(bridge.agent.connect())
            try:
                assert await bridge.agent.wait_until_ready(2)
                assert json.loads(await bridge.inbox())["available"] is False
                assert not (home / "hub-inbox-cursor").exists()
            finally:
                bridge.agent.running = False
                if bridge.agent.connection is not None:
                    await bridge.agent.connection.close()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        finally:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


@pytest.mark.parametrize("source", ["unknown", "hub"])
def test_invalid_mcp_source_configuration_refuses_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    monkeypatch.setenv("SYN_INBOX_SOURCE", source)
    with pytest.raises(ValueError):
        SynapseHubBridge(name="P/reader", inbox_feed=tmp_path / "feed")
