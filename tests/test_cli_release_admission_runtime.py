# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — manual release admission over real hub sockets
"""Keep claims intact when exact release confirmation cannot be established."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import git_repo
from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from test_cli_release_confirmation_runtime import LostReleaseReplyHub, claim, command


class AdmissionReplyHub(SynapseHub):
    """Inject one malformed or missing read response on an otherwise real hub."""

    def __init__(self, journal: EventStore, mode: str) -> None:
        super().__init__(journal=journal)
        self.mode = mode

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        if "release_confirmation" in data:
            if self.mode == "drop":
                return
            if self.mode == "late":
                await asyncio.sleep(0.3)
            elif self.mode in {"sender", "target", "hub_id", "request_id"}:
                data = {**data, self.mode: "unrelated"}
            else:
                projection = dict(data["release_confirmation"])
                if self.mode == "list":
                    data = {**data, "release_confirmation": []}
                elif self.mode == "confirmed":
                    data = {**data, "release_confirmation": {**projection, "status": "confirmed"}}
                else:
                    projection[self.mode] = "unrelated"
                    data = {**data, "release_confirmation": projection}
        await super().send_json(websocket, data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "drop",
        "late",
        "sender",
        "target",
        "hub_id",
        "request_id",
        "list",
        "confirmed",
        "status",
        "task_id",
        "operation_id",
        "request_digest",
    ],
)
async def test_invalid_or_missing_probe_never_sends_release(tmp_path: Path, mode: str) -> None:
    """Actual CLI admission failure leaves the live claim and journal unchanged."""
    repo = git_repo(tmp_path / "repository")
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(AdmissionReplyHub(journal, mode)) as (hub, uri):
            await claim(repo, uri)
            before = tuple(journal.iter_events())
            original = hub.state.claims["release-proof"]
            result = await command(
                repo, uri, "release", "release-proof", "--name=release-owner", "--reply-timeout=0.1"
            )
            assert result.returncode == 1, result.output
            assert "no release sent" in result.stdout
            assert "outcome unknown" not in result.stdout
            assert hub.state.claims["release-proof"] == original
            assert tuple(journal.iter_events()) == before


def _release_arguments(receipt_json: bool, reply_timeout: str) -> list[str]:
    """Build one release command. The timeout is a bound, not proof of a kept receipt."""
    arguments = [
        "release",
        "release-proof",
        "--name=release-owner",
        f"--reply-timeout={reply_timeout}",
        "--idem-key=pre-send-historical",
    ]
    if receipt_json:
        arguments.append("--receipt-json")
    return arguments


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_json", [False, True])
async def test_probe_returns_existing_exact_receipt_without_replaying(
    tmp_path: Path, receipt_json: bool
) -> None:
    """The pre-send read recovers a committed intent through the actual CLI.

    The hub keeps the receipt and then drops the grant. The test waits until
    that keep is signalled, and only then reads it back. A short reply budget
    is not the signal: under load it can end before the keep, and the command
    then reports the outcome unknown.
    """
    repo = git_repo(tmp_path / "repository")
    with EventStore(tmp_path / "hub.db") as journal:
        hub = LostReleaseReplyHub(journal=journal)
        async with running_hub(hub) as (_hub, uri):
            await claim(repo, uri)
            creator = asyncio.create_task(
                command(repo, uri, *_release_arguments(receipt_json, "2"))
            )
            await asyncio.wait_for(hub.committed.wait(), timeout=8)
            await creator
            before = tuple(journal.iter_events())
            recovered = await command(repo, uri, *_release_arguments(receipt_json, "5"))
            again = await command(repo, uri, *_release_arguments(receipt_json, "5"))
            assert recovered.ok(), recovered.output
            assert again.ok(), again.output
            if receipt_json:
                assert json.loads(recovered.stdout)["released"] is True
                assert again.stdout == recovered.stdout
            else:
                assert "historical operation; no mutation replay" in recovered.stdout
                assert "historical operation; no mutation replay" in again.stdout
            assert tuple(journal.iter_events()) == before
