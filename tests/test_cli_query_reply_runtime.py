# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real lock reply forwarding fixture
"""Missing confirmations never become success through the actual CLI and hub."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli
from cli_query_reply_helpers import query_reply_proxy
from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


@pytest.mark.real_hub
@pytest.mark.parametrize(
    ("action", "arguments", "request_type", "reply_type"),
    [
        (
            "declare",
            ("--title", "Compile"),
            MessageType.LEDGER_TASK,
            MessageType.LEDGER_TASK_POSTED,
        ),
        (
            "update",
            ("--status", "done"),
            MessageType.LEDGER_TASK_UPDATE,
            MessageType.LEDGER_TASK_UPDATED,
        ),
        (
            "progress",
            ("started",),
            MessageType.LEDGER_PROGRESS,
            MessageType.LEDGER_PROGRESS_POSTED,
        ),
    ],
)
async def test_lost_task_confirmation_is_unknown_without_repeating_a_write(
    tmp_path: Path, action: str, arguments: tuple[str, ...], request_type: str, reply_type: str
) -> None:
    """The actual mutation commits once even though the CLI receives no confirmation."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            if action != "declare":
                seed = await asyncio.to_thread(
                    run_cli, "task", "declare", "BUILD", "--title", "Compile", uri=uri
                )
                assert seed.ok(), seed.output
            async with query_reply_proxy(uri, response_type=reply_type, lose_reply=True) as (
                proxy,
                trace,
            ):
                result = await asyncio.to_thread(
                    run_cli,
                    "task",
                    action,
                    "BUILD",
                    *arguments,
                    "--idem-key",
                    "lost-confirmation",
                    "--name",
                    "write-author",
                    uri=proxy,
                )
                assert trace.requests.count(request_type) == 1
                assert trace.replies.count(reply_type) == 1
                assert trace.connections == 1
                assert "write-author" not in hub.clients.agent_sockets
            with sqlite3.connect(tmp_path / "hub.db") as db:
                committed = db.execute("SELECT count(*) FROM operations").fetchone()[0]
            assert committed == 1
            assert result.returncode == 1, result.output
            assert "no matching reply" in result.stderr
            assert "may already have been applied" in result.stderr
            assert result.stdout == ""
            board = await asyncio.to_thread(run_cli, "board", uri=uri)
            assert board.ok() and "BUILD" in board.stdout, board.output
    finally:
        journal.close()


@pytest.mark.real_hub
@pytest.mark.parametrize("lose_reply", [False, True])
@pytest.mark.parametrize(
    ("command", "extra", "reply_type"),
    [
        ("who", (), MessageType.WHO_SNAPSHOT),
        ("state", (), MessageType.STATE_SNAPSHOT),
        ("dead-letters", (), MessageType.STATE_SNAPSHOT),
        ("approvals", (), MessageType.STATE_SNAPSHOT),
        ("board", (), MessageType.BOARD_SNAPSHOT),
        ("manifest", (), MessageType.MANIFEST_SNAPSHOT),
        ("a2a-card", ("--endpoint-url", "http://127.0.0.1:9000"), MessageType.MANIFEST_SNAPSHOT),
    ],
)
async def test_real_query_requires_a_reply_even_when_the_snapshot_is_empty(
    command: str, extra: tuple[str, ...], reply_type: str, lose_reply: bool
) -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        async with query_reply_proxy(uri, response_type=reply_type, lose_reply=lose_reply) as (
            proxy,
            trace,
        ):
            result = await asyncio.to_thread(
                run_cli, command, *extra, "--name", "reader", uri=proxy
            )
            assert trace.replies.count(reply_type) == 1
            assert trace.connections == 1
            assert "reader" not in hub.clients.agent_sockets
            assert result.returncode == (1 if lose_reply else 0), result.output
            if lose_reply:
                assert "no matching reply" in result.stderr
                assert result.stdout == ""
            else:
                assert "no matching reply" not in result.output
