# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a follower stops when a peer's journal instance changes
"""A follower stops, visibly, when a peer's journal instance changes under its cursor.

A peer's store carries a journal instance, a random identity written when the journal
begins. A restored copy, a rotation or a format conversion starts a new instance whose
sequence numbers start again. A follower that kept its old cursor would ask the new journal
only for rows above it and never see the rows below: a silent gap in the observed view.

The follower therefore remembers the instance it read each peer under. When the instance
changes it merges nothing from that batch, quarantines the peer with digest-only evidence
(``cause`` ``journal_instance_changed``), and raises. Only the explicit, named recovery
(:meth:`MultiHubFollower.recover_peer`) rebinds the peer, and the next poll then reads the
new journal from its start. Every test uses real event stores, each with its own instance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.journal import EventKind, record_multihub_equivocation
from synapse_channel.core.multihub_equivocation import (
    FederationQuarantine,
    MultiHubIntegrityError,
    MultiHubPeerQuarantinedError,
)
from synapse_channel.core.multihub_follower import MultiHubFollower, store_fetcher
from synapse_channel.core.persistence import EventStore

_PEER = "peer-hub"


def _store(path: Path, titles: list[str]) -> EventStore:
    """Return a new event store (a new journal instance) holding one task per title."""
    store = EventStore(path)
    for index, title in enumerate(titles, start=1):
        store.append(
            EventKind.LEDGER_TASK,
            {"task_id": f"T{index}", "title": title, "status": "open"},
            ts=float(index),
        )
    return store


async def _followed(tmp_path: Path, follower: MultiHubFollower) -> None:
    """Poll the peer's first journal, three tasks, so the follower's cursor is 3."""
    first = _store(tmp_path / "first.db", ["one", "two", "three"])
    try:
        await follower.poll(_PEER, store_fetcher(first))
    finally:
        first.close()
    assert follower.cursor(_PEER) == 3


async def test_a_changed_journal_instance_stops_the_follower_visibly(tmp_path: Path) -> None:
    """The new journal's rows are not merged past the old cursor; the peer is quarantined."""
    follower = MultiHubFollower()
    await _followed(tmp_path, follower)
    second = _store(tmp_path / "second.db", ["uno", "dos", "tres", "cuatro", "cinco"])
    try:
        with pytest.raises(MultiHubIntegrityError, match="journal instance changed"):
            await follower.poll(_PEER, store_fetcher(second))
        assert follower.cursor(_PEER) == 3
        assert sorted(follower.observed().board) == ["T1", "T2", "T3"]
        (quarantine,) = follower.quarantines()
        assert (quarantine.peer_id, quarantine.cause, quarantine.seq) == (
            _PEER,
            "journal_instance_changed",
            3,
        )
        assert quarantine.accepted_fingerprint != quarantine.conflicting_fingerprint
        with pytest.raises(MultiHubPeerQuarantinedError, match="journal instance changed"):
            await follower.poll(_PEER, store_fetcher(second))
    finally:
        second.close()


async def test_the_stop_survives_a_follower_restart(tmp_path: Path) -> None:
    """With a journal, the quarantine and its cause are restored by a new follower."""
    journal = EventStore(tmp_path / "observer.db")
    try:
        await _followed(tmp_path, MultiHubFollower(journal=journal))
        # A restarted follower holds no cursor; it reads the peer's current journal afresh.
        follower = MultiHubFollower(journal=journal)
        current = _store(tmp_path / "current.db", ["one"])
        try:
            await follower.poll(_PEER, store_fetcher(current))
        finally:
            current.close()
        second = _store(tmp_path / "second.db", ["uno", "dos", "tres", "cuatro"])
        try:
            with pytest.raises(MultiHubIntegrityError, match="journal instance changed"):
                await follower.poll(_PEER, store_fetcher(second))
        finally:
            second.close()
        restored = MultiHubFollower(journal=journal)
        (quarantine,) = restored.quarantines()
        assert (quarantine.peer_id, quarantine.cause) == (_PEER, "journal_instance_changed")
    finally:
        journal.close()


async def test_only_the_named_recovery_rebinds_the_peer_to_its_new_journal(
    tmp_path: Path,
) -> None:
    """After recover_peer, the next poll reads the new journal from its first row."""
    follower = MultiHubFollower()
    await _followed(tmp_path, follower)
    second = _store(tmp_path / "second.db", ["uno", "dos", "tres", "cuatro", "cinco"])
    try:
        with pytest.raises(MultiHubIntegrityError):
            await follower.poll(_PEER, store_fetcher(second))
        instance = second.journal_instance()
        assert instance is not None
        follower.recover_peer(
            _PEER,
            recovered_by="operator/ops",
            reason="the peer restored its journal from a backup",
            new_log_generation=instance.instance,
        )
        observed = await follower.poll(_PEER, store_fetcher(second))
    finally:
        second.close()
    assert follower.cursor(_PEER) == 5
    assert follower.quarantines() == ()
    assert [observed.board[f"T{n}"]["title"] for n in range(1, 6)] == [
        "uno",
        "dos",
        "tres",
        "cuatro",
        "cinco",
    ]


async def test_the_same_journal_instance_keeps_its_cursor(tmp_path: Path) -> None:
    """Rows appended to the same journal are read past the cursor, as before."""
    store = _store(tmp_path / "peer.db", ["one", "two", "three"])
    follower = MultiHubFollower()
    try:
        await follower.poll(_PEER, store_fetcher(store))
        store.append(
            EventKind.LEDGER_TASK, {"task_id": "T4", "title": "four", "status": "open"}, ts=4.0
        )
        await follower.poll(_PEER, store_fetcher(store))
    finally:
        store.close()
    assert follower.cursor(_PEER) == 4
    assert follower.quarantines() == ()


async def test_a_new_instance_before_any_row_was_read_is_simply_followed(tmp_path: Path) -> None:
    """At cursor 0 nothing can be skipped, so a changed instance is read from its start."""
    empty = EventStore(tmp_path / "empty.db")
    follower = MultiHubFollower()
    try:
        await follower.poll(_PEER, store_fetcher(empty))
    finally:
        empty.close()
    assert follower.cursor(_PEER) == 0
    second = _store(tmp_path / "second.db", ["uno", "dos"])
    try:
        await follower.poll(_PEER, store_fetcher(second))
    finally:
        second.close()
    assert follower.cursor(_PEER) == 2
    assert follower.quarantines() == ()


def test_an_unknown_quarantine_cause_is_never_journalled(tmp_path: Path) -> None:
    """Only a recorded cause reaches the journal, so a replay never meets an invented one."""
    journal = EventStore(tmp_path / "observer.db")
    try:
        with pytest.raises(ValueError, match="cause"):
            record_multihub_equivocation(
                journal,
                FederationQuarantine(
                    peer_id=_PEER,
                    seq=3,
                    accepted_fingerprint="a" * 64,
                    conflicting_fingerprint="b" * 64,
                    detected_at=1.0,
                    cause="rebooted",
                ),
            )
    finally:
        journal.close()
