# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable HTTP fence hints and bounded cache failures
"""Exercise the retry cache through its public API and real SQLite files."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.mcp.http_operation_epochs import (
    MAX_OPERATION_CACHE_BYTES,
    HttpOperationEpochStore,
)


def test_original_fences_survive_new_instances_and_keep_native_scope(tmp_path: Path) -> None:
    """Only the same provisioned identity, hub and operation recover the original fence."""
    first = HttpOperationEpochStore("P/client\0key-binding", root=tmp_path)
    assert first.bind("hub-one", "release\0intent", 7) == 7
    recovered = HttpOperationEpochStore("P/client\0key-binding", root=tmp_path)
    assert recovered.bind("hub-one", "release\0intent", None) == 7
    assert recovered.bind("hub-one", "release\0intent", 99) == 7
    assert recovered.bind("hub-two", "release\0intent", None) is None
    assert recovered.bind("hub-one", "handoff\0intent", None) is None
    other = HttpOperationEpochStore("P/other\0key-binding", root=tmp_path)
    assert other.bind("hub-one", "release\0intent", None) is None
    assert other.path != first.path


def test_retention_evicts_hints_without_inventing_a_completed_operation(tmp_path: Path) -> None:
    """The disk retains at most its admitted rows; an expired hint stays unknown."""
    store = HttpOperationEpochStore("retention", root=tmp_path, limit=2)
    for epoch in range(3):
        assert store.bind("hub", str(epoch), epoch) == epoch
    assert store.bind("hub", "0", None) is None
    assert store.bind("hub", "1", None) == 1
    assert store.bind("hub", "2", None) == 2
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT count(*) FROM epochs").fetchone()[0] == 2
    assert store.path.stat().st_size <= MAX_OPERATION_CACHE_BYTES


@pytest.mark.parametrize("epoch", [-1, True, "7", 2**63])
def test_invalid_epoch_never_creates_a_cache(tmp_path: Path, epoch: object) -> None:
    """No boolean, conversion or out-of-range value can become a stored fence."""
    store = HttpOperationEpochStore("invalid-epoch", root=tmp_path)
    with pytest.raises(ValueError, match="invalid cached"):
        store.bind("hub", "release\0key", epoch)
    assert not store.path.exists()


@pytest.mark.parametrize(
    "context", [("", "key"), ("unknown", "key"), ("hub", ""), ("hub", "x" * 513)]
)
def test_unadmitted_context_never_creates_a_cache(tmp_path: Path, context: tuple[str, str]) -> None:
    """A fence without an admitted hub or bounded operation identity cannot be saved."""
    store = HttpOperationEpochStore("context", root=tmp_path)
    with pytest.raises(ValueError, match="admitted identity"):
        store.bind(*context, 1)
    assert not store.path.exists()


@pytest.mark.parametrize(
    "kwargs", [{"scope": ""}, {"scope": "x" * 8193}, {"limit": True}, {"limit": 0}, {"limit": 1025}]
)
def test_invalid_profile_is_refused_before_storage(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    """Profile capacity and namespace are explicit rather than silently clamped."""
    profile: dict[str, Any] = {"scope": "profile", "root": tmp_path, **kwargs}
    with pytest.raises(ValueError, match="scope or limit"):
        HttpOperationEpochStore(**profile)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "case", ["bytes", "page-size", "rows", "negative-epoch", "not-sqlite", "locked"]
)
def test_real_storage_faults_refuse_instead_of_replacing_hints(tmp_path: Path, case: str) -> None:
    """Byte/page/row budgets, corrupted content and a real writer lock fail closed."""
    store = HttpOperationEpochStore("storage", root=tmp_path, limit=2)
    assert store.bind("hub", "release\0key", 3) == 3
    if case == "bytes":
        with store.path.open("r+b") as file:
            file.truncate(MAX_OPERATION_CACHE_BYTES + 1)
    elif case == "not-sqlite":
        store.path.write_bytes(b"unreadable cache database")
    else:
        with closing(sqlite3.connect(store.path)) as connection:
            if case == "page-size":
                connection.execute("PRAGMA page_size=8192")
                connection.execute("VACUUM")
            elif case == "rows":
                connection.executemany(
                    "INSERT INTO epochs(key,epoch) VALUES (?,?)",
                    [("extra-one", 1), ("extra-two", 2)],
                )
                connection.commit()
            elif case == "negative-epoch":
                connection.execute("UPDATE epochs SET epoch=-1")
                connection.commit()
            else:
                connection.execute("BEGIN EXCLUSIVE")
                with pytest.raises(sqlite3.OperationalError):
                    store.bind("hub", "release\0key", None)
                connection.rollback()
                assert store.bind("hub", "release\0key", None) == 3
                return
    before = store.path.read_bytes()
    with pytest.raises((ValueError, sqlite3.Error)):
        store.bind("hub", "release\0key", None)
    assert store.path.read_bytes() == before
