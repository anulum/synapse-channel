# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — governed online enrolment of identity keys (SOL4-ID-01)
"""Online enrolment of identity keys under an authenticated authority.

A hub started with ``--identity-trust`` verifies each socket's signed
registration against an operator trust bundle. Adding a key to that file was an
offline edit and a restart, with no record of who enrolled what. With
``--identity-enrollments FILE`` the hub accepts an ``identity_enroll`` request
from an operator instead, after independent gates, and keeps the enrolled keys
in a hub-owned overlay file. The operator's trust file is never rewritten, and
it stays authoritative for the names it covers.

This module holds the pure parts: the overlay store, the merge into the
effective trust bundle, the ordered denial policy and the per-requester rate
bucket. The handler in :mod:`synapse_channel.core.handlers.identity_enrollments`
wires them to the hub's ACL, role grants, journal and identity gate.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import tempfile
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.identity_binding import parse_identity_trust_document
from synapse_channel.core.message_auth import EventSignatureKey, EventSignatureTrustBundle
from synapse_channel.core.secure_path import apply_owner_only_file, read_owner_only_file_bytes

ENROLLER_ROLE = "identity-enroller"
"""Role, under the requester's own project, that marks an identity as an enroller."""

MAX_ENROLLMENT_REASON_LENGTH = 500
"""Longest operator reason admitted to the durable audit event."""

MAX_ENROLLMENT_STORE_BYTES = 1024 * 1024
"""Largest overlay file the hub reads at start-up."""

DEFAULT_ENROLLMENT_RATE = 10
"""Default number of enrolment changes one requester may make per window."""

DEFAULT_ENROLLMENT_WINDOW_SECONDS = 3600.0
"""Default rate window, in seconds."""

_KEY_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_ED25519_RAW_PUBLIC_KEY_BYTES = 32


class IdentityEnrollmentError(SynapseError, ValueError):
    """Raised when the enrolment store is unusable or conflicts with the trust bundle."""

    code = "identity_enrollment"


def load_enrolled_keys(path: str | Path) -> dict[str, EventSignatureKey]:
    """Return the keys in the overlay store at ``path``; an absent file holds none.

    The file must be owner-only, since whoever can write it can enrol keys.

    Raises
    ------
    IdentityEnrollmentError
        When the file is not owner-only, too large, not JSON, or malformed.
    """
    file = Path(path).expanduser()
    if not file.exists():
        return {}
    try:
        raw = read_owner_only_file_bytes(
            file, purpose="identity enrolment store", max_bytes=MAX_ENROLLMENT_STORE_BYTES
        )
        return parse_identity_trust_document(json.loads(raw.decode("utf-8")))
    except (OSError, ValueError) as exc:
        raise IdentityEnrollmentError(f"identity enrolment store {file}: {exc}") from exc


def merge_enrolled_keys(
    static: EventSignatureTrustBundle, enrolled: Mapping[str, EventSignatureKey]
) -> EventSignatureTrustBundle:
    """Return the effective trust bundle: the operator's keys plus the enrolled ones.

    The result shares ``static``'s replay cache, so a nonce already accepted
    stays spent across a change.

    Raises
    ------
    IdentityEnrollmentError
        When a key id is in both, or a live enrolled key names a sender the
        operator's bundle already covers.
    """
    duplicates = sorted(set(static.keys) & set(enrolled))
    if duplicates:
        raise IdentityEnrollmentError(
            f"key id {duplicates[0]!r} is in both the identity trust bundle and the enrolment store"
        )
    covered = static_senders(static.keys)
    for key in enrolled.values():
        overlap = sorted(key.senders & covered)
        if overlap and not key.revoked:
            raise IdentityEnrollmentError(
                f"enrolled key {key.key_id!r} names {overlap[0]!r}, which the identity trust "
                "bundle already covers; the trust bundle stays authoritative for its names"
            )
    return EventSignatureTrustBundle(
        keys={**static.keys, **enrolled}, replay_cache=static.replay_cache
    )


def static_senders(keys: Mapping[str, EventSignatureKey]) -> frozenset[str]:
    """Return every sender one of ``keys`` may prove, revoked keys included."""
    return frozenset(sender for key in keys.values() for sender in key.senders)


def live_enrolled_key(
    enrolled: Mapping[str, EventSignatureKey], name: str, *, now: float
) -> EventSignatureKey | None:
    """Return the enrolled key currently usable for ``name``, if there is one."""
    for key in enrolled.values():
        if (
            name in key.senders
            and not key.revoked
            and (key.expires_at is None or key.expires_at > now)
        ):
            return key
    return None


def write_enrolled_keys(path: str | Path, enrolled: Mapping[str, EventSignatureKey]) -> None:
    """Persist the overlay atomically and owner-only, in the trust-bundle format.

    Raises
    ------
    OSError
        When the file cannot be written; nothing partial is left behind.
    """
    file = Path(path).expanduser()
    entries = [_entry(enrolled[key_id]) for key_id in sorted(enrolled)]
    text = json.dumps({"keys": entries}, indent=2, sort_keys=True) + "\n"
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=file.parent, prefix=f"{file.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        apply_owner_only_file(tmp)
        os.replace(tmp, file)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _entry(key: EventSignatureKey) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "key_id": key.key_id,
        "public_key": base64.b64encode(key.public_key).decode("ascii"),
        "senders": sorted(key.senders),
        "revoked": key.revoked,
    }
    if key.projects:
        entry["projects"] = sorted(key.projects)
    if key.expires_at is not None:
        entry["expires_at"] = key.expires_at
    return entry


def decode_public_key(value: object) -> bytes | None:
    """Return 32 raw Ed25519 public-key bytes from base64 text, or ``None``."""
    if not isinstance(value, str):
        return None
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == _ED25519_RAW_PUBLIC_KEY_BYTES else None


@dataclass(frozen=True)
class EnrollmentRequest:
    """One ``identity_enroll`` request, as the handler observed it."""

    requester: str
    name: str
    key_id: str
    public_key: bytes | None
    reason: str
    expected_key_id: str
    expires_at: object


def enrollment_denial(
    request: EnrollmentRequest,
    *,
    enabled: bool,
    requester_bound: bool,
    acl_allowed: bool,
    role_granted: bool,
    namespace_allowed: bool,
    rate_allowed: bool,
    static: Mapping[str, EventSignatureKey],
    enrolled: Mapping[str, EventSignatureKey],
    now: float,
) -> str:
    """Return the first fail-closed denial, or ``""`` when the enrolment may run.

    The requester's authority is checked before anything about the target is
    revealed: whether the name is covered, enrolled, or the key id taken.
    """
    return authority_denial(
        enabled=enabled,
        requester_bound=requester_bound,
        acl_allowed=acl_allowed,
        role_granted=role_granted,
        namespace_allowed=namespace_allowed,
        rate_allowed=rate_allowed,
    ) or _request_denial(request, static=static, enrolled=enrolled, now=now)


def authority_denial(
    *,
    enabled: bool,
    requester_bound: bool,
    acl_allowed: bool,
    role_granted: bool,
    namespace_allowed: bool,
    rate_allowed: bool,
) -> str:
    """Return the first failed authority gate shared by enrol and revoke, or ``""``."""
    if not enabled:
        return "online enrolment is disabled on this hub"
    if not requester_bound:
        return "enrolment requires a cryptographically proven requester identity"
    if not acl_allowed:
        return "not authorised to enrol keys for this name"
    if not role_granted:
        return f"enrolment requires the {ENROLLER_ROLE!r} role grant"
    if not namespace_allowed:
        return "this hub does not allow enrolment in the name's namespace"
    if not rate_allowed:
        return "enrolment rate limit reached; try again later"
    return ""


def revocation_denial(
    *,
    name: str,
    key_id: str,
    reason: str,
    static: Mapping[str, EventSignatureKey],
    enrolled: Mapping[str, EventSignatureKey],
) -> str:
    """Return why an authorised revocation of ``key_id`` for ``name`` cannot run, or ``""``.

    Only the hub-owned store is revocable online; a key in the operator's trust
    file is revoked by editing that file.
    """
    if "/" not in name or not name.split("/", 1)[1]:
        return "the name must be <project>/<id>"
    clean_reason = reason.strip()
    if not clean_reason:
        return "a non-empty operator reason is required"
    if len(clean_reason) > MAX_ENROLLMENT_REASON_LENGTH:
        return f"reason exceeds {MAX_ENROLLMENT_REASON_LENGTH} characters"
    if key_id in static:
        return "keys in the identity trust bundle are revoked by editing that file"
    key = enrolled.get(key_id)
    if key is None:
        return "no enrolled key has this id"
    if name not in key.senders:
        return "the enrolled key does not prove this name"
    if key.revoked:
        return "the enrolled key is already revoked"
    return ""


def _request_denial(
    request: EnrollmentRequest,
    *,
    static: Mapping[str, EventSignatureKey],
    enrolled: Mapping[str, EventSignatureKey],
    now: float,
) -> str:
    if "/" not in request.name or not request.name.split("/", 1)[1]:
        return "the name must be <project>/<id>"
    if _KEY_ID.fullmatch(request.key_id) is None:
        return "key id must be 1-64 characters of letters, digits, '.', '_' or '-'"
    if request.public_key is None:
        return "public key must be base64 of 32 raw Ed25519 bytes"
    reason = request.reason.strip()
    if not reason:
        return "a non-empty operator reason is required"
    if len(reason) > MAX_ENROLLMENT_REASON_LENGTH:
        return f"reason exceeds {MAX_ENROLLMENT_REASON_LENGTH} characters"
    expires = request.expires_at
    if expires is not None and (
        isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or not math.isfinite(expires)
        or expires <= now
    ):
        return "expires_at must be a future time"
    if request.key_id in static or request.key_id in enrolled:
        return "key id is already in use"
    if request.name in static_senders(static):
        return "the identity trust bundle already covers this name"
    current = live_enrolled_key(enrolled, request.name, now=now)
    if request.name == request.requester and not request.expected_key_id:
        return "an enroller may only rotate its own key, naming the current key id"
    if current is None:
        if request.expected_key_id:
            return "no current enrolled key to rotate for this name"
        return ""
    if request.expected_key_id != current.key_id:
        return "the name already has an enrolled key; rotate it by naming the current key id"
    return ""


@dataclass
class EnrollmentRateLimiter:
    """A per-requester sliding window of enrolment changes."""

    limit: int = DEFAULT_ENROLLMENT_RATE
    window_seconds: float = DEFAULT_ENROLLMENT_WINDOW_SECONDS
    _events: dict[str, deque[float]] = field(default_factory=dict)

    def allows(self, requester: str, *, now: float) -> bool:
        """Return whether ``requester`` may make one more change at ``now``."""
        events = self._events.get(requester)
        if events is None:
            return self.limit > 0
        while events and events[0] <= now - self.window_seconds:
            events.popleft()
        return len(events) < self.limit

    def record(self, requester: str, *, now: float) -> None:
        """Count one change by ``requester`` at ``now``."""
        self._events.setdefault(requester, deque()).append(now)


__all__ = [
    "DEFAULT_ENROLLMENT_RATE",
    "DEFAULT_ENROLLMENT_WINDOW_SECONDS",
    "ENROLLER_ROLE",
    "authority_denial",
    "revocation_denial",
    "EnrollmentRateLimiter",
    "EnrollmentRequest",
    "IdentityEnrollmentError",
    "decode_public_key",
    "enrollment_denial",
    "live_enrolled_key",
    "load_enrolled_keys",
    "merge_enrolled_keys",
    "static_senders",
    "write_enrolled_keys",
]
