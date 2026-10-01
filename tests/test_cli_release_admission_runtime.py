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

    async def _send_json(self, websocket: Any, data: dict[str, Any]) -> None:
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
        await super()._send_json(websocket, data)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_json", [False, True])
async def test_probe_returns_existing_exact_receipt_without_replaying(
    tmp_path: Path, receipt_json: bool
) -> None:
    """The pre-send read recovers a committed intent through the actual CLI."""
    repo = git_repo(tmp_path / "repository")
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(LostReleaseReplyHub(journal=journal)) as (_hub, uri):
            await claim(repo, uri)
            arguments = [
                "release",
                "release-proof",
                "--name=release-owner",
                "--reply-timeout=0.1",
                "--idem-key=pre-send-historical",
            ]
            if receipt_json:
                arguments.append("--receipt-json")
            first = await command(repo, uri, *arguments)
            assert first.ok(), first.output
            before = tuple(journal.iter_events())
            recovered = await command(repo, uri, *arguments)
            assert recovered.ok(), recovered.output
            if receipt_json:
                assert recovered.stdout == first.stdout
            else:
                assert "historical operation; no mutation replay" in recovered.stdout
            assert tuple(journal.iter_events()) == before
