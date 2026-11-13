# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — client-side delivery refusals against real hubs
"""Refuse delivery verbs locally when the hub or the requested stage cannot carry them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every default data path of a real hub inside the test's directory."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@asynccontextmanager
async def _connected(journal: Path, hub_id: str) -> AsyncIterator[SynapseAgent]:
    """Connect one agent to a real journalled hub and unwind both on exit."""
    async with running_hub(SynapseHub(journal=EventStore(journal), hub_id=hub_id)) as (_hub, uri):
        agent = SynapseAgent("P/sender", uri=uri, machine_identity=False)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready()
            yield agent
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.real_hub
async def test_hub_without_a_stable_identity_cannot_carry_delivery(tmp_path: Path) -> None:
    """A hub that announces no stable id refuses every delivery verb before any send."""
    async with _connected(tmp_path / "hub.db", "unknown") as agent:
        assert agent.hub_id == "unknown"
        assert agent.hub_protocol_version is not None
        with pytest.raises(DeliveryRefusal, match="^hub has no stable identity$") as status:
            await agent.request_delivery_status("a" * 64)
        assert status.value.code == "unsupported_profile"
        with pytest.raises(DeliveryRefusal, match="^hub has no stable identity$") as stage:
            await agent.report_delivery_stage(
                "a" * 64,
                request_id="request-1",
                task_id="task-1",
                mutation_id="mutation-1",
                stage="completed",
                evidence={"executor_ref": "local", "outcome_code": "success"},
            )
        assert stage.value.code == "unsupported_profile"


@pytest.mark.real_hub
async def test_recipient_cannot_report_a_hub_owned_stage(tmp_path: Path) -> None:
    """queued and expired belong to the hub; a recipient report of them is refused locally."""
    async with _connected(tmp_path / "hub.db", "hub-1") as agent:
        await agent.request_delivery_status("a" * 64)
        for stage in ("queued", "expired"):
            with pytest.raises(DeliveryRefusal, match="^unsupported recipient stage$") as caught:
                await agent.report_delivery_stage(
                    "a" * 64,
                    request_id="request-1",
                    task_id="task-1",
                    mutation_id=f"mutation-{stage}",
                    stage=stage,
                    evidence={},
                )
            assert caught.value.code == "invalid_shape"
