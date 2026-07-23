# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable capability-card lifecycle history tests
"""Exercise the real SQLite lifecycle store, restart path, and failure boundary."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core import capability_card_history as history_module
from synapse_channel.core.capability import CapabilityRegistry
from synapse_channel.core.capability_card_history import (
    MAX_CAPABILITY_CARD_HISTORY_CAPABILITIES_BYTES,
    PersistentCapabilityCardHistory,
)
from synapse_channel.core.capability_card_signing import sign_capability_card
from synapse_channel.core.capability_card_trust import (
    CapabilityCardHistoryResult,
    CapabilityCardTrustBundle,
    CapabilityCardTrustError,
)
from synapse_channel.core.capability_card_verification import CapabilityCardVerificationResult
from synapse_channel.core.identity_keys import generate_signing_key
from synapse_channel.core.message_auth import EventSignatureKey

# Minimal oversize payloads: one byte past the retained-field floors. Never embed
# multi-megabyte strings in pytest node ids — Windows Actions logs the full id
# on ERROR/FAIL and cancels the advisory cross-os job under log bloat.
_OVERSIZE_CAPABILITY = "x" * (MAX_CAPABILITY_CARD_HISTORY_CAPABILITIES_BYTES + 1)
# Valid JSON array of one string; total UTF-8 length is floor + 1.
_OVERSIZE_CAPABILITIES_JSON = (
    '["' + "x" * (MAX_CAPABILITY_CARD_HISTORY_CAPABILITIES_BYTES - 3) + '"]'
)


def _history(
    path: Path, *, capacity: int = 8, retention: float = 5.0
) -> PersistentCapabilityCardHistory:
    return PersistentCapabilityCardHistory(
        path,
        max_entries=capacity,
        retention_seconds=retention,
    )


def _assess(
    history: PersistentCapabilityCardHistory,
    *,
    agent: str = "P/worker",
    key_id: str = "P:key",
    sequence: int = 1,
    capabilities: frozenset[str] = frozenset({"skills:python"}),
    digest: str = "a" * 64,
    expires_at: float = 200.0,
    now: float = 100.0,
) -> CapabilityCardHistoryResult:
    return history.assess_and_remember(
        agent=agent,
        key_id=key_id,
        sequence=sequence,
        route_capabilities=capabilities,
        card_digest=digest,
        expires_at=expires_at,
        now=now,
    )


def _advertise(
    registry: CapabilityRegistry,
    private_key: Ed25519PrivateKey,
    *,
    sequence: int,
    skills: list[str],
) -> CapabilityCardVerificationResult:
    card: dict[str, object] = {
        "agent": "P/worker",
        "description": "worker",
        "skills": skills,
        "task_classes": ["code"],
        "model": "",
        "project": "P",
        "manifest_digest": "sha256:abc",
        "contracts": [],
        "meta": {},
    }
    signed = sign_capability_card(
        card,
        key_id="P:key",
        private_key=private_key,
        sequence=sequence,
        signed_at=100.0 + sequence,
        expires_at=200.0,
    )
    projected = registry.advertise(
        "P/worker",
        description="worker",
        skills=skills,
        task_classes=["code"],
        project="P",
        manifest_digest="sha256:abc",
        signature=signed["signature"],
        now=150.0,
    )
    return projected.verification.result


def _assess_unchecked(
    history: PersistentCapabilityCardHistory, overrides: dict[str, Any]
) -> CapabilityCardHistoryResult:
    """Drive invalid public inputs whose shapes strict typing correctly forbids."""
    values: dict[str, Any] = {
        "agent": "P/worker",
        "key_id": "P:key",
        "sequence": 1,
        "route_capabilities": frozenset({"skills:python"}),
        "card_digest": "a" * 64,
        "expires_at": 200.0,
        "now": 100.0,
    }
    values.update(overrides)
    return history.assess_and_remember(**values)


def test_registry_preserves_replay_and_downgrade_floors_across_restarts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cards" / "history.db"
    private = generate_signing_key()
    key = EventSignatureKey.from_private_key(
        key_id="P:key",
        private_key=private,
        senders=frozenset({"P/worker"}),
        projects=frozenset({"P"}),
    )

    def registry() -> CapabilityRegistry:
        return CapabilityRegistry(
            trust_bundle=CapabilityCardTrustBundle(
                keys={key.key_id: key},
                history=_history(path),
            )
        )

    assert (
        _advertise(registry(), private, sequence=1, skills=["python"])
        is CapabilityCardVerificationResult.VALID
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (
        _advertise(registry(), private, sequence=1, skills=["python"])
        is CapabilityCardVerificationResult.SEQUENCE_MISMATCH
    )
    assert (
        _advertise(registry(), private, sequence=2, skills=[])
        is CapabilityCardVerificationResult.CAPABILITY_DOWNGRADE
    )
    assert (
        _advertise(registry(), private, sequence=3, skills=[])
        is CapabilityCardVerificationResult.CAPABILITY_DOWNGRADE
    )
    assert (
        _advertise(registry(), private, sequence=4, skills=["python"])
        is CapabilityCardVerificationResult.VALID
    )


def test_capacity_remains_fail_closed_until_expiry_plus_retention(tmp_path: Path) -> None:
    history = _history(tmp_path / "history.db", capacity=1, retention=5.0)
    assert _assess(history, expires_at=10.0, now=1.0) is CapabilityCardHistoryResult.ACCEPTED
    assert (
        _assess(history, agent="P/other", key_id="P:other", expires_at=20.0, now=2.0)
        is CapabilityCardHistoryResult.HISTORY_FULL
    )
    assert (
        _assess(history, agent="P/other", key_id="P:other", expires_at=20.0, now=16.0)
        is CapabilityCardHistoryResult.ACCEPTED
    )


def test_two_store_instances_serialize_updates_in_one_database(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    first = _history(path)
    second = _history(path)
    assert _assess(first, sequence=1) is CapabilityCardHistoryResult.ACCEPTED
    assert _assess(second, sequence=2) is CapabilityCardHistoryResult.ACCEPTED
    assert _assess(first, sequence=2) is CapabilityCardHistoryResult.SEQUENCE_MISMATCH


def test_startup_rejects_unsafe_or_ambiguous_state_files(tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    empty.touch(mode=0o600)
    with pytest.raises(CapabilityCardTrustError, match="unknown or missing schema"):
        _history(empty)

    directory = tmp_path / "directory.db"
    directory.mkdir()
    with pytest.raises(CapabilityCardTrustError, match="regular non-symlink"):
        _history(directory)

    target = tmp_path / "target.db"
    _history(target)
    linked = tmp_path / "linked.db"
    linked.symlink_to(target)
    with pytest.raises(CapabilityCardTrustError, match="regular non-symlink"):
        _history(linked)

    if os.name == "posix":
        target.chmod(0o644)
        with pytest.raises(CapabilityCardTrustError, match="chmod 600"):
            _history(target)


def test_startup_rejects_schema_row_and_capacity_corruption(tmp_path: Path) -> None:
    wrong_schema = tmp_path / "wrong.db"
    wrong_schema.touch(mode=0o600)
    with sqlite3.connect(wrong_schema) as connection:
        connection.execute("CREATE TABLE capability_card_history (agent TEXT)")
        connection.execute("PRAGMA user_version=1")
    with pytest.raises(CapabilityCardTrustError, match="schema does not match"):
        _history(wrong_schema)

    corrupt_row = tmp_path / "row.db"
    history = _history(corrupt_row)
    assert _assess(history) is CapabilityCardHistoryResult.ACCEPTED
    with sqlite3.connect(corrupt_row) as connection:
        connection.execute(
            "UPDATE capability_card_history SET route_capabilities = ?",
            ('["skills:z","skills:a"]',),
        )
    with pytest.raises(CapabilityCardTrustError, match="not canonical and unique"):
        _history(corrupt_row)

    over_capacity = tmp_path / "capacity.db"
    history = _history(over_capacity, capacity=2)
    assert _assess(history) is CapabilityCardHistoryResult.ACCEPTED
    assert (
        _assess(history, agent="P/other", key_id="P:other") is CapabilityCardHistoryResult.ACCEPTED
    )
    with pytest.raises(CapabilityCardTrustError, match="exceeds the configured"):
        _history(over_capacity, capacity=1)


def test_runtime_lock_and_missing_database_project_history_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    history = _history(path)
    lock = sqlite3.connect(path, timeout=0.0, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        assert _assess(history) is CapabilityCardHistoryResult.HISTORY_UNAVAILABLE
    finally:
        lock.rollback()
        lock.close()

    path.unlink()
    assert _assess(history) is CapabilityCardHistoryResult.HISTORY_UNAVAILABLE


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent": ""},
        {"key_id": ""},
        {"sequence": 0},
        {"route_capabilities": {"skills:python"}},
        {"route_capabilities": frozenset({""})},
        {"card_digest": "x" * 513},
        {"expires_at": float("nan")},
        {"now": float("inf")},
        {"route_capabilities": frozenset({_OVERSIZE_CAPABILITY})},
    ],
    ids=[
        "empty-agent",
        "empty-key-id",
        "zero-sequence",
        "capabilities-not-frozenset",
        "empty-capability-token",
        "card-digest-too-large",
        "nan-expiry",
        "inf-now",
        "capability-token-too-large",
    ],
)
def test_invalid_runtime_transition_is_fail_visible(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    history = _history(tmp_path / "history.db")
    assert _assess_unchecked(history, overrides) is CapabilityCardHistoryResult.HISTORY_UNAVAILABLE


_CORRUPTION_UPDATES = {
    "agent": "UPDATE capability_card_history SET agent = ?",
    "sequence": "UPDATE capability_card_history SET sequence = ?",
    "route_capabilities": "UPDATE capability_card_history SET route_capabilities = ?",
    "card_digest": "UPDATE capability_card_history SET card_digest = ?",
    "expires_at": "UPDATE capability_card_history SET expires_at = ?",
    "observed_at": "UPDATE capability_card_history SET observed_at = ?",
}


@pytest.mark.parametrize(
    ("column", "value", "match"),
    [
        ("route_capabilities", sqlite3.Binary(b"x"), "non-text capabilities"),
        (
            "route_capabilities",
            _OVERSIZE_CAPABILITIES_JSON,
            "capability floor is too large",
        ),
        ("route_capabilities", "{", "invalid capability JSON"),
        ("route_capabilities", "{}", "capabilities must be non-empty strings"),
        ("route_capabilities", '[""]', "capabilities must be non-empty strings"),
        ("sequence", sqlite3.Binary(b"x"), "invalid sequence"),
        ("card_digest", sqlite3.Binary(b"x"), "invalid card digest"),
        ("expires_at", "bad", "invalid expiry"),
        ("observed_at", "bad", "invalid observation time"),
        ("expires_at", float("inf"), "non-finite timestamps"),
        ("agent", sqlite3.Binary(b"x"), "invalid binding"),
        ("agent", "x" * 513, "agent is too large"),
    ],
    ids=[
        "caps-non-text",
        "caps-floor-too-large",
        "caps-invalid-json",
        "caps-empty-object",
        "caps-empty-string-token",
        "sequence-binary",
        "digest-binary",
        "expiry-bad",
        "observed-bad",
        "expiry-inf",
        "agent-binary",
        "agent-too-large",
    ],
)
def test_startup_rejects_each_corrupt_retained_field(
    tmp_path: Path,
    column: str,
    value: object,
    match: str,
) -> None:
    path = tmp_path / f"{column}-{abs(hash(match))}.db"
    history = _history(path)
    assert _assess(history) is CapabilityCardHistoryResult.ACCEPTED
    with sqlite3.connect(path) as connection:
        connection.execute(_CORRUPTION_UPDATES[column], (value,))
    with pytest.raises(CapabilityCardTrustError, match=match):
        _history(path)


def test_runtime_corruption_rolls_back_and_projects_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    history = _history(path)
    assert _assess(history) is CapabilityCardHistoryResult.ACCEPTED
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE capability_card_history SET route_capabilities = '{'")
    assert _assess(history, sequence=2) is CapabilityCardHistoryResult.HISTORY_UNAVAILABLE
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT route_capabilities FROM capability_card_history"
        ).fetchone() == ("{",)


def test_creation_and_owner_validation_fail_before_sqlite_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    occupied_parent = tmp_path / "occupied"
    occupied_parent.write_text("not a directory", encoding="utf-8")
    with pytest.raises(CapabilityCardTrustError, match="cannot inspect"):
        _history(occupied_parent / "history.db")

    path = tmp_path / "history.db"
    _history(path)
    monkeypatch.setattr(os, "geteuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(CapabilityCardTrustError, match="owned by the current user"):
        _history(path)


def test_new_file_creation_failure_is_reported_without_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "history.db"

    def fail_open(*_args: object, **_kwargs: object) -> int:
        raise OSError("read-only directory")

    monkeypatch.setattr(os, "open", fail_open)
    with pytest.raises(CapabilityCardTrustError, match="cannot create"):
        _history(path)
    assert not path.exists()


def test_non_posix_runtime_uses_sqlite_without_posix_mode_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _history(tmp_path / "history.db")
    monkeypatch.setattr(os, "name", "nt")
    assert _assess(history) is CapabilityCardHistoryResult.ACCEPTED


class _JournalRefusingConnection(sqlite3.Connection):
    """Return a non-delete journal mode through the real connection surface."""

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        if sql == "PRAGMA journal_mode=DELETE":
            return super().execute("SELECT 'wal'")
        return super().execute(sql, parameters)


class _QuickCheckFailingConnection(sqlite3.Connection):
    """Return a failed quick-check result through the real connection surface."""

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        if sql == "PRAGMA quick_check":
            return super().execute("SELECT 'corrupt'")
        return super().execute(sql, parameters)


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (_JournalRefusingConnection, "refused DELETE journalling"),
        (_QuickCheckFailingConnection, "failed quick_check"),
    ],
)
def test_initialisation_rejects_storage_engine_invariant_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: type[sqlite3.Connection],
    match: str,
) -> None:
    original_connect = sqlite3.connect

    def connect(
        database: str,
        *,
        timeout: float,
        isolation_level: None,
    ) -> sqlite3.Connection:
        return original_connect(
            database,
            timeout=timeout,
            isolation_level=isolation_level,
            factory=factory,
        )

    monkeypatch.setattr(sqlite3, "connect", connect)
    path = tmp_path / "history.db"
    with pytest.raises(CapabilityCardTrustError, match=match):
        _history(path)
    assert not path.exists()


def test_new_database_guard_rejects_nonempty_created_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "history.db"
    _history(path)
    monkeypatch.setattr(history_module, "_prepare_history_file", lambda _path: True)
    with pytest.raises(CapabilityCardTrustError, match="new .* is not empty"):
        _history(path)


def test_failed_first_initialisation_removes_only_its_new_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "history.db"

    def fail_connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("storage unavailable")

    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    with pytest.raises(CapabilityCardTrustError, match="cannot open"):
        _history(path)
    assert not path.exists()


def test_failed_initialisation_reports_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "history.db"

    def fail_connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("storage unavailable")

    def fail_unlink(_self: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise OSError("read-only directory")

    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(CapabilityCardTrustError, match="cannot open"):
        _history(path)
    assert "cannot remove failed capability-card history file" in caplog.text
