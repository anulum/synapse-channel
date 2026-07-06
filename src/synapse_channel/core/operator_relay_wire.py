# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wire codec for relaying a governed operator action to a peer hub
"""Canonical wire codec for relaying a governed operator action to a peer hub.

A cross-hub operator relay lets an operator on one domain ask a peer hub to perform a
bounded, governed action inside a namespace that peer owns — the first being a
force-release of a stuck lease (:mod:`synapse_channel.core.operator_relay`). This module
names the two shapes of that exchange so the initiating side (a CLI client) and the
serving side (a handler on the acting hub) agree on the format without importing each
other, exactly as :mod:`synapse_channel.core.multihub_claim_wire` does for claims.

Two shapes ride the exchange:

* a :class:`RelayActionRequest` — which action to perform, the namespace and task it acts
  on, and the asserted operator and origin-hub provenance the acting hub audits;
* a :class:`RelayActionResult` — whether the acting hub applied the action, the id of the
  hub that answered, and a human-readable detail.

The codec is **pure**: no network, no clock, no hub dependency — it only converts these
shapes to and from the JSON-object bodies that ride the wire envelope
(:func:`synapse_channel.core.protocol.build_envelope`). Decoding is defensive because a
request and a result each arrive from another host: a malformed body raises
:class:`RelayWireError` rather than yielding a half-built shape, so a relay a hub cannot
parse is refused rather than acted on — the same fail-closed posture the governed apply
path holds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ACTION_FIELD = "action"
"""Request field: the relayable action id, resolved against the deny-by-default registry."""

NAMESPACE_FIELD = "namespace"
"""Request and result field: the namespace the action acts in, owned by the acting hub."""

TASK_ID_FIELD = "task_id"
"""Request and result field: the task the action targets, correlating a result to its request."""

OPERATOR_FIELD = "operator"
"""Request field: the operator identity the relay is asserted under, recorded for audit."""

ORIGIN_HUB_ID_FIELD = "origin_hub_id"
"""Request field: the hub id the relay asserts it originates from, recorded for audit."""

REASON_FIELD = "reason"
"""Request field: the operator's reason for the relay, recorded on both hubs; may be empty."""

BREAK_GLASS_FIELD = "break_glass"
"""Request field: whether the relay is tagged as a break-glass emergency override."""

APPLIED_FIELD = "applied"
"""Result field: whether the acting hub applied the action."""

OWNER_HUB_ID_FIELD = "owner_hub_id"
"""Result field: the id of the acting hub that authoritatively answered."""

DETAIL_FIELD = "detail"
"""Result field: the human-readable applied or refused message, possibly empty."""

PENDING_FIELD = "pending"
"""Result field: whether the action is recorded and awaiting a second operator's approval."""


class RelayWireError(ValueError):
    """Raised when an operator-relay wire body is malformed.

    Carries the fail-closed contract: a side that catches this refuses the relay and
    performs no action, so a corrupt or hostile request or result can never cause an
    action to be applied on doubt.
    """


@dataclass(frozen=True, slots=True)
class RelayActionRequest:
    """An operator's request that a peer hub perform a governed action on their behalf.

    Parameters
    ----------
    action : str
        The relayable action id (for example a force-release); the acting hub resolves it
        against its deny-by-default registry and refuses an unknown action. Non-empty.
    namespace : str
        The namespace the action acts in; the acting hub verifies it owns this. Non-empty.
    task_id : str
        The task the action targets, echoed back in the result for correlation. Non-empty.
    operator : str
        The operator identity the relay is asserted under, recorded in the acting hub's
        audit trail as descriptive provenance. Non-empty.
    origin_hub_id : str
        The hub id the relay asserts it originates from, recorded for audit alongside the
        cryptographically verified peer identity. Non-empty.
    reason : str, optional
        The operator's reason for the relay, recorded in the audit on both hubs. Empty by
        default; a hub may refuse a relay without one when it requires a reason (reason-required
        receipts). Not an identifier, so it is carried as-is, blank or not.
    break_glass : bool, optional
        Whether the relay is tagged a break-glass emergency override, recorded distinctly in the
        audit so an out-of-band, urgent action stands apart from routine governance in the log.
    """

    action: str
    namespace: str
    task_id: str
    operator: str
    origin_hub_id: str
    reason: str = ""
    break_glass: bool = False


@dataclass(frozen=True, slots=True)
class RelayActionResult:
    """The acting hub's answer to a relayed operator action.

    Parameters
    ----------
    applied : bool
        Whether the acting hub applied the action.
    action : str
        The action the result answers, matching the request. Non-empty.
    namespace : str
        The namespace the action concerned. Non-empty.
    task_id : str
        The task the action targeted, matching the request. Non-empty.
    owner_hub_id : str
        The id of the acting hub that produced this answer. Non-empty.
    detail : str
        The human-readable applied or refused message; may be empty.
    pending : bool, optional
        Whether the action was recorded and is awaiting a second operator's approval under a
        two-person policy — distinct from a refusal (``applied`` false, ``pending`` false) and from
        an applied action (``applied`` true). Defaults false, so a single-operator hub and an older
        initiator read exactly as before.
    """

    applied: bool
    action: str
    namespace: str
    task_id: str
    owner_hub_id: str
    detail: str = ""
    pending: bool = False


def encode_relay_request(request: RelayActionRequest) -> dict[str, Any]:
    """Return the JSON-object body for an operator-relay request.

    Raises
    ------
    RelayWireError
        If any identifier field is empty.
    """
    return {
        ACTION_FIELD: _require_nonempty(request.action, ACTION_FIELD),
        NAMESPACE_FIELD: _require_nonempty(request.namespace, NAMESPACE_FIELD),
        TASK_ID_FIELD: _require_nonempty(request.task_id, TASK_ID_FIELD),
        OPERATOR_FIELD: _require_nonempty(request.operator, OPERATOR_FIELD),
        ORIGIN_HUB_ID_FIELD: _require_nonempty(request.origin_hub_id, ORIGIN_HUB_ID_FIELD),
        REASON_FIELD: str(request.reason),
        BREAK_GLASS_FIELD: bool(request.break_glass),
    }


def decode_relay_request(raw: object) -> RelayActionRequest:
    """Reconstruct an operator-relay request from a decoded JSON object.

    Raises
    ------
    RelayWireError
        If the body is not a mapping or an identifier field is missing, non-string, or empty.
    """
    body = _require_mapping(raw, "request")
    raw_reason = body.get(REASON_FIELD)
    raw_break_glass = body.get(BREAK_GLASS_FIELD)
    return RelayActionRequest(
        action=_require_nonempty(body.get(ACTION_FIELD), ACTION_FIELD),
        namespace=_require_nonempty(body.get(NAMESPACE_FIELD), NAMESPACE_FIELD),
        task_id=_require_nonempty(body.get(TASK_ID_FIELD), TASK_ID_FIELD),
        operator=_require_nonempty(body.get(OPERATOR_FIELD), OPERATOR_FIELD),
        origin_hub_id=_require_nonempty(body.get(ORIGIN_HUB_ID_FIELD), ORIGIN_HUB_ID_FIELD),
        reason="" if raw_reason is None else _require_str(raw_reason, REASON_FIELD),
        break_glass=(
            False if raw_break_glass is None else _require_bool(raw_break_glass, BREAK_GLASS_FIELD)
        ),
    )


def encode_relay_result(result: RelayActionResult) -> dict[str, Any]:
    """Return the JSON-object body for an operator-relay result.

    Raises
    ------
    RelayWireError
        If ``action``, ``namespace``, ``task_id``, or ``owner_hub_id`` is empty.
    """
    return {
        APPLIED_FIELD: bool(result.applied),
        ACTION_FIELD: _require_nonempty(result.action, ACTION_FIELD),
        NAMESPACE_FIELD: _require_nonempty(result.namespace, NAMESPACE_FIELD),
        TASK_ID_FIELD: _require_nonempty(result.task_id, TASK_ID_FIELD),
        OWNER_HUB_ID_FIELD: _require_nonempty(result.owner_hub_id, OWNER_HUB_ID_FIELD),
        DETAIL_FIELD: str(result.detail),
        PENDING_FIELD: bool(result.pending),
    }


def decode_relay_result(raw: object) -> RelayActionResult:
    """Reconstruct an operator-relay result from a decoded JSON object.

    Raises
    ------
    RelayWireError
        If the body is not a mapping, ``applied`` is missing or not a boolean, an
        identifier field is missing/non-string/empty, or ``detail`` is present but not a string.
    """
    body = _require_mapping(raw, "result")
    raw_detail = body.get(DETAIL_FIELD)
    detail = "" if raw_detail is None else _require_str(raw_detail, DETAIL_FIELD)
    raw_pending = body.get(PENDING_FIELD)
    return RelayActionResult(
        applied=_require_bool(body.get(APPLIED_FIELD), APPLIED_FIELD),
        action=_require_nonempty(body.get(ACTION_FIELD), ACTION_FIELD),
        namespace=_require_nonempty(body.get(NAMESPACE_FIELD), NAMESPACE_FIELD),
        task_id=_require_nonempty(body.get(TASK_ID_FIELD), TASK_ID_FIELD),
        owner_hub_id=_require_nonempty(body.get(OWNER_HUB_ID_FIELD), OWNER_HUB_ID_FIELD),
        detail=detail,
        pending=False if raw_pending is None else _require_bool(raw_pending, PENDING_FIELD),
    )


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    """Return ``value`` as a mapping or raise :class:`RelayWireError`."""
    if not isinstance(value, Mapping):
        msg = f"{name} body must be a JSON object"
        raise RelayWireError(msg)
    return value


def _require_str(value: object, name: str) -> str:
    """Return ``value`` as a string or raise :class:`RelayWireError`."""
    if not isinstance(value, str):
        msg = f"{name} must be a string"
        raise RelayWireError(msg)
    return value


def _require_nonempty(value: object, name: str) -> str:
    """Return ``value`` as a non-blank string or raise :class:`RelayWireError`.

    An identifier that is missing, non-string, or blank after stripping cannot route or
    correlate a relay, so it fails closed rather than acting on an unaddressable request.
    """
    text = _require_str(value, name)
    if not text.strip():
        msg = f"{name} must not be empty"
        raise RelayWireError(msg)
    return text


def _require_bool(value: object, name: str) -> bool:
    """Return ``value`` as a boolean or raise :class:`RelayWireError`.

    A non-boolean applied verdict is rejected outright: a side never coerces a string or
    number into an applied decision.
    """
    if not isinstance(value, bool):
        msg = f"{name} must be a boolean"
        raise RelayWireError(msg)
    return value
