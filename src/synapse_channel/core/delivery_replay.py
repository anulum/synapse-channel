# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed durable delivery replay audit
"""Verify the delivery aggregate against each ordered, profile-bound event.

The event stream is the authority. A missing accepted row, skipped ordinal,
altered duplicate, incompatible profile, or aggregate/outbox mismatch stops
version-three delivery admission during hub startup instead of projecting a
plausible but unproven queue state.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, cast

from synapse_channel.core.delivery_modes import (
    DeliveryLifecycle,
    DeliveryRefusal,
    DeliveryStage,
    parse_delivery_intent,
)
from synapse_channel.core.delivery_persistence import (
    DELIVERY_ACCEPTED,
    DELIVERY_CANCEL_REQUESTED,
    DELIVERY_EVENT_KINDS,
    DELIVERY_QUEUED,
    DELIVERY_TRANSITION,
)

_REQUEST_FIELDS = frozenset(
    {
        "profile",
        "sender",
        "origin_hub",
        "request_id",
        "idempotency_key",
        "target",
        "target_incarnation",
        "mode",
        "allowed_fallbacks",
        "task_id",
        "body",
        "deadline",
    }
)


def _incompatible(detail: str) -> DeliveryRefusal:
    """Return one secret-free startup refusal for a malformed persisted row."""
    return DeliveryRefusal("replay_incompatible", detail)


def _object(raw: object, *, label: str) -> dict[str, Any]:
    """Decode a stored JSON object without trusting its column type or shape."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, ValueError) as exc:
        raise _incompatible(f"stored {label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise _incompatible(f"stored {label} is not an object")
    return value


def _canonical(value: object) -> str:
    """Encode a persisted object in the same canonical form as request admission."""
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise _incompatible("stored delivery value cannot be canonicalized") from exc


@dataclass
class _ReplayState:
    """Expected aggregate reconstructed from one request's ordered events."""

    request: dict[str, Any]
    digest: str
    stage: DeliveryStage
    cancel_requested: bool
    ordinal: int
    event_seq: int
    boundary_delivered: bool = False
    explicitly_acknowledged: bool = False
    selected_mode: str = ""
    quality: str = ""


def verify_delivery_replay(connection: Any) -> None:
    """Reject any disagreement among delivery events, aggregates and outbox.

    ``connection`` is the event store's SQLite or SQLCipher DB-API handle. The
    caller holds its connection lock during this read-side audit.
    """
    states: dict[str, _ReplayState] = {}
    idempotency: dict[tuple[str, str], str] = {}
    notifications: dict[str, tuple[str, str]] = {}
    mutations: dict[tuple[str, str], tuple[str, int]] = {}
    rows = connection.execute(
        "SELECT seq, kind, payload FROM events WHERE kind IN (?, ?, ?, ?) ORDER BY seq",
        tuple(sorted(DELIVERY_EVENT_KINDS)),
    ).fetchall()
    for seq_raw, kind, raw_payload in rows:
        seq = int(seq_raw)
        payload = _object(raw_payload, label="delivery event")
        if payload.get("profile") != 3:
            raise _incompatible("stored delivery event has an incompatible profile")
        key = payload.get("operation_key")
        if not isinstance(key, str) or len(key) != 64:
            raise _incompatible("stored delivery operation key is malformed")
        if kind == DELIVERY_ACCEPTED:
            _accept(states, idempotency, key, payload, seq)
        elif kind == DELIVERY_QUEUED:
            _queue(states, notifications, key, payload, seq)
        elif kind in (DELIVERY_TRANSITION, DELIVERY_CANCEL_REQUESTED):
            _transition(states, notifications, mutations, key, kind, payload, seq)
        else:
            raise _incompatible("stored delivery event kind is unknown")
    _verify_aggregates(connection, states)
    _verify_mutations(connection, mutations)
    _verify_notifications(connection, notifications)


def _accept(
    states: dict[str, _ReplayState],
    idempotency: dict[tuple[str, str], str],
    key: str,
    payload: dict[str, Any],
    seq: int,
) -> None:
    """Validate one first event and bind its exact canonical request content."""
    if key in states:
        raise _incompatible("duplicate accepted delivery request")
    request = payload.get("request")
    if not isinstance(request, dict) or set(request) != _REQUEST_FIELDS:
        raise _incompatible("stored delivery request shape is incompatible")
    if request.get("profile") != 3:
        raise _incompatible("stored delivery request profile is incompatible")
    sender = _required_text(request.get("sender"))
    hub = _required_text(request.get("origin_hub"))
    request_id = _required_text(request.get("request_id"))
    idem = _required_text(request.get("idempotency_key"))
    deadline = request.get("deadline")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise _incompatible("stored delivery deadline is malformed")
    try:
        finite_deadline = float(deadline)
    except OverflowError as exc:
        raise _incompatible("stored delivery deadline is malformed") from exc
    if not math.isfinite(finite_deadline):
        raise _incompatible("stored delivery deadline is malformed")
    frame = {
        field: value
        for field, value in request.items()
        if field not in ("profile", "sender", "origin_hub")
    }
    frame.update({"protocol_version": 3, "type": "delivery_request"})
    try:
        parsed = parse_delivery_intent(
            frame, sender=sender, origin_hub=hub, now=finite_deadline - 1
        )
    except DeliveryRefusal as exc:
        raise _incompatible("stored delivery request violates its profile") from exc
    if _canonical({"profile": 3, **asdict(parsed)}) != _canonical(request):
        raise _incompatible("stored delivery request changed after parsing")
    expected_key = hashlib.sha256(
        json.dumps([hub, sender, request_id], ensure_ascii=True, separators=(",", ":")).encode(
            "ascii"
        )
    ).hexdigest()
    if key != expected_key:
        raise _incompatible("stored delivery request key changed")
    digest_request = dict(request)
    digest_request.pop("profile")
    digest = hashlib.sha256(_canonical(digest_request).encode("ascii")).hexdigest()
    if payload.get("digest") != digest:
        raise _incompatible("stored delivery request digest changed")
    idem_pair = (sender, idem)
    if idem_pair in idempotency:
        raise _incompatible("duplicate delivery idempotency key")
    idempotency[idem_pair] = key
    states[key] = _ReplayState(request, digest, "accepted", False, 0, seq)


def _queue(
    states: dict[str, _ReplayState],
    notifications: dict[str, tuple[str, str]],
    key: str,
    payload: dict[str, Any],
    seq: int,
) -> None:
    """Validate the immediately following queued stage and stable offer id."""
    state = states.get(key)
    if state is None or state.stage != "accepted" or payload.get("ordinal") != 1:
        raise _incompatible("queued delivery lacks its accepted predecessor")
    if payload.get("stage") != "queued":
        raise _incompatible("queued delivery stage is malformed")
    selected = payload.get("selected_mode")
    fallbacks = state.request.get("allowed_fallbacks")
    if not isinstance(fallbacks, list):
        raise _incompatible("stored delivery fallback list is malformed")
    allowed = (state.request.get("mode"), *fallbacks)
    if (
        not isinstance(selected, str)
        or selected not in allowed
        or payload.get("quality") not in ("native", "emulated")
    ):
        raise _incompatible("queued delivery capability is inconsistent")
    notification_id = f"delivery:{key}:1"
    if payload.get("notification_id") != notification_id:
        raise _incompatible("queued delivery notification identity changed")
    state.stage = "queued"
    state.ordinal = 1
    state.event_seq = seq
    state.selected_mode = selected
    state.quality = str(payload["quality"])
    notifications[notification_id] = (key, state.request["target"])


def _transition(
    states: dict[str, _ReplayState],
    notifications: dict[str, tuple[str, str]],
    mutations: dict[tuple[str, str], tuple[str, int]],
    key: str,
    kind: str,
    payload: dict[str, Any],
    seq: int,
) -> None:
    """Reapply one contiguous transition or cancellation request."""
    state = states.get(key)
    if state is None or state.stage == "accepted":
        raise _incompatible("delivery transition lacks a queued predecessor")
    if payload.get("ordinal") != state.ordinal + 1 or payload.get("prior_stage") != state.stage:
        raise _incompatible("delivery transition has an ordinal gap or prior-stage mismatch")
    mutation_id = payload.get("mutation_id")
    mutation_digest = payload.get("mutation_digest")
    if (
        not isinstance(mutation_id, str)
        or not mutation_id
        or not isinstance(mutation_digest, str)
        or len(mutation_digest) != 64
        or any(char not in "0123456789abcdef" for char in mutation_digest)
    ):
        raise _incompatible("delivery mutation identity is malformed")
    mutation_key = (key, mutation_id)
    if mutation_key in mutations:
        raise _incompatible("duplicate delivery mutation identity")
    lifecycle = DeliveryLifecycle(state.stage, state.cancel_requested)
    try:
        if kind == DELIVERY_CANCEL_REQUESTED:
            if (
                payload.get("actor") != state.request.get("sender")
                or payload.get("source") != "sender"
            ):
                raise _incompatible("cancellation requester changed")
            next_lifecycle = lifecycle.request_cancel()
            audience = state.request["target"]
        else:
            next_stage = _delivery_stage(payload.get("stage"))
            next_lifecycle = lifecycle.advance(next_stage)
            source = payload.get("source")
            actor = payload.get("actor")
            if (
                next_stage
                in (
                    "boundary_delivered",
                    "acknowledged",
                    "completed",
                    "failed",
                    "cancelled",
                )
                and source != "recipient"
            ):
                raise _incompatible("delivery stage lacks recipient evidence")
            if next_stage == "expired" and source != "hub":
                raise _incompatible("delivery expiry lacks hub evidence")
            if source == "recipient" and actor != state.request.get("target"):
                raise _incompatible("recipient transition actor changed")
            if source == "hub" and actor != state.request.get("origin_hub"):
                raise _incompatible("hub transition actor changed")
            if source not in ("recipient", "hub"):
                raise _incompatible("transition evidence source is unknown")
            evidence = payload.get("evidence")
            if not isinstance(evidence, dict):
                raise _incompatible("delivery transition evidence is malformed")
            if next_stage in ("completed", "failed") and (
                evidence.get("request_id") != state.request.get("request_id")
                or evidence.get("task_id") != state.request.get("task_id")
            ):
                raise _incompatible("delivery outcome correlation changed")
            audience = state.request["sender"]
    except DeliveryRefusal as exc:
        raise _incompatible("delivery transition violates its lifecycle") from exc
    if (
        payload.get("stage") != next_lifecycle.stage
        or payload.get("cancel_requested") is not next_lifecycle.cancel_requested
    ):
        raise _incompatible("delivery transition projection changed")
    boundary_delivered = state.boundary_delivered or next_lifecycle.stage == "boundary_delivered"
    explicitly_acknowledged = (
        state.explicitly_acknowledged or next_lifecycle.stage == "acknowledged"
    )
    if (
        payload.get("boundary_delivered") is not boundary_delivered
        or payload.get("explicitly_acknowledged") is not explicitly_acknowledged
    ):
        raise _incompatible("delivery acknowledgement evidence changed")
    notification_id = f"delivery:{key}:{state.ordinal + 1}"
    if payload.get("notification_id") != notification_id:
        raise _incompatible("delivery transition notification identity changed")
    state.stage = next_lifecycle.stage
    state.cancel_requested = next_lifecycle.cancel_requested
    state.boundary_delivered = boundary_delivered
    state.explicitly_acknowledged = explicitly_acknowledged
    state.ordinal += 1
    state.event_seq = seq
    mutations[mutation_key] = (mutation_digest, seq)
    notifications[notification_id] = (key, audience)


def _required_text(value: object) -> str:
    """Return a non-empty persisted identity or fail replay without its contents."""
    if not isinstance(value, str) or not value:
        raise _incompatible("stored delivery request identity is malformed")
    return value


def _delivery_stage(value: object) -> DeliveryStage:
    """Narrow the wire stage to the explicit version-three enum."""
    stages = {
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
    }
    if not isinstance(value, str) or value not in stages:
        raise _incompatible("stored delivery stage is unknown")
    return cast(DeliveryStage, value)


def _verify_aggregates(connection: Any, states: dict[str, _ReplayState]) -> None:
    """Compare every projected state with the indexed durable aggregate table."""
    rows = connection.execute(
        "SELECT operation_key, sender, idempotency_key, request_digest, request_json, "
        "target, target_incarnation, deadline, selected_mode, quality, stage, "
        "cancel_requested, boundary_delivered, explicitly_acknowledged, "
        "ordinal, latest_event_seq FROM delivery_requests"
    ).fetchall()
    if len(rows) != len(states):
        raise _incompatible("delivery aggregate count differs from event stream")
    for row in rows:
        key = row[0]
        state = states.get(key)
        if state is None or state.stage == "accepted":
            raise _incompatible("delivery aggregate lacks a queued event")
        expected = (
            key,
            state.request["sender"],
            state.request["idempotency_key"],
            state.digest,
            _canonical(state.request),
            state.request["target"],
            state.request["target_incarnation"],
            state.request["deadline"],
            state.selected_mode,
            state.quality,
            state.stage,
            int(state.cancel_requested),
            int(state.boundary_delivered),
            int(state.explicitly_acknowledged),
            state.ordinal,
            state.event_seq,
        )
        if tuple(row) != expected:
            raise _incompatible("delivery aggregate differs from event stream")


def _verify_mutations(connection: Any, expected: dict[tuple[str, str], tuple[str, int]]) -> None:
    """Require an exact mutation index for every transition and no extras."""
    rows = connection.execute(
        "SELECT operation_key, mutation_id, mutation_digest, event_seq FROM delivery_mutations"
    ).fetchall()
    actual = {(row[0], row[1]): (row[2], row[3]) for row in rows}
    if actual != expected:
        raise _incompatible("delivery mutation index differs from event stream")


def _verify_notifications(connection: Any, expected: dict[str, tuple[str, str]]) -> None:
    """Require one exact identity and audience per committed notification."""
    rows = connection.execute(
        "SELECT notification_id, operation_key, audience, frame_json FROM delivery_notifications"
    ).fetchall()
    if len(rows) != len(expected):
        raise _incompatible("delivery notification count differs from event stream")
    for notification_id, key, audience, raw_frame in rows:
        if expected.get(notification_id) != (key, audience):
            raise _incompatible("delivery notification audience changed")
        frame = _object(raw_frame, label="delivery notification")
        if frame.get("operation_key") != key or frame.get("notification_id") != notification_id:
            raise _incompatible("delivery notification frame identity changed")
        if frame.get("type") == "delivery_status" and frame.get("audience") != audience:
            raise _incompatible("delivery status notification audience changed")
