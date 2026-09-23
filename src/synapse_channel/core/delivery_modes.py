# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — version-three delivery intent contract
"""Validate bounded delivery intents and their explicit lifecycle transitions.

The contract is deliberately independent of sockets and provider APIs. The hub
binds ``sender`` and ``origin_hub`` from its authenticated connection, then uses
these value objects to make a durable admission decision. A recipient's mode
advertisement is evidence of one live session's abilities, never authority for
the sender or proof that a task executed.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

DeliveryMode = Literal["interrupt", "steer", "follow_up", "next_turn"]
DeliveryQuality = Literal["native", "emulated"]
DeliveryStage = Literal[
    "accepted",
    "queued",
    "boundary_delivered",
    "acknowledged",
    "completed",
    "failed",
    "rejected",
    "expired",
    "cancelled",
    "superseded",
]

MODES = frozenset({"interrupt", "steer", "follow_up", "next_turn"})
QUALITIES = frozenset({"native", "emulated"})
TERMINAL_STAGES = frozenset(
    {"completed", "failed", "rejected", "expired", "cancelled", "superseded"}
)
MAX_BODY_BYTES = 8192
MAX_ID_BYTES = 128
MAX_TARGET_BYTES = 256
MAX_FALLBACKS = 3
MAX_DEADLINE_SECONDS = 86400.0

_NEXT_STAGES: Mapping[str, frozenset[str]] = {
    "accepted": frozenset({"queued", "rejected", "expired"}),
    "queued": frozenset({"boundary_delivered", "rejected", "expired", "cancelled", "superseded"}),
    "boundary_delivered": frozenset(
        {"acknowledged", "rejected", "failed", "expired", "cancelled", "superseded"}
    ),
    "acknowledged": frozenset(
        {"completed", "failed", "rejected", "expired", "cancelled", "superseded"}
    ),
}


class DeliveryRefusal(ValueError):
    """A stable, secret-free refusal suitable for a version-three error frame."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _bounded_text(value: object, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    """Require a printable UTF-8 string with a bounded encoded length."""
    if not isinstance(value, str):
        raise DeliveryRefusal("invalid_shape", f"{field} must be a string")
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise DeliveryRefusal("invalid_shape", f"{field} must be valid UTF-8") from exc
    if (not value and not allow_empty) or encoded_size > maximum:
        raise DeliveryRefusal("invalid_shape", f"{field} length is outside its limit")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise DeliveryRefusal("invalid_shape", f"{field} contains a control character")
    return value


def _exact_target(value: object) -> str:
    """Reject role, wildcard, and broadcast targets for session-bound intents."""
    target = _bounded_text(value, "target", MAX_TARGET_BYTES)
    if target == "all" or "*" in target or "?" in target or "[" in target:
        raise DeliveryRefusal("invalid_shape", "target must be one exact identity")
    return target


def _incarnation(value: object) -> str:
    """Validate the public digest of one recipient session token."""
    incarnation = _bounded_text(value, "target_incarnation", 64)
    if len(incarnation) != 64 or any(char not in "0123456789abcdef" for char in incarnation):
        raise DeliveryRefusal("invalid_shape", "target_incarnation must be a SHA-256 digest")
    return incarnation


def _mode(value: object) -> DeliveryMode:
    """Refuse an unknown mode rather than allowing it to become ordinary chat."""
    if value == "interrupt":
        return "interrupt"
    if value == "steer":
        return "steer"
    if value == "follow_up":
        return "follow_up"
    if value == "next_turn":
        return "next_turn"
    raise DeliveryRefusal("unsupported_mode", "delivery mode is unsupported")


@dataclass(frozen=True)
class DeliveryIntent:
    """One sender-scoped, session-bound request after shape validation."""

    sender: str
    origin_hub: str
    request_id: str
    idempotency_key: str
    target: str
    target_incarnation: str
    mode: DeliveryMode
    allowed_fallbacks: tuple[DeliveryMode, ...]
    task_id: str
    body: str
    deadline: float

    @property
    def operation_key(self) -> str:
        """Return the stable sender-scoped request identity for durable deduplication."""
        raw = json.dumps(
            [self.origin_hub, self.sender, self.request_id],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("ascii")).hexdigest()

    @property
    def digest(self) -> str:
        """Bind every semantic field, including the idempotency key, to one request id."""
        payload = {
            "sender": self.sender,
            "origin_hub": self.origin_hub,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "target": self.target,
            "target_incarnation": self.target_incarnation,
            "mode": self.mode,
            "allowed_fallbacks": self.allowed_fallbacks,
            "task_id": self.task_id,
            "body": self.body,
            "deadline": self.deadline,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def parse_delivery_intent(
    data: Mapping[str, Any], *, sender: str, origin_hub: str, now: float
) -> DeliveryIntent:
    """Parse a strict version-three request under the hub's authoritative clock.

    ``sender`` and ``origin_hub`` are supplied by the hub, never copied from the
    untrusted frame. Unknown security-sensitive fields are refused to avoid a
    v2 or later peer silently interpreting a different contract.
    """
    permitted = {
        "sender",
        "type",
        "target",
        "payload",
        "timestamp",
        "protocol_version",
        "request_id",
        "idempotency_key",
        "target_incarnation",
        "mode",
        "allowed_fallbacks",
        "task_id",
        "body",
        "deadline",
        "token",
        "idem_key",
        "auth",
    }
    if set(data) - permitted:
        raise DeliveryRefusal("invalid_shape", "request has unsupported fields")
    if data.get("protocol_version") != 3 or isinstance(data.get("protocol_version"), bool):
        raise DeliveryRefusal("unsupported_protocol", "delivery requires wire version 3")
    request_id = _bounded_text(data.get("request_id"), "request_id", MAX_ID_BYTES)
    idempotency_key = _bounded_text(data.get("idempotency_key"), "idempotency_key", MAX_ID_BYTES)
    target = _exact_target(data.get("target"))
    target_incarnation = _incarnation(data.get("target_incarnation"))
    mode = _mode(data.get("mode"))
    raw_fallbacks = data.get("allowed_fallbacks", [])
    if not isinstance(raw_fallbacks, list) or len(raw_fallbacks) > MAX_FALLBACKS:
        raise DeliveryRefusal("invalid_shape", "allowed_fallbacks must be a bounded list")
    fallbacks = tuple(_mode(raw) for raw in raw_fallbacks)
    if len(set(fallbacks)) != len(fallbacks) or mode in fallbacks:
        raise DeliveryRefusal("invalid_shape", "allowed_fallbacks must be distinct from mode")
    task_id = _bounded_text(data.get("task_id", ""), "task_id", MAX_ID_BYTES, allow_empty=True)
    body = _bounded_text(data.get("body"), "body", MAX_BODY_BYTES)
    raw_deadline = data.get("deadline")
    if isinstance(raw_deadline, bool) or not isinstance(raw_deadline, (int, float)):
        raise DeliveryRefusal("invalid_shape", "deadline must be a finite number")
    try:
        deadline = float(raw_deadline)
    except OverflowError as exc:
        raise DeliveryRefusal("invalid_shape", "deadline must be a finite number") from exc
    if not math.isfinite(deadline):
        raise DeliveryRefusal("invalid_shape", "deadline must be a finite number")
    if deadline <= now:
        raise DeliveryRefusal("deadline_expired", "delivery deadline has elapsed")
    if deadline > now + MAX_DEADLINE_SECONDS:
        raise DeliveryRefusal("invalid_shape", "deadline exceeds the supported horizon")
    return DeliveryIntent(
        sender=sender,
        origin_hub=origin_hub,
        request_id=request_id,
        idempotency_key=idempotency_key,
        target=target,
        target_incarnation=target_incarnation,
        mode=mode,
        allowed_fallbacks=fallbacks,
        task_id=task_id,
        body=body,
        deadline=deadline,
    )


def select_delivery_mode(
    intent: DeliveryIntent, capabilities: Mapping[str, str]
) -> tuple[DeliveryMode, DeliveryQuality]:
    """Choose only a capability advertised by the current recipient incarnation."""
    for candidate in (intent.mode, *intent.allowed_fallbacks):
        quality = capabilities.get(candidate)
        if quality == "native":
            return candidate, "native"
        if quality == "emulated":
            return candidate, "emulated"
    raise DeliveryRefusal("unsupported_mode", "recipient supports none of the allowed modes")


@dataclass(frozen=True)
class DeliveryLifecycle:
    """One projected stage and cancellation fact for a durable intent."""

    stage: DeliveryStage
    cancel_requested: bool = False

    def advance(self, stage: DeliveryStage) -> DeliveryLifecycle:
        """Apply one legal transition while keeping a racing cancellation fact."""
        if stage not in _NEXT_STAGES.get(self.stage, frozenset()):
            raise DeliveryRefusal("invalid_transition", "delivery stage transition is invalid")
        return replace(self, stage=stage)

    def request_cancel(self) -> DeliveryLifecycle:
        """Record a cancellation request without claiming executor confirmation."""
        if self.stage in TERMINAL_STAGES:
            raise DeliveryRefusal("invalid_transition", "terminal delivery cannot be cancelled")
        return replace(self, cancel_requested=True)
