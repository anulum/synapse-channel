# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded operation namespace read tests
"""Read real committed namespaces with exact prefix, row and encoded byte bounds."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Literal, cast

import pytest

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_operation_keys import read_operation_keys


def _commit(store: EventStore, key: str) -> None:
    """Retain an actual operation and event through the public transaction API."""
    result = store.commit_operation(
        operation_key=key,
        request_digest=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        response={"accepted": True},
        events=(("chat", {"sender": "EXAMPLE/key-reader", "payload": "retained"}),),
        intent={},
    )
    assert result.outcome == "inserted"


@pytest.mark.parametrize("encoding", ["UTF-8", "UTF-16le", "UTF-16be"])
@pytest.mark.parametrize("prefix", ["operation/", "literal%_*[?]/", "trailing~"])
def test_literal_namespace_is_complete_in_native_encoding_and_survives_reopen(
    tmp_path: Path, prefix: str, encoding: str
) -> None:
    """Real UTF databases preserve exact membership despite binary-range lookalikes."""
    path = tmp_path / "keys.db"
    with sqlite3.connect(path) as database:
        database.execute('PRAGMA encoding="' + encoding + '"')
        database.execute("CREATE TABLE encoding_anchor(value TEXT)")
    expected = {prefix, prefix + "A", prefix + "a", prefix + "é", prefix + "\U00010000"}
    lookalike = prefix[:-1] + chr(ord(prefix[-1]) + 256)
    with EventStore(path) as store:
        for key in ["unrelated/key", prefix[:-1], lookalike + "/other", *sorted(expected)]:
            _commit(store, key)
        before = store.read_all()
        keys = read_operation_keys(store, prefix, max_entries=len(expected), max_key_bytes=512)
        assert set(keys) == expected
        assert store.read_all() == before
    with EventStore(path) as reopened:
        assert (
            read_operation_keys(reopened, prefix, max_entries=len(expected), max_key_bytes=512)
            == keys
        )
        assert read_operation_keys(reopened, "absent/", max_entries=1, max_key_bytes=512) == ()


def test_namespace_row_budget_never_returns_a_partial_prefix(tmp_path: Path) -> None:
    """An exact native overflow refuses without altering any retained row."""
    with EventStore(tmp_path / "rows.db") as store:
        for key in ["scope/one", "scope/two", "scope/three"]:
            _commit(store, key)
        before = store.read_all()
        with pytest.raises(ValueError, match="row budget"):
            read_operation_keys(store, "scope/", max_entries=2, max_key_bytes=128)
        assert store.read_all() == before
        assert read_operation_keys(store, "scope/", max_entries=3, max_key_bytes=128) == (
            "scope/one",
            "scope/three",
            "scope/two",
        )


@pytest.mark.parametrize("encoding,budget", [("UTF-8", 24), ("UTF-16le", 28)])
def test_key_bytes_are_bounded_in_both_storage_and_return_encoding(
    tmp_path: Path, encoding: str, budget: int
) -> None:
    """Native encoding and multibyte UTF-8 cannot evade the per-key bound."""
    path = tmp_path / "bytes.db"
    with sqlite3.connect(path) as database:
        database.execute('PRAGMA encoding="' + encoding + '"')
        database.execute("CREATE TABLE encoding_anchor(value TEXT)")
    key = "scope/" + "漢" * 8
    with EventStore(path) as store:
        _commit(store, key)
        _commit(store, "other/" + "x" * 1024)
        before = store.read_all()
        with pytest.raises(ValueError, match="byte budget"):
            read_operation_keys(store, "scope/", max_entries=1, max_key_bytes=budget)
        assert store.read_all() == before
        assert read_operation_keys(store, "scope/", max_entries=1, max_key_bytes=512) == (key,)


@pytest.mark.parametrize("field", ["max_entries", "max_key_bytes"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, 2**53])
def test_invalid_read_budget_cannot_change_retained_history(
    tmp_path: Path, field: Literal["max_entries", "max_key_bytes"], value: object
) -> None:
    """Malformed explicit bounds refuse before a real metadata query can return."""
    with EventStore(tmp_path / "invalid-budget.db") as store:
        _commit(store, "scope/one")
        before = store.read_all()
        with pytest.raises(ValueError, match="read budget"):
            read_operation_keys(
                store,
                "scope/",
                max_entries=cast(int, value) if field == "max_entries" else 8,
                max_key_bytes=cast(int, value) if field == "max_key_bytes" else 128,
            )
        assert store.read_all() == before


@pytest.mark.parametrize("prefix", [None, True, 7, "", "x" * 129, "line\nbreak", "clé/", "\ud800"])
def test_invalid_namespace_never_widens_a_real_key_read(tmp_path: Path, prefix: object) -> None:
    """Only an explicitly bounded printable ASCII namespace is accepted."""
    with EventStore(tmp_path / "invalid-prefix.db") as store:
        _commit(store, "scope/one")
        before = store.read_all()
        with pytest.raises(ValueError, match="namespace"):
            read_operation_keys(store, cast(str, prefix), max_entries=1, max_key_bytes=128)
        assert store.read_all() == before


def test_key_metadata_read_does_not_deserialize_corrupted_response_json(tmp_path: Path) -> None:
    """Key enumeration is not falsely promoted to operation-response validation."""
    path = tmp_path / "metadata.db"
    with EventStore(path) as store:
        _commit(store, "scope/one")
        with sqlite3.connect(path) as database:
            database.execute("UPDATE operations SET response_json='broken'")
        assert read_operation_keys(store, "scope/", max_entries=1, max_key_bytes=128) == (
            "scope/one",
        )
        with pytest.raises(ValueError):
            store.get_operation("scope/one")


def test_closed_key_reader_cannot_return_a_cached_namespace(tmp_path: Path) -> None:
    """A real closed journal fails rather than inventing an empty metadata result."""
    path = tmp_path / "closed.db"
    store = EventStore(path)
    _commit(store, "scope/one")
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        read_operation_keys(store, "scope/", max_entries=1, max_key_bytes=128)
    with EventStore(path) as reopened:
        assert read_operation_keys(reopened, "scope/", max_entries=1, max_key_bytes=128) == (
            "scope/one",
        )
