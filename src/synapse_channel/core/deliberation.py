# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — AOT deliberation result and sealed export package
"""AOT deliberation result and its sealed, verifiable export package.

The CHANNEL AOT foundation (AOT-D1): a council — Land, Audit, or Research — ends
with a structured :class:`DeliberationResult`; that result is packaged into an
:class:`ExportPackage` carrying licence, retention, and train-eligibility tags,
and the package is **sealed** into a tamper-evident, verifiable receipt.

The seal reuses the shipping receipt machinery rather than inventing a new trust
root: the package's canonical content is committed to a single-leaf content root,
that commitment is signed with the deployment's Ed25519 receipt key through
:func:`~synapse_channel.core.receipt_signing.sign_merkle_commitment`, and the
signature is verified through
:func:`~synapse_channel.core.receipt_signing.check_receipt_merkle_signature`. An
export package therefore *is* a G7 receipt: signed, provenance-bound, and
independently checkable, with no new verifier.

Verification is deny-by-default and binds the signature to the content: a sealed
package is authentic only when the receipt-key signature over the commitment
verifies **and** the commitment root still equals the root recomputed from the
package body, so altering the body after signing fails closed.

This module is CHANNEL-native and has no cross-plane dependency. Remanentia
ingest and Director training consume an export package later; neither is required
here, and ``train_eligible`` defaults to ``False`` until a Director policy exists.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.receipt_signing import (
    MerkleSignatureCheck,
    ReceiptSigningKey,
    check_receipt_merkle_signature,
    sign_merkle_commitment,
)

#: Domain tag for the content commitment scheme, versioned so a later scheme is
#: distinguishable from this one.
AOT_COMMITMENT_SCHEME: str = "aot-deliberation-content-v0"
#: The export-package schema version stamped into every sealed document.
AOT_EXPORT_VERSION: str = "aot.export.v0"

#: The council patterns a deliberation may follow.
DELIBERATION_PATTERNS: frozenset[str] = frozenset(
    {"land_council", "audit_council", "research_council"}
)
#: Licence tags governing where an export package may flow.
LICENSE_TAGS: frozenset[str] = frozenset({"oss-ok", "customer-isolated", "internal-ops"})
#: Retention classes for an export package.
RETENTION_CLASSES: frozenset[str] = frozenset({"short", "standard", "long"})


class DeliberationError(SynapseError, ValueError):
    """Raised when a deliberation result or export package is malformed."""

    code = "deliberation"


@dataclass(frozen=True)
class GateCheck:
    """One G0–G7 gate outcome bound to its evidence.

    Attributes
    ----------
    gate : str
        The gate identifier, e.g. ``"G1_secret"`` or ``"G7_seal"``.
    status : str
        The outcome: ``pass``, ``fail``, ``degraded``, or ``n/a``.
    evidence : str
        A reference to the evidence — a digest, run id, claim id, or receipt id.
        Never renamed provenance; empty when the gate is ``n/a``.
    """

    gate: str
    status: str
    evidence: str = ""

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-ready mapping for the gate outcome."""
        return {"gate": self.gate, "status": self.status, "evidence": self.evidence}


@dataclass(frozen=True)
class DeliberationResult:
    """The structured output every council must produce on conclude.

    Only ``deliberation_id``, ``pattern``, ``project``, ``thesis``, and
    ``resolution`` are required; the sequence fields default empty. ``pattern``
    must be one of :data:`DELIBERATION_PATTERNS`.
    """

    deliberation_id: str
    pattern: str
    project: str
    thesis: str
    resolution: str
    objections: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    claims_needed: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    evidence_links: tuple[str, ...] = ()
    gate_checks: tuple[GateCheck, ...] = ()
    concluded_at: str = ""
    source_clock: str = ""

    def __post_init__(self) -> None:
        """Reject a result with an empty id, an unknown pattern, or no thesis/resolution."""
        if not self.deliberation_id.strip():
            raise DeliberationError("deliberation_id must be a non-empty identifier")
        if self.pattern not in DELIBERATION_PATTERNS:
            raise DeliberationError(
                f"unknown pattern {self.pattern!r}; expected one of {sorted(DELIBERATION_PATTERNS)}"
            )
        if not self.thesis.strip():
            raise DeliberationError("thesis must be a non-empty statement")
        if not self.resolution.strip():
            raise DeliberationError("resolution must be a non-empty statement")

    def canonical_content(self) -> dict[str, Any]:
        """Return the deliberation's stable JSON-ready content, key order fixed."""
        return {
            "deliberation_id": self.deliberation_id,
            "pattern": self.pattern,
            "project": self.project,
            "thesis": self.thesis,
            "resolution": self.resolution,
            "objections": list(self.objections),
            "actions": list(self.actions),
            "claims_needed": list(self.claims_needed),
            "open_questions": list(self.open_questions),
            "evidence_links": list(self.evidence_links),
            "gate_checks": [gate.as_dict() for gate in self.gate_checks],
            "concluded_at": self.concluded_at,
            "source_clock": self.source_clock,
        }


@dataclass(frozen=True)
class ExportPackage:
    """A deliberation result tagged for export and eventual downstream ingest.

    Attributes
    ----------
    result : DeliberationResult
        The sealed content.
    license_tag : str
        One of :data:`LICENSE_TAGS`; governs where the package may flow.
    retention_class : str
        One of :data:`RETENTION_CLASSES`.
    train_eligible : bool
        Whether a Director training corpus may include this package. Defaults to
        ``False`` and stays so until a Director policy and a passing redaction
        exist.
    source : str
        Provenance — a monorepo path and/or an upstream receipt id.
    redaction_status : str
        ``pass``, ``fail``, or ``none``; a non-``pass`` status forces
        ``train_eligible`` to ``False`` at construction.
    """

    result: DeliberationResult
    license_tag: str
    retention_class: str
    train_eligible: bool = False
    source: str = ""
    redaction_status: str = "none"

    def __post_init__(self) -> None:
        """Reject unknown tags and fail train-eligibility closed without passing redaction."""
        if self.license_tag not in LICENSE_TAGS:
            raise DeliberationError(
                f"unknown license_tag {self.license_tag!r}; expected one of {sorted(LICENSE_TAGS)}"
            )
        if self.retention_class not in RETENTION_CLASSES:
            raise DeliberationError(
                f"unknown retention_class {self.retention_class!r}; "
                f"expected one of {sorted(RETENTION_CLASSES)}"
            )
        # Fail closed: a package is never train-eligible unless redaction passed.
        if self.train_eligible and self.redaction_status != "pass":
            object.__setattr__(self, "train_eligible", False)

    def canonical_content(self) -> dict[str, Any]:
        """Return the package's stable JSON-ready content, key order fixed."""
        return {
            "aot_version": AOT_EXPORT_VERSION,
            "result": self.result.canonical_content(),
            "license_tag": self.license_tag,
            "retention_class": self.retention_class,
            "train_eligible": self.train_eligible,
            "source": self.source,
            "redaction_status": self.redaction_status,
        }


@dataclass(frozen=True)
class SealVerification:
    """The outcome of verifying a sealed export package.

    ``ok`` is ``True`` only when the receipt-key signature verifies *and* the
    committed root still matches the package body.
    """

    ok: bool
    signature: MerkleSignatureCheck
    content_bound: bool
    reason: str = ""


def _canonical_bytes(content: Mapping[str, Any]) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a content mapping."""
    return json.dumps(
        dict(content),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_commitment(package: ExportPackage) -> dict[str, object]:
    """Return the single-leaf content commitment over ``package``.

    The root is the SHA-256 of the package's canonical bytes — a one-leaf Merkle
    root — so any change to the package body changes the root.
    """
    root = hashlib.sha256(_canonical_bytes(package.canonical_content())).hexdigest()
    return {"scheme": AOT_COMMITMENT_SCHEME, "algorithm": "sha256", "root": root}


def seal_export_package(package: ExportPackage, *, key: ReceiptSigningKey) -> dict[str, object]:
    """Seal ``package`` into a verifiable receipt-shaped document.

    Parameters
    ----------
    package : ExportPackage
        The package to seal.
    key : ReceiptSigningKey
        The deployment's Ed25519 receipt-signing key.

    Returns
    -------
    dict[str, object]
        ``aot_version``, the ``package`` body, and a ``verification`` block with
        the content ``merkle`` commitment and its ``merkle_signature`` envelope —
        the same shape a release receipt uses, so
        :func:`~synapse_channel.core.receipt_signing.check_receipt_merkle_signature`
        verifies it unchanged.
    """
    commitment = content_commitment(package)
    signature = sign_merkle_commitment(commitment, key=key)
    return {
        "aot_version": AOT_EXPORT_VERSION,
        "package": package.canonical_content(),
        "verification": {"merkle": commitment, "merkle_signature": signature},
    }


def verify_sealed_package(
    sealed: Mapping[str, Any],
    *,
    trusted_keys: Mapping[str, bytes],
) -> SealVerification:
    """Verify a sealed export package: signature valid **and** content bound.

    Deny-by-default. The signature over the commitment is checked with the
    trusted receipt keys, and the committed root is recomputed from the package
    body — a body altered after signing no longer matches the committed root and
    fails closed even when the signature itself still verifies.

    Parameters
    ----------
    sealed : Mapping[str, Any]
        A document produced by :func:`seal_export_package`.
    trusted_keys : Mapping[str, bytes]
        Trusted raw Ed25519 public keys by key id.

    Returns
    -------
    SealVerification
        ``ok`` true only when both the signature verifies and the content binds.
    """
    signature = check_receipt_merkle_signature(sealed, trusted_keys=trusted_keys)

    verification = sealed.get("verification")
    committed = verification.get("merkle") if isinstance(verification, Mapping) else None
    package_body = sealed.get("package")
    content_bound = False
    reason = ""
    if not isinstance(committed, Mapping) or not isinstance(package_body, Mapping):
        reason = "sealed package is missing its commitment or body"
    else:
        recomputed = hashlib.sha256(_canonical_bytes(package_body)).hexdigest()
        if committed.get("scheme") != AOT_COMMITMENT_SCHEME:
            reason = "commitment scheme is not recognised"
        elif committed.get("root") != recomputed:
            reason = "commitment root does not match the package body (tampered or re-encoded)"
        else:
            content_bound = True

    ok = signature.status == "pass" and content_bound
    if not ok and not reason:
        reason = f"signature status {signature.status!r}"
    return SealVerification(
        ok=ok,
        signature=signature,
        content_bound=content_bound,
        reason=reason,
    )


def build_export_package(
    result: DeliberationResult,
    *,
    license_tag: str,
    retention_class: str,
    train_eligible: bool = False,
    source: str = "",
    redaction_status: str = "none",
) -> ExportPackage:
    """Construct an :class:`ExportPackage` from a concluded deliberation.

    A thin, validated constructor: the same tag validation and fail-closed
    train-eligibility rule apply as when instantiating :class:`ExportPackage`
    directly.
    """
    return ExportPackage(
        result=result,
        license_tag=license_tag,
        retention_class=retention_class,
        train_eligible=train_eligible,
        source=source,
        redaction_status=redaction_status,
    )


def _require_str(data: Mapping[str, Any], key: str) -> str:
    """Return ``data[key]`` as a string, raising when absent or the wrong type."""
    value = data.get(key)
    if not isinstance(value, str):
        raise DeliberationError(f"field {key!r} must be a string")
    return value


def _optional_str(data: Mapping[str, Any], key: str, default: str = "") -> str:
    """Return ``data[key]`` as a string, or ``default`` when absent."""
    value = data.get(key, default)
    if not isinstance(value, str):
        raise DeliberationError(f"field {key!r} must be a string")
    return value


def _str_tuple(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """Return ``data[key]`` as a tuple of strings, or empty when absent."""
    value = data.get(key, [])
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise DeliberationError(f"field {key!r} must be a list of strings")
    return tuple(value)


def _gate_checks(data: Mapping[str, Any]) -> tuple[GateCheck, ...]:
    """Return the ``gate_checks`` list parsed into :class:`GateCheck` values."""
    raw = data.get("gate_checks", [])
    if not isinstance(raw, (list, tuple)):
        raise DeliberationError("field 'gate_checks' must be a list of gate objects")
    checks: list[GateCheck] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise DeliberationError("each gate_check must be an object with gate/status")
        gate = entry.get("gate")
        status = entry.get("status")
        evidence = entry.get("evidence", "")
        if (
            not isinstance(gate, str)
            or not isinstance(status, str)
            or not isinstance(evidence, str)
        ):
            raise DeliberationError("gate_check gate/status/evidence must be strings")
        checks.append(GateCheck(gate=gate, status=status, evidence=evidence))
    return tuple(checks)


def export_package_from_mapping(data: Mapping[str, Any]) -> ExportPackage:
    """Parse a JSON-loaded mapping into a validated :class:`ExportPackage`.

    The reusable loader shared by the ``synapse deliberate`` CLI and any later
    consumer (for example a Remanentia ingest path). Every field is type-checked;
    a malformed or missing required field raises :class:`DeliberationError`, and
    the schema's own validation (unknown pattern/tag, empty thesis, train-eligible
    fail-closed) still applies through the dataclass constructors.
    """
    if not isinstance(data, Mapping):
        raise DeliberationError("deliberation spec must be a JSON object")
    train_eligible = data.get("train_eligible", False)
    if not isinstance(train_eligible, bool):
        raise DeliberationError("field 'train_eligible' must be a boolean")
    result = DeliberationResult(
        deliberation_id=_require_str(data, "deliberation_id"),
        pattern=_require_str(data, "pattern"),
        project=_require_str(data, "project"),
        thesis=_require_str(data, "thesis"),
        resolution=_require_str(data, "resolution"),
        objections=_str_tuple(data, "objections"),
        actions=_str_tuple(data, "actions"),
        claims_needed=_str_tuple(data, "claims_needed"),
        open_questions=_str_tuple(data, "open_questions"),
        evidence_links=_str_tuple(data, "evidence_links"),
        gate_checks=_gate_checks(data),
        concluded_at=_optional_str(data, "concluded_at"),
        source_clock=_optional_str(data, "source_clock"),
    )
    return build_export_package(
        result,
        license_tag=_require_str(data, "license_tag"),
        retention_class=_require_str(data, "retention_class"),
        train_eligible=train_eligible,
        source=_optional_str(data, "source"),
        redaction_status=_optional_str(data, "redaction_status", "none"),
    )


__all__ = [
    "AOT_COMMITMENT_SCHEME",
    "AOT_EXPORT_VERSION",
    "DELIBERATION_PATTERNS",
    "LICENSE_TAGS",
    "RETENTION_CLASSES",
    "DeliberationError",
    "GateCheck",
    "DeliberationResult",
    "ExportPackage",
    "SealVerification",
    "content_commitment",
    "seal_export_package",
    "verify_sealed_package",
    "build_export_package",
    "export_package_from_mapping",
]
