# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — K4-N1: a serving hub anchors its live log within a declared window
"""The anti-rollback checkpoint follows a serving hub, not only its start.

Before K4-N1 the hub anchored its log once, at start, and never closed the
checkpoint store; a tail cut of anything written while it served passed the next
start. These tests run real hubs on real files and prove the new contract:
post-start writes are anchored within ``checkpoint_interval``, a clean stop anchors
at once and releases the store, and a hub served again re-verifies first. The
declared window itself (writes newer than the last anchor) is shown explicitly.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_processes
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.merkle_checkpoint import (
    DEFAULT_CHECKPOINT_INTERVAL,
    AntiRollbackError,
    LiveCheckpoint,
    MerkleCheckpointStore,
    checkpoint_path_for,
)
from synapse_channel.core.persistence import EventStore


def _cut_after(db: Path, seq: int) -> None:
    """Delete every event after ``seq`` through a separate connection (the attack)."""
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM events WHERE seq > ?", (seq,))
    conn.commit()
    conn.close()


async def _write_chats(uri: str, count: int) -> None:
    writer = await connect_agent("P/writer", uri)
    try:
        for index in range(count):
            await writer.agent.chat(f"durable note {index}")
        await asyncio.sleep(0.2)
    finally:
        await close_agents(writer)


async def _until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition not met in time"
        await asyncio.sleep(0.05)


async def test_post_start_writes_are_anchored_within_the_window(tmp_path: Path) -> None:
    """Writes made while serving are anchored before shutdown; cutting them is detected."""
    db = tmp_path / "hub.db"
    store = EventStore(db)
    hub = SynapseHub(journal=store, checkpoint_interval=0.2)
    start_seq = store.max_seq()
    async with running_hub(hub) as (_hub, uri):
        await _write_chats(uri, 3)
        live = hub._checkpoint_store
        assert live is not None
        await _until(lambda: _latest_seq(live) == store.max_seq())
        anchored = store.max_seq()
        assert anchored > start_seq
        _cut_after(db, start_seq)  # remove everything written while serving
    store.close()

    reopened = EventStore(db)
    try:
        with pytest.raises(AntiRollbackError, match="tail truncation detected"):
            SynapseHub(journal=reopened)
    finally:
        reopened.close()


def _latest_seq(chain: MerkleCheckpointStore) -> int:
    latest = chain.latest()
    return -1 if latest is None else latest.seq


async def test_a_clean_stop_anchors_at_once_and_releases_the_store(tmp_path: Path) -> None:
    """Even with a long interval, stopping anchors every write and closes the store."""
    db = tmp_path / "hub.db"
    store = EventStore(db)
    hub = SynapseHub(journal=store, checkpoint_interval=3600.0)
    async with running_hub(hub) as (_hub, uri):
        await _write_chats(uri, 2)
        checkpoint = hub._checkpoint_store
        assert checkpoint is not None
        written = store.max_seq()
    assert hub._checkpoint_store is None
    with pytest.raises(sqlite3.ProgrammingError):
        checkpoint.latest()
    chain = MerkleCheckpointStore(checkpoint_path_for(db))
    try:
        latest = chain.latest()
        assert latest is not None
        assert latest.seq == written
        chain.verify(store)
    finally:
        chain.close()
        store.close()


async def test_a_hub_served_again_reverifies_before_serving(tmp_path: Path) -> None:
    """After a clean stop the store is reopened and the log verified again."""
    db = tmp_path / "hub.db"
    store = EventStore(db)
    hub = SynapseHub(journal=store, checkpoint_interval=3600.0)
    async with running_hub(hub) as (_hub, uri):
        await _write_chats(uri, 2)
    async with running_hub(hub) as (_hub, uri):
        assert hub._checkpoint_store is not None
        await _write_chats(uri, 1)
    _cut_after(db, 1)
    with pytest.raises(AntiRollbackError):
        await hub.serve("localhost", 0)
    assert hub._checkpoint_store is None
    store.close()


def test_the_declared_window_is_what_a_crash_can_lose(tmp_path: Path) -> None:
    """Writes after the last anchor can be cut undetected; anchored ones cannot."""
    db = tmp_path / "hub.db"
    store = EventStore(db)
    for seq in range(1, 4):
        store.append(EventKind.RECALL, {"actor": "alice", "seq": seq}, ts=float(seq))
    live = LiveCheckpoint(MerkleCheckpointStore(checkpoint_path_for(db)), store)
    store.append(EventKind.RECALL, {"actor": "alice", "seq": 4}, ts=4.0)
    live.close()  # a crash: no anchor after the fourth write
    _cut_after(db, 3)
    chain = MerkleCheckpointStore(checkpoint_path_for(db))
    try:
        chain.verify(store)  # inside the window: not detectable
        store.append(EventKind.RECALL, {"actor": "alice", "seq": 4}, ts=4.0)
        LiveCheckpoint(chain, store)  # anchors 4 again
        _cut_after(db, 3)
        with pytest.raises(AntiRollbackError, match="tail truncation detected"):
            chain.verify(store)
    finally:
        chain.close()
        store.close()


def test_an_incremental_anchor_matches_a_full_recompute(tmp_path: Path) -> None:
    """Folding only new events yields the same root the startup check recomputes."""
    db = tmp_path / "hub.db"
    store = EventStore(db)
    for seq in range(1, 6):
        store.append(EventKind.RECALL, {"actor": "alice", "seq": seq}, ts=float(seq))
    live = LiveCheckpoint(MerkleCheckpointStore(checkpoint_path_for(db)), store)
    for seq in range(6, 12):
        store.append(EventKind.RECALL, {"actor": "bob", "seq": seq}, ts=float(seq))
        live.anchor()
    unchanged = live.anchor()
    live.close()
    fresh = MerkleCheckpointStore(tmp_path / "fresh.checkpoint.db")
    try:
        assert fresh.anchor(store).root == unchanged.root
        assert unchanged.seq == store.max_seq()
        assert [event.seq for event in store.iter_events(after_seq=9)] == [10, 11]
    finally:
        fresh.close()
        store.close()


@pytest.mark.parametrize("interval", [0.0, -1.0, math.nan, math.inf])
def test_the_interval_must_be_positive_and_finite(interval: float) -> None:
    with pytest.raises(ValueError, match="checkpoint_interval"):
        SynapseHub(checkpoint_interval=interval)


def test_the_cli_threads_and_validates_the_interval(capsys: pytest.CaptureFixture[str]) -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["hub"]).checkpoint_interval == DEFAULT_CHECKPOINT_INTERVAL
    assert parser.parse_args(["hub", "--checkpoint-interval", "15"]).checkpoint_interval == 15.0
    with pytest.raises(SystemExit):
        parser.parse_args(["hub", "--checkpoint-interval", "0"])
    capsys.readouterr()
    built: dict[str, object] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        built.update(kwargs)
        return SynapseHub(**kwargs)

    ns = _hub_ns(checkpoint_interval=15.0)
    assert cli_processes._cmd_hub(ns, runner=_close_runner, hub_factory=build_hub) == 0
    assert built["checkpoint_interval"] == 15.0
