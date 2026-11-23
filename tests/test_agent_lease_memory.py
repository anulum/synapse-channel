# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lease memory prefers the seat's store
"""How an agent remembers a lease epoch, in memory and on disk."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_lease_memory import AgentLeaseMemory, seat_store_name
from synapse_channel.client.agent_outbound_tasks import _fence_epoch
from synapse_channel.client.lease_epoch_store import LeaseEpochStore


def _agent(name: str, root: Path, *, persist: bool = True) -> SynapseAgent:
    agent = SynapseAgent(
        name,
        verbose=False,
        machine_identity=False,
        persist_lease_epochs=persist,
        lease_epoch_root=root,
    )
    agent.session.hub_id = "hub-a"
    return agent


def test_memory_wins_over_the_store(tmp_path: Path) -> None:
    agent = _agent("seat", tmp_path)
    agent.lease_memory.remember("hub-a", "task", 9)
    agent.lease_epochs["task"] = 2
    assert agent.lease_epochs is agent.lease_memory.epochs
    assert _fence_epoch(agent, "task", None) == 2


def test_the_store_fills_a_gap_in_memory(tmp_path: Path) -> None:
    agent = _agent("seat", tmp_path)
    agent.lease_memory.remember("hub-a", "task", 4)
    agent.lease_epochs.pop("task", None)
    assert _fence_epoch(agent, "task", None) == 4


def test_a_new_agent_of_the_same_seat_reads_the_store(tmp_path: Path) -> None:
    first = _agent("seat", tmp_path)
    first.lease_memory.remember("hub-a", "task", 6)
    second = _agent("seat", tmp_path)
    assert _fence_epoch(second, "task", None) == 6


def test_a_sidecar_uses_the_seat_store(tmp_path: Path) -> None:
    assert seat_store_name("seat-release") == "seat"
    sidecar = _agent("seat-release", tmp_path)
    sidecar.lease_memory.remember("hub-a", "task", 3)
    seat = _agent("seat", tmp_path)
    assert _fence_epoch(seat, "task", None) == 3


def test_a_nested_sidecar_has_no_store(tmp_path: Path) -> None:
    assert seat_store_name("a-rx-release") is None
    agent = _agent("a-rx-release", tmp_path)
    agent.lease_memory.remember("hub-a", "task", 1)
    assert list(tmp_path.rglob("*")) == []


def test_persistence_off_writes_no_store(tmp_path: Path) -> None:
    agent = _agent("seat", tmp_path, persist=False)
    agent.lease_memory.remember("hub-a", "task", 1)
    assert list(tmp_path.rglob("*")) == []


def test_a_matching_read_back_records_the_epoch(tmp_path: Path) -> None:
    memory = AgentLeaseMemory(
        store_name="seat",
        persist=True,
        root=tmp_path,
        request_lease=False,
        owner_lease="",
        on_lease_granted=None,
        takeover=False,
    )
    assert memory.remember_verified("hub", "task", 9) is True
    assert memory.epochs["task"] == 9
    again = AgentLeaseMemory(
        store_name="seat",
        persist=True,
        root=tmp_path,
        request_lease=False,
        owner_lease="",
        on_lease_granted=None,
        takeover=False,
    )
    assert again.recall("hub", "task") == 9


def test_a_disagreeing_read_back_leaves_memory_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LeaseEpochStore, "load", lambda self, hub_id, task_id: 4)
    memory = AgentLeaseMemory(
        store_name="seat",
        persist=True,
        root=tmp_path,
        request_lease=False,
        owner_lease="",
        on_lease_granted=None,
        takeover=False,
    )
    assert memory.remember_verified("hub", "task", 9) is False
    assert memory.epochs == {}


def test_recall_strips_the_task_id_before_memory_and_the_store(tmp_path: Path) -> None:
    memory = AgentLeaseMemory(
        store_name="seat",
        persist=True,
        root=tmp_path,
        request_lease=False,
        owner_lease="",
        on_lease_granted=None,
        takeover=False,
    )
    memory.remember("hub", "task", 9)
    assert memory.epochs["task"] == 9
    assert LeaseEpochStore("seat", root=tmp_path).load("hub", "task") == 9
    assert memory.recall("hub", " task ") == 9
    memory.epochs.clear()
    assert memory.recall("hub", " task ") == 9


def test_remember_verified_without_a_store_records_the_epoch() -> None:
    memory = AgentLeaseMemory(
        store_name=None,
        persist=False,
        root=None,
        request_lease=False,
        owner_lease="",
        on_lease_granted=None,
        takeover=False,
    )
    assert memory.remember_verified("hub", "task", 9) is True
    assert memory.epochs["task"] == 9
