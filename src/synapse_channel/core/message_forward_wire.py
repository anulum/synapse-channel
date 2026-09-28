# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wire codec for forwarding a message to a peer hub
"""Canonical wire codec for forwarding one agent message to the peer hub that hosts its target.

A seat addressed as ``PROJECT/seat@HUB_ID`` lives on another hub. The sending hub forwards the
message to that peer as a
:data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_MESSAGE_FORWARD` frame, and the peer
answers with one :data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_MESSAGE_RESULT`. This
module is the one place that names both shapes, so the serving handler and the network client
agree on the format without importing each other, as
:mod:`synapse_channel.core.multihub_claim_wire` does for claims.

A request carries a ``kind``:

* ``chat`` — an ordinary directed chat for a local seat on the peer;
* ``delivery_request`` — a session-bound delivery intent for a local seat on the peer;
* ``delivery_status`` / ``delivery_cancel`` — a status query or cancellation for a delivery the
  peer admitted earlier through this route;
* ``who`` — the peer's roster and delivery sessions, so a sender can address a remote session.

The codec is pure (no network, clock or hub). Decoding is defensive because both shapes arrive
from another host: any malformed body raises :class:`MessageForwardWireError`, and the caller
refuses rather than acting on a half-read frame.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.hub_address import HUB_ADDRESS_SEPARATOR, is_valid_hub_id

ForwardKind = Literal["chat", "delivery_request", "delivery_status", "delivery_cancel", "who"]
"""Kinds of message a hub forwards to a peer."""

FORWARD_KINDS: frozenset[str] = frozenset(
    {"chat", "delivery_request", "delivery_status", "delivery_cancel", "who"}
)
"""Every accepted :data:`ForwardKind` value."""

TARGETED_KINDS: frozenset[str] = frozenset({"chat", "delivery_request"})
"""Kinds that name a local seat on the receiving hub and therefore require ``target``."""

ForwardDisposition = Literal["accepted", "duplicate", "refused"]
"""How the receiving hub answered a forward."""

FORWARD_DISPOSITIONS: frozenset[str] = frozenset({"accepted", "duplicate", "refused"})
"""Every accepted :data:`ForwardDisposition` value."""

MAX_FORWARD_ID_BYTES = 128
"""Longest accepted forward id, in UTF-8 bytes."""

MAX_NAME_BYTES = 256
"""Longest accepted sender or target seat name, in UTF-8 bytes."""

MAX_DETAIL_BYTES = 512
"""Longest accepted human-readable result detail, in UTF-8 bytes."""

MAX_BODY_BYTES = 262_144
"""Largest accepted request body or result payload, as compact JSON UTF-8 bytes."""

_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")

FORWARD_ID_FIELD = "forward_id"
KIND_FIELD = "kind"
SENDER_FIELD = "sender_seat"
TARGET_FIELD = "target_seat"
BODY_FIELD = "body"
DISPOSITION_FIELD = "disposition"
REASON_CODE_FIELD = "reason_code"
DETAIL_FIELD = "detail"
ANSWERING_HUB_FIELD = "answering_hub"
PAYLOAD_FIELD = "result"


class MessageForwardWireError(SynapseError, ValueError):
    """Raised when a message-forward wire body is malformed.

    A receiving hub that catches this refuses the forward; a sending hub that catches it
    treats the forward as failed and never reports delivery it did not see.
    """

    code = "message_forward_wire"


@dataclass(frozen=True, slots=True)
class MessageForwardRequest:
    """One message a hub forwards to the peer hub hosting its target.

    Parameters
    ----------
    forward_id : str
        Origin-unique idempotency key; a repeated id is answered ``duplicate``.
    kind : ForwardKind
        What the body carries.
    sender_seat : str
        The sender's seat name as authenticated on the origin hub (never hub-qualified).
    target_seat : str
        The receiving hub's local seat for ``chat`` and ``delivery_request``; empty otherwise.
    body : Mapping[str, Any]
        Kind-specific JSON object, at most :data:`MAX_BODY_BYTES` as compact JSON.
    """

    forward_id: str
    kind: ForwardKind
    sender_seat: str
    target_seat: str = ""
    body: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MessageForwardResult:
    """The receiving hub's answer to one forward.

    Parameters
    ----------
    forward_id : str
        The forward id this answers.
    disposition : ForwardDisposition
        ``accepted`` (acted on now), ``duplicate`` (acted on earlier) or ``refused``.
    answering_hub : str
        The id of the hub that answered.
    reason_code : str
        Stable lower-case refusal code; empty unless ``refused``.
    detail : str
        Human-readable detail; may be empty.
    result : Mapping[str, Any]
        Kind-specific JSON object (receipt verdict, delivery status, roster).
    """

    forward_id: str
    disposition: ForwardDisposition
    answering_hub: str
    reason_code: str = ""
    detail: str = ""
    result: Mapping[str, Any] = field(default_factory=dict)


def encode_message_forward_request(request: MessageForwardRequest) -> dict[str, Any]:
    """Return the JSON-object body for a forward request, validating it first.

    Parameters
    ----------
    request : MessageForwardRequest
        The forward to encode.

    Returns
    -------
    dict[str, Any]
        The wire fields ``forward_id``, ``kind``, ``sender_seat``, ``target_seat``, ``body``.

    Raises
    ------
    MessageForwardWireError
        If any field violates the shape :func:`decode_message_forward_request` enforces.
    """
    fields = {
        FORWARD_ID_FIELD: request.forward_id,
        KIND_FIELD: request.kind,
        SENDER_FIELD: request.sender_seat,
        TARGET_FIELD: request.target_seat,
        BODY_FIELD: dict(request.body),
    }
    decode_message_forward_request(fields)
    return fields


def decode_message_forward_request(raw: object) -> MessageForwardRequest:
    """Reconstruct a forward request from a decoded JSON object.

    Parameters
    ----------
    raw : object
        The decoded frame or body; expected to be a mapping.

    Returns
    -------
    MessageForwardRequest
        The validated request.

    Raises
    ------
    MessageForwardWireError
        If the body is not a mapping; ``forward_id`` is empty, oversized or contains control
        characters; ``kind`` is unknown; ``sender_seat`` is empty, oversized or hub-qualified;
        ``target_seat`` is missing for a targeted kind, present for another kind, oversized or
        hub-qualified; or ``body`` is not a JSON object within :data:`MAX_BODY_BYTES`.
    """
    body = _require_mapping(raw, "request")
    kind = body.get(KIND_FIELD)
    if kind not in FORWARD_KINDS:
        raise MessageForwardWireError(f"unknown forward kind {kind!r}")
    target = _text(body.get(TARGET_FIELD, ""), TARGET_FIELD, MAX_NAME_BYTES, allow_empty=True)
    if kind in TARGETED_KINDS and not target:
        raise MessageForwardWireError(f"{kind} forward requires {TARGET_FIELD}")
    if kind not in TARGETED_KINDS and target:
        raise MessageForwardWireError(f"{kind} forward must not name {TARGET_FIELD}")
    return MessageForwardRequest(
        forward_id=_text(body.get(FORWARD_ID_FIELD), FORWARD_ID_FIELD, MAX_FORWARD_ID_BYTES),
        kind=cast("ForwardKind", kind),
        sender_seat=_seat(body.get(SENDER_FIELD), SENDER_FIELD),
        target_seat=_seat(target, TARGET_FIELD) if target else "",
        body=_bounded_object(body.get(BODY_FIELD, {}), BODY_FIELD),
    )


def encode_message_forward_result(result: MessageForwardResult) -> dict[str, Any]:
    """Return the JSON-object body for a forward result, validating it first.

    Parameters
    ----------
    result : MessageForwardResult
        The answer to encode.

    Returns
    -------
    dict[str, Any]
        The wire fields ``forward_id``, ``disposition``, ``answering_hub``, ``reason_code``,
        ``detail`` and ``result``.

    Raises
    ------
    MessageForwardWireError
        If any field violates the shape :func:`decode_message_forward_result` enforces.
    """
    fields = {
        FORWARD_ID_FIELD: result.forward_id,
        DISPOSITION_FIELD: result.disposition,
        ANSWERING_HUB_FIELD: result.answering_hub,
        REASON_CODE_FIELD: result.reason_code,
        DETAIL_FIELD: result.detail,
        PAYLOAD_FIELD: dict(result.result),
    }
    decode_message_forward_result(fields)
    return fields


def decode_message_forward_result(raw: object) -> MessageForwardResult:
    """Reconstruct a forward result from a decoded JSON object.

    Parameters
    ----------
    raw : object
        The decoded frame or body; expected to be a mapping.

    Returns
    -------
    MessageForwardResult
        The validated result.

    Raises
    ------
    MessageForwardWireError
        If the body is not a mapping, ``disposition`` is unknown, ``answering_hub`` is not a
        valid hub id, ``reason_code`` is not a lower-case code (or is set on a non-refusal, or
        missing on a refusal), ``detail`` is oversized, or ``result`` is not a bounded object.
    """
    body = _require_mapping(raw, "result")
    disposition = body.get(DISPOSITION_FIELD)
    if disposition not in FORWARD_DISPOSITIONS:
        raise MessageForwardWireError(f"unknown forward disposition {disposition!r}")
    answering_hub = body.get(ANSWERING_HUB_FIELD)
    if not isinstance(answering_hub, str) or not is_valid_hub_id(answering_hub):
        raise MessageForwardWireError(f"invalid {ANSWERING_HUB_FIELD}")
    reason_code = body.get(REASON_CODE_FIELD, "")
    if not isinstance(reason_code, str):
        raise MessageForwardWireError(f"{REASON_CODE_FIELD} must be a string")
    if disposition == "refused":
        if _REASON_CODE.fullmatch(reason_code) is None:
            raise MessageForwardWireError(f"refusal requires a lower-case {REASON_CODE_FIELD}")
    elif reason_code:
        raise MessageForwardWireError(f"{REASON_CODE_FIELD} is only valid on a refusal")
    return MessageForwardResult(
        forward_id=_text(body.get(FORWARD_ID_FIELD), FORWARD_ID_FIELD, MAX_FORWARD_ID_BYTES),
        disposition=cast("ForwardDisposition", disposition),
        answering_hub=answering_hub,
        reason_code=reason_code,
        detail=_text(body.get(DETAIL_FIELD, ""), DETAIL_FIELD, MAX_DETAIL_BYTES, allow_empty=True),
        result=_bounded_object(body.get(PAYLOAD_FIELD, {}), PAYLOAD_FIELD),
    )


def _require_mapping(raw: object, name: str) -> Mapping[str, Any]:
    """Return ``raw`` as a mapping or raise."""
    if not isinstance(raw, Mapping):
        raise MessageForwardWireError(f"message forward {name} must be a JSON object")
    return raw


def _text(value: object, name: str, max_bytes: int, *, allow_empty: bool = False) -> str:
    """Return a bounded printable string field or raise."""
    if not isinstance(value, str):
        raise MessageForwardWireError(f"{name} must be a string")
    if not value and not allow_empty:
        raise MessageForwardWireError(f"{name} must not be empty")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise MessageForwardWireError(f"{name} is not valid UTF-8 text") from exc
    if size > max_bytes:
        raise MessageForwardWireError(f"{name} exceeds {max_bytes} bytes")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise MessageForwardWireError(f"{name} contains control characters")
    return value


def _seat(value: object, name: str) -> str:
    """Return a local seat name: bounded, printable, trimmed and never hub-qualified."""
    seat = _text(value, name, MAX_NAME_BYTES)
    if seat != seat.strip():
        raise MessageForwardWireError(f"{name} has surrounding whitespace")
    if HUB_ADDRESS_SEPARATOR in seat:
        raise MessageForwardWireError(f"{name} must be a local seat, not a hub-qualified address")
    return seat


def _bounded_object(value: object, name: str) -> dict[str, Any]:
    """Return a JSON object whose compact encoding fits :data:`MAX_BODY_BYTES`, or raise."""
    if not isinstance(value, Mapping):
        raise MessageForwardWireError(f"{name} must be a JSON object")
    try:
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MessageForwardWireError(f"{name} is not JSON-serialisable") from exc
    if len(encoded) > MAX_BODY_BYTES:
        raise MessageForwardWireError(f"{name} exceeds {MAX_BODY_BYTES} bytes")
    return dict(value)
