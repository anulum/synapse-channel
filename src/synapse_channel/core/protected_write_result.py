# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — request-bound protected-write result representation
"""Validate result bindings and declared state without trusting claimed evidence.

A parsed result is historical representation evidence. Signature verification,
trusted journal/quiescence checks and fresh authority state are still required.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from synapse_channel.core.protected_write_operations import (
    validate_protected_write_content_reference,
)
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import (
    _canonical,
    _counter,
    _decode_envelope,
    _fields,
    _identifier,
    parse_protected_write_request,
)

_SEQUENCES = ("admission_sequence", "begin_sequence", "settlement_sequence", "revocation_sequence")
_STATE_FIELDS = (
    "reservation_id",
    "operation_phase",
    "revocation_phase",
    "outcome",
    "writer_principal",
    "writer_incarnation",
    *_SEQUENCES,
)
_BODY_FIELDS = {
    "request_type",
    "request_digest",
    "disposition",
    "evidence_reference",
    "reason_code",
    *_STATE_FIELDS,
}


class ProtectedWriteResultError(ValueError):
    """A result is unbound to its request or declares an inconsistent state."""


@dataclass(frozen=True)
class ParsedProtectedWriteResult:
    """Immutable result representation; never proof of permission or quiescence.

    Parameters
    ----------
    canonical_bytes:
        Complete canonical result including unverified proof metadata.
    request_digest:
        Recomputed digest of the exact bound original request.
    """

    canonical_bytes: bytes
    request_digest: str


def parse_protected_write_result(
    raw: str | bytes,
    *,
    request: str | bytes,
    limits: ProtectedWriteProposalLimits,
    reason_codes: frozenset[str],
) -> ParsedProtectedWriteResult:
    """Parse a signed-result representation bound to an exact original request.

    Parameters
    ----------
    raw:
        Raw UTF-8 result message.
    request:
        Exact original raw request, independently parsed and hashed here.
    limits:
        Explicit representation and claim budgets.
    reason_codes:
        Immutable enrolled error-code vocabulary; never private free-text errors.

    Returns
    -------
    ParsedProtectedWriteResult
        Bound representation only, not a verified authority response.

    Raises
    ------
    ProtectedWriteResultError
        For changed bindings, missing proof metadata or inconsistent result state.
    ValueError
        For invalid request, raw JSON, envelope or content-reference structure.
    """
    original = parse_protected_write_request(request, limits=limits)
    source: dict[str, object] = json.loads(original.canonical_bytes)
    result = _decode_envelope(raw, limits)
    if result["type"] != "protected_write_result" or not ({"auth", "signature"} & set(result)):
        raise ProtectedWriteResultError("result type or authority proof metadata missing")
    if not isinstance(reason_codes, frozenset):
        raise ProtectedWriteResultError("immutable enrolled reason codes required")
    for code in reason_codes:
        _identifier(code, limits)
    for field in (
        "schema_version",
        "request_id",
        "authority_id",
        "authority_continuity",
        "session_id",
        "transaction_id",
        "proposal_sha256",
        "enrollment_revision",
    ):
        if result[field] != source[field]:
            raise ProtectedWriteResultError("result request binding mismatch")
    if result["sender"] != source["target"] or result["target"] != source["sender"]:
        raise ProtectedWriteResultError("result sender or recipient binding mismatch")
    body = _fields(result["body"], _BODY_FIELDS)
    if body["request_type"] != source["type"] or body["request_digest"] != original.request_digest:
        raise ProtectedWriteResultError("result semantic request binding mismatch")
    if body["disposition"] not in (
        "allowed",
        "denied",
        "accepted",
        "pending",
        "effective",
        "known",
        "unknown",
        "conflict",
    ):
        raise ProtectedWriteResultError("invalid result disposition")
    if body["reason_code"] is not None:
        code = _identifier(body["reason_code"], limits)
        if code not in reason_codes:
            raise ProtectedWriteResultError("unenrolled result reason code")
    for field in ("reservation_id", "writer_principal", "writer_incarnation"):
        if body[field] is not None:
            _identifier(body[field], limits)
    for field in _SEQUENCES:
        if body[field] is not None:
            _counter(body[field], limits)
    for field, values in (
        ("operation_phase", (None, "admitted", "executing", "settled", "recovery_required")),
        ("revocation_phase", (None, "open", "requested", "effective")),
        ("outcome", (None, "no_write", "committed", "partial", "unknown")),
    ):
        if body[field] not in values:
            raise ProtectedWriteResultError("invalid result phase or outcome")
    if body["evidence_reference"] is not None:
        validate_protected_write_content_reference(
            body["evidence_reference"], limits=limits.operation_limits
        )
    _state(body)
    _disposition(body)
    request_body = cast(dict[str, object], source["body"])
    requested_reservation = request_body.get("reservation_id")
    if (
        requested_reservation is not None
        and body["reservation_id"] is not None
        and requested_reservation != body["reservation_id"]
    ):
        raise ProtectedWriteResultError("result reservation binding mismatch")
    if (
        "writer_incarnation" in request_body
        and body["writer_incarnation"] is not None
        and request_body["writer_incarnation"] != body["writer_incarnation"]
    ):
        raise ProtectedWriteResultError("result writer incarnation binding mismatch")
    if (
        body["disposition"] == "accepted"
        and source["type"] in ("protected_write_begin", "protected_write_settle")
        and body["writer_principal"] != source["sender"]
    ):
        raise ProtectedWriteResultError("accepted result writer principal binding mismatch")
    return ParsedProtectedWriteResult(_canonical(result), original.request_digest)


def _state(body: dict[str, object]) -> None:
    phase, outcome = body["operation_phase"], body["outcome"]
    if body["reservation_id"] is None:
        if any(body[field] is not None for field in _STATE_FIELDS):
            raise ProtectedWriteResultError("unreserved result contains reservation state")
        return
    if any(
        body[field] is None
        for field in (
            "operation_phase",
            "revocation_phase",
            "writer_principal",
            "writer_incarnation",
            "admission_sequence",
        )
    ):
        raise ProtectedWriteResultError("incomplete reservation state")
    if phase in ("admitted", "executing"):
        if outcome is not None or body["settlement_sequence"] is not None:
            raise ProtectedWriteResultError("unsettled operation claims an outcome")
        if (phase == "admitted") != (body["begin_sequence"] is None):
            raise ProtectedWriteResultError("begin sequence does not match phase")
    else:
        if body["settlement_sequence"] is None:
            raise ProtectedWriteResultError("settlement sequence missing")
        if phase == "settled":
            if outcome not in ("no_write", "committed") or body["evidence_reference"] is None:
                raise ProtectedWriteResultError("settled result lacks known outcome evidence")
            if outcome == "committed" and body["begin_sequence"] is None:
                raise ProtectedWriteResultError("committed result lacks begin sequence")
        elif outcome not in ("partial", "unknown"):
            raise ProtectedWriteResultError("recovery state lacks uncertain outcome")
    if body["revocation_phase"] == "effective":
        if phase != "settled" or body["revocation_sequence"] is None:
            raise ProtectedWriteResultError("effective revocation lacks settled sequence")
    previous = cast(int, body["admission_sequence"])
    for field in ("begin_sequence", "settlement_sequence"):
        value = body[field]
        if value is not None:
            sequence = cast(int, value)
            if sequence < previous:
                raise ProtectedWriteResultError("result sequences are out of order")
            previous = sequence
    if (
        body["revocation_phase"] == "effective"
        and cast(int, body["revocation_sequence"]) < previous
    ):
        raise ProtectedWriteResultError("effective revocation precedes settlement")


def _disposition(body: dict[str, object]) -> None:
    verb = str(body["request_type"]).removeprefix("protected_write_")
    disposition = body["disposition"]
    if verb == "prepare":
        if (
            disposition not in ("allowed", "denied", "conflict")
            or body["reservation_id"] is not None
        ):
            raise ProtectedWriteResultError("prepare result cannot reserve or execute")
    if disposition == "accepted" and verb in ("admit", "recover", "begin", "settle"):
        phases = {
            "admit": ("admitted",),
            "recover": ("admitted",),
            "begin": ("executing",),
            "settle": ("settled", "recovery_required"),
        }
        if body["operation_phase"] not in phases[verb]:
            raise ProtectedWriteResultError("accepted result has wrong phase")
    if disposition == "effective":
        if verb != "revoke" or body["revocation_phase"] != "effective":
            raise ProtectedWriteResultError("effective result is not an effective revocation")
    if verb == "status" and disposition == "unknown":
        if body["reservation_id"] is not None:
            raise ProtectedWriteResultError("unknown status contains reservation state")
