# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded setup authorization envelopes
"""Validate and authorize one immutable setup plan without applying it."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, cast

from synapse_channel.core.errors import SynapseError
from synapse_channel.setup_contract import (
    LOCAL_SINGLE_USER_URIS,
    SETUP_SCHEMA_VERSION,
    SetupErrorCode,
    document_digest,
    validated_setup_generation,
    validated_setup_target,
)
from synapse_channel.setup_profiles import build_setup_spec, get_setup_profile

MAX_PLAN_BYTES = 65_536
MIN_AUTHORIZATION_SECONDS = 30
MAX_AUTHORIZATION_SECONDS = 900
MAX_RESTART_PID = 2_147_483_647

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_AUTHORITIES = ("operator_confirmation", "operator_restart_authority")
_STATUSES = {"warn", "fail", "unavailable"}
_DISPOSITIONS = {"planned", "blocked"}
_RULES: dict[str, tuple[str, str, str, bool]] = {
    "install_synapse_package": ("package", "unsupported", "environment_change", False),
    "select_supported_python": ("python", "unsupported", "environment_change", False),
    "select_supported_platform": ("platform", "unsupported", "host_migration", False),
    "expose_synapse_entrypoint": (
        "executable",
        "unsupported",
        "environment_change",
        False,
    ),
    "configure_coordination_identity": (
        "identity",
        "unsupported",
        "configuration_change",
        False,
    ),
    "establish_local_loopback_hub": (
        "hub",
        "operator_confirmation",
        "service_start",
        True,
    ),
    "establish_identity_waiter": (
        "waiter",
        "operator_confirmation",
        "service_start",
        True,
    ),
}
_PLAN_KEYS = {
    "schema_version",
    "document_kind",
    "profile",
    "profile_version",
    "read_only",
    "can_apply",
    "ready",
    "inspection_digest",
    "profile_digest",
    "target",
    "generation",
    "authority_required",
    "effects",
    "warnings",
    "plan_digest",
}
_EFFECT_KEYS = {
    "id",
    "trigger_check",
    "observed_status",
    "disposition",
    "authority",
    "disruption",
    "reversible",
    "verification_check",
    "process_id",
}
_AUTHORIZATION_KEYS = {
    "schema_version",
    "document_kind",
    "profile",
    "profile_version",
    "read_only",
    "can_apply",
    "plan_digest",
    "authorization_digest",
    "target",
    "confirmation_nonce",
    "issued_at",
    "expires_at",
    "authority_granted",
    "restart_authority",
    "consumption_required",
    "warnings",
}


class SetupAuthorizationError(SynapseError, ValueError):
    """Stable, non-reflective authorization failure."""

    code: SetupErrorCode = "authorization_failed"

    def __init__(self, code: SetupErrorCode) -> None:
        super().__init__(code)
        self.code = code


def _invalid_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_setup_plan(path: str | Path) -> dict[str, object]:
    """Read one bounded, non-symlink regular JSON plan and validate it."""
    return validate_setup_plan(_load_json_document(path, code="invalid_plan"))


def _load_json_document(path: str | Path, *, code: SetupErrorCode) -> dict[str, object]:
    """Read one bounded regular JSON object without following a replaced leaf."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise SetupAuthorizationError(code)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_PLAN_BYTES
                or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)
            ):
                raise SetupAuthorizationError(code)
            chunks: list[bytes] = []
            remaining = MAX_PLAN_BYTES + 1
            while remaining:  # pragma: no branch - false only after concurrent post-fstat growth
                chunk = os.read(descriptor, min(remaining, 8192))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > MAX_PLAN_BYTES:  # pragma: no cover - concurrent post-fstat growth
                raise SetupAuthorizationError(code)
        finally:
            os.close(descriptor)
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if not isinstance(document, dict):
            raise ValueError("setup document must be a JSON object")
        return cast(dict[str, object], document)
    except SetupAuthorizationError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise SetupAuthorizationError(code) from exc


def validate_setup_plan(document: dict[str, object]) -> dict[str, object]:
    """Validate the exact current plan contract without an optional dependency."""
    try:
        if set(document) != _PLAN_KEYS:
            raise ValueError("unexpected plan fields")
        profile_name = document.get("profile")
        profile = get_setup_profile(profile_name) if isinstance(profile_name, str) else None
        if (
            document.get("schema_version") != SETUP_SCHEMA_VERSION
            or document.get("document_kind") != "plan"
            or profile is None
            or document.get("profile_version") != profile.version
            or document.get("read_only") is not True
            or not isinstance(document.get("can_apply"), bool)
            or not isinstance(document.get("ready"), bool)
        ):
            raise ValueError("plan contract mismatch")
        for name in ("inspection_digest", "profile_digest", "plan_digest"):
            value = document.get(name)
            if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
                raise ValueError("invalid digest")
        if document["profile_digest"] != document_digest(build_setup_spec(profile)):
            raise ValueError("profile digest mismatch")
        target = validated_setup_target(document.get("target"))
        generation = validated_setup_generation(document.get("generation"))
        if (
            profile.profile_id == "local-single-user"
            and target["uri"] not in LOCAL_SINGLE_USER_URIS
        ):
            raise ValueError("profile target mismatch")
        effects = document.get("effects")
        if not isinstance(effects, list) or len(effects) > len(_RULES):
            raise ValueError("invalid effects")
        expected_authorities: list[str] = []
        seen_effects: set[str] = set()
        service_adapter_supported = (
            generation["platform_system"] == "Linux"
            and bool(generation["synapse_executable"])
            and bool(generation["service_manager_executable"])
        )
        for effect in effects:
            authority = _validate_effect(
                effect,
                seen_effects,
                service_adapter_supported=service_adapter_supported,
            )
            if (
                authority in _AUTHORITIES
                and cast(dict[str, object], effect)["disposition"] == "planned"
            ):
                if authority not in expected_authorities:
                    expected_authorities.append(authority)
        authorities = document.get("authority_required")
        if authorities != expected_authorities:
            raise ValueError("authority list does not match effects")
        blocked = any(
            cast(dict[str, object], effect)["disposition"] == "blocked" for effect in effects
        )
        expected_can_apply = bool(effects) and not blocked
        if not effects:
            expected_warnings = ["no_changes_required"]
        elif blocked:
            expected_warnings = ["manual_remediation_required"]
        else:
            expected_warnings = ["authorization_required"]
        if document.get("can_apply") is not expected_can_apply:
            raise ValueError("apply disposition does not match effects")
        if document.get("warnings") != expected_warnings:
            raise ValueError("warning list does not match effects")
        if document["ready"] is not (len(effects) == 0):
            raise ValueError("readiness does not match effects")
        unsigned = {key: value for key, value in document.items() if key != "plan_digest"}
        if document["plan_digest"] != document_digest(unsigned):
            raise ValueError("plan digest mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        raise SetupAuthorizationError("invalid_plan") from exc
    return document


def _validate_effect(
    effect: object,
    seen: set[str],
    *,
    service_adapter_supported: bool,
) -> str:
    if not isinstance(effect, dict) or set(effect) != _EFFECT_KEYS:
        raise ValueError("invalid effect")
    effect_id = effect.get("id")
    if not isinstance(effect_id, str) or effect_id not in _RULES or effect_id in seen:
        raise ValueError("unknown or duplicate effect")
    seen.add(effect_id)
    trigger, authority, disruption, reversible = _RULES[effect_id]
    process_id = effect.get("process_id")
    if effect_id == "establish_local_loopback_hub":
        if process_id is not None:
            if (
                not isinstance(process_id, int)
                or isinstance(process_id, bool)
                or not 1 < process_id <= MAX_RESTART_PID
            ):
                raise ValueError("hub process id is invalid")
            authority = "operator_restart_authority"
    elif process_id is not None:
        raise ValueError("effect process id is outside the allow-list")
    if (
        effect.get("trigger_check") != trigger
        or effect.get("verification_check") != trigger
        or effect.get("authority") != authority
        or effect.get("disruption") != disruption
        or effect.get("reversible") is not reversible
        or effect.get("observed_status") not in _STATUSES
        or effect.get("disposition") not in _DISPOSITIONS
    ):
        raise ValueError("effect does not match the allow-list")
    service_effect_blocked = (
        effect_id
        in {
            "establish_local_loopback_hub",
            "establish_identity_waiter",
        }
        and not service_adapter_supported
    )
    should_block = (
        effect["observed_status"] == "unavailable"
        or authority == "unsupported"
        or service_effect_blocked
    )
    if should_block != (effect["disposition"] == "blocked"):
        raise ValueError("effect disposition is not fail-closed")
    return authority


def _expected_restart_pid(effects: list[dict[str, object]]) -> int | None:
    for effect in effects:
        if effect["authority"] == "operator_restart_authority":
            return cast(int, effect["process_id"])
    return None


def build_setup_authorization(
    plan: dict[str, object],
    *,
    confirm_digest: str,
    nonce: str,
    expires_in: int,
    restart_pid: int | None,
    clock: Callable[[], float] = time.time,
) -> dict[str, object]:
    """Build an expiring authorization envelope; do not consume or apply it."""
    validated = validate_setup_plan(plan)
    if _DIGEST.fullmatch(confirm_digest) is None or confirm_digest != validated["plan_digest"]:
        raise SetupAuthorizationError("digest_mismatch")
    if _NONCE.fullmatch(nonce) is None:
        raise SetupAuthorizationError("invalid_nonce")
    if (
        isinstance(expires_in, bool)
        or not MIN_AUTHORIZATION_SECONDS <= expires_in <= MAX_AUTHORIZATION_SECONDS
    ):
        raise SetupAuthorizationError("invalid_expiry")
    effects = cast(list[dict[str, object]], validated["effects"])
    if validated["can_apply"] is not True:
        raise SetupAuthorizationError("plan_blocked")
    authorities = cast(list[str], validated["authority_required"])
    expected_restart_pid = _expected_restart_pid(effects)
    restart_required = expected_restart_pid is not None
    valid_pid = (
        restart_pid is not None
        and not isinstance(restart_pid, bool)
        and 1 < restart_pid <= MAX_RESTART_PID
    )
    if restart_required and not valid_pid:
        raise SetupAuthorizationError("restart_authority_required")
    if restart_required and restart_pid != expected_restart_pid:
        raise SetupAuthorizationError("authorization_mismatch")
    if not restart_required and restart_pid is not None:
        raise SetupAuthorizationError("unexpected_restart_authority")
    try:
        issued_at = int(clock())
    except (OverflowError, TypeError, ValueError) as exc:
        raise SetupAuthorizationError("invalid_expiry") from exc
    if issued_at < 0 or issued_at > 9_223_372_036_854_775_807 - expires_in:
        raise SetupAuthorizationError("invalid_expiry")
    restart_authority: dict[str, int] | None = None
    if restart_required:
        restart_authority = {"pid": cast(int, restart_pid)}
    document: dict[str, object] = {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "authorization",
        "profile": validated["profile"],
        "profile_version": validated["profile_version"],
        "read_only": True,
        "can_apply": True,
        "plan_digest": validated["plan_digest"],
        "target": validated["target"],
        "confirmation_nonce": nonce,
        "issued_at": issued_at,
        "expires_at": issued_at + expires_in,
        "authority_granted": authorities,
        "restart_authority": restart_authority,
        "consumption_required": True,
        "warnings": ["single_use_authorization"],
    }
    document["authorization_digest"] = document_digest(document)
    return document


def load_setup_authorization(
    path: str | Path,
    *,
    plan: dict[str, object],
    now: int,
) -> dict[str, object]:
    """Load and validate one authorization against its exact plan and time."""
    document = _load_json_document(path, code="invalid_authorization")
    return validate_setup_authorization(plan, document, now=now)


def validate_setup_authorization(
    plan: dict[str, object],
    authorization: dict[str, object],
    *,
    now: int,
) -> dict[str, object]:
    """Validate one authorization for future atomic ledger reservation."""
    validated_plan = validate_setup_plan(plan)
    try:
        if set(authorization) != _AUTHORIZATION_KEYS:
            raise ValueError("unexpected authorization fields")
        if (
            authorization.get("schema_version") != SETUP_SCHEMA_VERSION
            or authorization.get("document_kind") != "authorization"
            or authorization.get("profile") != validated_plan["profile"]
            or authorization.get("profile_version") != validated_plan["profile_version"]
            or authorization.get("read_only") is not True
            or authorization.get("can_apply") is not True
            or authorization.get("plan_digest") != validated_plan["plan_digest"]
            or authorization.get("target") != validated_plan["target"]
            or authorization.get("authority_granted") != validated_plan["authority_required"]
            or authorization.get("consumption_required") is not True
            or authorization.get("warnings") != ["single_use_authorization"]
        ):
            raise SetupAuthorizationError("authorization_mismatch")
        nonce = authorization.get("confirmation_nonce")
        auth_digest = authorization.get("authorization_digest")
        if (
            not isinstance(nonce, str)
            or _NONCE.fullmatch(nonce) is None
            or not isinstance(auth_digest, str)
            or _DIGEST.fullmatch(auth_digest) is None
        ):
            raise ValueError("authorization token or digest is invalid")
        issued_at = authorization.get("issued_at")
        expires_at = authorization.get("expires_at")
        if (
            isinstance(now, bool)
            or not isinstance(now, int)
            or isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or issued_at < 0
            or not MIN_AUTHORIZATION_SECONDS <= expires_at - issued_at <= MAX_AUTHORIZATION_SECONDS
            or now < issued_at
        ):
            raise ValueError("authorization time is invalid")
        if now >= expires_at:
            raise SetupAuthorizationError("authorization_expired")
        effects = cast(list[dict[str, object]], validated_plan["effects"])
        expected_pid = _expected_restart_pid(effects)
        restart = authorization.get("restart_authority")
        if expected_pid is None:
            if restart is not None:
                raise SetupAuthorizationError("authorization_mismatch")
        elif restart != {"pid": expected_pid}:
            raise SetupAuthorizationError("authorization_mismatch")
        unsigned = {
            key: value for key, value in authorization.items() if key != "authorization_digest"
        }
        if authorization["authorization_digest"] != document_digest(unsigned):
            raise SetupAuthorizationError("authorization_mismatch")
    except SetupAuthorizationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise SetupAuthorizationError("invalid_authorization") from exc
    return authorization
