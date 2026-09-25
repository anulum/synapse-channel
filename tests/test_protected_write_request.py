# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected-write request wire regressions
from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.message_auth import (
    MessageAuthKey,
    sign_event_frame,
    sign_frame,
    sign_legacy_event_frame,
)
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_request import (
    ProtectedWriteRequestError,
    parse_protected_write_request,
    protected_write_operation_key,
)
from test_protected_write_proposal import LIMITS, _proposal


def _request(verb: str) -> dict[str, object]:
    proposal = _proposal()
    parsed = parse_protected_write_proposal(json.dumps(proposal), limits=LIMITS)
    reference = proposal["content_reference"]
    bodies: dict[str, object] = {
        "prepare": {"proposal": proposal},
        "admit": {"proposal": proposal},
        "recover": {"parent_reservation_id": "parent", "proposal": proposal},
        "begin": {"reservation_id": "reservation", "writer_incarnation": "incarnation"},
        "revoke": {"reservation_id": "reservation"},
        "status": {"reservation_id": None},
        "settle": {
            "reservation_id": "reservation",
            "writer_incarnation": "incarnation",
            "outcome": "committed",
            "writer_journal_reference": reference,
            "quiescence_reference": reference,
            "operation_results": [
                {
                    "operation_id": "create-record",
                    "status": "completed",
                    "evidence_reference": reference,
                }
            ],
        },
    }
    return {
        "schema_version": "synapse-protected-write.v1",
        "type": f"protected_write_{verb}",
        "sender": "EXAMPLE/author",
        "target": "EXAMPLE/authority",
        "payload": "",
        "timestamp": 1788649200.0,
        "request_id": "request",
        "authority_id": "authority",
        "authority_continuity": "continuity",
        "session_id": "session",
        "transaction_id": "transaction",
        "proposal_sha256": parsed.proposal_sha256,
        "enrollment_revision": "enrollment",
        "body": bodies[verb],
    }


def _operation_key(request: dict[str, object]) -> str:
    return protected_write_operation_key(
        json.dumps(request),
        limits=LIMITS,
        authenticated_principal=str(request["sender"]),
        authority_id=str(request["authority_id"]),
        authority_continuity=str(request["authority_continuity"]),
    )


@pytest.mark.parametrize("field", ["sender", "authority_id", "authority_continuity", "request_id"])
def test_operation_key_separates_principal_authority_and_request(field: str) -> None:
    request = _request("status")
    original = _operation_key(request)
    assert original.startswith("synapse-protected-write.v1/operation:")
    assert "\x00" not in original
    request[field] = str(request[field]) + "-other"
    assert _operation_key(request) != original


@pytest.mark.parametrize("field", ["sender", "authority_id", "authority_continuity"])
def test_operation_key_refuses_unbound_context(field: str) -> None:
    request = _request("status")
    request[field] = "other"
    with pytest.raises(ProtectedWriteRequestError, match="authenticated authority context"):
        protected_write_operation_key(
            json.dumps(request),
            limits=LIMITS,
            authenticated_principal="EXAMPLE/author",
            authority_id="authority",
            authority_continuity="continuity",
        )


@pytest.mark.parametrize("field", ["type", "session_id", "transaction_id", "enrollment_revision"])
def test_semantic_change_preserves_key_and_changes_digest(field: str) -> None:
    request = _request("begin")
    key = _operation_key(request)
    digest = parse_protected_write_request(json.dumps(request), limits=LIMITS).request_digest
    if field == "type":
        request = _request("revoke")
    else:
        request[field] = str(request[field]) + "-changed"
    assert _operation_key(request) == key
    assert (
        parse_protected_write_request(json.dumps(request), limits=LIMITS).request_digest != digest
    )


def test_operation_key_uses_original_wire_validation() -> None:
    with pytest.raises(ValueError):
        protected_write_operation_key(
            '{"sender":"EXAMPLE/author","sender":"other"}',
            limits=LIMITS,
            authenticated_principal="EXAMPLE/author",
            authority_id="authority",
            authority_continuity="continuity",
        )


@pytest.mark.parametrize(
    "verb", ["prepare", "admit", "recover", "begin", "revoke", "status", "settle"]
)
def test_seven_request_forms_parse_without_authorising(verb: str) -> None:
    request = _request(verb)
    parsed = parse_protected_write_request(json.dumps(request).encode(), limits=LIMITS)
    assert json.loads(parsed.canonical_bytes) == request
    assert (parsed.proposal is not None) == (verb in ("prepare", "admit", "recover"))
    assert parse_protected_write_request(parsed.canonical_bytes, limits=LIMITS) == parsed


@pytest.mark.parametrize(
    "field,value",
    [
        ("extra", 1),
        ("schema_version", "unknown"),
        ("payload", "not-empty"),
        ("type", None),
        ("type", "chat"),
        ("type", "protected_write_result"),
        ("sender", ""),
        ("target", "all"),
        ("target", "EXAMPLE/*"),
        ("timestamp", 1),
        ("client_timestamp", 1),
        ("proposal_sha256", "bad"),
        ("body", None),
        ("body", {"reservation_id": None, "extra": None}),
    ],
)
def test_invalid_request_envelopes_refuse(field: str, value: object) -> None:
    request = _request("status")
    request[field] = value
    with pytest.raises(ProtectedWriteRequestError):
        parse_protected_write_request(json.dumps(request), limits=LIMITS)


def test_embedded_proposal_digest_must_match() -> None:
    request = _request("admit")
    request["proposal_sha256"] = "0" * 64
    with pytest.raises(ProtectedWriteRequestError, match="binding"):
        parse_protected_write_request(json.dumps(request), limits=LIMITS)


def test_client_timestamp_excluded_but_body_remains_digest_bound() -> None:
    request = _request("status")
    original = parse_protected_write_request(json.dumps(request), limits=LIMITS)
    request["client_timestamp"] = 1788649200.0
    assert (
        parse_protected_write_request(json.dumps(request), limits=LIMITS).request_digest
        == original.request_digest
    )
    request["body"] = {"reservation_id": "reservation"}
    assert (
        parse_protected_write_request(json.dumps(request), limits=LIMITS).request_digest
        != original.request_digest
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcome", "wrong"),
        ("quiescence_reference", None),
        ("operation_results", []),
        (
            "operation_results",
            [{"operation_id": "op", "status": "wrong", "evidence_reference": None}],
        ),
    ],
)
def test_incomplete_or_invalid_settlement_refuses(field: str, value: object) -> None:
    request = _request("settle")
    cast(dict[str, object], request["body"])[field] = value
    with pytest.raises(ProtectedWriteRequestError):
        parse_protected_write_request(json.dumps(request), limits=LIMITS)


def test_partial_settlement_may_preserve_unknown_quiescence() -> None:
    request = _request("settle")
    body = cast(dict[str, object], request["body"])
    body["outcome"], body["quiescence_reference"] = "partial", None
    body["operation_results"] = [
        {"operation_id": "op", "status": "unknown", "evidence_reference": None}
    ]
    parsed = parse_protected_write_request(json.dumps(request), limits=LIMITS)
    assert json.loads(parsed.canonical_bytes)["body"]["outcome"] == "partial"


@pytest.mark.parametrize("profile", ["hmac", "signature", "legacy-signature"])
def test_existing_core_signers_preserve_wire_shape_and_semantic_digest(profile: str) -> None:
    request = _request("status")
    original = parse_protected_write_request(json.dumps(request), limits=LIMITS)
    if profile == "hmac":
        signed = sign_frame(
            request,
            key=MessageAuthKey("test-key", b"test-only-message-secret"),
            nonce="nonce",
            sequence=1,
            timestamp=1788649200.0,
        )
    else:
        signer = sign_event_frame if profile == "signature" else sign_legacy_event_frame
        signed = signer(
            request,
            key_id="test-key",
            private_key=Ed25519PrivateKey.generate(),
            nonce="nonce",
            sequence=1,
            signed_at=1788649200.0,
        )
    parsed = parse_protected_write_request(json.dumps(signed), limits=LIMITS)
    assert json.loads(parsed.canonical_bytes) == signed
    assert parsed.request_digest == original.request_digest


@pytest.mark.parametrize(
    "field,value",
    [
        ("alg", "wrong"),
        ("sequence", True),
        ("timestamp", 1),
        ("value", "bad"),
        ("extra", 1),
    ],
)
def test_malformed_hmac_envelope_refuses(field: str, value: object) -> None:
    signed = sign_frame(
        _request("status"),
        key=MessageAuthKey("test-key", b"test-only-secret"),
        nonce="nonce",
        sequence=1,
        timestamp=1788649200.0,
    )
    signed["auth"][field] = value
    with pytest.raises(ProtectedWriteRequestError):
        parse_protected_write_request(json.dumps(signed), limits=LIMITS)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 3),
        ("algorithm", "wrong"),
        ("domain", "foreign"),
        ("value", None),
        ("value", "!invalid!"),
        ("value", "YQ=="),
        ("extra", None),
    ],
)
def test_malformed_signature_envelope_refuses(field: str, value: object) -> None:
    signed = sign_event_frame(
        _request("status"),
        key_id="test-key",
        private_key=Ed25519PrivateKey.generate(),
        nonce="nonce",
        sequence=1,
        signed_at=1788649200.0,
    )
    signed["signature"][field] = value
    with pytest.raises(ProtectedWriteRequestError):
        parse_protected_write_request(json.dumps(signed), limits=LIMITS)


def test_nested_proposal_budget_counts_original_wire_not_ascii_expansion() -> None:
    limits = replace(
        LIMITS, operation_limits=replace(LIMITS.operation_limits, max_identifier_chars=1024)
    )
    request = _request("admit")
    proposal_text = json.dumps(_proposal()).replace("records/note.md", "records/" + "é" * 400)
    parsed_proposal = parse_protected_write_proposal(proposal_text, limits=limits)
    request["body"] = {"proposal": json.loads(proposal_text)}
    request["proposal_sha256"] = parsed_proposal.proposal_sha256
    raw = json.dumps(request, ensure_ascii=False).encode()
    assert len(parsed_proposal.canonical_bytes) > len(raw)
    exact = replace(limits, json_limits=replace(limits.json_limits, max_wire_bytes=len(raw)))
    assert parse_protected_write_request(raw, limits=exact).proposal == parsed_proposal
    with pytest.raises(ValueError, match="wire budget"):
        parse_protected_write_request(
            raw,
            limits=replace(
                exact, json_limits=replace(exact.json_limits, max_wire_bytes=len(raw) - 1)
            ),
        )
