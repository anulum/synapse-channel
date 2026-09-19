# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — owner-local entitlement persistence tests
"""Verify durable append, replay refusal and private SQLite custody."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.entitlement_store import (
    EntitlementStoreError,
    append_event,
    default_entitlement_store,
    read_events,
)


def _account() -> dict[str, object]:
    return {
        "event_id": "account-record-1",
        "kind": "account",
        "recorded_at": "2026-09-19T10:00:00Z",
        "source": "operator:owner",
        "confidence": "operator",
        "account_id": "opaque-1",
        "label": "Private account",
        "status": "active",
    }


def test_private_durable_record_and_duplicate_replay(tmp_path: Path) -> None:
    path = tmp_path / "private" / "ledger.sqlite3"
    assert read_events(path) == ()
    assert append_event(path, _account()) is True
    assert append_event(path, _account()) is False
    assert read_events(path) == (_account(),)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    changed = {**_account(), "label": "Other private account"}
    with pytest.raises(EntitlementStoreError, match="different content"):
        append_event(path, changed)


def test_version_mismatch_and_unversioned_file_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "private" / "ledger.sqlite3"
    append_event(path, _account())
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=2")
    with pytest.raises(EntitlementStoreError, match="unsupported.*version"):
        read_events(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=0")
    with pytest.raises(EntitlementStoreError, match="unversioned"):
        read_events(path)


def test_corrupt_record_and_restricted_path_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "private" / "ledger.sqlite3"
    append_event(path, _account())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET payload='not-json'")
    with pytest.raises(EntitlementStoreError, match="corrupt"):
        read_events(path)
    if os.name == "posix":
        path.chmod(0o644)
        with pytest.raises(EntitlementStoreError, match="owner-only"):
            read_events(path)


def test_symlink_store_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    path = real / "ledger.sqlite3"
    append_event(path, _account())
    alias = real / "alias.sqlite3"
    alias.symlink_to(path)
    with pytest.raises(EntitlementStoreError, match="symlink"):
        append_event(alias, _account())


def test_private_directory_and_corrupt_database_are_refused(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(private, target_is_directory=True)
    with pytest.raises(EntitlementStoreError, match="symlink"):
        append_event(alias / "ledger.sqlite3", _account())
    if os.name == "posix":
        private.chmod(0o755)
        with pytest.raises(EntitlementStoreError, match="owner-only"):
            append_event(private / "ledger.sqlite3", _account())
        private.chmod(0o700)
    path = private / "ledger.sqlite3"
    path.write_bytes(b"not a SQLite database")
    path.chmod(0o600)
    with pytest.raises(EntitlementStoreError, match="cannot open"):
        read_events(path)


def test_invalid_event_does_not_create_store(tmp_path: Path) -> None:
    path = tmp_path / "private" / "ledger.sqlite3"
    with pytest.raises(EntitlementStoreError, match="status"):
        append_event(path, {**_account(), "status": "unknown"})
    assert not path.exists()


def test_default_store_is_private_and_independent_of_shared_syn_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("SYN_HOME", str(tmp_path / "shared-synapse"))
    path = default_entitlement_store()
    assert state in path.parents
    assert "shared-synapse" not in str(path)
    assert append_event(path, _account())
    assert read_events(path) == (_account(),)
    if os.name == "posix":
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.parent.parent.stat().st_mode & 0o777 == 0o700


def test_corrupt_domain_record_cannot_be_replayed_as_valid(tmp_path: Path) -> None:
    path = tmp_path / "private" / "ledger.sqlite3"
    append_event(path, _account())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET payload='{}'")
    with pytest.raises(EntitlementStoreError, match="stored entitlement event is invalid"):
        append_event(path, _account())


def test_non_object_record_and_missing_table_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "private" / "ledger.sqlite3"
    append_event(path, _account())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET payload='42'")
    with pytest.raises(EntitlementStoreError, match="JSON object"):
        read_events(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE events")
    with pytest.raises(EntitlementStoreError, match="cannot append"):
        append_event(path, _account())
    with pytest.raises(EntitlementStoreError, match="records are corrupt"):
        read_events(path)


def test_missing_parent_and_store_symlink_fail_closed(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(EntitlementStoreError, match="cannot create private entitlement home"):
        append_event(blocker / "deep" / "private" / "ledger.sqlite3", _account())
    actual = tmp_path / "actual"
    append_event(actual / "ledger.sqlite3", _account())
    alias = actual / "alias.sqlite3"
    alias.symlink_to(actual / "ledger.sqlite3")
    with pytest.raises(EntitlementStoreError, match="symlink"):
        read_events(alias)
