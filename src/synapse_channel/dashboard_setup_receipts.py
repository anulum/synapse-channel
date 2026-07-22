# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable capability-bound setup receipt chain
"""Persist canonical, token-free setup receipts in a tamper-evident chain.

The store is intentionally independent of HTTP and effect adapters. It gives
later setup slices one fail-closed persistence boundary that can be proved
writable before mutation is advertised. Browser projections contain only the
same bounded evidence persisted in the receipt; no bearer, confirmation nonce,
filesystem path, subprocess output, or free-form error text enters this model.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sqlite3
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, cast
from uuid import UUID

from synapse_channel.core.aef_canonical import (
    IJSON_MAX_INTEGER,
    AefCanonicalizationError,
    canonical_json,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.persistence import BUSY_TIMEOUT_MS
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.dashboard_setup_contract import SetupEffectKind, SetupProfileId

SETUP_RECEIPT_VERSION = 1
SETUP_RECEIPT_DATABASE = "setup-receipts.db"
MAX_SETUP_RECEIPT_EFFECTS = 16
MAX_SETUP_RECEIPT_ARTIFACTS = 16

SetupCapability = Literal["setup_plan", "setup_apply"]
SetupReceiptOutcome = Literal[
    "planned",
    "authorised",
    "applied",
    "already_satisfied",
    "drifted",
    "partial",
    "denied",
    "expired",
    "refused",
    "rate_limited",
]
SetupInspectionVerdict = Literal[
    "not_checked",
    "absent",
    "satisfied",
    "unsatisfied",
    "blocked",
    "unverifiable",
    "failed",
]
SetupReceiptReason = Literal[
    "none",
    "capability_denied",
    "plan_expired",
    "plan_drift",
    "unsafe_posture",
    "rate_limit",
    "effect_timeout",
    "effect_failed",
    "postcondition_unverified",
    "partial_effects",
]

_GENESIS_DIGEST = "0" * 64
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{22,128}\Z")
_SAFE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@:+-]*\Z")
_OUTCOMES = frozenset(
    {
        "planned",
        "authorised",
        "applied",
        "already_satisfied",
        "drifted",
        "partial",
        "denied",
        "expired",
        "refused",
        "rate_limited",
    }
)
_CAPABILITIES = frozenset({"setup_plan", "setup_apply"})
_VERDICTS = frozenset(
    {
        "not_checked",
        "absent",
        "satisfied",
        "unsatisfied",
        "blocked",
        "unverifiable",
        "failed",
    }
)
_REASONS = frozenset(
    {
        "none",
        "capability_denied",
        "plan_expired",
        "plan_drift",
        "unsafe_posture",
        "rate_limit",
        "effect_timeout",
        "effect_failed",
        "postcondition_unverified",
        "partial_effects",
    }
)
_PROFILE_IDS = frozenset({"local-ephemeral", "local-durable-existing"})
_EFFECT_KINDS = frozenset({"runtime_directory", "user_unit", "durable_store"})


class SetupReceiptStoreError(SynapseError, ValueError):
    """Base class for a setup receipt persistence or integrity refusal."""

    code = "setup_receipt_store"


class SetupReceiptPersistenceError(SetupReceiptStoreError):
    """A receipt could not be durably persisted without exposing raw I/O detail."""

    code = "setup_receipt_persistence"


class SetupReceiptIntegrityError(SetupReceiptStoreError):
    """The durable receipt database or hash chain failed closed verification."""

    code = "setup_receipt_integrity"


@dataclass(frozen=True, slots=True)
class SetupEffectReceipt:
    """Bounded before/after evidence for one package-owned setup effect."""

    kind: SetupEffectKind
    target: str
    before: SetupInspectionVerdict
    after: SetupInspectionVerdict

    def __post_init__(self) -> None:
        """Reject unknown effect and verdict tokens or unsafe target text."""
        if self.kind not in _EFFECT_KINDS:
            raise ValueError("setup receipt effect kind is invalid")
        _validate_safe_text(self.target, name="effect target", limit=128)
        if self.before not in _VERDICTS or self.after not in _VERDICTS:
            raise ValueError("setup receipt effect verdict is invalid")

    def as_dict(self) -> dict[str, str]:
        """Return the canonical token-only effect projection."""
        return {
            "kind": self.kind,
            "target": self.target,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True, slots=True)
class SetupArtifactDigest:
    """One non-secret package or template artifact digest."""

    artifact: str
    digest: str

    def __post_init__(self) -> None:
        """Require a bounded package-owned name and lowercase SHA-256 digest."""
        _validate_safe_text(self.artifact, name="artifact", limit=128)
        _validate_digest(self.digest, name="artifact digest")

    def as_dict(self) -> dict[str, str]:
        """Return the canonical artifact projection."""
        return {"artifact": self.artifact, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class SetupReceiptDraft:
    """Server-owned evidence awaiting an assigned sequence and chain digest."""

    request_id: str
    plan_id: str
    plan_digest: str
    principal_id: str
    capability: SetupCapability
    profile: SetupProfileId
    profile_version: int
    configuration_generation: str
    timestamp_ms: int
    outcome: SetupReceiptOutcome
    effects: tuple[SetupEffectReceipt, ...]
    package_version: str
    template_version: str
    artifacts: tuple[SetupArtifactDigest, ...] = ()
    reason: SetupReceiptReason = "none"

    def __post_init__(self) -> None:
        """Validate every persisted field before a transaction begins."""
        _validate_uuid(self.request_id)
        if _OPAQUE_ID.fullmatch(self.plan_id) is None:
            raise ValueError("setup receipt plan id is invalid")
        _validate_digest(self.plan_digest, name="plan digest")
        _validate_safe_text(self.principal_id, name="principal", limit=128)
        if self.capability not in _CAPABILITIES:
            raise ValueError("setup receipt capability is invalid")
        if self.profile not in _PROFILE_IDS:
            raise ValueError("setup receipt profile is invalid")
        if type(self.profile_version) is not int or not 1 <= self.profile_version <= 65535:
            raise ValueError("setup receipt profile version is invalid")
        _validate_digest(self.configuration_generation, name="configuration generation")
        if type(self.timestamp_ms) is not int or not 1 <= self.timestamp_ms <= IJSON_MAX_INTEGER:
            raise ValueError("setup receipt timestamp is invalid")
        if self.outcome not in _OUTCOMES:
            raise ValueError("setup receipt outcome is invalid")
        if not 0 <= len(self.effects) <= MAX_SETUP_RECEIPT_EFFECTS:
            raise ValueError("setup receipt effect count is invalid")
        if not 0 <= len(self.artifacts) <= MAX_SETUP_RECEIPT_ARTIFACTS:
            raise ValueError("setup receipt artifact count is invalid")
        _validate_safe_text(self.package_version, name="package version", limit=64)
        _validate_safe_text(self.template_version, name="template version", limit=64)
        if self.reason not in _REASONS:
            raise ValueError("setup receipt reason is invalid")
        if self.outcome in {"planned", "authorised", "applied", "already_satisfied"}:
            if self.reason != "none":
                raise ValueError("successful setup receipt outcome cannot carry a failure reason")
        elif self.reason == "none":
            raise ValueError("unsuccessful setup receipt outcome requires a bounded reason")
        artifact_names = [item.artifact for item in self.artifacts]
        if len(set(artifact_names)) != len(artifact_names):
            raise ValueError("setup receipt artifact names must be unique")


@dataclass(frozen=True, slots=True)
class SetupReceipt:
    """One immutable, canonical setup receipt returned after durable commit."""

    sequence: int
    draft: SetupReceiptDraft
    previous_receipt_digest: str
    receipt_digest: str

    def as_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        """Return the canonical receipt document or its digest preimage."""
        document: dict[str, object] = {
            "version": SETUP_RECEIPT_VERSION,
            "sequence": self.sequence,
            "request_id": self.draft.request_id,
            "plan_id": self.draft.plan_id,
            "plan_digest": self.draft.plan_digest,
            "principal_id": self.draft.principal_id,
            "capability": self.draft.capability,
            "profile": self.draft.profile,
            "profile_version": self.draft.profile_version,
            "configuration_generation": self.draft.configuration_generation,
            "timestamp_ms": self.draft.timestamp_ms,
            "outcome": self.draft.outcome,
            "effects": [effect.as_dict() for effect in self.draft.effects],
            "package_version": self.draft.package_version,
            "template_version": self.draft.template_version,
            "artifacts": [artifact.as_dict() for artifact in self.draft.artifacts],
            "reason": self.draft.reason,
            "previous_receipt_digest": self.previous_receipt_digest,
        }
        if include_digest:
            document["receipt_digest"] = self.receipt_digest
        return document

    def browser_projection(self) -> dict[str, object]:
        """Return the complete token-free receipt evidence for an authorised UI."""
        return self.as_dict()


class SetupReceiptStore:
    """Thread-safe FULL-synchronous append-only setup receipt chain."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = ensure_private_dir(
            directory,
            parents=True,
            purpose="setup receipt directory",
        )
        self.path = self.directory / SETUP_RECEIPT_DATABASE
        _prepare_database_file(self.path)
        try:
            self._conn = sqlite3.connect(
                self.path,
                isolation_level=None,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise SetupReceiptPersistenceError("setup receipt store could not be opened") from exc
        self._lock = threading.Lock()
        try:
            self._configure()
        except BaseException:
            self._conn.close()
            raise

    def append(self, draft: SetupReceiptDraft) -> SetupReceipt:
        """Atomically assign, chain, and durably commit one setup receipt."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                rows = self._conn.execute(
                    "SELECT sequence, receipt_digest, previous_receipt_digest, "
                    "canonical_receipt FROM setup_receipts ORDER BY sequence"
                ).fetchall()
                existing = _decode_rows(rows)
                sequence = len(existing) + 1
                previous = _GENESIS_DIGEST if not existing else existing[-1].receipt_digest
                receipt = _build_receipt(sequence, draft, previous)
                canonical = canonical_json(receipt.as_dict())
                self._conn.execute(
                    "INSERT INTO setup_receipts "
                    "(sequence, receipt_digest, previous_receipt_digest, canonical_receipt) "
                    "VALUES (?, ?, ?, ?)",
                    (sequence, receipt.receipt_digest, previous, canonical),
                )
                _commit(self._conn)
            except BaseException as exc:
                with contextlib.suppress(BaseException):
                    self._conn.rollback()
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                if isinstance(exc, SetupReceiptStoreError):
                    raise
                raise SetupReceiptPersistenceError(
                    "setup receipt could not be durably persisted"
                ) from exc
        return receipt

    def read_all(self) -> tuple[SetupReceipt, ...]:
        """Read and verify every canonical receipt in sequence order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sequence, receipt_digest, previous_receipt_digest, canonical_receipt "
                "FROM setup_receipts ORDER BY sequence"
            ).fetchall()
            return _decode_rows(rows)

    def count(self) -> int:
        """Return the number of durably committed setup receipts."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM setup_receipts").fetchone()
        return int(row[0])

    def close(self) -> None:
        """Close the receipt database connection."""
        self._conn.close()

    def __enter__(self) -> SetupReceiptStore:
        """Return this open store for context-manager use."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the store when leaving a context manager."""
        self.close()

    def _configure(self) -> None:
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS setup_receipt_metadata ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
                "version INTEGER NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS setup_receipts ("
                "sequence INTEGER PRIMARY KEY CHECK(sequence >= 1), "
                "receipt_digest TEXT NOT NULL UNIQUE, "
                "previous_receipt_digest TEXT NOT NULL, "
                "canonical_receipt BLOB NOT NULL)"
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO setup_receipt_metadata (singleton, version) VALUES (1, ?)",
                (SETUP_RECEIPT_VERSION,),
            )
            metadata = self._conn.execute(
                "SELECT version FROM setup_receipt_metadata WHERE singleton = 1"
            ).fetchone()
            if metadata != (SETUP_RECEIPT_VERSION,):
                raise SetupReceiptIntegrityError("setup receipt metadata version is invalid")
            integrity = self._conn.execute("PRAGMA integrity_check").fetchone()
            _require_database_integrity(integrity)
            rows = self._conn.execute(
                "SELECT sequence, receipt_digest, previous_receipt_digest, canonical_receipt "
                "FROM setup_receipts ORDER BY sequence"
            ).fetchall()
            _decode_rows(rows)
            _commit(self._conn)
            _restrict_file(self.path)
            _restrict_file(Path(f"{self.path}-wal"))
            _restrict_file(Path(f"{self.path}-shm"))
        except SetupReceiptStoreError:
            with contextlib.suppress(BaseException):
                self._conn.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            with contextlib.suppress(BaseException):
                self._conn.rollback()
            raise SetupReceiptPersistenceError(
                "setup receipt store could not be initialised"
            ) from exc


def _build_receipt(
    sequence: int,
    draft: SetupReceiptDraft,
    previous_receipt_digest: str,
) -> SetupReceipt:
    _validate_digest(previous_receipt_digest, name="previous receipt digest")
    unsigned = SetupReceipt(sequence, draft, previous_receipt_digest, "")
    digest = hashlib.sha256(
        b"synapse-setup-receipt-v1\x00" + canonical_json(unsigned.as_dict(include_digest=False))
    ).hexdigest()
    return SetupReceipt(sequence, draft, previous_receipt_digest, digest)


def _decode_rows(rows: object) -> tuple[SetupReceipt, ...]:
    if not isinstance(rows, list | tuple):
        raise SetupReceiptIntegrityError("stored setup receipt rows are malformed")
    receipts: list[SetupReceipt] = []
    expected_sequence = 1
    expected_previous = _GENESIS_DIGEST
    for row in rows:
        receipt = _decode_row(
            row,
            expected_sequence=expected_sequence,
            expected_previous=expected_previous,
        )
        receipts.append(receipt)
        expected_sequence += 1
        expected_previous = receipt.receipt_digest
    return tuple(receipts)


def _decode_row(
    row: object,
    *,
    expected_sequence: int,
    expected_previous: str,
) -> SetupReceipt:
    if not isinstance(row, tuple) or len(row) != 4:
        raise SetupReceiptIntegrityError("stored setup receipt row is malformed")
    sequence, indexed_digest, indexed_previous, raw_value = row
    try:
        raw = bytes(raw_value)
        value = _load_canonical_receipt(raw)
        receipt = _receipt_from_document(value)
    except (AefCanonicalizationError, TypeError, ValueError) as exc:
        raise SetupReceiptIntegrityError("stored setup receipt is malformed") from exc
    rebuilt = _build_receipt(receipt.sequence, receipt.draft, receipt.previous_receipt_digest)
    if (
        sequence != expected_sequence
        or receipt.sequence != expected_sequence
        or indexed_previous != expected_previous
        or receipt.previous_receipt_digest != expected_previous
        or indexed_digest != receipt.receipt_digest
        or receipt.receipt_digest != rebuilt.receipt_digest
        or canonical_json(receipt.as_dict()) != raw
    ):
        raise SetupReceiptIntegrityError("stored setup receipt chain is invalid")
    return receipt


def _load_canonical_receipt(raw: bytes) -> dict[str, object]:
    import json

    value = json.loads(raw)
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise SetupReceiptIntegrityError("stored setup receipt is not canonical")
    return cast(dict[str, object], value)


def _receipt_from_document(document: dict[str, object]) -> SetupReceipt:
    expected = {
        "version",
        "sequence",
        "request_id",
        "plan_id",
        "plan_digest",
        "principal_id",
        "capability",
        "profile",
        "profile_version",
        "configuration_generation",
        "timestamp_ms",
        "outcome",
        "effects",
        "package_version",
        "template_version",
        "artifacts",
        "reason",
        "previous_receipt_digest",
        "receipt_digest",
    }
    if set(document) != expected or document["version"] != SETUP_RECEIPT_VERSION:
        raise ValueError("setup receipt fields are invalid")
    effects_raw = document["effects"]
    artifacts_raw = document["artifacts"]
    if not isinstance(effects_raw, list) or not isinstance(artifacts_raw, list):
        raise ValueError("setup receipt collections are invalid")
    effects = tuple(_effect_from_document(item) for item in effects_raw)
    artifacts = tuple(_artifact_from_document(item) for item in artifacts_raw)
    draft = SetupReceiptDraft(
        request_id=_require_str(document["request_id"]),
        plan_id=_require_str(document["plan_id"]),
        plan_digest=_require_str(document["plan_digest"]),
        principal_id=_require_str(document["principal_id"]),
        capability=cast(SetupCapability, _require_str(document["capability"])),
        profile=cast(SetupProfileId, _require_str(document["profile"])),
        profile_version=_require_int(document["profile_version"]),
        configuration_generation=_require_str(document["configuration_generation"]),
        timestamp_ms=_require_int(document["timestamp_ms"]),
        outcome=cast(SetupReceiptOutcome, _require_str(document["outcome"])),
        effects=effects,
        package_version=_require_str(document["package_version"]),
        template_version=_require_str(document["template_version"]),
        artifacts=artifacts,
        reason=cast(SetupReceiptReason, _require_str(document["reason"])),
    )
    return SetupReceipt(
        sequence=_require_int(document["sequence"]),
        draft=draft,
        previous_receipt_digest=_require_str(document["previous_receipt_digest"]),
        receipt_digest=_require_str(document["receipt_digest"]),
    )


def _effect_from_document(value: object) -> SetupEffectReceipt:
    if not isinstance(value, dict) or set(value) != {"kind", "target", "before", "after"}:
        raise ValueError("setup receipt effect fields are invalid")
    return SetupEffectReceipt(
        kind=cast(SetupEffectKind, _require_str(value["kind"])),
        target=_require_str(value["target"]),
        before=cast(SetupInspectionVerdict, _require_str(value["before"])),
        after=cast(SetupInspectionVerdict, _require_str(value["after"])),
    )


def _artifact_from_document(value: object) -> SetupArtifactDigest:
    if not isinstance(value, dict) or set(value) != {"artifact", "digest"}:
        raise ValueError("setup receipt artifact fields are invalid")
    return SetupArtifactDigest(
        artifact=_require_str(value["artifact"]),
        digest=_require_str(value["digest"]),
    )


def _prepare_database_file(path: Path) -> None:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise SetupReceiptPersistenceError("setup receipt database path is unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (
            hasattr(os, "geteuid") and info.st_uid != os.geteuid()
        ):
            raise SetupReceiptPersistenceError("setup receipt database path is unsafe")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _restrict_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        os.chmod(path, 0o600, follow_symlinks=False)
    except OSError as exc:
        raise SetupReceiptPersistenceError("setup receipt database permissions are unsafe") from exc


def _commit(connection: sqlite3.Connection) -> None:
    connection.commit()


def _require_database_integrity(result: object) -> None:
    if result != ("ok",):
        raise SetupReceiptIntegrityError("setup receipt database integrity check failed")


def _validate_uuid(value: str) -> None:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError("setup receipt request id is invalid") from exc
    if str(parsed) != value:
        raise ValueError("setup receipt request id is invalid")


def _validate_digest(value: str, *, name: str) -> None:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise ValueError(f"setup receipt {name} is invalid")


def _validate_safe_text(value: str, *, name: str, limit: int) -> None:
    if (
        not isinstance(value, str)
        or len(value.encode("ascii", errors="ignore")) != len(value)
        or not 1 <= len(value) <= limit
        or _SAFE_TEXT.fullmatch(value) is None
    ):
        raise ValueError(f"setup receipt {name} is invalid")


def _require_str(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("setup receipt field must be text")
    return value


def _require_int(value: object) -> int:
    if type(value) is not int:
        raise ValueError("setup receipt field must be an integer")
    return value
