# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — evidence receipts for claim release closeout
"""Evidence receipts attached to claim-release closeout messages."""

from __future__ import annotations

from typing import TypedDict

MAX_RELEASE_RECEIPT_ITEMS = 50
"""Maximum number of values retained for any repeated receipt field."""

MAX_RELEASE_RECEIPT_ITEM_CHARS = 500
"""Maximum characters retained for one receipt field value."""

DEFAULT_RELEASE_EVIDENCE_FRESHNESS_SECONDS = 3600.0
"""Default age limit for evidence to be treated as fresh in receipt metadata."""

EpistemicStatus = str
"""Advisory evidence-status label stored on a release receipt.

One of ``"unverified"`` (evidence present and fresh but not cryptographically
verified — the default for any hub-broadcast release receipt), ``"supported"``
(commitment signature present and verified — applied only by the signing path),
``"disputed"`` (a commitment signature was present but failed verification),
``"stale"`` (evidence older than the freshness window), ``"needs_freshness"``
(no freshness supplied), ``"degraded"`` (declared known failures), or
``"unsupported"`` (no positive evidence at all). Presence of evidence alone never
yields ``"supported"``; only a verified signature does.
"""


class _ReleaseReceiptRequired(TypedDict):
    task_id: str
    owner: str
    released: bool
    evidence: list[str]
    artifacts: list[str]
    known_failures: list[str]
    changed_files: list[str]
    generated_artifacts: list[str]
    approvals: list[str]
    epistemic_status: EpistemicStatus
    epistemic_reasons: list[str]


class _ReleaseReceiptOptional(TypedDict, total=False):
    confidence: str
    freshness_seconds: float


class ReleaseReceipt(_ReleaseReceiptRequired, _ReleaseReceiptOptional):
    """Machine-readable evidence attached to a successful claim release."""


def clean_receipt_items(raw: object) -> list[str]:
    """Return bounded, stripped strings from one repeated receipt field.

    Parameters
    ----------
    raw : object
        A string, a list/tuple of values, or any other value supplied by a CLI or
        wire payload.

    Returns
    -------
    list[str]
        Non-empty strings, capped by :data:`MAX_RELEASE_RECEIPT_ITEMS` and
        :data:`MAX_RELEASE_RECEIPT_ITEM_CHARS`.
    """
    if isinstance(raw, str):
        values: tuple[object, ...] | list[object] = (raw,)
    elif isinstance(raw, (list, tuple)):
        values = raw
    else:
        values = ()
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            cleaned.append(text[:MAX_RELEASE_RECEIPT_ITEM_CHARS])
        if len(cleaned) >= MAX_RELEASE_RECEIPT_ITEMS:
            break
    return cleaned


def build_release_receipt(
    *,
    task_id: str,
    owner: str,
    evidence: object = (),
    artifacts: object = (),
    known_failures: object = (),
    changed_files: object = (),
    generated_artifacts: object = (),
    approvals: object = (),
    confidence: object = "",
    freshness_seconds: object = None,
) -> ReleaseReceipt:
    """Build the canonical receipt dictionary echoed by the hub and CLI.

    Parameters
    ----------
    task_id, owner : str
        Released claim id and releasing identity.
    evidence, artifacts, known_failures, changed_files, generated_artifacts, approvals : object
        Repeated evidence fields accepted from argparse or JSON payloads.
    confidence : object, optional
        Optional caller-supplied confidence label.
    freshness_seconds : object, optional
        Optional age, in seconds, of the newest evidence.

    Returns
    -------
    ReleaseReceipt
        A JSON-serialisable release receipt with bounded repeated fields.
    """
    evidence_items = clean_receipt_items(evidence)
    artifact_items = clean_receipt_items(artifacts)
    known_failure_items = clean_receipt_items(known_failures)
    changed_file_items = clean_receipt_items(changed_files)
    generated_artifact_items = clean_receipt_items(generated_artifacts)
    approval_items = clean_receipt_items(approvals)
    epistemic_status, epistemic_reasons = _assess_release_evidence(
        evidence=evidence_items,
        artifacts=artifact_items,
        known_failures=known_failure_items,
        changed_files=changed_file_items,
        generated_artifacts=generated_artifact_items,
        approvals=approval_items,
        freshness_seconds=freshness_seconds,
    )
    receipt: ReleaseReceipt = {
        "task_id": task_id.strip(),
        "owner": owner.strip(),
        "released": True,
        "evidence": evidence_items,
        "artifacts": artifact_items,
        "known_failures": known_failure_items,
        "changed_files": changed_file_items,
        "generated_artifacts": generated_artifact_items,
        "approvals": approval_items,
        "epistemic_status": epistemic_status,
        "epistemic_reasons": epistemic_reasons,
    }
    confidence_text = str(confidence).strip()
    if confidence_text:
        receipt["confidence"] = confidence_text[:MAX_RELEASE_RECEIPT_ITEM_CHARS]
    if freshness_seconds is not None:
        try:
            receipt["freshness_seconds"] = max(float(str(freshness_seconds)), 0.0)
        except (TypeError, ValueError):
            pass
    return receipt


def _assess_release_evidence(
    *,
    evidence: list[str],
    artifacts: list[str],
    known_failures: list[str],
    changed_files: list[str],
    generated_artifacts: list[str],
    approvals: list[str],
    freshness_seconds: object,
) -> tuple[EpistemicStatus, list[str]]:
    """Return the advisory evidence-quality status for a release receipt.

    This grades the *quality* of the supplied evidence (present, fresh, stale,
    degraded by declared failures, or absent) — it makes **no** trust judgement.
    Presence of evidence is not verification: the values are caller-supplied
    strings the hub cannot check, so the strongest status this returns is
    ``"unverified"``. ``"supported"`` is reserved for a receipt whose commitment
    carries a cryptographic signature that actually verifies, and is applied only
    by the signing path in
    :func:`~synapse_channel.core.release_verification.build_verified_release_receipt`
    (checked with
    :func:`~synapse_channel.core.receipt_signing.check_receipt_merkle_signature`).
    Grading presence as ``"supported"`` would let a forged release frame launder
    fabricated evidence into a trusted verdict — see the release-receipt handler.
    """
    reasons: list[str] = []
    has_positive_evidence = (
        bool(evidence)
        or bool(artifacts)
        or bool(changed_files)
        or bool(generated_artifacts)
        or bool(approvals)
    )
    if has_positive_evidence:
        reasons.append("positive evidence present")
    else:
        reasons.append(
            "no positive evidence, artifact, changed file, generated artifact, or approval"
        )

    if known_failures:
        reasons.insert(0, "known failures declared")
        return "degraded", reasons
    if not has_positive_evidence:
        return "unsupported", reasons
    try:
        age = float(str(freshness_seconds))
    except (TypeError, ValueError):
        reasons.append("freshness_seconds missing")
        return "needs_freshness", reasons
    if age > DEFAULT_RELEASE_EVIDENCE_FRESHNESS_SECONDS:
        reasons.append("evidence age exceeds 3600 seconds")
        return "stale", reasons
    reasons.append("fresh evidence present but unverified")
    return "unverified", reasons


def release_receipt_has_evidence(receipt: ReleaseReceipt) -> bool:
    """Return whether ``receipt`` carries any caller-supplied evidence."""
    return (
        bool(receipt["evidence"])
        or bool(receipt["artifacts"])
        or bool(receipt["known_failures"])
        or bool(receipt["changed_files"])
        or bool(receipt["generated_artifacts"])
        or bool(receipt["approvals"])
        or bool(receipt.get("confidence"))
        or "freshness_seconds" in receipt
    )


def format_release_receipt_note(receipt: ReleaseReceipt) -> str:
    """Render ``receipt`` as one compact blackboard progress note."""
    sections: list[str] = []
    if receipt["evidence"]:
        sections.append(f"evidence={', '.join(receipt['evidence'])}")
    if receipt["artifacts"]:
        sections.append(f"artifacts={', '.join(receipt['artifacts'])}")
    if receipt["known_failures"]:
        sections.append(f"known_failures={', '.join(receipt['known_failures'])}")
    if receipt["changed_files"]:
        sections.append(f"changed_files={', '.join(receipt['changed_files'])}")
    if receipt["generated_artifacts"]:
        sections.append(f"generated_artifacts={', '.join(receipt['generated_artifacts'])}")
    if receipt["approvals"]:
        sections.append(f"approvals={', '.join(receipt['approvals'])}")
    if receipt.get("confidence"):
        sections.append(f"confidence={receipt['confidence']}")
    if "freshness_seconds" in receipt:
        sections.append(f"freshness_seconds={receipt['freshness_seconds']}")
    sections.append(f"epistemic_status={receipt['epistemic_status']}")
    sections.append(f"epistemic_reasons={', '.join(receipt['epistemic_reasons'])}")
    return "release receipt: " + "; ".join(sections)
