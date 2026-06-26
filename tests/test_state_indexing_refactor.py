# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — structural regression tests for state indexing seams

from __future__ import annotations

from synapse_channel.core import state as state_module
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_leases import LeaseIndex
from synapse_channel.core.state_models import (
    GitContext,
    ResourceOffer,
    TaskClaim,
)
from synapse_channel.core.state_resources import ResourceRegistry


def test_state_reexports_model_classes_from_state_models() -> None:
    assert state_module.GitContext is GitContext
    assert state_module.TaskClaim is TaskClaim
    assert state_module.ResourceOffer is ResourceOffer


def test_synapse_state_owns_lease_index_and_preserves_heap_compatibility() -> None:
    state = SynapseState(default_ttl_seconds=100.0)

    assert isinstance(state._lease_index, LeaseIndex)
    assert state._lease_heap is state._lease_index.entries

    assert state.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    assert state._lease_heap == [(100.0, "T1", 1)]

    replacement = [(999.0, "ghost", 999)]
    state._lease_heap = replacement
    assert state._lease_index.entries is replacement

    state.reindex_leases()
    assert state._lease_heap == [(100.0, "T1", 1)]


def test_synapse_state_owns_resource_registry_and_preserves_resources_dict() -> None:
    state = SynapseState(max_offers_per_agent=1)

    assert isinstance(state._resource_registry, ResourceRegistry)
    assert state.resources is state._resource_registry.resources

    key = state.offer_resource(
        "A",
        kind="llm",
        name="model",
        capacity=0,
        meta={"vram": "8G"},
        now=10.0,
    )

    assert key == "A:llm:model"
    assert state.resources[key].capacity == 1
    assert state.query_resources(kind="llm") == [
        {
            "agent": "A",
            "kind": "llm",
            "name": "model",
            "capacity": 1,
            "meta": {"vram": "8G"},
        }
    ]
    assert state.offer_resource("A", kind="llm", name="overflow", now=11.0) is None
