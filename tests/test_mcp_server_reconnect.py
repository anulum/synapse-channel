# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native MCP stdio backend recovery
"""Preserve a real MCP protocol session across private hub restarts."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from hub_e2e_helpers import close_agents, connect_agent
from mcp_reconnect_helpers import stop_hub, wait_for_identity
from synapse_channel import cli as cli_module
from synapse_channel.core.hub import SynapseHub


async def test_stdio_recovers_twice_without_replaying_offline_sends(tmp_path: Path) -> None:
    """The same native stdio session reconnects; rejected sends stay rejected."""
    identity = "MCP-RECONNECT/stdio"
    hub = SynapseHub(hub_id="syn-reconnect-fixture")
    task = asyncio.create_task(hub.serve("127.0.0.1", 0))
    try:
        host, port = await hub.wait_until_serving()
        home = tmp_path / "home"
        home.mkdir()
        environment = {
            key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}
        }
        environment.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_DATA_HOME=str(home / "data"),
            XDG_STATE_HOME=str(home / "state"),
            XDG_CACHE_HOME=str(home / "cache"),
            SYNAPSE_AUTO_CONNECT="0",
            PYTHONPATH=str(Path(cli_module.__file__).resolve().parent.parent),
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "synapse_channel.cli",
                "mcp",
                "--uri",
                f"ws://{host}:{port}",
                "--name",
                identity,
                "--request-timeout",
                "0.2",
            ],
            env=environment,
            cwd=str(tmp_path),
        )
        with (tmp_path / "stdio.stderr").open("w") as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await wait_for_identity(session, hub, identity)
                    await asyncio.sleep(1.1)
                    for incarnation in range(2):
                        await stop_hub(task)
                        # The MCP process remains alive while its backend is absent.
                        assert (await session.list_tools()).tools
                        await session.call_tool("synapse_status", {})
                        rejected = await session.call_tool(
                            "synapse_send",
                            {"target": "MCP-RECONNECT/receiver", "message": "must-not-replay"},
                        )
                        assert rejected.isError
                        await asyncio.sleep(0.7)
                        hub = SynapseHub(hub_id="syn-reconnect-fixture")
                        task = asyncio.create_task(hub.serve(host, port))
                        await hub.wait_until_serving()
                        sink = await connect_agent("MCP-RECONNECT/receiver", f"ws://{host}:{port}")
                        try:
                            await wait_for_identity(session, hub, identity)
                            message = f"after-reconnect-{incarnation}"
                            sent = await session.call_tool(
                                "synapse_send",
                                {"target": "MCP-RECONNECT/receiver", "message": message},
                            )
                            assert not sent.isError

                            def matches_message(
                                event: dict[str, object], expected: str = message
                            ) -> bool:
                                return (
                                    event.get("type") == "chat" and event.get("payload") == expected
                                )

                            await sink.recorder.wait_for(matches_message)
                            payloads = [
                                event.get("payload")
                                for event in sink.recorder.messages
                                if event.get("type") == "chat"
                            ]
                            assert payloads == [message]
                        finally:
                            await close_agents(sink)
    finally:
        await stop_hub(task)
