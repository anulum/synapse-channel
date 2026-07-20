# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

from __future__ import annotations

import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from synapse_channel.core.aef_replay_store import AefDurableReceiptIndex
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.persistence import EventStore

_LOG_ID = "a" * 64
_RECEIPT_1 = "aef1:" + "1" * 64
_RECEIPT_2 = "aef1:" + "2" * 64


def test_durable_index_survives_reopen_and_classifies_both_collision_axes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "aef.db"
    with AefDurableReceiptIndex(path) as index:
        assert index.classify_and_remember(_LOG_ID, 1, _RECEIPT_1) is None
        assert index.count() == 1

    with AefDurableReceiptIndex(path) as reopened:
        assert reopened.classify_and_remember(_LOG_ID, 1, _RECEIPT_1) is AefVerdictCode.REPLAYED
        assert reopened.classify_and_remember(_LOG_ID, 2, _RECEIPT_1) is AefVerdictCode.REPLAYED
        assert (
            reopened.classify_and_remember(_LOG_ID, 1, _RECEIPT_2) is AefVerdictCode.CHAIN_CONFLICT
        )
        assert reopened.count() == 1


def test_durable_index_can_share_event_store_without_touching_legacy_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hub.db"
    with EventStore(path) as events:
        event_seq = events.append("chat", {"payload": "legacy"})
    with AefDurableReceiptIndex(path) as index:
        assert index.classify_and_remember(_LOG_ID, 1, _RECEIPT_1) is None
    with EventStore(path) as events:
        stored = events.read_all()

    assert [(event.seq, event.kind, event.payload) for event in stored] == [
        (event_seq, "chat", {"payload": "legacy"})
    ]


def test_two_connections_cannot_both_accept_one_identity(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.db"
    with AefDurableReceiptIndex(path):
        pass
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda _: _classify_in_own_connection(path, _RECEIPT_1),
                range(2),
            )
        )

    assert outcomes.count(None) == 1
    assert outcomes.count(AefVerdictCode.REPLAYED) == 1


def test_conflicting_connections_preserve_the_first_committed_identity(tmp_path: Path) -> None:
    path = tmp_path / "conflict.db"
    with AefDurableReceiptIndex(path):
        pass
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda receipt_id: _classify_in_own_connection(path, receipt_id),
                (_RECEIPT_1, _RECEIPT_2),
            )
        )

    assert outcomes.count(None) == 1
    assert outcomes.count(AefVerdictCode.CHAIN_CONFLICT) == 1


@pytest.mark.parametrize(
    ("log_id", "seq", "receipt_id", "message"),
    [
        ("bad", 1, _RECEIPT_1, "log id"),
        (_LOG_ID, True, _RECEIPT_1, "sequence"),
        (_LOG_ID, 0, _RECEIPT_1, "sequence"),
        (_LOG_ID, 1, "bad", "receipt id"),
    ],
)
def test_durable_index_rejects_noncanonical_identity_fields(
    tmp_path: Path,
    log_id: str,
    seq: int,
    receipt_id: str,
    message: str,
) -> None:
    with AefDurableReceiptIndex(tmp_path / "invalid.db") as index:
        with pytest.raises(ValueError, match=message):
            index.classify_and_remember(log_id, seq, receipt_id)
        assert index.count() == 0


def test_durable_index_files_are_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "private.db"
    with AefDurableReceiptIndex(path) as index:
        assert index.classify_and_remember(_LOG_ID, 1, _RECEIPT_1) is None

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


def test_plaintext_property_and_closed_connection_failure_are_fail_closed(
    tmp_path: Path,
) -> None:
    index = AefDurableReceiptIndex(tmp_path / "closed.db")
    assert index.encrypted is False
    index.close()

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        index.classify_and_remember(_LOG_ID, 1, _RECEIPT_1)


def test_in_memory_permission_restriction_is_a_noop() -> None:
    AefDurableReceiptIndex._restrict(":memory:")


def _classify_in_own_connection(path: Path, receipt_id: str) -> AefVerdictCode | None:
    with AefDurableReceiptIndex(path) as index:
        return index.classify_and_remember(_LOG_ID, 1, receipt_id)
