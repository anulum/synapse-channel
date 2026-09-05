# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict setup verification documents and replay ledger
"""Build and validate single-use strict setup verification documents."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import stat
import time
from collections.abc import Callable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn, cast

from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.setup_authorization import (
    MAX_AUTHORIZATION_SECONDS,
    MAX_PLAN_BYTES,
    MAX_RESTART_PID,
    MIN_AUTHORIZATION_SECONDS,
    SetupAuthorizationError,
    validate_setup_authorization,
    validate_setup_plan,
)
from synapse_channel.setup_contract import (
    LOCAL_SINGLE_USER_URIS,
    SETUP_SCHEMA_VERSION,
    SetupErrorCode,
    document_digest,
    validated_setup_generation,
    validated_setup_target,
)
from synapse_channel.setup_planner import setup_generation_from_inspection

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_APPLICATION_KEYS = {
    "schema_version",
    "document_kind",
    "profile",
    "profile_version",
    "plan_digest",
    "authorization_digest",
    "receipt_digest",
    "target",
    "started_at",
    "completed_at",
    "outcome",
    "ledger_state",
    "effects",
    "protected_processes",
    "recovery",
    "effect_receipt_digest",
}
_VERIFICATION_PLAN_KEYS = {
    "schema_version",
    "document_kind",
    "profile",
    "profile_version",
    "read_only",
    "can_verify",
    "plan_digest",
    "authorization_digest",
    "application_receipt_digest",
    "inspection_digest",
    "target",
    "generation",
    "current_hub_pid",
    "required_checks",
    "warnings",
    "verification_plan_digest",
}
_VERIFICATION_AUTHORIZATION_KEYS = {
    "schema_version",
    "document_kind",
    "profile",
    "profile_version",
    "read_only",
    "can_verify",
    "verification_plan_digest",
    "verification_authorization_digest",
    "target",
    "confirmation_nonce",
    "issued_at",
    "expires_at",
    "restart_authority",
    "consumption_required",
    "warnings",
}
_APPLICATION_EFFECT_KEYS = {"id", "unit", "outcome"}
_PROTECTED_PROCESS_KEYS = {"pid", "before_alive", "after_alive"}
_REQUIRED_CHECKS = [
    "directed_canary_delivery",
    "exact_waiter_consumption",
    "durable_restart_replay",
    "strict_reinspection",
    "protected_process_continuity",
]
_LEDGER_FILENAME = "setup-verification-v1.sqlite3"
_NONCE_DOMAIN = b"synapse-setup-verification-nonce-v1\x00"


class SetupVerificationError(SetupAuthorizationError):
    """Stable strict-verification refusal."""

    code: SetupErrorCode = "verification_planning_failed"

    def __init__(
        self,
        code: SetupErrorCode,
        *,
        receipt: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        self.receipt = receipt


def _invalid_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_document(path: str | Path, *, code: SetupErrorCode) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_PLAN_BYTES:
            raise SetupVerificationError(code)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size > MAX_PLAN_BYTES
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise SetupVerificationError(  # pragma: no cover - lstat/open replacement race
                    code
                )
            payload = os.read(descriptor, MAX_PLAN_BYTES + 1)
            if len(payload) > MAX_PLAN_BYTES:
                raise SetupVerificationError(  # pragma: no cover - post-fstat growth race
                    code
                )
        finally:
            os.close(descriptor)
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if not isinstance(value, dict):
            raise ValueError("document is not an object")
        return cast(dict[str, object], value)
    except SetupVerificationError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        raise SetupVerificationError(code) from exc


def validate_application_receipt(
    plan: dict[str, object],
    authorization: dict[str, object],
    receipt: dict[str, object],
) -> dict[str, object]:
    """Validate a successful application receipt against its exact ancestors."""
    validated_plan = validate_setup_plan(plan)
    issued_at = authorization.get("issued_at")
    if isinstance(issued_at, bool) or not isinstance(issued_at, int):
        raise SetupVerificationError("invalid_application_receipt")
    try:
        validated_authorization = validate_setup_authorization(
            validated_plan,
            authorization,
            now=issued_at,
        )
        if set(receipt) != _APPLICATION_KEYS:
            raise ValueError("unexpected application receipt fields")
        if (
            receipt.get("schema_version") != SETUP_SCHEMA_VERSION
            or receipt.get("document_kind") != "application_receipt"
            or receipt.get("profile") != validated_plan["profile"]
            or receipt.get("profile_version") != validated_plan["profile_version"]
            or receipt.get("plan_digest") != validated_plan["plan_digest"]
            or receipt.get("authorization_digest")
            != validated_authorization["authorization_digest"]
            or receipt.get("target") != validated_plan["target"]
            or receipt.get("outcome") != "applied"
            or receipt.get("ledger_state") != "applied"
            or receipt.get("recovery") != "not_required"
            or receipt.get("effect_receipt_digest") is not None
        ):
            raise ValueError("application receipt is not a successful exact result")
        started_at = receipt.get("started_at")
        completed_at = receipt.get("completed_at")
        if (
            isinstance(started_at, bool)
            or not isinstance(started_at, int)
            or isinstance(completed_at, bool)
            or not isinstance(completed_at, int)
            or started_at < 0
            or completed_at < started_at
        ):
            raise ValueError("invalid application timing")
        effects = receipt.get("effects")
        if not isinstance(effects, list) or len(effects) > 2:
            raise ValueError("invalid application effects")
        effect_ids: set[str] = set()
        for effect in effects:
            if not isinstance(effect, dict) or set(effect) != _APPLICATION_EFFECT_KEYS:
                raise ValueError("invalid application effect receipt")
            effect_id = effect.get("id")
            if (
                effect_id not in {"establish_local_loopback_hub", "establish_identity_waiter"}
                or effect_id in effect_ids
                or effect.get("outcome") not in {"applied", "already_satisfied"}
                or not isinstance(effect.get("unit"), str)
                or (effect.get("outcome") == "applied" and not effect.get("unit"))
                or (effect.get("outcome") == "already_satisfied" and effect.get("unit") != "")
            ):
                raise ValueError("application effect receipt mismatch")
            effect_ids.add(cast(str, effect_id))
        processes = receipt.get("protected_processes")
        if not isinstance(processes, list) or not processes:
            raise ValueError("application protected-process evidence is absent")
        process_ids: set[int] = set()
        for process in processes:
            if not isinstance(process, dict) or set(process) != _PROTECTED_PROCESS_KEYS:
                raise ValueError("invalid protected-process evidence")
            pid = process.get("pid")
            if (
                isinstance(pid, bool)
                or not isinstance(pid, int)
                or not 1 < pid <= MAX_RESTART_PID
                or pid in process_ids
                or process.get("before_alive") is not True
                or process.get("after_alive") is not True
            ):
                raise ValueError("protected-process evidence mismatch")
            process_ids.add(pid)
        receipt_digest = receipt.get("receipt_digest")
        if not isinstance(receipt_digest, str) or _DIGEST.fullmatch(receipt_digest) is None:
            raise ValueError("invalid application receipt digest")
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
        if receipt_digest != document_digest(unsigned):
            raise ValueError("application receipt digest mismatch")
    except (KeyError, TypeError, ValueError, SetupAuthorizationError) as exc:
        raise SetupVerificationError("invalid_application_receipt") from exc
    return receipt


def load_application_receipt(
    path: str | Path,
    *,
    plan: dict[str, object],
    authorization: dict[str, object],
) -> dict[str, object]:
    """Load and validate one bounded application receipt."""
    return validate_application_receipt(
        plan,
        authorization,
        _load_document(path, code="invalid_application_receipt"),
    )


def load_historical_setup_authorization(
    path: str | Path,
    *,
    plan: dict[str, object],
) -> dict[str, object]:
    """Load an application authorization at its issuance time for provenance checks."""
    document = _load_document(path, code="invalid_authorization")
    issued_at = document.get("issued_at")
    if isinstance(issued_at, bool) or not isinstance(issued_at, int):
        raise SetupVerificationError("invalid_authorization")
    try:
        return validate_setup_authorization(plan, document, now=issued_at)
    except SetupAuthorizationError as exc:
        raise SetupVerificationError(exc.code) from exc


def _hub_pid(inspection: dict[str, object]) -> int:
    checks = inspection.get("checks")
    if not isinstance(checks, list):
        raise ValueError("inspection checks are absent")
    service = next(
        (item for item in checks if isinstance(item, dict) and item.get("id") == "service_manager"),
        None,
    )
    if not isinstance(service, dict) or service.get("status") != "pass":
        raise ValueError("service manager is not ready")
    value = service.get("value")
    pid = value.get("hub_pid") if isinstance(value, dict) else None
    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 < pid <= MAX_RESTART_PID:
        raise ValueError("hub PID is unavailable")
    return pid


def build_verification_plan(
    plan: dict[str, object],
    authorization: dict[str, object],
    application_receipt: dict[str, object],
    inspection: dict[str, object],
) -> dict[str, object]:
    """Build a read-only strict-verification plan from current exact evidence."""
    validated_plan = validate_setup_plan(plan)
    validated_receipt = validate_application_receipt(plan, authorization, application_receipt)
    try:
        target = validated_setup_target(inspection.get("target"))
        current_hub_pid = _hub_pid(inspection)
        generation = setup_generation_from_inspection(inspection)
        if (
            inspection.get("schema_version") != SETUP_SCHEMA_VERSION
            or inspection.get("document_kind") != "inspection"
            or inspection.get("profile") != validated_plan["profile"]
            or inspection.get("profile_version") != validated_plan["profile_version"]
            or inspection.get("read_only") is not True
            or inspection.get("ready") is not True
            or target != validated_plan["target"]
            or generation != validated_plan["generation"]
            or target["uri"] not in LOCAL_SINGLE_USER_URIS
            or generation["platform_system"] != "Linux"
        ):
            raise ValueError("inspection is not exact and ready")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SetupVerificationError("verification_target_changed") from exc
    document: dict[str, object] = {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "verification_plan",
        "profile": validated_plan["profile"],
        "profile_version": validated_plan["profile_version"],
        "read_only": True,
        "can_verify": True,
        "plan_digest": validated_plan["plan_digest"],
        "authorization_digest": authorization["authorization_digest"],
        "application_receipt_digest": validated_receipt["receipt_digest"],
        "inspection_digest": document_digest(inspection),
        "target": target,
        "generation": generation,
        "current_hub_pid": current_hub_pid,
        "required_checks": list(_REQUIRED_CHECKS),
        "warnings": ["restart_authorization_required", "single_use_verification"],
    }
    document["verification_plan_digest"] = document_digest(document)
    return document


def validate_verification_plan(document: dict[str, object]) -> dict[str, object]:
    """Validate an exact current strict-verification plan."""
    try:
        if set(document) != _VERIFICATION_PLAN_KEYS:
            raise ValueError("unexpected verification-plan fields")
        target = validated_setup_target(document.get("target"))
        generation = validated_setup_generation(document.get("generation"))
        pid = document.get("current_hub_pid")
        if (
            document.get("schema_version") != SETUP_SCHEMA_VERSION
            or document.get("document_kind") != "verification_plan"
            or document.get("profile") != "local-single-user"
            or document.get("profile_version") != 1
            or document.get("read_only") is not True
            or document.get("can_verify") is not True
            or target["uri"] not in LOCAL_SINGLE_USER_URIS
            or generation["platform_system"] != "Linux"
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or not 1 < pid <= MAX_RESTART_PID
            or document.get("required_checks") != _REQUIRED_CHECKS
            or document.get("warnings")
            != ["restart_authorization_required", "single_use_verification"]
        ):
            raise ValueError("verification-plan contract mismatch")
        for name in (
            "plan_digest",
            "authorization_digest",
            "application_receipt_digest",
            "inspection_digest",
            "verification_plan_digest",
        ):
            value = document.get(name)
            if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
                raise ValueError("invalid verification-plan digest")
        unsigned = {
            key: value for key, value in document.items() if key != "verification_plan_digest"
        }
        if document["verification_plan_digest"] != document_digest(unsigned):
            raise ValueError("verification-plan digest mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        raise SetupVerificationError("invalid_verification_plan") from exc
    return document


def load_verification_plan(path: str | Path) -> dict[str, object]:
    """Load and validate one bounded verification plan."""
    return validate_verification_plan(_load_document(path, code="invalid_verification_plan"))


def build_verification_authorization(
    verification_plan: dict[str, object],
    *,
    confirm_digest: str,
    nonce: str,
    expires_in: int,
    restart_pid: int,
    clock: Callable[[], float] = time.time,
) -> dict[str, object]:
    """Build a fresh single-use authorization for one exact verification restart."""
    plan = validate_verification_plan(verification_plan)
    if confirm_digest != plan["verification_plan_digest"]:
        raise SetupVerificationError("digest_mismatch")
    if _NONCE.fullmatch(nonce) is None:
        raise SetupVerificationError("invalid_nonce")
    if (
        isinstance(expires_in, bool)
        or not MIN_AUTHORIZATION_SECONDS <= expires_in <= MAX_AUTHORIZATION_SECONDS
    ):
        raise SetupVerificationError("invalid_expiry")
    if (
        isinstance(restart_pid, bool)
        or not isinstance(restart_pid, int)
        or restart_pid != plan["current_hub_pid"]
    ):
        raise SetupVerificationError("authorization_mismatch")
    try:
        issued_at = int(clock())
    except (OverflowError, TypeError, ValueError) as exc:
        raise SetupVerificationError("invalid_expiry") from exc
    if issued_at < 0 or issued_at > 9_223_372_036_854_775_807 - expires_in:
        raise SetupVerificationError("invalid_expiry")
    document: dict[str, object] = {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "verification_authorization",
        "profile": plan["profile"],
        "profile_version": plan["profile_version"],
        "read_only": True,
        "can_verify": True,
        "verification_plan_digest": plan["verification_plan_digest"],
        "target": plan["target"],
        "confirmation_nonce": nonce,
        "issued_at": issued_at,
        "expires_at": issued_at + expires_in,
        "restart_authority": {"pid": restart_pid},
        "consumption_required": True,
        "warnings": ["single_use_verification_authorization"],
    }
    document["verification_authorization_digest"] = document_digest(document)
    return document


def validate_verification_authorization(
    verification_plan: dict[str, object],
    authorization: dict[str, object],
    *,
    now: int,
) -> dict[str, object]:
    """Validate a verification authorization against its plan and current time."""
    plan = validate_verification_plan(verification_plan)
    try:
        if set(authorization) != _VERIFICATION_AUTHORIZATION_KEYS:
            raise ValueError("unexpected verification-authorization fields")
        nonce = authorization.get("confirmation_nonce")
        digest = authorization.get("verification_authorization_digest")
        issued_at = authorization.get("issued_at")
        expires_at = authorization.get("expires_at")
        if (
            authorization.get("schema_version") != SETUP_SCHEMA_VERSION
            or authorization.get("document_kind") != "verification_authorization"
            or authorization.get("profile") != plan["profile"]
            or authorization.get("profile_version") != plan["profile_version"]
            or authorization.get("read_only") is not True
            or authorization.get("can_verify") is not True
            or authorization.get("verification_plan_digest") != plan["verification_plan_digest"]
            or authorization.get("target") != plan["target"]
            or authorization.get("restart_authority") != {"pid": plan["current_hub_pid"]}
            or authorization.get("consumption_required") is not True
            or authorization.get("warnings") != ["single_use_verification_authorization"]
            or not isinstance(nonce, str)
            or _NONCE.fullmatch(nonce) is None
            or not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None
            or isinstance(now, bool)
            or not isinstance(now, int)
            or isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or issued_at < 0
            or not MIN_AUTHORIZATION_SECONDS <= expires_at - issued_at <= MAX_AUTHORIZATION_SECONDS
            or now < issued_at
        ):
            raise ValueError("verification-authorization contract mismatch")
        if now >= expires_at:
            raise SetupVerificationError("authorization_expired")
        unsigned = {
            key: value
            for key, value in authorization.items()
            if key != "verification_authorization_digest"
        }
        if digest != document_digest(unsigned):
            raise SetupVerificationError("authorization_mismatch")
    except SetupVerificationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise SetupVerificationError("invalid_verification_authorization") from exc
    return authorization


def load_verification_authorization(
    path: str | Path,
    *,
    verification_plan: dict[str, object],
    now: int,
) -> dict[str, object]:
    """Load and validate one bounded verification authorization."""
    return validate_verification_authorization(
        verification_plan,
        _load_document(path, code="invalid_verification_authorization"),
        now=now,
    )


def default_verification_ledger_dir(*, env: Mapping[str, str] | None = None) -> Path:
    """Return the owner state directory for strict-verification replay protection."""
    environment = os.environ if env is None else env
    state = environment.get("XDG_STATE_HOME")
    if state:
        path = Path(state)
    else:
        home = environment.get("HOME")
        path = Path(home) / ".local" / "state" if home else Path.home() / ".local" / "state"
    if not path.is_absolute():
        raise SetupVerificationError("verification_ledger_unavailable")
    return path / "synapse-channel"


class SetupVerificationLedger:
    """Owner-only SQLite ledger for single-use verification authorizations."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> SetupVerificationLedger:
        """Open and validate the owner-only verification ledger."""
        try:
            self.directory = ensure_private_dir(
                self.directory,
                parents=True,
                purpose="setup verification ledger directory",
            )
            path = self.directory / _LEDGER_FILENAME
            flags = (
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor = os.open(path, flags, 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                    raise OSError(  # pragma: no cover - post-open platform/FS invariant
                        "unsafe verification ledger leaf"
                    )
                os.fchmod(descriptor, 0o600)
                self._connection = sqlite3.connect(path, timeout=0.0, isolation_level=None)
                current = os.lstat(path)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise OSError(  # pragma: no cover - open/connect replacement race
                        "verification ledger leaf changed during open"
                    )
            finally:
                os.close(descriptor)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS verification_authorizations ("
                "authorization_digest TEXT PRIMARY KEY, plan_digest TEXT NOT NULL, "
                "nonce_digest TEXT NOT NULL UNIQUE, state TEXT NOT NULL, receipt_digest TEXT)"
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.close()
            raise SetupVerificationError("verification_ledger_unavailable") from exc
        return self

    def reserve(
        self,
        plan: dict[str, object],
        authorization: dict[str, object],
        *,
        now: int,
    ) -> None:
        """Atomically reserve a fresh authorization before the canary write."""
        validated = validate_verification_authorization(plan, authorization, now=now)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO verification_authorizations "
                "(authorization_digest, plan_digest, nonce_digest, state, receipt_digest) "
                "VALUES (?, ?, ?, 'reserved', NULL)",
                (
                    validated["verification_authorization_digest"],
                    plan["verification_plan_digest"],
                    sha256(
                        _NONCE_DOMAIN + cast(str, validated["confirmation_nonce"]).encode("ascii")
                    ).hexdigest(),
                ),
            )
            connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise SetupVerificationError("verification_authorization_replayed") from exc
        except sqlite3.Error as exc:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise SetupVerificationError("verification_ledger_unavailable") from exc

    def finish(self, authorization_digest: str, *, outcome: str, receipt_digest: str) -> None:
        """Finalize one reserved verification as verified or failed."""
        if outcome not in {"verified", "failed"}:
            raise SetupVerificationError("authorization_transition_invalid")
        connection = self._require_connection()
        try:
            cursor = connection.execute(
                "UPDATE verification_authorizations SET state = ?, receipt_digest = ? "
                "WHERE authorization_digest = ? AND state = 'reserved'",
                (outcome, receipt_digest, authorization_digest),
            )
        except sqlite3.Error as exc:
            raise SetupVerificationError("verification_ledger_unavailable") from exc
        if cursor.rowcount != 1:
            raise SetupVerificationError("authorization_transition_invalid")

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise SetupVerificationError("verification_ledger_unavailable")
        return self._connection

    def close(self) -> None:
        """Close the ledger if it was opened."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Close the verification ledger."""
        self.close()
