# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub tests for the async client lifecycle

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
from collections.abc import Coroutine
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel.client.agent import (
    DEFAULT_HUB_URI,
    HUB_URI_ENV_VAR,
    MINIMUM_HEARTBEAT_INTERVAL,
    SynapseAgent,
    default_hub_uri,
)
from synapse_channel.client.agent_lifecycle import _received_close


def test_defaults_and_heartbeat_clamp() -> None:
    agent = SynapseAgent("A")
    assert agent.uri == DEFAULT_HUB_URI
    assert agent.heartbeat_interval == 20.0
    agent_fast = SynapseAgent("B", heartbeat_interval=1.0)
    assert agent_fast.heartbeat_interval == MINIMUM_HEARTBEAT_INTERVAL


def test_default_hub_uri_falls_back_to_loopback_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With SYNAPSE_URI unset the default resolves to the literal loopback constant.
    monkeypatch.delenv(HUB_URI_ENV_VAR, raising=False)
    assert default_hub_uri() == DEFAULT_HUB_URI


def test_default_hub_uri_honours_the_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A set SYNAPSE_URI redirects the whole CLI to a non-default hub.
    monkeypatch.setenv(HUB_URI_ENV_VAR, "ws://coordinator.internal:9931")
    assert default_hub_uri() == "ws://coordinator.internal:9931"


def test_default_hub_uri_trims_and_ignores_a_blank_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Whitespace-only is treated as unset, so a stray blank never yields "".
    monkeypatch.setenv(HUB_URI_ENV_VAR, "   ")
    assert default_hub_uri() == DEFAULT_HUB_URI
    monkeypatch.setenv(HUB_URI_ENV_VAR, "  ws://trimmed:8000  ")
    assert default_hub_uri() == "ws://trimmed:8000"


def test_default_hub_uri_leaves_the_literal_constant_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The override is a client-connection default only; the bind-port constant
    # stays the fixed loopback literal so it can still describe where a hub binds.
    monkeypatch.setenv(HUB_URI_ENV_VAR, "ws://elsewhere:1")
    assert DEFAULT_HUB_URI == "ws://localhost:8876"


def test_agent_starts_with_no_recorded_close() -> None:
    agent = SynapseAgent("A")
    assert agent.last_close_code is None
    assert agent.last_close_reason == ""


def test_received_close_reads_the_hub_close_frame() -> None:
    exc = ConnectionClosedError(Close(4013, "hub at capacity"), None)

    assert _received_close(exc) == (4013, "hub at capacity")


def test_received_close_is_empty_when_this_side_closed() -> None:
    # No received Close frame (rcvd is None) means there is no hub-supplied code.
    exc = ConnectionClosedError(None, Close(1000, "bye"))

    assert _received_close(exc) == (None, "")


def test_ping_keepalive_defaults() -> None:
    agent = SynapseAgent("A")
    assert agent.ping_interval == 20.0
    assert agent.ping_timeout == 20.0


async def test_connect_accepts_custom_ping_keepalive_on_real_hub() -> None:
    async with running_hub() as (_, uri):
        handle = await connect_agent(
            "A",
            uri,
            wait_presence=False,
            takeover=False,
        )
        try:
            agent = SynapseAgent(
                "B",
                uri=uri,
                ping_interval=7.0,
                ping_timeout=9.0,
                verbose=False,
            )
            task = asyncio.create_task(agent.connect())
            try:
                assert await agent.wait_until_ready(timeout=1.0) is True
                assert agent.ping_interval == 7.0
                assert agent.ping_timeout == 9.0
            finally:
                agent.running = False
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        finally:
            await close_agents(handle)


async def test_connect_registers_dispatches_and_filters_echo() -> None:
    received: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        received.append(data)

    async with running_hub() as (_, uri):
        agent_a = SynapseAgent("A", callback, uri=uri, heartbeat_interval=60.0, verbose=True)
        task_a = asyncio.create_task(agent_a.connect())
        try:
            assert await agent_a.wait_until_ready(timeout=1.0) is True
            handle_b = await connect_agent("B", uri, wait_presence=False)
            try:
                await agent_a.chat("mine", target="all")
                await handle_b.agent.chat("hi", target="A")
                for _ in range(50):
                    chat_payloads = [
                        message.get("payload")
                        for message in received
                        if message.get("type") == "chat"
                    ]
                    if "hi" in chat_payloads:
                        break
                    await asyncio.sleep(0.01)
                assert agent_a.hub_id != "unknown"
                assert agent_a.ready_event.is_set()
                assert "hi" in chat_payloads
                assert "mine" not in chat_payloads
            finally:
                await close_agents(handle_b)
        finally:
            agent_a.running = False
            task_a.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task_a


async def test_dispatch_rejects_malformed_json_without_callback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        received.append(data)

    agent = SynapseAgent("A", callback, verbose=True)
    await agent._dispatch("this-is-not-json")

    assert received == []
    assert "malformed JSON" in capsys.readouterr().out


async def test_connect_without_callback_still_processes_welcome() -> None:
    async with running_hub() as (_, uri):
        agent = SynapseAgent("A", None, uri=uri, heartbeat_interval=60.0, verbose=False)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=1.0) is True
            assert agent.hub_id != "unknown"
        finally:
            agent.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def test_quiet_dispatch_skips_lifecycle_prints(capsys: pytest.CaptureFixture[str]) -> None:
    agent = SynapseAgent("A", verbose=False)
    await agent._dispatch(json.dumps({"type": "welcome", "hub_id": "h"}))
    await agent._dispatch("not-json")

    out = capsys.readouterr().out
    assert "connected to Synapse" not in out
    assert "malformed JSON" not in out
    assert agent.hub_id == "h"


async def test_connect_stops_when_running_cleared_by_callback() -> None:
    seen: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        seen.append(data)
        if data.get("type") == "chat":
            agent.running = False

    async with running_hub() as (_, uri):
        agent = SynapseAgent("A", callback, uri=uri, heartbeat_interval=60.0, verbose=False)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=1.0) is True
            handle_b = await connect_agent("B", uri, wait_presence=False)
            try:
                await handle_b.agent.chat("1", target="A")
                await handle_b.agent.chat("2", target="A")
                for _ in range(50):
                    if not agent.running:
                        break
                    await asyncio.sleep(0.01)
            finally:
                await close_agents(handle_b)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert any(message.get("payload") == "1" for message in seen)


async def test_connect_handles_connection_refused(capsys: pytest.CaptureFixture[str]) -> None:
    agent = SynapseAgent("A", uri=f"ws://localhost:{_free_port()}", verbose=True)
    await agent.connect()
    assert "could not connect" in capsys.readouterr().out


async def test_connect_refused_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    agent = SynapseAgent("A", uri=f"ws://localhost:{_free_port()}", verbose=False)
    await agent.connect()
    assert capsys.readouterr().out == ""


async def test_connect_names_a_bare_oserror_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # asyncio surfaces a refusal as a bare OSError (its "Multiple exceptions"
    # wrapper) whose message — not its type — carries the ECONNREFUSED errno, so
    # it skips the ConnectionRefusedError clause. The `(errno, strerror)` form is
    # avoided on purpose: Python auto-promotes it to ConnectionRefusedError, which
    # would test the wrong branch. The verbose path must still name this one.
    from synapse_channel.client import agent_lifecycle

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise OSError(
            f"Multiple exceptions: Connect call failed [Errno {errno.ECONNREFUSED}] 127.0.0.1:1"
        )

    monkeypatch.setattr(agent_lifecycle, "connect", refuse)
    agent = SynapseAgent("A", uri="ws://localhost:1", verbose=True)
    await agent.connect()
    assert "could not connect" in capsys.readouterr().out


# --- refused-connection classification and verbose failure paths ---------------


def test_is_connection_refused_matches_each_refusal_shape() -> None:
    from synapse_channel.client.agent_lifecycle import _is_connection_refused

    assert _is_connection_refused(ConnectionRefusedError()) is True
    assert _is_connection_refused(OSError(errno.ECONNREFUSED, "refused")) is True
    multi = OSError(
        f"Multiple exceptions: Connect call failed [Errno {errno.ECONNREFUSED}] 127.0.0.1"
    )
    assert _is_connection_refused(multi) is True
    assert _is_connection_refused(OSError(errno.EHOSTUNREACH, "unreachable")) is False
    assert _is_connection_refused(OSError("Connect call failed, plain text")) is False


async def test_verbose_agent_reports_a_refused_connection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A verbose agent prints the is-the-hub-running hint on a dead port."""
    agent = SynapseAgent(
        "LONELY", _noop_handler, uri=f"ws://127.0.0.1:{_free_port()}", verbose=True
    )
    await agent.connect()
    out = capsys.readouterr().out
    assert "could not connect. Is the hub running?" in out


async def test_verbose_agent_reports_a_non_refusal_os_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An OSError that is not a refusal prints the connection-lost line."""

    def unreachable(*_args: object, **_kwargs: object) -> object:
        raise OSError(errno.EHOSTUNREACH, "No route to host")

    monkeypatch.setattr("synapse_channel.client.agent_lifecycle.connect", unreachable)
    agent = SynapseAgent("LONELY", _noop_handler, uri="ws://10.255.255.1:9", verbose=True)
    await agent.connect()
    assert "Connection lost:" in capsys.readouterr().out


async def test_verbose_agent_reports_a_closed_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ConnectionClosedError records the close code and prints the loss."""

    def closed(*_args: object, **_kwargs: object) -> object:
        raise ConnectionClosedError(Close(4008, "policy"), None)

    monkeypatch.setattr("synapse_channel.client.agent_lifecycle.connect", closed)
    agent = SynapseAgent("LONELY", _noop_handler, uri="ws://unused", verbose=True)
    await agent.connect()
    assert agent.last_close_code == 4008
    assert agent.last_close_reason == "policy"
    assert "Connection lost:" in capsys.readouterr().out


def test_start_reports_a_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The blocking entry point catches Ctrl-C and reports the shutdown."""
    agent = SynapseAgent("SCRIPT", _noop_handler, uri="ws://unused", verbose=True)

    def interrupt(_coro: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.client.agent_lifecycle.asyncio.run", _closing(interrupt))
    agent.start()
    assert "Shutting down." in capsys.readouterr().out


def _closing(runner: object) -> object:
    """Wrap a fake asyncio.run so the un-awaited coroutine is closed first."""

    def run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]
        runner(coro)  # type: ignore[operator]

    return run


async def _noop_handler(_data: object) -> None:
    return None


def test_is_connection_refused_reads_a_manually_set_errno() -> None:
    """A bare OSError with the refusal errno set after construction still counts.

    Constructing ``OSError(ECONNREFUSED, …)`` promotes to ConnectionRefusedError,
    so the errno attribute branch is reachable only through an instance whose
    errno was assigned separately (as wrapped transport errors do).
    """
    from synapse_channel.client.agent_lifecycle import _is_connection_refused

    exc = OSError("wrapped transport failure")
    exc.errno = errno.ECONNREFUSED
    assert _is_connection_refused(exc) is True


async def test_quiet_agent_swallows_a_non_refusal_os_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """verbose=False silences the connection-lost line for a non-refusal OSError."""

    def unreachable(*_args: object, **_kwargs: object) -> object:
        raise OSError(errno.EHOSTUNREACH, "No route to host")

    monkeypatch.setattr("synapse_channel.client.agent_lifecycle.connect", unreachable)
    agent = SynapseAgent("LONELY", _noop_handler, uri="ws://10.255.255.1:9", verbose=False)
    await agent.connect()
    assert capsys.readouterr().out == ""


def test_start_quiet_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """verbose=False keeps the Ctrl-C shutdown note silent."""
    agent = SynapseAgent("LONELY", _noop_handler, verbose=False)

    def interrupt(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("asyncio.run", interrupt)
    agent.start()
    assert capsys.readouterr().out == ""
