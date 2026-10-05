# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual CLI delivery outcome and uncertainty journeys
"""Compare real process exits with actual recipient sockets and durable receipts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import CliResult, run_cli
from hub_e2e_helpers import AgentHandle, Recorder, close_agents, connect_agent, running_hub
from synapse_channel.cli_messaging_send import _send
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


class ReceiptTransportHub(SynapseHub):
    """Change only the receipt wire boundary after real routing and persistence."""

    def __init__(self, journal: EventStore, profile: str) -> None:
        """Attach a real journal and select its receipt transport profile."""
        super().__init__(journal=journal)
        self.profile = profile

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        """Exercise delayed, lost, legacy or malformed real receipt transport."""
        if data.get("type") == MessageType.DELIVERY_RECEIPT:
            if self.profile == "delayed":
                await asyncio.sleep(0.2)
            elif self.profile == "legacy":
                return
            elif self.profile == "lost":
                await websocket.close()
                return
            elif self.profile == "foreign":
                data = {**data, "sender": "OTHER"}
            elif self.profile == "wrong_target":
                data = {**data, "message_target": "OTHER"}
            elif self.profile == "non_boolean":
                data = {**data, "delivered": "false"}
        await super().send_json(websocket, data)


async def send(uri: str, *, required: bool, timeout: str = "0.05") -> CliResult:
    """Run the actual packaged command with finite subprocess supervision."""
    args = [
        "send",
        "outcome-marker",
        "--name",
        "SENDER",
        "--target",
        "RECIPIENT",
        "--wait-seconds",
        "0",
        "--receipt-timeout",
        timeout,
    ]
    if required:
        args.append("--require-recipient")
    return await asyncio.to_thread(run_cli, *args, uri=uri, timeout=8)


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("online", [False, True])
@pytest.mark.parametrize("profile", ["current", "delayed", "legacy", "lost"])
async def test_actual_cli_exit_distinguishes_delivery_from_missing_confirmation(
    tmp_path: Path,
    profile: str,
    online: bool,
    required: bool,
) -> None:
    """One real send cannot be classified from transport silence or local success."""
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(ReceiptTransportHub(journal, profile)) as (_, uri):
            receiver = await connect_agent("RECIPIENT", uri) if online else None
            try:
                result = await send(
                    uri, required=required, timeout="2" if profile == "current" else "0.05"
                )
                if receiver:
                    await receiver.recorder.wait_for(
                        lambda row: row.get("payload") == "outcome-marker"
                    )
                await asyncio.sleep(0.25)
                rows = list(journal.iter_events())
                chats = [row for row in rows if row.kind == "chat"]
                receipts = [row for row in rows if row.kind == "delivery_receipt_immediate"]
                assert len(chats) == len(receipts) == 1
                assert receipts[0].payload["delivered"] is online
                assert receipts[0].payload["client_msg_id"] == chats[0].payload["client_msg_id"]
                expected = (0 if online else 1) if profile == "current" else 3
                assert result.returncode == expected, result.output
                assert result.stderr == ""
                if expected == 3:
                    assert "delivery unknown:" in result.stdout
                    assert chats[0].payload["client_msg_id"] in result.stdout
                    assert "before retrying" in result.stdout
                elif expected == 1:
                    assert "no online recipient" in result.stdout
                elif required:
                    assert "delivered to RECIPIENT" in result.stdout
                else:
                    assert result.stdout == ""
            finally:
                if receiver:
                    await close_agents(receiver)


@pytest.mark.parametrize("profile", ["foreign", "wrong_target", "non_boolean"])
async def test_uncorrelated_or_malformed_receipt_cannot_confirm_a_real_send(
    tmp_path: Path,
    profile: str,
) -> None:
    """Actual delivery and receipt persistence cannot validate another wire verdict."""
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(ReceiptTransportHub(journal, profile)) as (_, uri):
            receiver = await connect_agent("RECIPIENT", uri)
            try:
                result = await send(uri, required=False)
                await receiver.recorder.wait_for(lambda row: row.get("payload") == "outcome-marker")
                assert result.returncode == 3, result.output
                assert "delivery unknown:" in result.stdout
                receipts = [
                    row for row in journal.iter_events() if row.kind == "delivery_receipt_immediate"
                ]
                assert len(receipts) == 1 and receipts[0].payload["delivered"] is True
            finally:
                await close_agents(receiver)


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0", "-1", "301"])
async def test_invalid_exchange_deadline_fails_before_connecting_or_sending(
    tmp_path: Path,
    timeout: str,
) -> None:
    """Malformed deadlines cannot create an unbounded send or journal effect."""
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            result = await asyncio.to_thread(
                run_cli,
                "send",
                "never-send",
                "--name=SENDER",
                "--target=RECIPIENT",
                f"--receipt-timeout={timeout}",
                uri=uri,
                timeout=8,
            )
            assert result.returncode == 1, result.output
            assert "invalid receipt timeout" in result.stdout
            assert "SENDER" not in hub.roster_liveness()
            assert not any(row.kind == "chat" for row in journal.iter_events())


async def test_expired_send_budget_does_not_resend_or_report_a_negative_receipt(
    tmp_path: Path,
) -> None:
    """A real process with an expired send deadline returns uncertainty once."""
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            result = await send(uri, required=False, timeout="0.000000001")
            assert result.returncode == 3, result.output
            assert "delivery unknown:" in result.stdout
            assert sum(row.kind == "chat" for row in journal.iter_events()) <= 1


class ClosedChatSocketAgent(SynapseAgent):
    """Close a real admitted socket just before its actual CHAT write."""

    async def send_message(self, msg_type: str, **extra: Any) -> None:
        """Keep the actual SDK serialization and raise its native closed-write error."""
        if msg_type == MessageType.CHAT and self.connection is not None:
            connection = self.connection
            await connection.close()
            # Retain the real closed socket to exercise the SDK write-loss race.
            self.connection = connection
        await super().send_message(msg_type, **extra)


async def test_actual_closed_write_returns_unknown_without_exception_disclosure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A physical socket failure has no invented negative delivery receipt."""
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            result = await _send(
                uri=uri,
                name="SENDER",
                target="RECIPIENT",
                message="not-written",
                wait_seconds=0,
                agent_factory=ClosedChatSocketAgent,
                receipt_timeout=0.1,
            )
            assert result == 3
            output = capsys.readouterr().out
            assert "delivery unknown:" in output and "ConnectionClosed" not in output
            assert not any(row.kind == "chat" for row in journal.iter_events())


async def test_later_mailbox_ack_preserves_original_negative_and_deferred_receipts(
    tmp_path: Path,
) -> None:
    """A later actual recipient ACK is separate evidence, without repeating CHAT."""
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            result = await send(uri, required=False, timeout="2")
            assert result.returncode == 1, result.output
            recorder = Recorder()
            agent = SynapseAgent("RECIPIENT", recorder, uri=uri, verbose=False, mailbox=True)
            receiver = AgentHandle(agent, recorder, asyncio.create_task(agent.connect()))
            try:
                assert await agent.wait_until_ready(timeout=3)
                await receiver.recorder.wait_for(lambda row: row.get("payload") == "outcome-marker")
                sender = await connect_agent("SENDER", uri)
                try:
                    await sender.recorder.wait_for(
                        lambda row: (
                            row.get("type") == "delivery_receipt" and row.get("deferred") is True
                        ),
                    )
                finally:
                    await close_agents(sender)
            finally:
                await close_agents(receiver)
            rows = list(journal.iter_events())
            assert sum(row.kind == "chat" for row in rows) == 1
            negative = next(row for row in rows if row.kind == "delivery_receipt_immediate")
            deferred = next(row for row in rows if row.kind == "delivery_receipt_deferred")
            assert negative.payload["delivered"] is False
            assert deferred.payload["delivered"] is True
            assert negative.payload["client_msg_id"] == deferred.payload["client_msg_id"]
