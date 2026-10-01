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

from synapse_channel.hub_inbox import read_hub_inbox
from synapse_channel.hub_inbox_cursor import (
    hub_inbox_cursor_path,
)
from synapse_channel.mcp.bridge import SynapseHubBridge
from test_cli_lock_legacy_runtime import legacy_release_profile as legacy_release_profile


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": True},
        {"version": 2},
        {"available": False},
        {"identity": "P/foreign"},
        {"hub_id": ""},
        {"hub_id": "foreign-hub"},
        {"cursor": True},
        {"cursor": -1},
        {"cursor": 2**63},
        {"has_more": "yes"},
        {"has_more": True, "cursor": 0},
        {"messages": {}},
        {"messages": [None]},
        {"messages": [{"type": "chat", "seq": True}]},
        {"messages": [{"type": "chat", "seq": 2}]},
        {"messages": [{"type": "presence", "seq": 1}]},
        {"messages": [{"type": "chat", "seq": 1, "channel": "private"}]},
        {"messages": [{"type": "chat", "seq": 1, "sender": "P/reader"}]},
        {"messages": [{"type": "chat", "seq": 1, "target": "P/foreign"}]},
        {"messages": [{"type": "chat", "seq": 1, "target": "P/reader"}] * 101},
    ],
)
@pytest.mark.parametrize("mode", ["cli", "mcp"])
async def test_untrusted_network_pages_cannot_advance_cursor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, Any],
    mode: str,
) -> None:
    from websockets.asyncio.server import ServerConnection, serve

    async def endpoint(socket: ServerConnection) -> None:
        await socket.send(json.dumps({"type": "welcome", "hub_id": "adversarial-hub"}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") != "history_request":
                continue
            page = {
                "version": 1,
                "available": True,
                "identity": "P/reader",
                "hub_id": "adversarial-hub",
                "cursor": 1,
                "has_more": False,
                "messages": [],
                **mutation,
            }
            await socket.send(
                json.dumps(
                    {"type": "history_snapshot", "request_id": "unrelated", "inbox_page": page}
                )
            )
            await socket.send(
                json.dumps(
                    {
                        "type": "history_snapshot",
                        "request_id": request["request_id"],
                        "inbox_page": page,
                    }
                )
            )

    async with serve(endpoint, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}"
        home = tmp_path / "reader"
        path = hub_inbox_cursor_path(home, uri, "P/reader")
        if mode == "cli":
            assert await read_hub_inbox(uri=uri, identity="P/reader", home=home, timeout=1) == 1
            result = json.loads(capsys.readouterr().out)
        else:
            monkeypatch.setenv("SYN_INBOX_SOURCE", "hub")
            monkeypatch.setenv("SYN_HOME", str(home))
            bridge = SynapseHubBridge(uri=uri, name="P/reader", request_timeout=1)
            task = asyncio.create_task(bridge.agent.connect())
            try:
                assert await bridge.agent.wait_until_ready(1)
                result = json.loads(await bridge.inbox())
            finally:
                bridge.agent.running = False
                if bridge.agent.connection is not None:
                    await bridge.agent.connection.close()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        assert result["available"] is False
        assert result["error"] == "cannot read durable hub inbox; cursor unchanged"
        assert not path.exists()


async def test_handshake_refusal_returns_fixed_result_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from websockets.asyncio.server import ServerConnection, serve
    from websockets.http11 import Request, Response

    async def reject(connection: ServerConnection, request: Request) -> Response:
        return connection.respond(403, "fixed handshake refusal\n")

    async def unused(connection: ServerConnection) -> None:
        raise AssertionError("a rejected handshake cannot reach the handler")

    async with serve(unused, "127.0.0.1", 0, process_request=reject) as server:
        port = server.sockets[0].getsockname()[1]
        assert (
            await read_hub_inbox(
                uri=f"ws://127.0.0.1:{port}", identity="P/reader", home=tmp_path, timeout=0.1
            )
            == 1
        )
        output = capsys.readouterr()
        assert json.loads(output.out)["error"] == "cannot read durable hub inbox; cursor unchanged"
        assert "Traceback" not in output.err
        assert not (tmp_path / "hub-inbox-cursor").exists()
