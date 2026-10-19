# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — conflict snapshot transport recovery qualification
"""Exercise actual conflict CLI reads across an owned snapshot fault relay."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from cli_e2e_helpers import run_cli
from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub


@contextlib.asynccontextmanager
async def snapshot_relay(uri: str, fault: str) -> AsyncIterator[tuple[str, list[str]]]:
    """Forward real frames, withholding only selected genuine snapshot replies."""
    requests: list[str] = []
    registration_id: object = None

    async def handler(client: ServerConnection) -> None:
        """Join both actual socket directions on every disconnect."""
        with contextlib.suppress(ConnectionClosed):
            async with connect(uri) as upstream:

                async def inbound() -> None:
                    """Count real snapshot requests without modifying the upstream frame."""
                    nonlocal registration_id
                    async for raw in client:
                        frame: dict[str, object] = json.loads(raw)
                        if frame.get("type") == "state_request":
                            if not requests:
                                registration_id = frame.get("request_id")
                            requests.append(raw.decode() if isinstance(raw, bytes) else raw)
                        await upstream.send(raw)

                async def outbound() -> None:
                    """Keep registration and heartbeat delivery intact during the read fault."""
                    snapshots = 0
                    async for raw in upstream:
                        frame: dict[str, object] = json.loads(raw)
                        if (
                            frame.get("type") == "state_snapshot"
                            and frame.get("request_id") != registration_id
                        ):
                            snapshots += 1
                            if fault == "all" or (fault == "first" and snapshots == 1):
                                continue
                        await client.send(raw)

                tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    async with serve(handler, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", requests


@pytest.mark.parametrize("fault", ["none", "first", "all"])
async def test_conflicts_recovers_only_a_transient_snapshot_loss(
    tmp_path: Path, fault: str
) -> None:
    """A real CLI read recovers once while a persistent loss still fails closed."""
    name = f"CONFLICT-QA/reader-{uuid.uuid4().hex}"
    async with running_hub(SynapseHub()) as (hub, uri):
        async with snapshot_relay(uri, fault) as (relay, requests):
            result = await asyncio.to_thread(
                run_cli, "conflicts", "--name", name, uri=relay, cwd=tmp_path
            )
        assert len(requests) == (2 if fault == "none" else 3)
        assert isinstance(json.loads(requests[0])["request_id"], str)
        request_ids = [str(uuid.UUID(json.loads(raw)["request_id"])) for raw in requests]
        assert len(set(request_ids)) == len(request_ids)
        assert all(json.loads(raw)["payload"] == "snapshot" for raw in requests)
        assert all("release_confirmation" not in json.loads(raw) for raw in requests)
        if fault == "all":
            assert result.returncode == 1
            assert "no state snapshot received" in result.stdout
            assert "No predicted conflicts" not in result.stdout
        else:
            assert result.returncode == 0, result.output
            assert "No predicted conflicts" in result.stdout
        assert hub.state.snapshot()["active_claims"] == []
