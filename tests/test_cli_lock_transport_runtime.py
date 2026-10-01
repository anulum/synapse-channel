# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lock and release transport confirmation journeys
"""Exercise confirmation loss with unchanged frames from an actual isolated hub."""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from cli_e2e_helpers import CliResult, git_repo, git_run, run_cli
from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


@pytest.mark.asyncio
@pytest.mark.parametrize("same_task", [False, True])
async def test_other_release_confirmation_cannot_satisfy_cli_operation(
    tmp_path: Path, same_task: bool
) -> None:
    """Only the requested task and releasing owner may confirm this CLI release."""
    repo = git_repo(tmp_path / "repository")
    (repo / "other.md").write_text("other scope\n", encoding="utf-8")
    git_run(repo, "add", "other.md")
    git_run(repo, "commit", "-q", "-m", "other scope")
    journal = EventStore(tmp_path / "hub.db")
    request_seen = asyncio.Event()
    deliver_request = asyncio.Event()
    other_confirmation_sent = asyncio.Event()
    environment = {"SYNAPSE_TOKEN": ""}
    other_task = "target-edit" if same_task else "other-edit"
    operation: asyncio.Task[CliResult] | None = None
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, hub_uri):
            claims = [(other_task, "other-owner")]
            if not same_task:
                claims.append(("target-edit", "cli-owner"))
            for task_id, owner in claims:
                claim = await asyncio.to_thread(
                    run_cli,
                    "git-claim",
                    task_id,
                    "--name",
                    owner,
                    "--paths",
                    "README.md" if task_id == "target-edit" else "other.md",
                    "--base",
                    "HEAD",
                    "--auto-release-on",
                    "manual",
                    uri=hub_uri,
                    cwd=repo,
                    env=environment,
                )
                assert claim.ok(), claim.output

            async def forward(client: ServerConnection) -> None:
                """Delay the actual release request while unrelated hub grants arrive."""
                async with connect(hub_uri) as upstream:

                    async def requests() -> None:
                        """Gate the caller's original release frame without altering it."""
                        async for raw in client:
                            if json.loads(raw).get("type") == MessageType.RELEASE:
                                request_seen.set()
                                await deliver_request.wait()
                            await upstream.send(raw)

                    async def responses() -> None:
                        """Deliver original hub frames in order, observing the other owner."""
                        async for raw in upstream:
                            await client.send(raw)
                            message = json.loads(raw)
                            if (
                                message.get("type") == MessageType.RELEASE_GRANTED
                                and message.get("owner") == "other-owner"
                            ):
                                other_confirmation_sent.set()

                    tasks = [asyncio.create_task(requests()), asyncio.create_task(responses())]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        for result in results:
                            if isinstance(result, BaseException) and not isinstance(
                                result, asyncio.CancelledError | ConnectionClosed
                            ):
                                raise result

            async with serve(forward, "127.0.0.1", 0) as proxy:
                proxy_uri = f"ws://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
                operation = asyncio.create_task(
                    asyncio.to_thread(
                        run_cli,
                        "release",
                        "target-edit",
                        "--name",
                        "cli-owner",
                        "--receipt-json",
                        uri=proxy_uri,
                        cwd=repo,
                        env=environment,
                        timeout=15,
                    )
                )
                try:
                    await asyncio.wait_for(request_seen.wait(), timeout=5)
                    other_release = await asyncio.to_thread(
                        run_cli,
                        "release",
                        other_task,
                        "--name",
                        "other-owner",
                        uri=hub_uri,
                        cwd=repo,
                        env=environment,
                    )
                    assert other_release.ok(), other_release.output
                    await asyncio.wait_for(other_confirmation_sent.wait(), timeout=5)
                finally:
                    deliver_request.set()
                result = await operation
                assert result.returncode == (1 if same_task else 0), result.output
                if same_task:
                    assert "release refused for 'target-edit'" in result.stdout
                    assert '"released": true' not in result.stdout
                else:
                    receipt = json.loads(result.stdout)
                    assert receipt["task_id"] == "target-edit"
                    assert receipt["owner"] == "cli-owner"
                    assert receipt["released"] is True
                assert "target-edit" not in hub.state.claims
                assert other_task not in hub.state.claims
                assert any(
                    row.kind == "release" and row.payload.get("task_id") == "target-edit"
                    for row in journal.iter_events()
                )
    finally:
        deliver_request.set()
        try:
            if operation is not None:
                await operation
        finally:
            journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["lock", "release"])
async def test_unconfirmed_durable_operation_is_reported_without_false_success(
    tmp_path: Path, operation: str
) -> None:
    """A held actual grant cannot start a command or confirm an already-applied release."""
    repo = git_repo(tmp_path / "repository")
    journal = EventStore(tmp_path / "hub.db")
    intercepted = asyncio.Event()
    pending_confirmation = asyncio.Event()
    grant_type = MessageType.CLAIM_GRANTED if operation == "lock" else MessageType.RELEASE_GRANTED
    environment = {"SYNAPSE_TOKEN": ""}
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, hub_uri):

            async def forward(client: ServerConnection) -> None:
                """Forward original frames, withholding only the hub's actual task grant."""
                async with connect(hub_uri) as upstream:

                    async def requests() -> None:
                        """Deliver every actual client request unchanged to the hub."""
                        async for raw in client:
                            await upstream.send(raw)

                    async def responses() -> None:
                        """Preserve response order while holding the selected confirmation."""
                        async for raw in upstream:
                            message = json.loads(raw)
                            if (
                                message.get("type") == grant_type
                                and message.get("task_id") == "unconfirmed-edit"
                            ):
                                intercepted.set()
                                await pending_confirmation.wait()
                            await client.send(raw)

                    tasks = [asyncio.create_task(requests()), asyncio.create_task(responses())]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        for result in results:
                            if isinstance(result, BaseException) and not isinstance(
                                result, asyncio.CancelledError | ConnectionClosed
                            ):
                                raise result

            if operation == "release":
                claim = await asyncio.to_thread(
                    run_cli,
                    "git-claim",
                    "unconfirmed-edit",
                    "--name",
                    "unconfirmed-owner",
                    "--paths",
                    "README.md",
                    "--base",
                    "HEAD",
                    "--auto-release-on",
                    "manual",
                    uri=hub_uri,
                    cwd=repo,
                    env=environment,
                )
                assert claim.ok(), claim.output
            async with serve(forward, "127.0.0.1", 0) as proxy:
                proxy_uri = f"ws://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
                arguments = (
                    ["--receipt-json", "--reply-timeout", "1"]
                    if operation == "release"
                    else [
                        "--wait-timeout",
                        "0",
                        "--",
                        sys.executable,
                        "-c",
                        "print('unconfirmed-command-ran')",
                    ]
                )
                result = await asyncio.to_thread(
                    run_cli,
                    operation,
                    "unconfirmed-edit",
                    "--name",
                    "unconfirmed-owner",
                    *arguments,
                    uri=proxy_uri,
                    cwd=repo,
                    env=environment,
                    timeout=15,
                )
                assert intercepted.is_set(), "the actual hub confirmation was not intercepted"
                assert result.returncode == (3 if operation == "release" else 1), result.output
                assert "unconfirmed-command-ran" not in result.stdout
                assert not result.stderr
                if operation == "release":
                    assert "outcome unknown" in result.stdout
                    assert "do not replay release" in result.stdout
                    assert '"released": true' not in result.stdout
                    assert "unconfirmed-edit" not in hub.state.claims
                    assert any(
                        row.kind == "release" and row.payload.get("task_id") == "unconfirmed-edit"
                        for row in journal.iter_events()
                    )
                    assert sum(row.kind == "release" for row in journal.iter_events()) == 1
                    recovery_line = next(
                        line
                        for line in result.stdout.splitlines()
                        if line.startswith("Read-only recovery: ")
                    )
                    recovery = [
                        value
                        for value in shlex.split(
                            recovery_line.removeprefix("Read-only recovery: ")
                        )[1:]
                        if not value.startswith("--uri=")
                    ]
                    recovery[recovery.index("--") : recovery.index("--")] = [
                        "--receipt-json",
                        "--reply-timeout",
                        "1",
                    ]
                    confirmed = await asyncio.to_thread(
                        run_cli,
                        *recovery,
                        uri=hub_uri,
                        cwd=repo,
                        env=environment,
                        timeout=15,
                    )
                    assert confirmed.ok(), confirmed.output
                    receipt = json.loads(confirmed.stdout)
                    assert receipt["task_id"] == "unconfirmed-edit"
                    assert receipt["owner"] == "unconfirmed-owner"
                    assert receipt["released"] is True
                    assert sum(row.kind == "release" for row in journal.iter_events()) == 1
                else:
                    assert "timed out" in result.stdout
                    assert hub.state.claims["unconfirmed-edit"].owner == "unconfirmed-owner"
                    assert any(
                        row.kind == "claim" and row.payload.get("task_id") == "unconfirmed-edit"
                        for row in journal.iter_events()
                    )
                    assert not any(row.kind == "release" for row in journal.iter_events())
    finally:
        journal.close()
