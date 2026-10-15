# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public command lease renewal acceptance
"""Exercise real commands and durable ownership across renewal and loss."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypeVar

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from cli_e2e_helpers import CliResult, run_cli
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.cli_locking import _lock
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

_Result = TypeVar("_Result")

_CHILD = (
    "import sys,time\nfrom pathlib import Path\n"
    "Path(sys.argv[1]).touch()\n"
    "while not Path(sys.argv[2]).exists(): time.sleep(0.02)\n"
    "print('CHILD_FINISHED',flush=True)\n"
)


async def wait_started(started: Path, operation: asyncio.Task[_Result]) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while not started.exists():
        assert not operation.done(), operation.result()
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.02)


def locked_command(uri: str, root: Path, *, timeout: float = 50) -> CliResult:
    return run_cli(
        "lock",
        "supervised",
        "--name",
        "lock-owner",
        "--wait-timeout",
        "0",
        "--ready-timeout",
        "1",
        "--release-timeout",
        "1",
        "--",
        sys.executable,
        "-c",
        _CHILD,
        str(root / "started"),
        str(root / "finish"),
        uri=uri,
        env={"SYN_HOME": str(root / "home"), "SYNAPSE_TOKEN": "", "SYNAPSE_TOKEN_FILE": ""},
        timeout=timeout,
    )


async def test_long_command_excludes_successor_past_original_expiry(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(default_ttl_seconds=30, journal=journal)) as (hub, uri):
            command = asyncio.create_task(asyncio.to_thread(locked_command, uri, tmp_path))
            try:
                await wait_started(tmp_path / "started", command)
                original = hub.state.claims["supervised"]
                await asyncio.sleep(32)
                assert not command.done()
                renewed = hub.state.claims["supervised"]
                assert renewed.epoch == original.epoch
                assert renewed.lease_expires_at > original.lease_expires_at + 20
                contender = await asyncio.to_thread(
                    run_cli,
                    "lock",
                    "supervised",
                    "--name",
                    "successor",
                    "--wait-timeout",
                    "0",
                    "--",
                    sys.executable,
                    "-c",
                    "print('BAD_OVERLAP')",
                    uri=uri,
                )
                assert contender.returncode == 1, contender.output
                assert "BAD_OVERLAP" not in contender.output
            finally:
                (tmp_path / "finish").touch()
                result = await asyncio.wait_for(command, 10)
            assert result.returncode == 0, result.output
            assert "supervised" not in hub.state.claims
            claims = [event.payload for event in journal.iter_events() if event.kind == "claim"]
            assert len(claims) >= 3
            assert {row["epoch"] for row in claims} == {original.epoch}
    finally:
        journal.close()


async def test_expired_original_lease_cannot_be_reacquired_by_renewal() -> None:
    async with running_hub(SynapseHub(default_ttl_seconds=30)) as (hub, uri):
        owner = await connect_agent("owner", uri)
        try:
            await owner.agent.claim("expires")
            grant = await owner.recorder.wait_for(
                lambda row: row.get("type") == MessageType.CLAIM_GRANTED
            )
            await asyncio.sleep(31)
            await owner.agent.send_message(
                MessageType.CLAIM,
                target="System",
                payload="expires",
                task_id="expires",
                renew_epoch=grant["epoch"],
                renewal_id="expired",
            )
            denial = await owner.recorder.wait_for(lambda row: row.get("renewal_id") == "expired")
            assert denial["type"] == MessageType.CLAIM_DENIED
            assert "expires" not in hub.state.claims
        finally:
            await close_agents(owner)


@pytest.mark.parametrize("anchor", [True, None, "1", -1, 2, 1.0])
async def test_invalid_durable_ownership_anchor_refuses_hub_reconstruction(
    tmp_path: Path,
    anchor: object,
) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            owner = await connect_agent("owner", uri)
            try:
                await owner.agent.claim("durable")
                await owner.recorder.wait_for(
                    lambda row: row.get("type") == MessageType.CLAIM_GRANTED
                )
                original = hub.state.claims["durable"].as_persisted_dict()
            finally:
                await close_agents(owner)
        journal.append("claim", {**original, "ownership_epoch": anchor})
        with pytest.raises(ValueError, match="invalid persisted ownership epoch"):
            SynapseHub(journal=journal)
        assert len([row for row in journal.iter_events() if row.kind == "claim"]) == 2
    finally:
        journal.close()


async def test_transport_loss_stops_owned_command_with_unknown_outcome(tmp_path: Path) -> None:
    context = running_hub(SynapseHub(default_ttl_seconds=30))
    hub, uri = await context.__aenter__()
    closed = False
    command = asyncio.create_task(asyncio.to_thread(locked_command, uri, tmp_path))
    try:
        await wait_started(tmp_path / "started", command)
        await context.__aexit__(None, None, None)
        closed = True
        result = await asyncio.wait_for(command, 12)
        assert result.returncode == 3, result.output
        assert "lease unknown" in result.stderr
        assert "CHILD_FINISHED" not in result.stdout
        assert hub.state.claims["supervised"].owner == "lock-owner"
    finally:
        (tmp_path / "finish").touch()
        await asyncio.wait_for(command, 10)
        if not closed:
            await context.__aexit__(None, None, None)


async def test_supervisor_cancellation_after_real_renewal_reaps_command(tmp_path: Path) -> None:
    async with running_hub(SynapseHub(default_ttl_seconds=30)) as (hub, uri):
        command = asyncio.create_task(
            _lock(
                uri=uri,
                name="cancel-owner",
                task_id="cancel-supervised",
                paths=[],
                wait_timeout=0,
                command=[
                    sys.executable,
                    "-c",
                    _CHILD,
                    str(tmp_path / "started"),
                    str(tmp_path / "finish"),
                ],
            )
        )
        try:
            await wait_started(tmp_path / "started", command)
            original = hub.state.claims["cancel-supervised"]
            await asyncio.sleep(6)
            assert (
                hub.state.claims["cancel-supervised"].lease_expires_at > original.lease_expires_at
            )
            command.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(command, 10)
            assert "cancel-supervised" not in hub.state.claims
        finally:
            (tmp_path / "finish").touch()
            command.cancel()
            await asyncio.gather(command, return_exceptions=True)


@pytest.mark.parametrize("loss", ["release", "handoff", "replace", "scope-change"])
async def test_lost_ownership_stops_command_and_preserves_successor(
    tmp_path: Path,
    loss: str,
) -> None:
    async with running_hub(SynapseHub(default_ttl_seconds=30)) as (hub, uri):
        command = asyncio.create_task(asyncio.to_thread(locked_command, uri, tmp_path))
        owner = successor = None
        try:
            await wait_started(tmp_path / "started", command)
            owner = await connect_agent("lock-owner", uri)
            original = hub.state.claims["supervised"].epoch
            if loss == "handoff":
                successor = await connect_agent("successor", uri)
                await owner.agent.handoff("supervised", "successor", epoch=original)
                await owner.recorder.wait_for(
                    lambda row: row.get("type") == MessageType.HANDOFF_GRANTED
                )
            elif loss == "scope-change":
                await owner.agent.claim("supervised", worktree="changed")
                await owner.recorder.wait_for(
                    lambda row: row.get("type") == MessageType.CLAIM_GRANTED
                )
            else:
                await owner.agent.release("supervised", epoch=original)
                await owner.recorder.wait_for(
                    lambda row: row.get("type") == MessageType.RELEASE_GRANTED
                )
                if loss == "replace":
                    await owner.agent.claim("supervised")
                    await owner.recorder.wait_for(
                        lambda row: row.get("type") == MessageType.CLAIM_GRANTED
                    )
            await close_agents(owner)
            owner = None
            result = await asyncio.wait_for(command, 12)
            assert result.returncode == 1, result.output
            assert "lease lost" in result.stderr
            assert "CHILD_FINISHED" not in result.stdout
            if loss == "release":
                assert "supervised" not in hub.state.claims
            else:
                retained = hub.state.claims["supervised"]
                assert retained.epoch != original
                assert retained.owner == ("successor" if loss == "handoff" else "lock-owner")
        finally:
            (tmp_path / "finish").touch()
            await asyncio.wait_for(command, 10)
            await close_agents(*(agent for agent in (owner, successor) if agent is not None))


@contextlib.asynccontextmanager
async def renewal_proxy(
    uri: str,
    fault: str,
    *,
    gate: Path | None = None,
) -> AsyncIterator[str]:
    """Forward actual wire traffic while corrupting or withholding selected replies."""

    acquired = asyncio.Event()
    saved_grant: dict[str, object] | None = None
    connections = 0

    async def handle(socket: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        if fault == "stall-reconnect" and connections > 1:
            if gate is not None:
                gate.touch()
            await asyncio.sleep(2)
        async with connect(uri) as upstream:

            async def inbound() -> None:
                async for raw in socket:
                    request = json.loads(raw)
                    if request.get("type") == MessageType.CLAIM:
                        acquired.set()
                    await upstream.send(raw)

            async def outbound() -> None:
                try:
                    await forward_replies()
                finally:
                    if upstream.close_code is not None and upstream.close_code != 1006:
                        await socket.close(
                            code=upstream.close_code,
                            reason=upstream.close_reason or "",
                        )

            async def forward_replies() -> None:
                nonlocal saved_grant
                async for raw in upstream:
                    data = json.loads(raw)
                    renewed = bool(data.get("renewal_id"))
                    if data.get("type") == MessageType.CLAIM_GRANTED and not renewed:
                        if saved_grant is None:
                            saved_grant = dict(data)
                    if (
                        fault == "replay-acquisition"
                        and data.get("type") == MessageType.CLAIM_DENIED
                        and saved_grant is not None
                    ):
                        await socket.send(json.dumps(saved_grant))
                    if (
                        data.get("type") == MessageType.CLAIM_GRANTED
                        and not renewed
                        and fault == "admission-delay"
                    ):
                        assert gate is not None
                        gate.touch()
                        await asyncio.sleep(1)
                    if renewed and fault == "delay":
                        assert gate is not None
                        gate.touch()
                        await asyncio.sleep(1)
                    if data.get("type") == MessageType.WELCOME and fault == "legacy":
                        data.pop("lock_renewal_session", None)
                    if (
                        data.get("type") == MessageType.STATE_SNAPSHOT
                        and fault == "drop-state"
                        and acquired.is_set()
                    ):
                        continue
                    if (
                        data.get("type") == MessageType.STATE_SNAPSHOT
                        and fault == "malformed-state"
                    ):
                        data["snapshot"] = {}
                    if data.get("type") == MessageType.CLAIM_GRANTED:
                        if fault == "missing-duration":
                            data.pop("lease_duration_seconds", None)
                        if fault == "boolean-renewal-epoch" and renewed:
                            data["epoch"] = True
                        if fault == "invalid-epoch-grant":
                            data["epoch"] = True
                        if fault == "infinite-grant":
                            data["lease_duration_seconds"] = float("inf")
                        if fault == "short-grant":
                            data["lease_duration_seconds"] = 1
                        if fault == "wrong-anchor" and renewed:
                            data["epoch"] = int(data["epoch"]) + 1
                            data["ownership_epoch"] = True
                        if fault == "drop" and renewed:
                            continue
                        if fault == "invalid-grant" or fault == "invalid-renewal" and renewed:
                            data["lease_duration_seconds"] = True
                        if fault == "foreign" and renewed:
                            for change in (
                                {"renewal_id": "other"},
                                {"hub_id": "other"},
                                {"sender": "other"},
                                {"owner": "other"},
                                {"task_id": "other"},
                            ):
                                await socket.send(json.dumps({**data, **change}))
                    await socket.send(json.dumps(data))

            tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async with serve(handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


@pytest.mark.parametrize(
    "fault",
    [
        "legacy",
        "invalid-grant",
        "invalid-epoch-grant",
        "missing-duration",
        "boolean-renewal-epoch",
        "infinite-grant",
        "short-grant",
        "invalid-renewal",
        "drop",
        "drop-state",
        "malformed-state",
        "wrong-anchor",
        "foreign",
    ],
)
async def test_real_wire_loss_and_legacy_admission(tmp_path: Path, fault: str) -> None:
    admission_faults = {
        "legacy",
        "invalid-grant",
        "invalid-epoch-grant",
        "missing-duration",
        "infinite-grant",
        "short-grant",
    }
    async with running_hub(SynapseHub(default_ttl_seconds=30)) as (hub, uri):
        async with renewal_proxy(uri, fault) as proxy:
            command = asyncio.create_task(asyncio.to_thread(locked_command, proxy, tmp_path))
            try:
                if fault not in admission_faults:
                    await wait_started(tmp_path / "started", command)
                    await asyncio.sleep(6.5)
                if fault in ("foreign", "malformed-state"):
                    assert not command.done()
                    (tmp_path / "finish").touch()
                result = await asyncio.wait_for(command, 10)
                assert result.returncode == (
                    1 if fault == "legacy" else 0 if fault == "foreign" else 3
                ), result.output
                if fault in admission_faults:
                    assert not (tmp_path / "started").exists()
                if fault == "legacy":
                    assert "supervised" not in hub.state.claims
                if fault in (
                    "drop",
                    "invalid-renewal",
                    "drop-state",
                    "wrong-anchor",
                    "boolean-renewal-epoch",
                ):
                    assert "lease unknown" in result.stderr
                    assert "CHILD_FINISHED" not in result.stdout
            finally:
                (tmp_path / "finish").touch()
                await asyncio.wait_for(command, 10)


@pytest.mark.parametrize("epoch", [None, True, -1, "1", 999999])
async def test_guarded_renewal_never_acquires_or_changes_scope(
    tmp_path: Path, epoch: object
) -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        owner = await connect_agent("owner", uri)
        try:
            await owner.agent.claim("owned", note="retain", worktree="scope", paths=["src"])
            await owner.recorder.wait_for(
                lambda data: data.get("type") == MessageType.CLAIM_GRANTED
            )
            original = hub.state.claims["owned"]
            await owner.agent.send_message(
                MessageType.CLAIM,
                target="System",
                payload="owned",
                task_id="owned",
                renew_epoch=epoch,
                renewal_id="invalid",
                worktree="changed",
                paths=["other"],
            )
            denial = await owner.recorder.wait_for(lambda data: data.get("renewal_id") == "invalid")
            assert denial["type"] == MessageType.CLAIM_DENIED
            assert hub.state.claims["owned"].as_persisted_dict() == original.as_persisted_dict()
            await owner.agent.send_message(
                MessageType.CLAIM,
                target="System",
                payload="absent",
                task_id="absent",
                renew_epoch=original.epoch,
                renewal_id="absent",
            )
            absent = await owner.recorder.wait_for(lambda data: data.get("renewal_id") == "absent")
            assert absent["type"] == MessageType.CLAIM_DENIED
            assert "absent" not in hub.state.claims
            await owner.agent.send_message(
                MessageType.CLAIM,
                target="System",
                payload="owned",
                task_id="owned",
                renew_epoch=original.epoch,
                renewal_id="valid",
                worktree="changed",
                paths=["src"],
            )
            grant = await owner.recorder.wait_for(lambda data: data.get("renewal_id") == "valid")
            assert grant["type"] == MessageType.CLAIM_GRANTED
            renewed = hub.state.claims["owned"]
            assert (renewed.epoch, renewed.note, renewed.worktree, renewed.paths) == (
                original.epoch,
                original.note,
                original.worktree,
                original.paths,
            )
        finally:
            await close_agents(owner)
