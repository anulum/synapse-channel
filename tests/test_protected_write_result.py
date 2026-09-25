# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — request-bound protected-write result regressions
from __future__ import annotations

import json
from typing import cast

import pytest

from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.protected_write_request import parse_protected_write_request
from synapse_channel.core.protected_write_result import (
    ProtectedWriteResultError,
    parse_protected_write_result,
)
from test_protected_write_proposal import LIMITS, _proposal
from test_protected_write_request import _request


def _result(verb: str) -> tuple[dict[str, object], dict[str, object]]:
    request = _request(verb)
    if verb in ("begin", "settle"):
        request["sender"] = "EXAMPLE/writer"
    parsed = parse_protected_write_request(json.dumps(request), limits=LIMITS)
    body: dict[str, object] = {
        "request_type": request["type"],
        "request_digest": parsed.request_digest,
        "disposition": "unknown" if verb == "status" else "allowed",
        "reservation_id": None,
        "operation_phase": None,
        "revocation_phase": None,
        "outcome": None,
        "writer_principal": None,
        "writer_incarnation": None,
        "admission_sequence": None,
        "begin_sequence": None,
        "settlement_sequence": None,
        "revocation_sequence": None,
        "evidence_reference": None,
        "reason_code": None,
    }
    if verb not in ("status", "prepare"):
        body.update(
            disposition="accepted",
            reservation_id="reservation",
            operation_phase="admitted",
            revocation_phase="open",
            writer_principal="EXAMPLE/writer",
            writer_incarnation="incarnation",
            admission_sequence=1,
        )
    if verb in ("begin", "settle", "revoke"):
        body.update(operation_phase="executing", begin_sequence=2)
    if verb in ("settle", "revoke"):
        body.update(
            operation_phase="settled",
            outcome="committed",
            settlement_sequence=3,
            evidence_reference=_proposal()["content_reference"],
        )
    if verb == "revoke":
        body.update(disposition="effective", revocation_phase="effective", revocation_sequence=4)
    result = {
        **request,
        "type": "protected_write_result",
        "sender": request["target"],
        "target": request["sender"],
        "body": body,
    }
    signed = sign_frame(
        result,
        key=MessageAuthKey("authority-key", b"test-only-authority-secret"),
        nonce="response-nonce",
        sequence=1,
        timestamp=1788649200.0,
    )
    return request, signed


def _parse(request: dict[str, object], result: dict[str, object]) -> bytes:
    return parse_protected_write_result(
        json.dumps(result),
        request=json.dumps(request),
        limits=LIMITS,
        reason_codes=frozenset({"denied"}),
    ).canonical_bytes


@pytest.mark.parametrize(
    "verb", ["prepare", "admit", "recover", "begin", "settle", "revoke", "status"]
)
def test_all_result_forms_bind_the_original_request(verb: str) -> None:
    request, result = _result(verb)
    assert json.loads(_parse(request, result)) == result


@pytest.mark.parametrize(
    "field",
    [
        "request_id",
        "authority_id",
        "authority_continuity",
        "session_id",
        "transaction_id",
        "enrollment_revision",
        "sender",
        "target",
    ],
)
def test_changed_envelope_binding_refuses(field: str) -> None:
    request, result = _result("status")
    result[field] = "other"
    with pytest.raises(ProtectedWriteResultError, match="binding"):
        _parse(request, result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_type", "protected_write_begin"),
        ("request_digest", "0" * 64),
        ("disposition", "invented"),
        ("reason_code", "not-enrolled"),
        ("operation_phase", "invented"),
        ("revocation_phase", "invented"),
        ("outcome", "invented"),
        ("admission_sequence", 0),
    ],
)
def test_invalid_or_unreserved_state_refuses(field: str, value: object) -> None:
    request, result = _result("status")
    cast(dict[str, object], result["body"])[field] = value
    with pytest.raises(ProtectedWriteResultError):
        _parse(request, result)


@pytest.mark.parametrize("mutation", ["wrong-type", "no-proof", "bad-reasons"])
def test_result_requires_declared_proof_and_reason_vocabulary(mutation: str) -> None:
    request, result = _result("status")
    codes = frozenset({"denied"})
    if mutation == "wrong-type":
        result["type"] = "protected_write_status"
    elif mutation == "no-proof":
        del result["auth"]
    else:
        codes = cast(frozenset[str], ["denied"])
    with pytest.raises(ProtectedWriteResultError):
        parse_protected_write_result(
            json.dumps(result), request=json.dumps(request), limits=LIMITS, reason_codes=codes
        )


def test_enrolled_reason_code_allowed_without_private_free_text() -> None:
    request, result = _result("status")
    cast(dict[str, object], result["body"]).update(disposition="denied", reason_code="denied")
    assert json.loads(_parse(request, result))["body"]["reason_code"] == "denied"


@pytest.mark.parametrize(
    "verb,field,value",
    [
        ("admit", "writer_principal", None),
        ("admit", "outcome", "committed"),
        ("admit", "begin_sequence", 2),
        ("begin", "begin_sequence", None),
        ("settle", "settlement_sequence", None),
        ("settle", "evidence_reference", None),
        ("settle", "begin_sequence", None),
        ("revoke", "revocation_sequence", None),
        ("revoke", "revocation_sequence", 2),
        ("settle", "settlement_sequence", 1),
        ("begin", "reservation_id", "foreign"),
        ("begin", "writer_incarnation", "foreign"),
        ("begin", "writer_principal", "EXAMPLE/foreign"),
        ("prepare", "disposition", "accepted"),
        ("status", "disposition", "effective"),
        ("admit", "operation_phase", "executing"),
    ],
)
def test_inconsistent_reservation_results_refuse(verb: str, field: str, value: object) -> None:
    request, result = _result(verb)
    cast(dict[str, object], result["body"])[field] = value
    with pytest.raises(ProtectedWriteResultError):
        _parse(request, result)


def test_cancelled_before_begin_has_no_write_settlement() -> None:
    request, result = _result("revoke")
    cast(dict[str, object], result["body"]).update(begin_sequence=None, outcome="no_write")
    assert json.loads(_parse(request, result))["body"]["begin_sequence"] is None


def test_pending_revocation_is_not_effective_and_partial_stays_recovery_required() -> None:
    request, result = _result("revoke")
    body = cast(dict[str, object], result["body"])
    body.update(
        disposition="pending",
        revocation_phase="requested",
        operation_phase="executing",
        settlement_sequence=None,
        outcome=None,
        evidence_reference=None,
    )
    assert json.loads(_parse(request, result))["body"]["disposition"] == "pending"
    body.update(operation_phase="recovery_required", outcome="partial", settlement_sequence=5)
    assert json.loads(_parse(request, result))["body"]["operation_phase"] == "recovery_required"
    body["outcome"] = "committed"
    with pytest.raises(ProtectedWriteResultError, match="uncertain"):
        _parse(request, result)


def test_accepted_begin_cannot_return_a_merely_admitted_state() -> None:
    request, result = _result("begin")
    cast(dict[str, object], result["body"]).update(operation_phase="admitted", begin_sequence=None)
    with pytest.raises(ProtectedWriteResultError, match="wrong phase"):
        _parse(request, result)


def test_unknown_status_cannot_carry_a_reservation() -> None:
    request, result = _result("admit")
    status_request = _request("status")
    parsed = parse_protected_write_request(json.dumps(status_request), limits=LIMITS)
    cast(dict[str, object], result["body"]).update(
        request_type=status_request["type"],
        request_digest=parsed.request_digest,
        disposition="unknown",
    )
    with pytest.raises(ProtectedWriteResultError, match="unknown status"):
        _parse(status_request, result)
