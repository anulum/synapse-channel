# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual listener revocation before hub readiness
"""Keep failed startup observable when an actual bound listener is revoked."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_factory import build_hub
from synapse_channel.core.persistence import EventStore


async def test_a_real_revoked_listener_never_publishes_ready(tmp_path: Path) -> None:
    """Close a real listener at its accepting yield and preserve caller resources."""
    loop = asyncio.get_running_loop()
    create_server = loop.create_server
    bound: asyncio.Future[tuple[asyncio.Server, tuple[str, int]]] = loop.create_future()
    interceptions = 0
    with EventStore(tmp_path / "journal.db") as journal:
        hub = build_hub(HubConfig(journal=journal))
        checkpoint = hub._checkpoint_store
        assert checkpoint is not None
        tasks_before = asyncio.all_tasks()

        async def revoke_listener(*args: Any, **kwargs: Any) -> asyncio.Server:
            """Delegate the actual bind, then close that same server on the next turn."""
            nonlocal interceptions
            server = await create_server(*args, **kwargs)
            interceptions += 1
            address = server.sockets[0].getsockname()
            bound.set_result((server, (str(address[0]), int(address[1]))))
            loop.call_soon(server.close)
            return server

        interception = pytest.MonkeyPatch()
        interception.setattr(loop, "create_server", revoke_listener)
        try:
            with pytest.raises(RuntimeError, match="hub server bound without a listening socket"):
                await asyncio.wait_for(hub.serve("127.0.0.1", 0), 5)
            server, address = bound.result()
            await server.wait_closed()
            assert interceptions == 1
            assert not server.sockets
            assert hub.bound_address is None
            with pytest.raises(RuntimeError, match="readiness without a bound address"):
                await hub.wait_until_serving()
            latest = checkpoint.latest()
            assert latest is not None and latest.seq == journal.max_seq() == 0
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as replacement:
                replacement.bind(address)
        finally:
            interception.undo()
            hub.close()
        assert journal.max_seq() == 0
        assert asyncio.all_tasks() <= tasks_before
