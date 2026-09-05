# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable one-use setup authorization ledger
"""Reserve setup authorizations atomically before a future executor mutates."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, cast

from synapse_channel.core.persistence import BUSY_TIMEOUT_MS
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.setup_authorization import (
    SetupAuthorizationError,
    validate_setup_authorization,
)
from synapse_channel.setup_contract import SetupErrorCode

SETUP_LEDGER_VERSION = 1
SETUP_LEDGER_DATABASE = "setup-authorizations.db"

SetupLedgerState = Literal["reserved", "applied", "failed", "recovered"]
SetupEffectOutcome = Literal["applied", "failed"]

_DIGEST_LENGTH = 64
_NONCE_DOMAIN = b"synapse-setup-authorization-nonce-v1\x00"


class SetupLedgerError(SetupAuthorizationError):
    """Stable fail-closed ledger refusal."""

    code: SetupErrorCode = "authorization_ledger_unavailable"

    def __init__(self, code: SetupErrorCode) -> None:
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SetupLedgerRecord:
    """One token-free durable authorization lifecycle record."""

    authorization_digest: str
    plan_digest: str
    nonce_digest: str
    reserved_at: int
    state: SetupLedgerState
    effect_receipt_digest: str | None = None
    recovery_receipt_digest: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return the bounded token-free record projection."""
        return {
            "authorization_digest": self.authorization_digest,
            "plan_digest": self.plan_digest,
            "nonce_digest": self.nonce_digest,
            "reserved_at": self.reserved_at,
            "state": self.state,
            "effect_receipt_digest": self.effect_receipt_digest,
            "recovery_receipt_digest": self.recovery_receipt_digest,
        }


def default_setup_ledger_dir(*, env: Mapping[str, str] | None = None) -> Path:
    """Return the private per-user setup ledger directory without creating it."""
    environment = os.environ if env is None else env
    configured = environment.get("XDG_STATE_HOME", "").strip()
    if configured:
        root = Path(configured)
    else:
        root = Path(environment.get("HOME", str(Path.home()))) / ".local" / "state"
    if not root.is_absolute():
        raise SetupLedgerError("authorization_ledger_unavailable")
    return root / "synapse-channel"


class SetupAuthorizationLedger:
    """Owner-only SQLite ledger with atomic one-use nonce reservation."""

    def __init__(self, directory: str | Path) -> None:
        try:
            self.directory = ensure_private_dir(
                directory,
                parents=True,
                purpose="setup authorization ledger directory",
            )
            self.path = self.directory / SETUP_LEDGER_DATABASE
            _prepare_database_file(self.path)
            self._connection = sqlite3.connect(
                self.path,
                isolation_level=None,
                check_same_thread=False,
                timeout=BUSY_TIMEOUT_MS / 1000,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise SetupLedgerError("authorization_ledger_unavailable") from exc
        self._lock = threading.Lock()
        try:
            self._configure()
        except BaseException:
            self._connection.close()
            raise

    def reserve(
        self,
        plan: dict[str, object],
        authorization: dict[str, object],
        *,
        now: int,
    ) -> SetupLedgerRecord:
        """Atomically reserve one valid nonce before the first future effect."""
        validated = validate_setup_authorization(plan, authorization, now=now)
        auth_digest = cast(str, validated["authorization_digest"])
        plan_digest = cast(str, validated["plan_digest"])
        nonce_digest = _nonce_digest(cast(str, validated["confirmation_nonce"]))
        record = SetupLedgerRecord(
            authorization_digest=auth_digest,
            plan_digest=plan_digest,
            nonce_digest=nonce_digest,
            reserved_at=now,
            state="reserved",
        )
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                existing = self._connection.execute(
                    "SELECT 1 FROM setup_authorizations "
                    "WHERE nonce_digest = ? OR authorization_digest = ? LIMIT 1",
                    (nonce_digest, auth_digest),
                ).fetchone()
                if existing is not None:
                    self._connection.rollback()
                    raise SetupLedgerError("authorization_replayed")
                self._connection.execute(
                    "INSERT INTO setup_authorizations "
                    "(nonce_digest, authorization_digest, plan_digest, reserved_at, state, "
                    "effect_receipt_digest, recovery_receipt_digest) "
                    "VALUES (?, ?, ?, ?, 'reserved', NULL, NULL)",
                    (nonce_digest, auth_digest, plan_digest, now),
                )
                self._connection.commit()
            except SetupLedgerError:
                raise
            except (OSError, sqlite3.Error) as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._connection.rollback()
                raise SetupLedgerError("authorization_ledger_unavailable") from exc
        return record

    def finish(
        self,
        authorization_digest: str,
        *,
        outcome: SetupEffectOutcome,
        receipt_digest: str,
    ) -> SetupLedgerRecord:
        """Finish a reserved authorization as applied or failed exactly once."""
        _require_transition_digest(authorization_digest)
        _require_transition_digest(receipt_digest)
        if outcome not in {"applied", "failed"}:
            raise SetupLedgerError("authorization_transition_invalid")
        return self._transition(
            authorization_digest,
            allowed_from=frozenset({"reserved"}),
            state=outcome,
            effect_receipt_digest=receipt_digest,
        )

    def recover(
        self,
        authorization_digest: str,
        *,
        receipt_digest: str,
    ) -> SetupLedgerRecord:
        """Mark a reserved or failed authorization as explicitly recovered."""
        _require_transition_digest(authorization_digest)
        _require_transition_digest(receipt_digest)
        return self._transition(
            authorization_digest,
            allowed_from=frozenset({"reserved", "failed"}),
            state="recovered",
            recovery_receipt_digest=receipt_digest,
        )

    def get(self, authorization_digest: str) -> SetupLedgerRecord | None:
        """Return one validated token-free record by authorization digest."""
        _require_transition_digest(authorization_digest)
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT authorization_digest, plan_digest, nonce_digest, reserved_at, state, "
                    "effect_receipt_digest, recovery_receipt_digest "
                    "FROM setup_authorizations WHERE authorization_digest = ?",
                    (authorization_digest,),
                ).fetchone()
            return None if row is None else _decode_record(row)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise SetupLedgerError("authorization_ledger_unavailable") from exc

    def close(self) -> None:
        """Close the ledger database connection."""
        self._connection.close()

    def __enter__(self) -> SetupAuthorizationLedger:
        """Return this open ledger."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the ledger when leaving a context manager."""
        self.close()

    def _transition(
        self,
        authorization_digest: str,
        *,
        allowed_from: frozenset[str],
        state: SetupLedgerState,
        effect_receipt_digest: str | None = None,
        recovery_receipt_digest: str | None = None,
    ) -> SetupLedgerRecord:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT authorization_digest, plan_digest, nonce_digest, reserved_at, state, "
                    "effect_receipt_digest, recovery_receipt_digest "
                    "FROM setup_authorizations WHERE authorization_digest = ?",
                    (authorization_digest,),
                ).fetchone()
                if row is None:
                    self._connection.rollback()
                    raise SetupLedgerError("authorization_transition_invalid")
                current = _decode_record(row)
                if current.state not in allowed_from:
                    self._connection.rollback()
                    raise SetupLedgerError("authorization_transition_invalid")
                effect_digest = effect_receipt_digest or current.effect_receipt_digest
                recovery_digest = recovery_receipt_digest or current.recovery_receipt_digest
                self._connection.execute(
                    "UPDATE setup_authorizations SET state = ?, effect_receipt_digest = ?, "
                    "recovery_receipt_digest = ? WHERE authorization_digest = ?",
                    (state, effect_digest, recovery_digest, authorization_digest),
                )
                self._connection.commit()
            except SetupLedgerError:
                raise
            except (OSError, sqlite3.Error, ValueError) as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._connection.rollback()
                raise SetupLedgerError("authorization_ledger_unavailable") from exc
        return SetupLedgerRecord(
            authorization_digest=current.authorization_digest,
            plan_digest=current.plan_digest,
            nonce_digest=current.nonce_digest,
            reserved_at=current.reserved_at,
            state=state,
            effect_receipt_digest=effect_digest,
            recovery_receipt_digest=recovery_digest,
        )

    def _configure(self) -> None:
        try:
            self._connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS setup_ledger_metadata ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version INTEGER NOT NULL)"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS setup_authorizations ("
                "nonce_digest TEXT PRIMARY KEY, authorization_digest TEXT NOT NULL UNIQUE, "
                "plan_digest TEXT NOT NULL, reserved_at INTEGER NOT NULL CHECK(reserved_at >= 0), "
                "state TEXT NOT NULL CHECK(state IN ('reserved','applied','failed','recovered')), "
                "effect_receipt_digest TEXT, recovery_receipt_digest TEXT)"
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO setup_ledger_metadata (singleton, version) VALUES (1, ?)",
                (SETUP_LEDGER_VERSION,),
            )
            metadata = self._connection.execute(
                "SELECT version FROM setup_ledger_metadata WHERE singleton = 1"
            ).fetchone()
            integrity = self._connection.execute("PRAGMA integrity_check").fetchone()
            if metadata != (SETUP_LEDGER_VERSION,) or integrity != ("ok",):
                raise SetupLedgerError("authorization_ledger_unavailable")
            rows = self._connection.execute(
                "SELECT authorization_digest, plan_digest, nonce_digest, reserved_at, state, "
                "effect_receipt_digest, recovery_receipt_digest FROM setup_authorizations"
            ).fetchall()
            for row in rows:
                _decode_record(row)
            self._connection.commit()
            _restrict_file(self.path, required=True)
            _restrict_file(Path(f"{self.path}-wal"))
            _restrict_file(Path(f"{self.path}-shm"))
        except SetupLedgerError:
            with contextlib.suppress(sqlite3.Error):
                self._connection.rollback()
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            with contextlib.suppress(sqlite3.Error):
                self._connection.rollback()
            raise SetupLedgerError("authorization_ledger_unavailable") from exc


def _nonce_digest(nonce: str) -> str:
    return hashlib.sha256(_NONCE_DOMAIN + nonce.encode("ascii")).hexdigest()


def _validate_digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("setup authorization ledger digest is invalid")
    return value


def _require_transition_digest(value: object) -> str:
    try:
        return _validate_digest(value)
    except ValueError as exc:
        raise SetupLedgerError("authorization_transition_invalid") from exc


def _decode_record(
    row: tuple[object, object, object, object, object, object, object],
) -> SetupLedgerRecord:
    authorization_digest, plan_digest, nonce_digest, reserved_at, state, effect, recovery = row
    authorization_value = _validate_digest(authorization_digest)
    plan_value = _validate_digest(plan_digest)
    nonce_value = _validate_digest(nonce_digest)
    if not isinstance(reserved_at, int) or reserved_at < 0:
        raise ValueError("setup authorization reservation time is invalid")
    if state not in {"reserved", "applied", "failed", "recovered"}:
        raise ValueError("setup authorization ledger state is invalid")
    effect_value = None if effect is None else _validate_digest(effect)
    recovery_value = None if recovery is None else _validate_digest(recovery)
    return SetupLedgerRecord(
        authorization_digest=authorization_value,
        plan_digest=plan_value,
        nonce_digest=nonce_value,
        reserved_at=reserved_at,
        state=cast(SetupLedgerState, state),
        effect_receipt_digest=effect_value,
        recovery_receipt_digest=recovery_value,
    )


def _prepare_database_file(path: Path) -> None:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (
            hasattr(os, "geteuid") and info.st_uid != os.geteuid()
        ):
            raise OSError("unsafe setup authorization database")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _restrict_file(path: Path, *, required: bool = False) -> None:
    if required:
        os.chmod(path, 0o600, follow_symlinks=False)
        return
    try:
        os.chmod(path, 0o600, follow_symlinks=False)
    except FileNotFoundError:  # pragma: no cover - optional SQLite sidecar deletion race
        return
