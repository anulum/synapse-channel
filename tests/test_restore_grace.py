# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests: the restore's own connection proves the hub stayed up
"""The hub grace: its held connection, the answer before each start, and the shortcut."""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import _CLI, _await_listening, free_port
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_dispatch import MessageCallback
from synapse_channel.core.protocol import MessageType
from synapse_channel.restore_grace import (
    RESTORE_GRACE_SECONDS,
    HubGrace,
    HubGraceError,
    health_reading,
)

GRACE = 0.4


class FakeHub:
    """A scripted hub: whether it welcomes, answers, refuses, and which id it names."""

    def __init__(self, hub_id: str = "hub-a") -> None:
        self.hub_id = hub_id
        self.welcomes = True
        self.refusal: tuple[int, str] | None = None
        self.mute: set[int] = set()  # connections that stop answering
        self.agents: list[FakeAgent] = []

    def factory(self, name: str, callback: object, **options: Any) -> FakeAgent:
        agent = FakeAgent(self, name, len(self.agents) + 1, options)
        self.agents.append(agent)
        return agent


class FakeAgent:
    def __init__(self, hub: FakeHub, name: str, number: int, options: dict[str, Any]) -> None:
        self.hub, self.name, self.number, self.options = hub, name, number, options
        self.running = True
        self.hub_id = hub.hub_id
        self.last_close_code: int | None = None
        self.last_close_reason = ""
        self.callback: MessageCallback | None = None
        self.asked = 0
        self.ended = asyncio.Event()

    async def connect(self) -> None:
        await self.ended.wait()

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        if not self.hub.welcomes:
            await asyncio.sleep(min(timeout, 0.05))
        return self.hub.welcomes

    async def request_who(self, hub: str | None = None) -> None:
        self.asked += 1
        if self.hub.refusal is not None:
            self.last_close_code, self.last_close_reason = self.hub.refusal
            self.running = False
        elif self.number not in self.hub.mute and self.callback is not None:
            await self.callback({"type": MessageType.WHO_SNAPSHOT, "agents": []})


def _grace(hub: FakeHub, **options: Any) -> HubGrace:
    settings: dict[str, Any] = {"grace": GRACE, "answer": 0.2, "limit": 5.0, "health": None}
    settings.update(options)
    return HubGrace(
        "ws-restore-planner", uri="ws://127.0.0.1:1", token="t", factory=hub.factory, **settings
    )


async def _timed(grace: HubGrace) -> float:
    began = time.monotonic()
    await grace.ready()
    return time.monotonic() - began


async def test_the_first_start_waits_out_the_grace_on_its_own_connection() -> None:
    hub = FakeHub()
    grace = _grace(hub)
    try:
        assert await _timed(grace) >= GRACE
        (agent,) = hub.agents
        assert agent.name == "ws-restore-planner"
        assert agent.options["takeover"] is False and agent.options["token"] == "t"
        assert grace.hub_id == "hub-a"
        # Later starts need only the hub's answer on the same connection.
        asked = agent.asked
        assert await _timed(grace) < GRACE
        assert agent.asked == asked + 1 and len(hub.agents) == 1
    finally:
        await grace.close()
        await grace.close()  # closing twice is harmless


async def test_a_hub_that_stops_answering_is_a_drop_and_the_grace_starts_again() -> None:
    hub = FakeHub()
    grace = _grace(hub)
    try:
        await grace.ready()
        hub.mute.add(1)  # the held connection stays open but is no longer served
        assert await _timed(grace) >= GRACE
        assert len(hub.agents) == 2 and hub.agents[0].running is False
    finally:
        await grace.close()


async def test_a_dropped_connection_is_reopened_and_the_grace_starts_again() -> None:
    hub = FakeHub()
    grace = _grace(hub)
    try:
        await grace.ready()
        hub.agents[0].ended.set()  # the hub closed the socket
        await asyncio.sleep(0)
        assert await _timed(grace) >= GRACE and len(hub.agents) == 2
    finally:
        await grace.close()


async def test_a_different_hub_at_the_reconnect_stops_the_run() -> None:
    hub = FakeHub()
    grace = _grace(hub)
    try:
        await grace.ready()
        hub.hub_id = "hub-b"
        hub.agents[0].ended.set()
        await asyncio.sleep(0)
        with pytest.raises(HubGraceError, match=r"the hub changed \(hub-b\)") as stop:
            await grace.ready()
        assert stop.value.exit_code == 69 and stop.value.code == "restore_hub_grace"
    finally:
        await grace.close()


async def test_a_hub_that_cannot_be_reached_stops_the_run() -> None:
    hub = FakeHub()
    hub.welcomes = False
    with pytest.raises(HubGraceError, match="the hub cannot be reached") as stop:
        await _grace(hub).ready()
    assert stop.value.exit_code == 69 and hub.agents[0].running is False


@pytest.mark.parametrize(
    ("refusal", "exit_code", "message"),
    [
        ((4009, "name conflict"), 75, "another restore holds ws-restore-planner"),
        ((4009, "reserved identity"), 69, "the hub refused the restore planner"),
        ((4013, "identity proof"), 69, "the hub refused the restore planner"),
    ],
)
async def test_a_refused_planner_name_or_sender_stops_the_run(
    refusal: tuple[int, str], exit_code: int, message: str
) -> None:
    hub = FakeHub()
    hub.refusal = refusal
    with pytest.raises(HubGraceError, match=message) as stop:
        await _grace(hub).ready()
    assert stop.value.exit_code == exit_code


async def test_a_grace_that_cannot_be_met_in_time_stops_the_run() -> None:
    with pytest.raises(HubGraceError, match="did not stay up long enough"):
        await _grace(FakeHub(), limit=GRACE / 2).ready()


@pytest.mark.parametrize(
    ("reading", "waits"),
    [
        (("hub-a", GRACE), False),  # this hub, up for the grace
        (("hub-a", RESTORE_GRACE_SECONDS), False),
        (("hub-other", 1000.0), True),  # another hub's health does not count
        (("hub-a", GRACE / 2), True),  # up too briefly
        (None, True),  # no /health served
    ],
)
async def test_health_is_only_a_shortcut_for_the_first_wait_of_the_same_hub(
    reading: tuple[str, float] | None, waits: bool
) -> None:
    hub = FakeHub()
    grace = _grace(hub, health=lambda _uri: reading)
    try:
        assert (await _timed(grace) >= GRACE) is waits
        # The connection is held all the same, and checked before the next start.
        assert len(hub.agents) == 1 and hub.agents[0].running
    finally:
        await grace.close()


async def test_health_is_not_read_again_after_a_drop() -> None:
    hub = FakeHub()
    reads: list[str] = []

    def health(uri: str) -> tuple[str, float]:
        reads.append(uri)
        return ("hub-a", 100.0)

    grace = _grace(hub, health=health)
    try:
        assert await _timed(grace) < GRACE
        hub.agents[0].ended.set()
        await asyncio.sleep(0)
        assert await _timed(grace) >= GRACE and reads == ["ws://127.0.0.1:1"]
    finally:
        await grace.close()


# --- /health over HTTP ---------------------------------------------------------------


@contextlib.contextmanager
def _http(status: int, body: bytes) -> Iterator[int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("status", "body", "reading"),
    [
        (200, {"hub_id": "hub-a", "uptime_seconds": 61.5}, ("hub-a", 61.5)),
        (200, {"hub_id": "hub-a", "uptime_seconds": 61}, ("hub-a", 61.0)),
        (403, {"hub_id": "hub-a", "uptime_seconds": 61}, None),  # metrics off
        (200, {"hub_id": 7, "uptime_seconds": 61}, None),
        (200, {"hub_id": "hub-a", "uptime_seconds": True}, None),
        (200, {"hub_id": "hub-a", "uptime_seconds": "61"}, None),
        (200, ["hub-a", 61], None),
        (200, "not json", None),
    ],
)
def test_health_is_read_from_the_hubs_own_address(
    status: int, body: object, reading: tuple[str, float] | None
) -> None:
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    with _http(status, raw) as port:
        assert health_reading(f"ws://127.0.0.1:{port}/path?x=1") == reading


@pytest.mark.parametrize("uri", ["http://127.0.0.1:1", "file:///etc/passwd", "ws://", "nonsense"])
def test_health_is_never_read_from_anything_but_a_hub_uri(uri: str) -> None:
    assert health_reading(uri) is None


def test_health_from_no_listener_is_none() -> None:
    assert health_reading(f"wss://127.0.0.1:{free_port()}", timeout=1.0) is None


# --- a real hub ----------------------------------------------------------------------


def _grace_on(uri: str, **options: Any) -> HubGrace:
    settings: dict[str, Any] = {"grace": GRACE, "answer": 2.0, "limit": 20.0, "health": None}
    settings.update(options)
    return HubGrace("ws-restore-planner", uri=uri, token=None, factory=SynapseAgent, **settings)


async def test_a_real_hub_holds_the_planners_name_and_answers_before_each_start() -> None:
    async with running_hub() as (hub, uri):
        grace = _grace_on(uri)
        try:
            assert await _timed(grace) >= GRACE
            assert "ws-restore-planner" in hub.online_agents()
            assert grace.hub_id == "syn-test"
            assert await _timed(grace) < GRACE
        finally:
            await grace.close()


async def test_a_live_planner_name_on_a_real_hub_stops_a_second_run() -> None:
    async with running_hub() as (_hub, uri):
        live = await connect_agent("ws-restore-planner", uri)
        try:
            with pytest.raises(HubGraceError) as stop:
                await _grace_on(uri).ready()
            assert stop.value.exit_code == 75
        finally:
            await close_agents(live)


@contextlib.contextmanager
def _hub_process(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[bytes], str]]:
    port = free_port()
    proc = subprocess.Popen(
        [
            *_CLI,
            "hub",
            "--port",
            str(port),
            "--db",
            str(tmp_path / "hub.db"),
            "--identity-pins",
            str(tmp_path / "pins.json"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _await_listening(port, timeout=30.0)  # a loaded host starts a hub slowly
        yield proc, f"ws://127.0.0.1:{port}"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(proc.pid, signal.SIGCONT)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGSTOP is POSIX")
async def test_a_stopped_hub_is_a_drop_at_the_next_start(tmp_path: Path) -> None:
    with _hub_process(tmp_path) as (proc, uri):
        grace = _grace_on(uri, answer=1.0)
        try:
            await grace.ready()
            # The socket stays open while the hub's process is stopped: only an answer tells.
            os.kill(proc.pid, signal.SIGSTOP)
            began = time.monotonic()
            with pytest.raises(HubGraceError, match="the hub cannot be reached") as stop:
                await grace.ready()
            assert stop.value.exit_code == 69
            # The missing answer is seen within 1 s; closing a socket the stopped hub cannot
            # close back takes the client's close timeout (10 s), and the reconnect 1 s more.
            assert time.monotonic() - began < 30.0
        finally:
            os.kill(proc.pid, signal.SIGCONT)
            await grace.close()
