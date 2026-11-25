# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a secured hub fans out only to sockets that authenticated
"""A secured hub writes a fan-out frame only to sockets that passed its token gate.

A socket joins the hub's connection table when it connects, before its first frame
is authenticated. These tests drive real sockets through a served hub. A socket in
its pre-authentication window must receive no chat, directed or not, and no
presence frame. A socket that authenticates late must never receive a frame that was
fanned out before it authenticated. And the property the fan-out rests on, that a
socket is bound to a name only after its first frame authenticated, holds for a bad
token and for a takeover attempt alike. A source scan keeps every other module from
walking the connection table, so a later fan-out path cannot reach pre-authentication
sockets either.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import collect_available, read_json, read_until_type, running_hub, send_json
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub

_TOKEN = "s3cret"
_SOURCE = Path(__file__).resolve().parents[1] / "src" / "synapse_channel"


def _secured_hub(*, auth_timeout: float = 10.0) -> SynapseHub:
    """Return a hub that admits any name presenting :data:`_TOKEN`."""
    return SynapseHub(
        default_ttl_seconds=300.0,
        hub_id="syn-test",
        authenticator=TokenAuthenticator([_TOKEN]),
        auth_timeout=auth_timeout,
    )


def _close_code(exc: ConnectionClosed) -> int | None:
    """Return the close code the hub sent, or the one this side sent."""
    if exc.rcvd is not None:
        return exc.rcvd.code
    if exc.sent is not None:
        return exc.sent.code
    return None


async def _until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    """Wait until ``predicate`` holds on the hub's own state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise TimeoutError("the hub did not reach the expected state")
        await asyncio.sleep(0.01)


async def _authenticate(websocket: ClientConnection, name: str) -> list[dict[str, Any]]:
    """Authenticate ``websocket`` as ``name`` and return every frame before its welcome."""
    await send_json(websocket, sender=name, type="heartbeat", token=_TOKEN)
    before: list[dict[str, Any]] = []
    for _ in range(50):
        frame = await read_json(websocket)
        if frame.get("type") == "welcome":
            return before
        before.append(frame)
    raise TimeoutError(f"{name} was not welcomed")


async def _read_chat(websocket: ClientConnection, payload: str) -> dict[str, Any]:
    """Read frames until the chat carrying ``payload`` arrives."""
    for _ in range(50):
        frame = await read_until_type(websocket, "chat")
        if frame.get("payload") == payload:
            return frame
    raise TimeoutError(f"the chat {payload!r} did not arrive")


async def _read_joined(websocket: ClientConnection, agent: str) -> dict[str, Any]:
    """Read frames until the presence update announcing that ``agent`` joined."""
    for _ in range(50):
        frame = await read_until_type(websocket, "presence_update")
        if frame.get("event") == "joined" and frame.get("agent") == agent:
            return frame
    raise TimeoutError(f"no presence update announced {agent}")


def _fanned_out(frames: list[dict[str, Any]]) -> list[tuple[Any, Any]]:
    """Return the chat and presence frames among ``frames``, by type and payload."""
    return [
        (frame.get("type"), frame.get("payload"))
        for frame in frames
        if frame.get("type") in ("chat", "presence_update")
    ]


async def test_an_unauthenticated_socket_receives_no_chat_and_no_presence() -> None:
    """A socket that never authenticates hears nothing before the hub closes it.

    While it waits in its window, an authenticated seat broadcasts to everyone,
    sends a chat directed to another seat, and a third seat joins. The waiting
    socket receives none of those frames, and the hub then closes it as idle.
    """
    hub = _secured_hub(auth_timeout=4.0)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as sender, connect(uri) as peer:
            await _authenticate(sender, "P/sender")
            await _authenticate(peer, "P/peer")
            async with connect(uri) as waiting:
                await _until(lambda: len(hub.unauth_clients) == 1)
                await send_json(
                    sender, sender="P/sender", type="chat", target="all", payload="to everyone"
                )
                await send_json(
                    sender, sender="P/sender", type="chat", target="P/peer", payload="to the peer"
                )
                await _read_chat(peer, "to everyone")
                await _read_chat(peer, "to the peer")
                async with connect(uri) as late:
                    await _authenticate(late, "P/late")
                    await _read_joined(sender, "P/late")
                    unbound = [s for s in hub.connected_clients if s not in hub.socket_agent]
                    assert len(unbound) == 1, "the window closed before the fan-out"
                frames: list[dict[str, Any]] = []
                with pytest.raises(ConnectionClosed) as closed:
                    while True:
                        frames.append(await read_json(waiting, timeout=10.0))

    assert _fanned_out(frames) == []
    assert _close_code(closed.value) == 4012


async def test_a_late_authenticator_never_receives_a_frame_fanned_out_before() -> None:
    """Frames fanned out during a socket's window never reach it once it authenticates."""
    hub = _secured_hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as sender, connect(uri) as peer:
            await _authenticate(sender, "P/sender")
            await _authenticate(peer, "P/peer")
            async with connect(uri) as late:
                await _until(lambda: len(hub.unauth_clients) == 1)
                await send_json(
                    sender, sender="P/sender", type="chat", target="all", payload="early, to all"
                )
                await send_json(
                    sender, sender="P/sender", type="chat", target="P/peer", payload="early, direct"
                )
                await _read_chat(peer, "early, to all")
                await _read_chat(peer, "early, direct")
                before_welcome = await _authenticate(late, "P/late")
                await send_json(
                    sender, sender="P/sender", type="chat", target="all", payload="after, to all"
                )
                await _read_chat(late, "after, to all")
                afterwards = await collect_available(late, duration=0.2)

    payloads = {payload for _type, payload in _fanned_out(before_welcome + afterwards)}
    assert "early, to all" not in payloads
    assert "early, direct" not in payloads
    assert _fanned_out(before_welcome) == []


@pytest.mark.parametrize("token", [None, "wrong"])
async def test_a_first_frame_without_a_valid_token_binds_no_name(token: str | None) -> None:
    """A refused first frame leaves the name unbound, so the socket stays outside fan-out."""
    hub = _secured_hub()
    frame: dict[str, Any] = {"sender": "P/x", "type": "heartbeat"}
    if token is not None:
        frame["token"] = token
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, **frame)
            await read_until_type(websocket, "auth_denied")
            assert "P/x" not in hub.agent_sockets
            assert "P/x" not in hub.socket_agent.values()
            with pytest.raises(ConnectionClosed) as closed:
                await read_json(websocket)

    assert _close_code(closed.value) == 4010


@pytest.mark.parametrize("token", [None, "wrong"])
async def test_a_takeover_without_a_valid_token_moves_no_name(token: str | None) -> None:
    """A takeover request that fails the token gate leaves the name with its owner."""
    hub = _secured_hub()
    frame: dict[str, Any] = {"sender": "P/x", "type": "heartbeat", "takeover": True}
    if token is not None:
        frame["token"] = token
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as owner, connect(uri) as claimant:
            await _authenticate(owner, "P/x")
            owner_socket = hub.agent_sockets["P/x"]
            await send_json(claimant, **frame)
            await read_until_type(claimant, "auth_denied")
            with pytest.raises(ConnectionClosed) as closed:
                await read_json(claimant)
            assert hub.agent_sockets["P/x"] is owner_socket
            assert list(hub.socket_agent.values()).count("P/x") == 1
            await send_json(owner, sender="P/x", type="chat", target="all", payload="still mine")
            assert (await _read_chat(owner, "still mine"))["sender"] == "P/x"

    assert _close_code(closed.value) == 4010


def _walks_of_the_connection_table(path: Path) -> list[int]:
    """Return the lines of ``path`` that use the connection table other than to count it.

    Counting it with ``len()`` is allowed, and so is the hub's public alias, an
    assignment of one ``connected_clients`` attribute to another.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "connected_clients"):
            continue
        parent = parents.get(node)
        counted = (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "len"
            and parent.args == [node]
        )
        aliased = (
            isinstance(parent, ast.Assign)
            and len(parent.targets) == 1
            and isinstance(parent.targets[0], ast.Attribute)
            and parent.targets[0].attr == "connected_clients"
            and isinstance(parent.value, ast.Attribute)
            and parent.value.attr == "connected_clients"
        )
        if not (counted or aliased):
            lines.append(node.lineno)
    return lines


def test_only_the_client_registry_walks_the_connection_table() -> None:
    """Outside the client registry the connection table is only counted, never walked.

    A fan-out takes its sockets from ``HubClientRegistry.fanout_clients``, so a new
    one-to-many path cannot reach a socket that has not authenticated.
    """
    registry = _SOURCE / "core" / "hub_clients.py"
    offenders = [
        f"{path.relative_to(_SOURCE)}:{line}"
        for path in sorted(_SOURCE.rglob("*.py"))
        if path != registry
        for line in _walks_of_the_connection_table(path)
    ]
    assert offenders == []
    assert _walks_of_the_connection_table(registry) != []
