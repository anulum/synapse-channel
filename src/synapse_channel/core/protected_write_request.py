# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected-write request representation
"""Parse seven protected-write request forms without admitting any mutation.

Proof shape validation is not cryptographic verification. Authority/session
binding, replay, freshness and reservation checks remain mandatory downstream.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass

from synapse_channel.core.aef_domain import AEF_LEGACY_EVENT_DOMAIN
from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.protected_write_json import decode_protected_write_json
from synapse_channel.core.protected_write_operations import (
    validate_protected_write_content_reference,
)
from synapse_channel.core.protected_write_proposal import (
    ParsedProtectedWriteProposal,
    ProtectedWriteProposalLimits,
    _bind_protected_write_proposal,
)

_COMMON = {
    "schema_version",
    "type",
    "sender",
    "target",
    "payload",
    "timestamp",
    "request_id",
    "authority_id",
    "authority_continuity",
    "session_id",
    "transaction_id",
    "proposal_sha256",
    "enrollment_revision",
    "body",
}
_BODIES = {
    "prepare": {"proposal"},
    "admit": {"proposal"},
    "begin": {"reservation_id", "writer_incarnation"},
    "settle": {
        "reservation_id",
        "writer_incarnation",
        "outcome",
        "writer_journal_reference",
        "operation_results",
        "quiescence_reference",
    },
    "revoke": {"reservation_id"},
    "status": {"reservation_id"},
    "recover": {"parent_reservation_id", "proposal"},
}


class ProtectedWriteRequestError(SynapseError, ValueError):
    """A protected-write request has malformed or inconsistent declared fields.

    Attributes
    ----------
    code : str
        Stable classification returned by ``error_code``.
    """

    code = "protected_write_request"


@dataclass(frozen=True)
class ParsedProtectedWriteRequest:
    """Immutable request representation, not a verified principal or grant.

    Parameters
    ----------
    canonical_bytes:
        Canonical full request including any proof fields and timestamps.
    request_digest:
        Existing Core semantic digest with only its documented exclusions.
    proposal:
        Parsed proposal for prepare/admit/recover, otherwise None.
    """

    canonical_bytes: bytes
    request_digest: str
    proposal: ParsedProtectedWriteProposal | None


def protected_write_operation_key(
    raw: str | bytes,
    *,
    limits: ProtectedWriteProposalLimits,
    authenticated_principal: str,
    authority_id: str,
    authority_continuity: str,
) -> str:
    """Bind a validated request key to server-resolved identity and continuity.

    Parameters
    ----------
    raw:
        Original request JSON, not a caller-constructed parsed value.
    limits:
        Explicit request representation limits.
    authenticated_principal:
        Principal already authenticated by Core, never copied from raw sender.
    authority_id:
        Current server authority identifier.
    authority_continuity:
        Current server authority continuity identifier.

    Returns
    -------
    str
        Domain-separated journal key. Request type, session and body remain in
        the semantic digest, not the key: changing them must conflict on reuse.

    Raises
    ------
    ProtectedWriteRequestError
        If the declared sender or authority does not match server context.
    ValueError
        If the original request representation is invalid.

    Notes
    -----
    This is an identity binding, not authentication, freshness checking or an
    execution permit. Cryptographic verification and active-session policy must
    precede journal lookup. Digest equality must still be enforced by the actor.
    The no-NUL key cannot alias the legacy sender-NUL-type-NUL-idem_key format.
    """
    parsed = parse_protected_write_request(raw, limits=limits)
    request = json.loads(parsed.canonical_bytes)
    if (
        request["sender"] != authenticated_principal
        or request["authority_id"] != authority_id
        or request["authority_continuity"] != authority_continuity
    ):
        raise ProtectedWriteRequestError("request does not match authenticated authority context")
    namespace = {
        "domain": "synapse-protected-write.v1/operation",
        "principal": authenticated_principal,
        "authority_id": authority_id,
        "authority_continuity": authority_continuity,
        "request_id": request["request_id"],
    }
    digest = hashlib.sha256(_canonical(namespace)).hexdigest()
    return f"synapse-protected-write.v1/operation:{digest}"


def parse_protected_write_request(
    raw: str | bytes, *, limits: ProtectedWriteProposalLimits
) -> ParsedProtectedWriteRequest:
    """Validate a complete request representation and embedded proposal binding.

    Parameters
    ----------
    raw:
        Raw UTF-8 JSON request, preserving duplicate-key/numeric token evidence.
    limits:
        Explicit representation budgets and enrolled vocabulary.

    Returns
    -------
    ParsedProtectedWriteRequest
        Immutable parsed evidence. Never use this value as a begin permit.

    Raises
    ------
    ProtectedWriteRequestError
        For unknown fields, unsupported type/schema or inconsistent bindings.
    ValueError
        For invalid raw JSON, proposal or content-reference representations.
    """
    request = _decode_envelope(raw, limits)
    message_type = request["type"]
    if not isinstance(message_type, str) or not message_type.startswith("protected_write_"):
        raise ProtectedWriteRequestError("unsupported protected request type")
    verb = message_type.removeprefix("protected_write_")
    if verb not in _BODIES:
        raise ProtectedWriteRequestError("unsupported protected request type")
    body = _fields(request["body"], _BODIES[verb])
    proposal = None
    if verb in ("prepare", "admit", "recover"):
        proposal = _bind_protected_write_proposal(body["proposal"], limits=limits)
        if proposal.proposal_sha256 != request["proposal_sha256"]:
            raise ProtectedWriteRequestError("proposal digest binding mismatch")
        if verb == "recover":
            _identifier(body["parent_reservation_id"], limits)
    else:
        if verb != "status" or body["reservation_id"] is not None:
            _identifier(body["reservation_id"], limits)
        if verb in ("begin", "settle"):
            _identifier(body["writer_incarnation"], limits)
        if verb == "settle":
            _settlement(body, limits)
    return ParsedProtectedWriteRequest(
        _canonical(request), canonical_request_digest(request), proposal
    )


def _decode_envelope(raw: str | bytes, limits: ProtectedWriteProposalLimits) -> dict[str, object]:
    request = decode_protected_write_json(raw, limits=limits.json_limits)
    if not _COMMON <= set(request) or set(request) - _COMMON - {
        "client_timestamp",
        "auth",
        "signature",
    }:
        raise ProtectedWriteRequestError("incorrect request envelope fields")
    if request["schema_version"] != "synapse-protected-write.v1" or request["payload"] != "":
        raise ProtectedWriteRequestError("unsupported request schema or payload")
    for field in (
        "sender",
        "target",
        "request_id",
        "authority_id",
        "authority_continuity",
        "session_id",
        "transaction_id",
        "enrollment_revision",
    ):
        _identifier(request[field], limits)
    if request["target"] == "all":
        raise ProtectedWriteRequestError("broadcast request target forbidden")
    _digest(request["proposal_sha256"])
    _timestamp(request["timestamp"])
    if "client_timestamp" in request:
        _timestamp(request["client_timestamp"])
    _proofs(request, limits)
    return request


def _settlement(body: dict[str, object], limits: ProtectedWriteProposalLimits) -> None:
    outcome = body["outcome"]
    if outcome not in ("no_write", "committed", "partial", "unknown"):
        raise ProtectedWriteRequestError("invalid settlement outcome")
    validate_protected_write_content_reference(
        body["writer_journal_reference"], limits=limits.operation_limits
    )
    quiescence = body["quiescence_reference"]
    if quiescence is None:
        if outcome in ("committed", "no_write"):
            raise ProtectedWriteRequestError("known settlement requires quiescence reference")
    else:
        validate_protected_write_content_reference(quiescence, limits=limits.operation_limits)
    results = body["operation_results"]
    if (
        not isinstance(results, list)
        or not 0 < len(results) <= limits.operation_limits.max_operations
    ):
        raise ProtectedWriteRequestError("invalid operation result array")
    seen: set[str] = set()
    for value in results:
        result = _fields(value, {"operation_id", "status", "evidence_reference"})
        identifier = _identifier(result["operation_id"], limits)
        if identifier in seen or result["status"] not in (
            "completed",
            "not_started",
            "failed",
            "unknown",
        ):
            raise ProtectedWriteRequestError("invalid or duplicate operation result")
        seen.add(identifier)
        if result["evidence_reference"] is not None:
            validate_protected_write_content_reference(
                result["evidence_reference"], limits=limits.operation_limits
            )


def _proofs(request: dict[str, object], limits: ProtectedWriteProposalLimits) -> None:
    if "auth" in request:
        auth = _fields(request["auth"], {"alg", "kid", "nonce", "sequence", "timestamp", "value"})
        if auth["alg"] != "hmac-sha256":
            raise ProtectedWriteRequestError("unsupported authentication algorithm")
        for field in ("kid", "nonce"):
            _identifier(auth[field], limits)
        _counter(auth["sequence"], limits)
        _timestamp(auth["timestamp"])
        _digest(auth["value"])
    if "signature" in request:
        value = request["signature"]
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value["version"] not in (1, 2)
        ):
            raise ProtectedWriteRequestError("unsupported signature profile")
        fields = {"version", "key_id", "algorithm", "nonce", "sequence", "signed_at", "value"}
        if value["version"] == 2:
            fields.add("domain")
        signature = _fields(value, fields)
        if signature["algorithm"] != "ed25519" or (
            value["version"] == 2 and signature["domain"] != str(AEF_LEGACY_EVENT_DOMAIN)
        ):
            raise ProtectedWriteRequestError("unsupported signature algorithm or domain")
        for field in ("key_id", "nonce"):
            _identifier(signature[field], limits)
        _counter(signature["sequence"], limits)
        _timestamp(signature["signed_at"])
        encoded = signature["value"]
        if not isinstance(encoded, str):
            raise ProtectedWriteRequestError("invalid encoded signature")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ProtectedWriteRequestError("invalid encoded signature") from exc
        if len(decoded) != 64 or base64.b64encode(decoded).decode("ascii") != encoded:
            raise ProtectedWriteRequestError("invalid signature encoding or length")


def _fields(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProtectedWriteRequestError("incorrect request object fields")
    return dict(value)


def _identifier(value: object, limits: ProtectedWriteProposalLimits) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limits.operation_limits.max_identifier_chars
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) is None
    ):
        raise ProtectedWriteRequestError("invalid request identifier")
    return value


def _digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ProtectedWriteRequestError("invalid request digest")


def _timestamp(value: object) -> None:
    if type(value) is not float:
        raise ProtectedWriteRequestError("request timestamp must be binary64")


def _counter(value: object, limits: ProtectedWriteProposalLimits) -> None:
    if type(value) is not int or not 0 <= value <= limits.max_counter:
        raise ProtectedWriteRequestError("invalid request counter")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
