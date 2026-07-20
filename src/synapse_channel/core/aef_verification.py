# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format receipt verification
"""Verify AEF v0.1 receipts and their RFC 6962 inclusion proofs.

The verifier is deliberately separate from historical Synapse receipt and
event-signature paths. It accepts only the restricted AEF canonical profile,
derives receipt identity from signed content, and never upgrades a legacy
document into an AEF-valid result. Durable replay storage remains a caller
responsibility; :class:`AefReceiptIndex` is an explicit in-memory boundary for
conformance and offline batch verification.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TypeGuard

from synapse_channel.core.aef_canonical import AefCanonicalizationError, canonical_json
from synapse_channel.core.aef_domain import AEF_RECEIPT_DOMAIN, AEF_STH_DOMAIN, AefDomain
from synapse_channel.core.aef_time import AefTimestampError, validate_epoch_ms
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.merkle import InclusionProof, verify_inclusion
from synapse_channel.core.receipt_signing import receipt_key_id

_AEF_VERSION = "0.1"
_ED25519 = "ed25519"
_MAX_LIST_ITEMS = 1024
_MAX_STRING_BYTES = 4096
_PUBLIC_KEY_BYTES = 32
_SIGNATURE_BYTES = 64
_LOG_ID = re.compile(r"[0-9a-f]{64}")
_KEY_ID = re.compile(r"[0-9a-f]{16}")
_RECEIPT_ID = re.compile(r"aef1:[0-9a-f]{64}")
_ACTIONS = {
    "lease": frozenset({"grant", "renew", "expire", "takeover", "deny"}),
    "message": frozenset({"send", "deliver", "dead_letter"}),
    "tool_call": frozenset({"guard_eval", "execute"}),
    "federation": frozenset({"import", "export", "partition_mark", "heal"}),
}


@dataclass(frozen=True, slots=True)
class AefTrustedKey:
    """One Ed25519 verification key and its receipt-policy constraints.

    Parameters
    ----------
    public_key:
        Raw 32-byte Ed25519 public key.
    revoked:
        Whether the key is refused by current trust policy.
    not_before, not_after:
        Optional inclusive AEF epoch-millisecond validity bounds.
    senders:
        Optional exact allow-list for ``actor.agent_id``.
    """

    public_key: bytes
    revoked: bool = False
    not_before: int | None = None
    not_after: int | None = None
    senders: frozenset[str] | None = None

    def __post_init__(self) -> None:
        """Reject malformed key material and policy metadata."""
        if not isinstance(self.public_key, bytes) or len(self.public_key) != _PUBLIC_KEY_BYTES:
            raise ValueError("AEF trusted keys must contain 32 raw Ed25519 bytes")
        for label, value in (("not_before", self.not_before), ("not_after", self.not_after)):
            if value is not None:
                try:
                    validate_epoch_ms(value)
                except AefTimestampError as exc:
                    raise ValueError(f"AEF key {label} is not a canonical timestamp") from exc
        if self.not_before is not None and self.not_after is not None:
            if self.not_before > self.not_after:
                raise ValueError("AEF key validity window is inverted")
        if self.senders is not None and any(not _bounded_text(sender) for sender in self.senders):
            raise ValueError("AEF key sender constraints must be bounded non-empty text")


@dataclass(frozen=True, slots=True)
class AefTrustStore:
    """Trusted receipt keys and expected STH signer for each AEF log.

    ``logs`` maps each trusted ``log_id`` to the key id authorised to sign its
    tree heads. Every key id is recomputed from its public key at construction;
    an asserted alias cannot enter the verifier.
    """

    keys: Mapping[str, AefTrustedKey]
    logs: Mapping[str, str]

    def __post_init__(self) -> None:
        """Validate key fingerprints and log bindings before verification."""
        keys = dict(self.keys)
        logs = dict(self.logs)
        for key_id, key in keys.items():
            if _KEY_ID.fullmatch(key_id) is None or receipt_key_id(key.public_key) != key_id:
                raise ValueError("AEF trust-store key id does not match its public key")
        for log_id, key_id in logs.items():
            if _LOG_ID.fullmatch(log_id) is None:
                raise ValueError("AEF trust-store log id must be 64 lowercase hex characters")
            if key_id not in keys:
                raise ValueError("AEF trust-store log names an unknown STH key")
        object.__setattr__(self, "keys", MappingProxyType(keys))
        object.__setattr__(self, "logs", MappingProxyType(logs))


@dataclass(frozen=True, slots=True)
class AefVerification:
    """Closed receipt-verification result with stable identity fields."""

    verdict: AefVerdictCode
    receipt_id: str = ""
    key_id: str = ""
    reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class AefReceiptIndex:
    """In-memory replay and sequence-conflict index for one verification run.

    This class intentionally makes no durability claim. A hub or long-lived
    auditor must replace it with the separately tracked durable index before
    relying on replay decisions across restarts.
    """

    _by_sequence: dict[tuple[str, int], str] = field(default_factory=dict)
    _receipt_ids: set[tuple[str, str]] = field(default_factory=set)

    def classify(self, log_id: str, seq: int, receipt_id: str) -> AefVerdictCode | None:
        """Return replay/conflict status without changing the index."""
        prior = self._by_sequence.get((log_id, seq))
        if prior is not None and prior != receipt_id:
            return AefVerdictCode.CHAIN_CONFLICT
        if prior == receipt_id or (log_id, receipt_id) in self._receipt_ids:
            return AefVerdictCode.REPLAYED
        return None

    def remember(self, log_id: str, seq: int, receipt_id: str) -> None:
        """Record one already-validated receipt."""
        self._by_sequence[(log_id, seq)] = receipt_id
        self._receipt_ids.add((log_id, receipt_id))


class AefInclusionVerdict(str, Enum):
    """Closed outcome of receipt inclusion against a signed tree head."""

    INCLUSION_VALID = "INCLUSION_VALID"
    INCLUSION_INVALID = "INCLUSION_INVALID"
    STH_INVALID = "STH_INVALID"
    STH_UNTRUSTED = "STH_UNTRUSTED"


def receipt_id_for(receipt: Mapping[str, object]) -> str:
    """Derive the AEF content id after removing id and signature metadata."""
    content = dict(receipt)
    content.pop("receipt_id", None)
    content.pop("signature", None)
    return "aef1:" + hashlib.sha256(canonical_json(content)).hexdigest()


def verify_aef_receipt(
    receipt: Mapping[str, object],
    *,
    trust_store: AefTrustStore,
    now_ms: int,
    seen: AefReceiptIndex | None = None,
) -> AefVerification:
    """Verify one AEF v0.1 receipt under explicit trust and time inputs.

    Evaluation is fail-closed and follows the published AEF order: structure,
    version and type, domain, log trust, key policy, content identity,
    signature, freshness, then replay/conflict classification. ``now_ms`` is
    caller supplied; the verifier never reads ambient wall clock.
    """
    try:
        fields = _receipt_fields(receipt)
    except (AefCanonicalizationError, AefTimestampError, ValueError) as exc:
        return _result(AefVerdictCode.MALFORMED, reason=str(exc))
    receipt_id, log_id, seq, issued_at, receipt_type, envelope, actor_id = fields
    if receipt.get("aef") != _AEF_VERSION:
        return _result(AefVerdictCode.UNSUPPORTED_VERSION, receipt_id, reason="unsupported aef")
    if receipt_type not in _ACTIONS:
        return _result(AefVerdictCode.UNVERIFIABLE_TYPE, receipt_id, reason="unknown type")
    if receipt.get("action") not in _ACTIONS[receipt_type]:
        return _result(AefVerdictCode.MALFORMED, receipt_id, reason="unknown action")
    if envelope.get("domain") != str(AEF_RECEIPT_DOMAIN):
        return _result(AefVerdictCode.INVALID_DOMAIN, receipt_id, reason="wrong domain")
    if log_id not in trust_store.logs:
        return _result(AefVerdictCode.UNTRUSTED_LOG, receipt_id, reason="untrusted log")
    key_id = _required_text(envelope, "key_id")
    key = trust_store.keys.get(key_id)
    if key is None:
        return _result(AefVerdictCode.UNKNOWN_KEY, receipt_id, key_id, "unknown key")
    if key.revoked:
        return _result(AefVerdictCode.REVOKED_KEY, receipt_id, key_id, "revoked key")
    if (key.not_before is not None and issued_at < key.not_before) or (
        key.not_after is not None and issued_at > key.not_after
    ):
        return _result(AefVerdictCode.KEY_WINDOW_INVALID, receipt_id, key_id, "key window")
    if key.senders is not None and actor_id not in key.senders:
        return _result(AefVerdictCode.SENDER_SCOPE_MISMATCH, receipt_id, key_id, "sender scope")
    derived_id = receipt_id_for(receipt)
    if derived_id != receipt_id:
        return _result(AefVerdictCode.INVALID_RECEIPT_ID, receipt_id, key_id, "receipt id")
    if not _verify_document_signature(receipt, key.public_key, AEF_RECEIPT_DOMAIN):
        return _result(AefVerdictCode.INVALID_SIGNATURE, receipt_id, key_id, "signature")
    try:
        trusted_now = validate_epoch_ms(now_ms)
    except AefTimestampError as exc:
        return _result(AefVerdictCode.MALFORMED, receipt_id, key_id, str(exc))
    expires_at = receipt.get("expires_at")
    if expires_at is not None and validate_epoch_ms(expires_at) < trusted_now:
        return _result(AefVerdictCode.EXPIRED, receipt_id, key_id, "expired")
    if seen is not None:
        replay = seen.classify(log_id, seq, receipt_id)
        if replay is not None:
            return _result(replay, receipt_id, key_id, replay.value.lower())
        seen.remember(log_id, seq, receipt_id)
    return _result(AefVerdictCode.VALID, receipt_id, key_id, "verified")


def verify_aef_inclusion(
    receipt: Mapping[str, object],
    sth: Mapping[str, object],
    proof: Mapping[str, object],
    *,
    trust_store: AefTrustStore,
) -> AefInclusionVerdict:
    """Verify an AEF receipt leaf against a trusted, signed tree head."""
    try:
        log_id = _required_text(sth, "log_id")
        if _LOG_ID.fullmatch(log_id) is None or receipt.get("log_id") != log_id:
            return AefInclusionVerdict.STH_INVALID
        expected_key_id = trust_store.logs.get(log_id)
        if expected_key_id is None:
            return AefInclusionVerdict.STH_UNTRUSTED
        envelope = _signature_envelope(sth)
        if envelope["key_id"] != expected_key_id:
            return AefInclusionVerdict.STH_UNTRUSTED
        key = trust_store.keys[expected_key_id]
        if key.revoked or envelope["domain"] != str(AEF_STH_DOMAIN):
            return AefInclusionVerdict.STH_INVALID
        _require_exact_aef_document(sth)
        if sth.get("aef") != _AEF_VERSION:
            return AefInclusionVerdict.STH_INVALID
        tree_size = _required_positive_integer(sth, "tree_size")
        root = _required_hex(sth, "root", 64)
        validate_epoch_ms(sth.get("timestamp"))
        if not _verify_document_signature(sth, key.public_key, AEF_STH_DOMAIN):
            return AefInclusionVerdict.STH_INVALID
    except (AefCanonicalizationError, AefTimestampError, KeyError, ValueError):
        return AefInclusionVerdict.STH_INVALID
    try:
        index = _required_nonnegative_integer(proof, "leaf_index")
        if _required_positive_integer(proof, "tree_size") != tree_size:
            return AefInclusionVerdict.INCLUSION_INVALID
        leaf = _required_hex(proof, "leaf_hash", 64)
        path_value = proof.get("audit_path")
        if not isinstance(path_value, list) or any(
            not isinstance(item, str) or _LOG_ID.fullmatch(item) is None for item in path_value
        ):
            return AefInclusionVerdict.INCLUSION_INVALID
        computed_leaf = hashlib.sha256(b"\x00" + canonical_json(dict(receipt))).hexdigest()
        if leaf != computed_leaf:
            return AefInclusionVerdict.INCLUSION_INVALID
        inclusion = InclusionProof(
            seq=index + 1,
            index=index,
            tree_size=tree_size,
            leaf=leaf,
            path=tuple(path_value),
            root=root,
        )
    except (AefCanonicalizationError, ValueError):
        return AefInclusionVerdict.INCLUSION_INVALID
    return (
        AefInclusionVerdict.INCLUSION_VALID
        if verify_inclusion(inclusion)
        else AefInclusionVerdict.INCLUSION_INVALID
    )


def _receipt_fields(
    receipt: Mapping[str, object],
) -> tuple[str, str, int, int, str, Mapping[str, object], str]:
    _require_exact_aef_document(receipt)
    _required_text(receipt, "aef")
    receipt_id = _required_text(receipt, "receipt_id")
    if _RECEIPT_ID.fullmatch(receipt_id) is None:
        raise ValueError("invalid AEF receipt id")
    log_id = _required_hex(receipt, "log_id", 64)
    seq = _required_positive_integer(receipt, "seq")
    issued_at = validate_epoch_ms(receipt.get("issued_at"))
    receipt_type = _required_text(receipt, "receipt_type")
    _required_text(receipt, "action")
    _required_text(receipt, "hub_id")
    prev_receipt = _required_text(receipt, "prev_receipt")
    if _RECEIPT_ID.fullmatch(prev_receipt) is None:
        raise ValueError("invalid previous AEF receipt id")
    actor = receipt.get("actor")
    subject = receipt.get("subject")
    if not isinstance(actor, Mapping) or not isinstance(subject, Mapping):
        raise ValueError("AEF actor and subject must be objects")
    actor_id = _required_text(actor, "agent_id")
    _validate_subject(receipt_type, receipt.get("action"), subject)
    decision = receipt.get("decision")
    if decision is not None and decision not in {"allow", "deny"}:
        raise ValueError("invalid AEF decision")
    if decision == "deny" and not _bounded_text(receipt.get("reason_code")):
        raise ValueError("denied AEF receipts require a reason code")
    expires_at = receipt.get("expires_at")
    if expires_at is not None:
        validate_epoch_ms(expires_at)
    envelope = _signature_envelope(receipt)
    return receipt_id, log_id, seq, issued_at, receipt_type, envelope, actor_id


def _signature_envelope(document: Mapping[str, object]) -> Mapping[str, object]:
    envelope = document.get("signature")
    if not isinstance(envelope, Mapping):
        raise ValueError("AEF signature must be an object")
    if envelope.get("alg") != _ED25519:
        raise ValueError("unsupported AEF signature algorithm")
    _required_text(envelope, "domain")
    key_id = _required_text(envelope, "key_id")
    if _KEY_ID.fullmatch(key_id) is None:
        raise ValueError("invalid AEF key id")
    _required_text(envelope, "value")
    return envelope


def _validate_subject(receipt_type: str, action: object, subject: Mapping[str, object]) -> None:
    if receipt_type == "lease":
        _required_text(subject, "task_id")
        if action in {"grant", "renew", "takeover"}:
            _required_nonnegative_integer(subject, "epoch")
            validate_epoch_ms(subject.get("lease_expires_at"))
        if action == "deny":
            _required_text(subject, "holder")
        if action == "takeover":
            _required_text(subject, "prev_owner")
        _optional_text_list(subject, "paths")
        _optional_text(subject, "worktree")
        return
    if receipt_type == "message":
        _required_positive_integer(subject, "message_id")
        _required_positive_integer(subject, "message_seq")
        _required_text(subject, "sender")
        _required_text(subject, "target")
        _required_hex(subject, "body_sha256", 64)
        return
    if receipt_type == "tool_call":
        _required_text(subject, "tool")
        _required_text(subject, "guard")
        _required_text(subject, "call_id")
        _optional_text_list(subject, "paths")
        if action == "execute":
            _required_text(subject, "exit")
        return
    if receipt_type == "federation":
        _required_text(subject, "peer_domain")
        _required_text(subject, "namespace")
        if subject.get("direction") not in {"in", "out"}:
            raise ValueError("AEF federation direction must be in or out")


def _verify_document_signature(
    document: Mapping[str, object], public_key: bytes, expected_domain: AefDomain
) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    envelope = document.get("signature")
    if not isinstance(envelope, Mapping) or envelope.get("domain") != str(expected_domain):
        return False
    value = envelope.get("value")
    if not isinstance(value, str):
        return False
    unsigned = dict(document)
    unsigned_envelope = dict(envelope)
    unsigned_envelope.pop("value", None)
    unsigned["signature"] = unsigned_envelope
    try:
        signature = base64.b64decode(value, altchars=b"-_", validate=True)
        if len(signature) != _SIGNATURE_BYTES:
            return False
        if base64.urlsafe_b64encode(signature).decode("ascii") != value:
            return False
        payload = expected_domain.preimage(canonical_json(unsigned))
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (AefCanonicalizationError, InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def _require_exact_aef_document(document: Mapping[str, object]) -> None:
    canonical_json(dict(document))
    _require_bounds(document)


def _require_bounds(value: object) -> None:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_STRING_BYTES:
            raise ValueError("AEF string exceeds 4096 UTF-8 bytes")
        return
    if isinstance(value, list):
        if len(value) > _MAX_LIST_ITEMS:
            raise ValueError("AEF list exceeds 1024 items")
        for item in value:
            _require_bounds(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if item is None:
                raise ValueError("AEF object members must omit optional null values")
            _require_bounds(key)
            _require_bounds(item)


def _bounded_text(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or not value:
        return False
    try:
        return len(value.encode("utf-8")) <= _MAX_STRING_BYTES
    except UnicodeEncodeError:
        return False


def _required_text(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not _bounded_text(value):
        raise ValueError(f"AEF {key} must be bounded non-empty text")
    return value


def _required_positive_integer(document: Mapping[str, object], key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"AEF {key} must be a positive integer")
    return value


def _required_nonnegative_integer(document: Mapping[str, object], key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"AEF {key} must be a non-negative integer")
    return value


def _required_hex(document: Mapping[str, object], key: str, characters: int) -> str:
    value = _required_text(document, key)
    if len(value) != characters or re.fullmatch(r"[0-9a-f]+", value) is None:
        raise ValueError(f"AEF {key} must be {characters} lowercase hex characters")
    return value


def _optional_text(document: Mapping[str, object], key: str) -> None:
    if key in document:
        _required_text(document, key)


def _optional_text_list(document: Mapping[str, object], key: str) -> None:
    value = document.get(key)
    if value is None:
        return
    if not isinstance(value, list) or any(not _bounded_text(item) for item in value):
        raise ValueError(f"AEF {key} must be a list of bounded non-empty text")


def _result(
    verdict: AefVerdictCode,
    receipt_id: str = "",
    key_id: str = "",
    reason: str = "",
) -> AefVerification:
    reasons = (reason,) if reason else ()
    return AefVerification(verdict, receipt_id, key_id, reasons)
