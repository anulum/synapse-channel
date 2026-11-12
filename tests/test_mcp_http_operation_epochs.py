# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a retried remote MCP mutation carries the epoch it was first sent with
"""Tests for :class:`synapse_channel.mcp.http_agent.OperationEpochs`.

The end-to-end replay of a granted release through the HTTP transport is
``tests/test_mcp_http_application.py``; these tests pin the bounded memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse_channel.client.lease_epoch_store import default_lease_epoch_root
from synapse_channel.mcp.http_agent import MAX_OPERATION_EPOCHS, HttpHubAgent, OperationEpochs
from synapse_channel.mcp.http_operation_epochs import HttpOperationEpochStore


def test_the_first_epoch_is_restored_on_a_replay_without_one() -> None:
    epochs = OperationEpochs()
    first: dict[str, Any] = {"task_id": "T", "epoch": 3}
    epochs.apply("release\0op-1", first)
    replay: dict[str, Any] = {"task_id": "T"}
    epochs.apply("release\0op-1", replay)
    assert replay == first
    unrelated: dict[str, Any] = {"task_id": "T"}
    epochs.apply("release\0op-2", unrelated)
    assert "epoch" not in unrelated


def test_the_memory_is_bounded_oldest_first() -> None:
    epochs = OperationEpochs(limit=2)
    for index in range(3):
        epochs.apply(f"k{index}", {"epoch": index})
    for key, expected in (("k0", None), ("k1", 1), ("k2", 2)):
        probe: dict[str, Any] = {}
        epochs.apply(key, probe)
        assert probe.get("epoch") == expected
    assert MAX_OPERATION_EPOCHS == 1024
    assert OperationEpochs(limit=0)._limit == 1


def test_a_new_map_restores_the_first_fence_from_the_durable_store(tmp_path: Path) -> None:
    """After an adapter restart the retried operation goes out with its first fence."""

    def restarted() -> OperationEpochs:
        store = HttpOperationEpochStore("ALPHA/alice", root=tmp_path / "epochs")
        return OperationEpochs(store=store)

    first: dict[str, Any] = {"epoch": 7}
    restarted().apply("claim\0op-1", first, hub_id="hub-a")
    assert first == {"epoch": 7}

    replay: dict[str, Any] = {}
    restarted().apply("claim\0op-1", replay, hub_id="hub-a")
    assert replay == {"epoch": 7}

    later: dict[str, Any] = {"epoch": 9}
    restarted().apply("claim\0op-1", later, hub_id="hub-a")
    assert later == {"epoch": 7}

    another_hub: dict[str, Any] = {}
    restarted().apply("claim\0op-1", another_hub, hub_id="hub-b")
    assert another_hub == {}


def test_the_remote_agent_keeps_one_private_store_per_identity() -> None:
    """Each provisioned identity has its own file under the data home's retry cache."""
    root = default_lease_epoch_root().parent / "http-operation-epochs"
    alice = HttpHubAgent("ALPHA/alice", verbose=False)._operation_epochs.store
    again = HttpHubAgent("ALPHA/alice", verbose=False)._operation_epochs.store
    bob = HttpHubAgent("ALPHA/bob", verbose=False)._operation_epochs.store
    assert alice is not None and again is not None and bob is not None
    assert alice.path.parent == root
    assert alice.path == again.path
    assert alice.path != bob.path
