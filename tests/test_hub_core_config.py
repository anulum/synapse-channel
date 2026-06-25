# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - tests for hub configuration and compaction hints

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.hub import (
    SynapseHub,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def test_default_hub_id_is_generated() -> None:
    hub = SynapseHub()
    assert hub.hub_id.startswith("syn-")
    assert len(hub.hub_id) == 12  # "syn-" + 8 hex chars


def test_hub_threads_per_agent_quotas_to_state() -> None:
    hub = SynapseHub(max_claims_per_agent=5, max_offers_per_agent=9, max_paths_per_claim=7)
    assert hub.state.max_claims_per_agent == 5
    assert hub.state.max_offers_per_agent == 9
    assert hub.state.max_paths_per_claim == 7


def test_hub_with_journal_threads_per_agent_quotas_to_state(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        journal=store, max_claims_per_agent=4, max_offers_per_agent=6, max_paths_per_claim=3
    )
    store.close()
    assert hub.state.max_claims_per_agent == 4
    assert hub.state.max_offers_per_agent == 6
    assert hub.state.max_paths_per_claim == 3


def test_hub_hints_at_compaction_when_the_log_is_oversized(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append(EventKind.CHAT, {"msg_id": 1})
    store.append(EventKind.CHAT, {"msg_id": 2})
    with caplog.at_level("WARNING", logger="synapse.hub"):
        SynapseHub(journal=store, compact_hint_threshold=1)
    store.close()
    assert any("synapse compact" in message for message in caplog.messages)


def test_hub_stays_quiet_when_the_log_is_within_the_compact_threshold(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append(EventKind.CHAT, {"msg_id": 1})
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub = SynapseHub(journal=store, compact_hint_threshold=100)
    store.close()
    assert hub.compact_hint_threshold == 100
    assert not any("synapse compact" in message for message in caplog.messages)


def test_compact_hint_threshold_clamps_up_to_one() -> None:
    assert SynapseHub(compact_hint_threshold=0).compact_hint_threshold == 1
    assert SynapseHub(compact_hint_threshold=-9).compact_hint_threshold == 1
