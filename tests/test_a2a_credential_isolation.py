# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the A2A client bearer never reaches the SYNAPSE hub
"""An A2A client's bearer authenticates that client to the bridge and nothing else.

The bridge reaches SYNAPSE with its own hub credential. These tests run the
product's HTTP server in front of a bridge whose agent is connected to a real,
token-secured hub, send authenticated requests on every message route, and
record every frame the hub receives from every connection. The client bearer
must appear in none of them, while the hub credential and the message text do
appear, which shows that the recording would have seen a leak.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from hub_wire_recorder import WireRecordingHub
from synapse_channel.a2a_http import make_a2a_http_server
from synapse_channel.a2a_server import A2ABridge, SynapseAgentRuntime
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.auth import TokenAuthenticator

A2A_BEARER = "a2a-client-bearer-7c1f0e5d"
HUB_TOKEN = "hub-credential-94b2aa31"
AUTHORITY = "a2a.example.test"
TARGET = "credential-worker"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serving(bridge: A2ABridge) -> Iterator[int]:
    """Serve ``bridge`` with the product HTTP server on a free loopback port."""
    port = _free_port()
    server = make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5.0)


def _post(port: int, path: str, body: dict[str, Any]) -> tuple[int, bytes]:
    """Send one authenticated A2A 1.0 request and return status and body."""
    payload = json.dumps(body).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
    try:
        connection.request(
            "POST",
            path,
            body=payload,
            headers={
                "Host": AUTHORITY,
                "Authorization": f"Bearer {A2A_BEARER}",
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
            },
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _message(text: str) -> dict[str, Any]:
    return {"messageId": f"m-{text}", "role": "ROLE_USER", "parts": [{"text": text}]}


def _payload_is(expected: str) -> Callable[[dict[str, Any]], bool]:
    """Match a chat frame whose payload is exactly ``expected``."""

    def matches(frame: dict[str, Any]) -> bool:
        return bool(frame.get("payload") == expected)

    return matches


async def test_client_bearer_never_reaches_the_hub_on_any_message_route() -> None:
    """REST send, REST stream and JSON-RPC send forward work without the client bearer."""
    hub = WireRecordingHub(authenticator=TokenAuthenticator([HUB_TOKEN]))
    async with running_hub(hub) as (_hub, uri):
        worker = await connect_agent(TARGET, uri, token=HUB_TOKEN)
        agent = SynapseAgent("a2a-credential-bridge", uri=uri, verbose=False, token=HUB_TOKEN)
        runtime = SynapseAgentRuntime(agent)
        try:
            assert await asyncio.to_thread(runtime.start)
            bridge = A2ABridge(
                agent=agent,
                agent_card={"name": "credential-bridge", "capabilities": {"streaming": True}},
                target=TARGET,
                store=A2ATaskStore(),
                submit=runtime.run,
                auth_token=A2A_BEARER,
                allowed_authorities=(AUTHORITY,),
            )
            with _serving(bridge) as port:
                sent = await asyncio.to_thread(
                    _post, port, "/message:send", {"message": _message("rest-send")}
                )
                streamed = await asyncio.to_thread(
                    _post, port, "/message:stream", {"message": _message("rest-stream")}
                )
                rpc = await asyncio.to_thread(
                    _post,
                    port,
                    "/rpc",
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "message/send",
                        "params": {"message": _message("rpc-send")},
                    },
                )
                for text in ("rest-send", "rest-stream", "rpc-send"):
                    await worker.recorder.wait_for(_payload_is(text))
        finally:
            await asyncio.to_thread(runtime.stop)
            await close_agents(worker)
        received = hub.inbound_text()

    assert [sent[0], streamed[0], rpc[0]] == [200, 200, 200]
    assert json.loads(rpc[1])["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    # The recording sees credentials and message text when they are on the wire ...
    assert any(HUB_TOKEN in frame for frame in received)
    for text in ("rest-send", "rest-stream", "rpc-send"):
        assert any(text in frame for frame in received)
    # ... and the A2A client bearer is in none of the frames from any connection.
    assert all(A2A_BEARER not in frame for frame in received)


async def test_unauthenticated_request_forwards_nothing_to_the_hub() -> None:
    """A refused request leaves no chat frame for the target on the hub wire."""
    hub = WireRecordingHub(authenticator=TokenAuthenticator([HUB_TOKEN]))
    async with running_hub(hub) as (_hub, uri):
        agent = SynapseAgent("a2a-refusing-bridge", uri=uri, verbose=False, token=HUB_TOKEN)
        runtime = SynapseAgentRuntime(agent)
        try:
            assert await asyncio.to_thread(runtime.start)
            bridge = A2ABridge(
                agent=agent,
                agent_card={"name": "refusing-bridge", "capabilities": {}},
                target=TARGET,
                store=A2ATaskStore(),
                submit=runtime.run,
                auth_token="a-different-bearer",
                allowed_authorities=(AUTHORITY,),
            )
            with _serving(bridge) as port:
                status, body = await asyncio.to_thread(
                    _post, port, "/message:send", {"message": _message("refused-send")}
                )
        finally:
            await asyncio.to_thread(runtime.stop)
        received = hub.inbound_text()

    assert status == 401
    assert json.loads(body)["title"] == "Unauthorized"
    assert bridge.store.list_tasks() == []
    assert all("refused-send" not in frame for frame in received)
    assert all(A2A_BEARER not in frame for frame in received)
