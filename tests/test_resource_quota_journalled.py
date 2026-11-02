# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — resource-offer quota on a journalled hub, keyed and unkeyed
"""Over-quota offers are refused on a durable hub and leave no resource event behind."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub_e2e_helpers import Recorder, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import MAX_OFFERS_PER_AGENT


async def _quota_error(recorder: Recorder) -> None:
    """Wait for one quota refusal and consume it, so the next wait needs a fresh one."""
    error = await recorder.wait_for(
        lambda m: m.get("type") == "error" and m.get("payload") == "resource offer quota exceeded"
    )
    recorder.messages.remove(error)


def _resource_events(path: Path) -> int:
    store = EventStore(path)
    try:
        return len(store.read_window(kinds=(EventKind.RESOURCE,)))
    finally:
        store.close()


@pytest.mark.real_hub
async def test_over_quota_offers_are_refused_with_and_without_a_key(tmp_path: Path) -> None:
    """Both the plain and the idempotency-keyed path refuse the overflow and journal nothing."""
    journal_path = tmp_path / "hub.db"
    async with running_hub(SynapseHub(journal=EventStore(journal_path), hub_id="hub-1")) as (
        _hub,
        uri,
    ):
        alpha = await connect_agent("ALPHA", uri)
        try:
            for index in range(MAX_OFFERS_PER_AGENT):
                await alpha.agent.send_message("resource", kind="llm", name=f"m{index}")
            await alpha.recorder.wait_for(
                lambda m: (
                    m.get("type") == "resource_offered"
                    and m.get("name") == f"m{MAX_OFFERS_PER_AGENT - 1}"
                )
            )
            # Unkeyed: the plain journalled path persists nothing for the refusal.
            await alpha.agent.send_message("resource", kind="llm", name="overflow")
            await _quota_error(alpha.recorder)
            # Keyed: the atomic path prepares no durable draft for the refusal.
            await alpha.agent.send_message(
                "resource", kind="llm", name="overflow", idem_key="overflow-key"
            )
            await _quota_error(alpha.recorder)
        finally:
            await close_agents(alpha)
    assert _resource_events(journal_path) == MAX_OFFERS_PER_AGENT
