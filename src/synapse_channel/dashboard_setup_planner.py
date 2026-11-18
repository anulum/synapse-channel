# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded read-only setup doctor and one-use plan store
"""Issue and authorise bounded setup plans without connecting an effect seam.

This module deliberately performs no HTTP, filesystem, subprocess, service
manager, or secret-file operation. Request authentication and loopback Host and
Origin validation remain upstream responsibilities; their exact validated
values are bound here so a plan cannot cross request contexts.

The preflight a browser reads is a projection of the command line's own
inspection of its ``local-single-user`` profile: each requirement with its fixed
text and the status the inspection observed, and nothing the inspection
measured, so no path, hub address or identity reaches the browser.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Final, Literal

from synapse_channel.dashboard_setup_contract import (
    SETUP_CONTRACT_VERSION,
    SetupApplyRequest,
    SetupPlan,
    SetupPlanRequest,
    build_setup_plan,
)
from synapse_channel.setup_profiles import SetupProfile

DEFAULT_SETUP_PLAN_CAPACITY = 128
MAX_SETUP_PLAN_CAPACITY = 1024
DEFAULT_SETUP_PLAN_TTL_SECONDS = 180
MAX_SETUP_PLAN_TTL_SECONDS = 900

SetupPlanStoreRefusalCode = Literal[
    "capacity",
    "duplicate_request",
    "invalid_token",
    "token_collision",
    "not_found",
    "expired",
    "mismatch",
    "replayed",
]

_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{22,128}\Z")


_CHECK_STATUSES: Final = frozenset({"pass", "warn", "fail", "unavailable"})

SETUP_PREFLIGHT_LIMITS: Final = {
    "creates_secrets": False,
    "broader_bind": False,
    "system_services": False,
}
"""What no setup plan from the dashboard does, stated in every preflight."""


@dataclass(frozen=True, slots=True)
class SetupPlanBinding:
    """Validated request context to which one setup plan is cryptographically bound."""

    principal_id: str
    host: str
    origin: str
    configuration_generation: str

    def __post_init__(self) -> None:
        """Refuse empty, oversized, control-bearing, or non-ASCII context values."""
        _validate_context_value(self.principal_id, name="principal", limit=128)
        _validate_context_value(self.host, name="host", limit=255)
        _validate_context_value(self.origin, name="origin", limit=512)
        if _LOWER_SHA256.fullmatch(self.configuration_generation) is None:
            raise ValueError("configuration generation must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class IssuedSetupPlan:
    """Browser-facing plan response including a one-use non-credential nonce."""

    plan_id: str
    plan: SetupPlan
    confirmation_nonce: str

    def as_dict(self) -> dict[str, object]:
        """Return the stable plan response without exposing server binding state."""
        return {
            "version": SETUP_CONTRACT_VERSION,
            "plan_id": self.plan_id,
            "plan_digest": self.plan.digest,
            "confirmation_nonce": self.confirmation_nonce,
            **self.plan.as_dict(),
            "mutates_local_state": True,
        }


@dataclass(frozen=True, slots=True)
class AuthorisedSetupPlan:
    """A single-use plan proven to match its original request context."""

    plan_id: str
    plan: SetupPlan


@dataclass(frozen=True, slots=True)
class SetupPlanStoreRefusal:
    """One stable refusal that never reflects attacker-controlled values."""

    code: SetupPlanStoreRefusalCode


@dataclass(frozen=True, slots=True)
class _StoredSetupPlan:
    plan_id: str
    plan: SetupPlan
    binding_digest: bytes
    nonce_salt: bytes
    nonce_digest: bytes
    consumed: bool = False


TokenFactory = Callable[[int], str]
SaltFactory = Callable[[int], bytes]


def build_setup_preflight(
    profile: SetupProfile, inspection: Mapping[str, object]
) -> dict[str, object]:
    """Project one inspection of ``profile`` for an authorised browser.

    Parameters
    ----------
    profile : SetupProfile
        The command line's profile the inspection was made for.
    inspection : Mapping
        The document :func:`~synapse_channel.setup_inspector.inspect_setup` returned.

    Returns
    -------
    dict
        Each requirement of ``profile`` with its fixed description, evidence
        source and remedy (the desired state) and the status the inspection
        observed for it (the effective state), ``unavailable`` where the
        inspection gave none; ``ready`` when every required one passed; and the
        fixed limits. The inspection's values, details and remedies are left
        out: they hold paths, the hub's address and identities.
    """
    checks = inspection.get("checks")
    statuses: dict[str, str] = {}
    for check in checks if isinstance(checks, list) else ():
        if isinstance(check, Mapping) and isinstance(check.get("id"), str):
            status = check.get("status")
            statuses[str(check["id"])] = status if status in _CHECK_STATUSES else "unavailable"
    requirements = [
        {**requirement.as_dict(), "status": statuses.get(requirement.requirement_id, "unavailable")}
        for requirement in profile.requirements
    ]
    return {
        "version": SETUP_CONTRACT_VERSION,
        "profile": profile.profile_id,
        "profile_version": profile.version,
        "ready": all(item["status"] == "pass" for item in requirements if item["required"]),
        "requirements": requirements,
        "limits": dict(SETUP_PREFLIGHT_LIMITS),
    }


class SetupPlanStore:
    """Thread-safe, capacity-bounded, expiring store for one-use setup plans."""

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_SETUP_PLAN_CAPACITY,
        ttl_seconds: int = DEFAULT_SETUP_PLAN_TTL_SECONDS,
        token_factory: TokenFactory = secrets.token_urlsafe,
        salt_factory: SaltFactory = secrets.token_bytes,
    ) -> None:
        if type(capacity) is not int or not 1 <= capacity <= MAX_SETUP_PLAN_CAPACITY:
            raise ValueError(f"setup plan capacity must be between 1 and {MAX_SETUP_PLAN_CAPACITY}")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_SETUP_PLAN_TTL_SECONDS:
            raise ValueError(
                f"setup plan TTL must be between 1 and {MAX_SETUP_PLAN_TTL_SECONDS} seconds"
            )
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._token_factory = token_factory
        self._salt_factory = salt_factory
        self._records: OrderedDict[str, _StoredSetupPlan] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def record_count(self) -> int:
        """Return the bounded number of live and consumed-unexpired records."""
        with self._lock:
            return len(self._records)

    def issue(
        self,
        request: SetupPlanRequest,
        *,
        binding: SetupPlanBinding,
        cli_plan: Mapping[str, object],
        now: int,
    ) -> IssuedSetupPlan | SetupPlanStoreRefusal:
        """Issue one expiring plan bound to ``cli_plan``, or refuse without evicting a record.

        Raises
        ------
        ValueError
            If ``cli_plan`` cannot be bound (see
            :func:`~synapse_channel.dashboard_setup_contract.build_setup_plan`), or
            its generation is not the one ``binding`` names.
        """
        _validate_now(now)
        expires_at = now + self._ttl_seconds
        plan = build_setup_plan(request, cli_plan=cli_plan, expires_at=expires_at)
        if plan.configuration_generation != binding.configuration_generation:
            raise ValueError("the binding's configuration generation is not the plan's")
        binding_digest = _binding_digest(binding)
        with self._lock:
            self._purge_expired_locked(now)
            duplicate_request = any(
                record.plan.request_id == request.request_id for record in self._records.values()
            )
            if duplicate_request:
                return SetupPlanStoreRefusal("duplicate_request")
            if len(self._records) >= self._capacity:
                return SetupPlanStoreRefusal("capacity")
            tokens = self._new_tokens_locked()
            if isinstance(tokens, SetupPlanStoreRefusal):
                return tokens
            plan_id, nonce = tokens
            salt = self._salt_factory(32)
            if not isinstance(salt, bytes) or len(salt) != 32:
                return SetupPlanStoreRefusal("invalid_token")
            record = _StoredSetupPlan(
                plan_id=plan_id,
                plan=plan,
                binding_digest=binding_digest,
                nonce_salt=salt,
                nonce_digest=_nonce_digest(
                    nonce,
                    salt=salt,
                    binding_digest=binding_digest,
                    plan_digest=plan.digest,
                ),
            )
            self._records[plan_id] = record
        return IssuedSetupPlan(plan_id, plan, nonce)

    def authorise_once(
        self,
        request: SetupApplyRequest,
        *,
        binding: SetupPlanBinding,
        now: int,
    ) -> AuthorisedSetupPlan | SetupPlanStoreRefusal:
        """Atomically consume a matching plan; no caller can authorise it twice."""
        _validate_now(now)
        if not _valid_apply_reference(request):
            return SetupPlanStoreRefusal("mismatch")
        binding_digest = _binding_digest(binding)
        with self._lock:
            record = self._records.get(request.plan_id)
            if record is None:
                self._purge_expired_locked(now)
                return SetupPlanStoreRefusal("not_found")
            if record.plan.expires_at <= now:
                del self._records[request.plan_id]
                self._purge_expired_locked(now)
                return SetupPlanStoreRefusal("expired")
            if record.consumed:
                return SetupPlanStoreRefusal("replayed")
            nonce_digest = _nonce_digest(
                request.confirmation_nonce,
                salt=record.nonce_salt,
                binding_digest=binding_digest,
                plan_digest=request.plan_digest,
            )
            checks = (
                hmac.compare_digest(record.plan.request_id, request.request_id),
                hmac.compare_digest(record.plan.digest, request.plan_digest),
                hmac.compare_digest(record.binding_digest, binding_digest),
                hmac.compare_digest(record.nonce_digest, nonce_digest),
            )
            if not all(checks):
                return SetupPlanStoreRefusal("mismatch")
            self._records[request.plan_id] = replace(record, consumed=True)
            return AuthorisedSetupPlan(request.plan_id, record.plan)

    def generation_of(self, plan_id: str) -> str | None:
        """Return the configuration generation an issued plan was bound to, without consuming it.

        An apply reads it to tell a changed host (``plan_drift``) from any other
        mismatch before it asks for the one use.
        """
        with self._lock:
            record = self._records.get(plan_id)
        return None if record is None else record.plan.configuration_generation

    def discard(self, plan_id: str) -> bool:
        """Drop one issued plan so it can never be authorised, and return whether it was held.

        A plan whose ``planned`` receipt could not be written is discarded at once:
        no plan exists that the receipt chain does not record.
        """
        with self._lock:
            return self._records.pop(plan_id, None) is not None

    def purge_expired(self, *, now: int) -> int:
        """Drop expired records and return the exact number removed."""
        _validate_now(now)
        with self._lock:
            return self._purge_expired_locked(now)

    def _purge_expired_locked(self, now: int) -> int:
        expired = [
            plan_id for plan_id, record in self._records.items() if record.plan.expires_at <= now
        ]
        for plan_id in expired:
            del self._records[plan_id]
        return len(expired)

    def _new_tokens_locked(self) -> tuple[str, str] | SetupPlanStoreRefusal:
        plan_id = self._token_factory(24)
        nonce = self._token_factory(24)
        if (
            not isinstance(plan_id, str)
            or not isinstance(nonce, str)
            or _OPAQUE_ID.fullmatch(plan_id) is None
            or _OPAQUE_ID.fullmatch(nonce) is None
        ):
            return SetupPlanStoreRefusal("invalid_token")
        if plan_id in self._records:
            return SetupPlanStoreRefusal("token_collision")
        return plan_id, nonce


def _validate_context_value(value: str, *, name: str, limit: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or not value.isascii()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"setup plan {name} binding is invalid")


def _validate_now(now: int) -> None:
    if type(now) is not int or now < 0:
        raise ValueError("setup plan time must be a non-negative integer")


def _valid_apply_reference(request: SetupApplyRequest) -> bool:
    return (
        isinstance(request.request_id, str)
        and isinstance(request.plan_id, str)
        and _OPAQUE_ID.fullmatch(request.plan_id) is not None
        and isinstance(request.plan_digest, str)
        and _LOWER_SHA256.fullmatch(request.plan_digest) is not None
        and isinstance(request.confirmation_nonce, str)
        and _OPAQUE_ID.fullmatch(request.confirmation_nonce) is not None
    )


def _binding_digest(binding: SetupPlanBinding) -> bytes:
    digest = sha256()
    for value in (
        binding.principal_id,
        binding.host,
        binding.origin,
        binding.configuration_generation,
    ):
        encoded = value.encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def _nonce_digest(
    nonce: str,
    *,
    salt: bytes,
    binding_digest: bytes,
    plan_digest: str,
) -> bytes:
    digest = sha256()
    digest.update(salt)
    digest.update(binding_digest)
    digest.update(plan_digest.encode("ascii"))
    digest.update(nonce.encode("ascii"))
    return digest.digest()


__all__ = [
    "AuthorisedSetupPlan",
    "DEFAULT_SETUP_PLAN_CAPACITY",
    "DEFAULT_SETUP_PLAN_TTL_SECONDS",
    "IssuedSetupPlan",
    "MAX_SETUP_PLAN_CAPACITY",
    "MAX_SETUP_PLAN_TTL_SECONDS",
    "SetupPlanBinding",
    "SetupPlanStore",
    "SetupPlanStoreRefusal",
    "SETUP_PREFLIGHT_LIMITS",
    "build_setup_preflight",
]
