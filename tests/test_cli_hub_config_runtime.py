# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — grouped CLI configuration through real socket admission
"""Exercise the CLI's host connection policy at the actual listening hub."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Coroutine

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from synapse_channel.cli import build_parser
from synapse_channel.cli_processes_hub import _cmd_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_defaults import DEFAULT_MAX_CONNECTIONS_PER_HOST


@pytest.mark.parametrize("raw_cap", [None, 0, 1])
def test_resolved_cli_host_cap_controls_actual_second_connection(raw_cap: int | None) -> None:
    """Omitted, disabled and enforced settings reach real socket admission."""
    hubs: list[SynapseHub] = []

    def build_hub(**options: object) -> SynapseHub:
        """Retain the actual constructed hub for its assigned listening address."""
        hub = SynapseHub.from_config(HubConfig.from_kwargs(options))
        hubs.append(hub)
        return hub

    async def exercise(server: Coroutine[object, object, None]) -> None:
        """Hold a registered connection while observing second-client admission."""
        task = asyncio.create_task(server)
        try:
            host, port = await hubs[0].wait_until_serving()
            uri = f"ws://{host}:{port}"
            async with connect(uri) as first:
                await first.recv()
                await first.send(json.dumps({"sender": "first", "type": "heartbeat"}))
                if raw_cap == 1:
                    with pytest.raises(ConnectionClosedError) as error:
                        async with connect(uri) as second:
                            await second.recv()
                    assert error.value.rcvd is not None
                    assert error.value.rcvd.code == 4015
                else:
                    async with connect(uri) as second:
                        welcome = json.loads(await second.recv())
                        assert welcome["type"] == "welcome"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def runner(server: Coroutine[object, object, None]) -> None:
        """Drive the actual command server and reap it after the admission probe."""
        asyncio.run(asyncio.wait_for(exercise(server), timeout=20.0))

    args = build_parser().parse_args(["hub", "--port", "0", "--identity-pins", ""])
    # Library callers may pass the omitted sentinel after parsing profiles.
    # The command must resolve it as it historically did, without disabling it.
    args.max_connections_per_host = raw_cap
    assert _cmd_hub(args, hub_factory=build_hub, runner=runner) == 0
    expected = DEFAULT_MAX_CONNECTIONS_PER_HOST if raw_cap is None else raw_cap or None
    assert hubs[0].max_connections_per_host == expected
