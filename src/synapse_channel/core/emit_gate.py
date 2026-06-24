# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — emit-time admission gate that keeps every memory atom honest
"""The emit gate — admit, floor, or reject a finding at the hub edge.

A finding never enters the durable memory spine without passing this gate, so a
dishonest atom cannot become a trusted input downstream. The gate is a pure
function over a parsed :class:`~synapse_channel.core.finding.Finding`: it makes no
network or disk call and the hub stays memory-agnostic, calling it only at the
write edge.

Three outcomes:

* **reject** — a record missing the structure honesty needs (no statement, no
  provenance, no validity, no claim status where one is required, no evidence
  basis where one is required). The atom is refused; nothing is journalled.
* **floor** — a record whose stated standing is stronger than its evidence
  supports. The claim status is lowered to the boundary and the atom is admitted
  with the reasons recorded, so the producer learns what was downgraded.
* **accept** — a record whose claims its evidence already supports.

The floors encode the write-side invariants: falsified evidence renders a claim
refuted (INV-2), producer-asserted testimony cannot be born reference-validated
(INV-6), and a reference-validated claim needs a reference to stand on (INV-1).
An unknown enum member is carried opaquely and never matches a known-member
check, so it is neither floored up nor rejected — the read-side degrades it
(INV-3). Honesty-propagation across findings (INV-4) is read-side and not gated
here; this gate only guarantees each atom is a truthful input for it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from synapse_channel.core.finding import (
    ABOVE_BOUNDARY,
    BOUNDARY_FLOOR,
    EVIDENCE_REQUIRED_SUBKINDS,
    SCIENTIFIC_SUBKINDS,
    ClaimStatus,
    EvidenceKind,
    Finding,
)

ACCEPT = "accept"
"""Verdict for a record admitted unchanged."""

FLOOR = "floor"
"""Verdict for a record admitted with its claim status lowered to the boundary."""

REJECT = "reject"
"""Verdict for a record refused for missing the structure honesty requires."""


@dataclass(frozen=True)
class Decision:
    """The outcome of running a finding through the emit gate.

    Attributes
    ----------
    verdict : str
        One of :data:`ACCEPT`, :data:`FLOOR`, or :data:`REJECT`.
    finding : Finding or None
        The admitted record — the original on accept, the lowered record on
        floor, ``None`` on reject.
    reasons : tuple[str, ...]
        Why the record was floored or rejected; empty on a clean accept.
    """

    verdict: str
    finding: Finding | None
    reasons: tuple[str, ...]


def _structural_violations(finding: Finding) -> list[str]:
    """Return the structural reasons a finding must be rejected, in order.

    Enforces INV-5 (a record needs provenance, validity, and — for a scientific
    subkind — a claim status) and the §2 rule that a factual subkind must name
    its evidence basis, plus the basic requirement of a non-empty statement and
    subkind. An unknown subkind is treated as non-scientific, so it is carried
    rather than rejected for a missing claim status.
    """
    reasons: list[str] = []
    if not finding.statement:
        reasons.append("empty statement")
    if not finding.subkind:
        reasons.append("empty subkind")
    if finding.provenance is None:
        reasons.append("missing provenance (INV-5)")
    if finding.validity is None:
        reasons.append("missing validity (INV-5)")
    if finding.subkind in SCIENTIFIC_SUBKINDS and not finding.claim_status:
        reasons.append(f"missing claim_status for scientific subkind '{finding.subkind}' (INV-5)")
    if finding.subkind in EVIDENCE_REQUIRED_SUBKINDS and finding.evidence_kind is None:
        reasons.append(f"missing evidence_kind for subkind '{finding.subkind}'")
    return reasons


def admit(finding: Finding) -> Decision:
    """Decide whether a finding is admitted, floored, or rejected.

    Structural requirements are checked first; a record that fails any is
    rejected outright. Otherwise the claim status is lowered where the evidence
    cannot support it, in an order that preserves honesty: falsified evidence
    renders the claim refuted (INV-2) before the producer-assertion ceiling
    (INV-6) and the reference-validation floor (INV-1) are applied, so a
    contradiction is resolved to its most honest standing exactly once.

    Parameters
    ----------
    finding : Finding
        The parsed record to admit.

    Returns
    -------
    Decision
        The verdict, the admitted record (or ``None`` on reject), and the reasons.
    """
    rejections = _structural_violations(finding)
    if rejections:
        return Decision(REJECT, None, tuple(rejections))

    status = finding.claim_status
    reasons: list[str] = []

    # INV-2: falsified evidence cannot back a standing claim — it renders it refuted.
    if finding.evidence_kind == EvidenceKind.FALSIFIED and status != ClaimStatus.REFUTED:
        reasons.append(f"INV-2: falsified evidence renders the claim refuted, not '{status}'")
        status = ClaimStatus.REFUTED

    # INV-6: producer-asserted testimony cannot be born above the boundary; an
    # external measured/curated/proven re-check is what lifts it later.
    if finding.evidence_kind == EvidenceKind.PRODUCER_ASSERTED and status in ABOVE_BOUNDARY:
        reasons.append(
            f"INV-6: producer-asserted testimony cannot be born '{status}'; "
            f"capped at {BOUNDARY_FLOOR}"
        )
        status = BOUNDARY_FLOOR

    # INV-1: a reference-validated claim needs a reference to stand on.
    if status == ClaimStatus.REFERENCE_VALIDATED and not finding.evidence_ref:
        reasons.append(
            f"INV-1: reference-validated without an evidence_ref is floored to {BOUNDARY_FLOOR}"
        )
        status = BOUNDARY_FLOOR

    if reasons:
        return Decision(FLOOR, replace(finding, claim_status=status), tuple(reasons))
    return Decision(ACCEPT, finding, ())
