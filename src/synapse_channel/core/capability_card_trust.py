# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed capability-card trust material and replay history
"""Operator trust and bounded lifecycle state for signed capability cards.

Capability-card keys deliberately live in a bundle separate from connection
identity and signed-event keys.  The JSON entry shape is compatible with those
profiles, but loading it here creates a distinct replay/downgrade history so a
signature accepted for one protocol can never satisfy another protocol's state.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.message_auth import EventSignatureKey

DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS = 30.0
"""Default tolerated future/past clock skew for card signatures."""

DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY = 4096
"""Maximum agent/key histories retained for replay and downgrade checks."""

DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS = 3600.0
"""Seconds an expired card history remains useful for replay detection."""

_ED25519_RAW_PUBLIC_KEY_BYTES = 32


class CapabilityCardTrustError(SynapseError, ValueError):
    """Raised when capability-card trust material is malformed or unwritable."""

    code = "capability_card_trust"


class CapabilityCardHistoryResult(str, Enum):
    """Outcome of applying sequence and downgrade policy to a verified card."""

    ACCEPTED = "accepted"
    SEQUENCE_MISMATCH = "sequence_mismatch"
    CAPABILITY_DOWNGRADE = "capability_downgrade"
    HISTORY_FULL = "history_full"


@dataclass(frozen=True)
class CapabilityCardHistoryEntry:
    """Latest sequence and accepted capability floor for one agent/key binding."""

    sequence: int
    route_capabilities: frozenset[str]
    card_digest: str
    expires_at: float
    observed_at: float


class CapabilityCardHistory:
    """Bounded replay and route-capability downgrade history.

    Entries are keyed by ``(agent, key_id)``.  An expired entry remains until
    ``retention_seconds`` passes so restarting or re-signing an old card cannot
    immediately defeat replay detection.  When the live bound is full, a new
    binding fails visibly rather than evicting an unexpired replay guard.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY,
        retention_seconds: float = DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise CapabilityCardTrustError("capability-card history capacity must be positive")
        retention = float(retention_seconds)
        if not _finite(retention) or retention < 0.0:
            raise CapabilityCardTrustError(
                "capability-card history retention must be finite and non-negative"
            )
        self.max_entries = max_entries
        self.retention_seconds = retention
        self._entries: OrderedDict[tuple[str, str], CapabilityCardHistoryEntry] = OrderedDict()

    def assess_and_remember(
        self,
        *,
        agent: str,
        key_id: str,
        sequence: int,
        route_capabilities: frozenset[str],
        card_digest: str,
        expires_at: float,
        now: float,
    ) -> CapabilityCardHistoryResult:
        """Apply sequence/downgrade policy and retain an accepted new state."""
        self._evict(now)
        binding = (agent, key_id)
        previous = self._entries.get(binding)
        if previous is not None and sequence <= previous.sequence:
            return CapabilityCardHistoryResult.SEQUENCE_MISMATCH
        if previous is None and len(self._entries) >= self.max_entries:
            return CapabilityCardHistoryResult.HISTORY_FULL

        result = CapabilityCardHistoryResult.ACCEPTED
        remembered_capabilities = route_capabilities
        remembered_expiry = float(expires_at)
        if previous is not None and not previous.route_capabilities.issubset(route_capabilities):
            result = CapabilityCardHistoryResult.CAPABILITY_DOWNGRADE
            remembered_capabilities = previous.route_capabilities
            remembered_expiry = max(previous.expires_at, remembered_expiry)
        self._entries[binding] = CapabilityCardHistoryEntry(
            sequence=sequence,
            route_capabilities=remembered_capabilities,
            card_digest=card_digest,
            expires_at=remembered_expiry,
            observed_at=float(now),
        )
        self._entries.move_to_end(binding)
        return result

    def _evict(self, now: float) -> None:
        """Drop histories whose signed-card expiry plus retention has passed."""
        cutoff = float(now)
        stale = [
            binding
            for binding, entry in self._entries.items()
            if entry.expires_at + self.retention_seconds < cutoff
        ]
        for binding in stale:
            self._entries.pop(binding, None)


@dataclass(frozen=True)
class CapabilityCardTrustBundle:
    """Operator-managed keys plus profile-local card lifecycle state."""

    keys: Mapping[str, EventSignatureKey]
    history: CapabilityCardHistory = field(default_factory=CapabilityCardHistory)
    clock_skew_seconds: float = DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS

    def __post_init__(self) -> None:
        """Reject programmatic bundles whose time policy would fail open."""
        skew = float(self.clock_skew_seconds)
        if not _finite(skew) or skew < 0.0:
            raise CapabilityCardTrustError(
                "capability-card clock skew must be finite and non-negative"
            )
        object.__setattr__(self, "clock_skew_seconds", skew)


def _string_set(value: object, key_id: str, field_name: str) -> frozenset[str]:
    """Parse a required JSON list of non-empty strings."""
    if not isinstance(value, list):
        raise CapabilityCardTrustError(
            f"capability-card key {key_id!r} field {field_name!r} must be a list"
        )
    if not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise CapabilityCardTrustError(
            f"capability-card key {key_id!r} needs non-empty string {field_name[:-1]} bindings"
        )
    return frozenset(item.strip() for item in value)


def _optional_expiry(value: object, key_id: str) -> float | None:
    """Parse an optional finite key-level expiry."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CapabilityCardTrustError(
            f"capability-card key {key_id!r} expires_at must be a number"
        )
    expiry = float(value)
    if not _finite(expiry):
        raise CapabilityCardTrustError(f"capability-card key {key_id!r} expires_at must be finite")
    return expiry


def _finite(value: float) -> bool:
    """Return whether ``value`` is neither NaN nor an infinity."""
    return value == value and value not in (float("inf"), float("-inf"))


def _parse_key(entry: object, index: int) -> EventSignatureKey:
    """Parse one capability-card trust entry."""
    if not isinstance(entry, Mapping):
        raise CapabilityCardTrustError(f"capability-card key {index} must be an object")
    key_id = str(entry.get("key_id", "")).strip()
    if not key_id:
        raise CapabilityCardTrustError(f"capability-card key {index} needs a non-empty key_id")
    try:
        public_key = base64.b64decode(str(entry.get("public_key", "")).strip(), validate=True)
    except (TypeError, ValueError) as exc:
        raise CapabilityCardTrustError(
            f"capability-card key {key_id!r} has an invalid base64 public_key"
        ) from exc
    if len(public_key) != _ED25519_RAW_PUBLIC_KEY_BYTES:
        raise CapabilityCardTrustError(
            f"capability-card key {key_id!r} public_key must be "
            f"{_ED25519_RAW_PUBLIC_KEY_BYTES} raw Ed25519 bytes"
        )
    raw_revoked = entry.get("revoked", False)
    if not isinstance(raw_revoked, bool):
        raise CapabilityCardTrustError(f"capability-card key {key_id!r} revoked must be a boolean")
    key = EventSignatureKey(
        key_id=key_id,
        public_key=public_key,
        senders=_string_set(entry.get("agents"), key_id, "agents"),
        projects=_string_set(entry.get("projects"), key_id, "projects"),
        expires_at=_optional_expiry(entry.get("expires_at"), key_id),
        revoked=raw_revoked,
    )
    try:
        key.verifier()
    except (ImportError, ValueError) as exc:
        raise CapabilityCardTrustError(
            "capability-card trust requires a valid Ed25519 key and the security extra"
        ) from exc
    return key


def _load_bundle_mapping(path: str | Path) -> tuple[Path, Mapping[str, Any]]:
    """Read and shape-check one trust-bundle JSON object."""
    file = Path(path).expanduser()

    def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CapabilityCardTrustError(
                    f"duplicate JSON key {key!r} in capability-card trust bundle {file}"
                )
            result[key] = value
        return result

    try:
        data = json.loads(file.read_text(encoding="utf-8"), object_pairs_hook=object_from_pairs)
    except FileNotFoundError as exc:
        raise CapabilityCardTrustError(
            f"capability-card trust bundle does not exist: {file}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityCardTrustError(f"invalid capability-card trust JSON: {exc}") from exc
    if not isinstance(data, Mapping) or not isinstance(data.get("keys"), list):
        raise CapabilityCardTrustError(
            "capability-card trust bundle must be a mapping with a 'keys' list"
        )
    return file, data


def _parse_keys(entries: list[object]) -> dict[str, EventSignatureKey]:
    """Parse a key list and reject duplicate normalized ids."""
    keys: dict[str, EventSignatureKey] = {}
    for index, entry in enumerate(entries):
        key = _parse_key(entry, index)
        if key.key_id in keys:
            raise CapabilityCardTrustError(
                f"duplicate key id {key.key_id!r} in capability-card trust bundle"
            )
        keys[key.key_id] = key
    return keys


def load_capability_card_trust_bundle(
    path: str | Path,
    *,
    clock_skew_seconds: float = DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS,
    history_capacity: int = DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY,
    history_retention_seconds: float = DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS,
) -> CapabilityCardTrustBundle:
    """Load a separate signed-card trust bundle from an operator JSON file."""
    _file, data = _load_bundle_mapping(path)
    keys = _parse_keys(data["keys"])
    skew = float(clock_skew_seconds)
    if not _finite(skew) or skew < 0.0:
        raise CapabilityCardTrustError("capability-card clock skew must be finite and non-negative")
    return CapabilityCardTrustBundle(
        keys=keys,
        history=CapabilityCardHistory(
            max_entries=history_capacity,
            retention_seconds=history_retention_seconds,
        ),
        clock_skew_seconds=skew,
    )


def enroll_capability_card_key(
    path: str | Path,
    *,
    key_id: str,
    public_key_b64: str,
    agents: Iterable[str],
    projects: Iterable[str],
    expires_at: float | None = None,
) -> None:
    """Append one validated key to a bundle and replace the file atomically."""
    file = Path(path).expanduser()
    if file.is_file():
        _file, data = _load_bundle_mapping(file)
        entries = list(data["keys"])
    else:
        entries = []
    parsed = _parse_keys(entries)
    normalized_key_id = str(key_id).strip()
    if not normalized_key_id:
        raise CapabilityCardTrustError("capability-card key needs a non-empty key_id")
    if normalized_key_id in parsed:
        raise CapabilityCardTrustError(f"key id {normalized_key_id!r} already enrolled in {file}")
    entry: dict[str, Any] = {
        "agents": list(agents),
        "key_id": normalized_key_id,
        "projects": list(projects),
        "public_key": public_key_b64,
    }
    if expires_at is not None:
        entry["expires_at"] = expires_at
    _parse_key(entry, len(entries))
    entries.append(entry)
    _write_bundle(file, {"keys": entries})


def _write_bundle(file: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write an owner-only trust bundle."""
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=file.parent, prefix=f"{file.name}.", suffix=".tmp")
    except OSError as exc:
        raise CapabilityCardTrustError(
            f"cannot write capability-card trust bundle {file}: {exc}"
        ) from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, file)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
