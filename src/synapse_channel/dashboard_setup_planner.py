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
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal

from synapse_channel.dashboard_setup_contract import (
    SETUP_CONTRACT_VERSION,
    SetupApplyRequest,
    SetupPlan,
    SetupPlanRequest,
    available_setup_profiles,
    build_setup_plan,
)

DEFAULT_SETUP_PLAN_CAPACITY = 128
MAX_SETUP_PLAN_CAPACITY = 1024
DEFAULT_SETUP_PLAN_TTL_SECONDS = 180
MAX_SETUP_PLAN_TTL_SECONDS = 900

SetupProbeState = Literal["ready", "absent", "blocked", "unverifiable"]
SetupPlanStoreErrorCode = Literal[
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


@dataclass(frozen=True, slots=True)
class SetupDoctorFacts:
    """Already-bounded local observations used by the setup preflight view."""

    apply_armed: bool
    loopback: bool
    runtime: SetupProbeState
    user_services: SetupProbeState
    receipt_store: SetupProbeState


@dataclass(frozen=True, slots=True)
class SetupPreflight:
    """Token-free setup doctor projection safe for an authorised browser."""

    apply_armed: bool
    loopback: bool
    runtime: SetupProbeState
    user_services: SetupProbeState
    receipt_store: SetupProbeState

    def as_dict(self) -> dict[str, object]:
        """Return the stable versioned preflight document."""
        return {
            "version": SETUP_CONTRACT_VERSION,
            "apply_armed": self.apply_armed,
            "loopback": self.loopback,
            "runtime": self.runtime,
            "user_services": self.user_services,
            "receipt_store": self.receipt_store,
            "profiles": list(available_setup_profiles()),
            "limits": {
                "creates_secrets": False,
                "broader_bind": False,
                "system_services": False,
            },
        }


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
class SetupPlanStoreError:
    """One stable refusal that never reflects attacker-controlled values."""

    code: SetupPlanStoreErrorCode


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


def build_setup_preflight(facts: SetupDoctorFacts) -> SetupPreflight:
    """Project bounded observations without probing a host or revealing paths."""
    return SetupPreflight(
        apply_armed=facts.apply_armed,
        loopback=facts.loopback,
        runtime=facts.runtime,
        user_services=facts.user_services,
        receipt_store=facts.receipt_store,
    )


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
        now: int,
    ) -> IssuedSetupPlan | SetupPlanStoreError:
        """Issue one expiring plan or refuse without evicting an unexpired record."""
        _validate_now(now)
        expires_at = now + self._ttl_seconds
        plan = build_setup_plan(
            request,
            configuration_generation=binding.configuration_generation,
            expires_at=expires_at,
        )
        binding_digest = _binding_digest(binding)
        with self._lock:
            self._purge_expired_locked(now)
            duplicate_request = any(
                record.plan.request_id == request.request_id for record in self._records.values()
            )
            if duplicate_request:
                return SetupPlanStoreError("duplicate_request")
            if len(self._records) >= self._capacity:
                return SetupPlanStoreError("capacity")
            tokens = self._new_tokens_locked()
            if isinstance(tokens, SetupPlanStoreError):
                return tokens
            plan_id, nonce = tokens
            salt = self._salt_factory(32)
            if not isinstance(salt, bytes) or len(salt) != 32:
                return SetupPlanStoreError("invalid_token")
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
    ) -> AuthorisedSetupPlan | SetupPlanStoreError:
        """Atomically consume a matching plan; no caller can authorise it twice."""
        _validate_now(now)
        if not _valid_apply_reference(request):
            return SetupPlanStoreError("mismatch")
        binding_digest = _binding_digest(binding)
        with self._lock:
            record = self._records.get(request.plan_id)
            if record is None:
                self._purge_expired_locked(now)
                return SetupPlanStoreError("not_found")
            if record.plan.expires_at <= now:
                del self._records[request.plan_id]
                self._purge_expired_locked(now)
                return SetupPlanStoreError("expired")
            if record.consumed:
                return SetupPlanStoreError("replayed")
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
                return SetupPlanStoreError("mismatch")
            self._records[request.plan_id] = replace(record, consumed=True)
            return AuthorisedSetupPlan(request.plan_id, record.plan)

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

    def _new_tokens_locked(self) -> tuple[str, str] | SetupPlanStoreError:
        plan_id = self._token_factory(24)
        nonce = self._token_factory(24)
        if (
            not isinstance(plan_id, str)
            or not isinstance(nonce, str)
            or _OPAQUE_ID.fullmatch(plan_id) is None
            or _OPAQUE_ID.fullmatch(nonce) is None
        ):
            return SetupPlanStoreError("invalid_token")
        if plan_id in self._records:
            return SetupPlanStoreError("token_collision")
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
    "SetupDoctorFacts",
    "SetupPlanBinding",
    "SetupPlanStore",
    "SetupPlanStoreError",
    "SetupPreflight",
    "build_setup_preflight",
]
