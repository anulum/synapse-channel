# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the emit gate (admit / floor / reject invariants)

from __future__ import annotations

from typing import Any

from synapse_channel.core.emit_gate import ACCEPT, FLOOR, REJECT, Decision, admit
from synapse_channel.core.finding import ClaimStatus, Finding


def _finding(**overrides: Any) -> Finding:
    base: dict[str, Any] = {
        "statement": "studio bundle validates byte-parity across federation",
        "subkind": "codebase-fact",
        "evidence_kind": "measured",
        "claim_status": "reference-validated",
        "evidence_ref": "tests/test_federation.py:42",
        "provenance": {"project": "SCPN-STUDIO", "session": "s9"},
        "validity": {"valid_from": 1.0},
        # Re-checked this session, so freshness derives to verified-at-source — the
        # bar a reference-validated claim must clear (INV-1).
        "verified_at_source": {"checked_this_session": True, "source_ref": "federation run"},
    }
    base.update(overrides)
    return Finding.from_dict(base)


# --- accept ------------------------------------------------------------------


def test_accepts_a_reference_validated_claim_with_evidence() -> None:
    decision = admit(_finding())
    assert decision.verdict == ACCEPT
    assert decision.finding is not None
    assert decision.finding.claim_status == ClaimStatus.REFERENCE_VALIDATED
    assert decision.reasons == ()


def test_accepts_a_decision_without_claim_status_or_evidence_kind() -> None:
    # A decision is the one known subkind exempt from the claim-status and
    # evidence-kind requirements.
    decision = admit(_finding(subkind="decision", claim_status=None, evidence_kind=None))
    assert decision.verdict == ACCEPT


def test_accepts_an_outcome_without_evidence_kind() -> None:
    # ``outcome`` still needs a claim_status (it is scientific) but may omit the
    # evidence basis.
    decision = admit(_finding(subkind="outcome", evidence_kind=None))
    assert decision.verdict == ACCEPT


# --- structural rejects (INV-5 + empties) ------------------------------------


def test_rejects_an_empty_statement() -> None:
    decision = admit(_finding(statement=""))
    assert decision.verdict == REJECT
    assert decision.finding is None
    assert any("statement" in r for r in decision.reasons)


def test_rejects_an_empty_subkind() -> None:
    decision = admit(_finding(subkind=""))
    assert decision.verdict == REJECT
    assert any("subkind" in r for r in decision.reasons)


def test_rejects_a_missing_provenance() -> None:
    decision = admit(_finding(provenance="not-a-mapping"))
    assert decision.verdict == REJECT
    assert any("provenance" in r for r in decision.reasons)


def test_rejects_a_missing_validity() -> None:
    decision = admit(_finding(validity=None))
    assert decision.verdict == REJECT
    assert any("validity" in r for r in decision.reasons)


def test_rejects_a_scientific_subkind_without_claim_status() -> None:
    decision = admit(_finding(subkind="lesson", claim_status=None))
    assert decision.verdict == REJECT
    assert any("claim_status" in r for r in decision.reasons)


def test_rejects_a_factual_subkind_without_evidence_kind() -> None:
    decision = admit(_finding(subkind="dead-end", evidence_kind=None))
    assert decision.verdict == REJECT
    assert any("evidence_kind" in r for r in decision.reasons)


def test_rejection_collects_every_violation() -> None:
    decision = admit(_finding(statement="", provenance=None, validity=None))
    assert decision.verdict == REJECT
    assert len(decision.reasons) == 3


# --- INV-2: falsified renders refuted ----------------------------------------


def test_falsified_evidence_floors_a_standing_claim_to_refuted() -> None:
    decision = admit(_finding(evidence_kind="falsified", claim_status="bounded-model"))
    assert decision.verdict == FLOOR
    assert decision.finding is not None
    assert decision.finding.claim_status == ClaimStatus.REFUTED
    assert any("INV-2" in r for r in decision.reasons)


def test_falsified_evidence_already_refuted_is_accepted_unchanged() -> None:
    decision = admit(_finding(evidence_kind="falsified", claim_status="refuted"))
    assert decision.verdict == ACCEPT
    assert decision.finding is not None
    assert decision.finding.claim_status == ClaimStatus.REFUTED


# --- INV-6: producer-asserted ceiling ----------------------------------------


def test_producer_asserted_cannot_be_born_reference_validated() -> None:
    decision = admit(_finding(evidence_kind="producer-asserted"))
    assert decision.verdict == FLOOR
    assert decision.finding is not None
    assert decision.finding.claim_status == ClaimStatus.BOUNDED_SUPPORT
    assert any("INV-6" in r for r in decision.reasons)


def test_producer_asserted_bounded_model_is_capped_at_the_boundary() -> None:
    decision = admit(_finding(evidence_kind="producer-asserted", claim_status="bounded-model"))
    assert decision.verdict == FLOOR
    assert decision.finding is not None
    assert decision.finding.claim_status == ClaimStatus.BOUNDED_SUPPORT


def test_producer_asserted_below_the_boundary_is_left_alone() -> None:
    # ``validation-gap`` already sits at or below the boundary, so the ceiling
    # neither lifts nor lowers it; an honest producer-asserted record (not claiming
    # verified-at-source) passes untouched.
    decision = admit(
        _finding(
            evidence_kind="producer-asserted",
            claim_status="validation-gap",
            evidence_ref=None,
            verified_at_source={"checked_this_session": False, "source_ref": ""},
        )
    )
    assert decision.verdict == ACCEPT
    assert decision.finding is not None
    assert decision.finding.claim_status == ClaimStatus.VALIDATION_GAP


# --- INV-1: reference-validated needs a reference AND source-verified freshness --


def test_reference_validated_without_evidence_ref_is_floored() -> None:
    decision = admit(_finding(evidence_ref=None))
    assert decision.verdict == FLOOR
    assert decision.finding is not None
    assert decision.finding.claim_status == ClaimStatus.BOUNDED_SUPPORT
    assert any("INV-1" in r for r in decision.reasons)


def test_reference_validated_without_source_verified_freshness_is_floored() -> None:
    # The trap: a reference exists but was not re-checked this session, so freshness
    # derives to traceable-unchecked — reference-validated does not survive it.
    decision = admit(
        _finding(verified_at_source={"checked_this_session": False, "source_ref": "old run"})
    )
    assert decision.finding is not None and decision.finding.freshness == "traceable-unchecked"
    assert decision.verdict == FLOOR
    assert decision.finding.claim_status == ClaimStatus.BOUNDED_SUPPORT
    assert any("INV-1" in r for r in decision.reasons)


# --- LOCK-4: falsified cannot claim reference-validated ----------------------


def test_falsified_reference_validated_is_rejected() -> None:
    # A direct contradiction — refused outright, not floored to refuted like other
    # falsified claims.
    decision = admit(_finding(evidence_kind="falsified"))
    assert decision.verdict == REJECT
    assert decision.finding is None
    assert any("LOCK-4" in r for r in decision.reasons)


# --- producer-asserted freshness floor ---------------------------------------


def test_producer_asserted_cannot_be_verified_at_source() -> None:
    # Testimony was not independently re-checked, so a declared verified-at-source
    # is lowered; the claim status here is below the boundary to isolate the floor.
    decision = admit(_finding(evidence_kind="producer-asserted", claim_status="bounded-support"))
    assert decision.verdict == FLOOR
    assert decision.finding is not None
    assert decision.finding.freshness == "traceable-unchecked"
    assert decision.finding.claim_status == ClaimStatus.BOUNDED_SUPPORT


def test_producer_asserted_unevidenced_is_capped_by_inv6_not_inv1() -> None:
    # An honest producer-asserted record (freshness already traceable) is capped to
    # bounded-support by INV-6 before INV-1 can fire, so only INV-6 reports.
    decision = admit(
        _finding(
            evidence_kind="producer-asserted",
            evidence_ref=None,
            verified_at_source={"checked_this_session": False, "source_ref": "x"},
        )
    )
    assert decision.verdict == FLOOR
    assert decision.finding is not None
    assert decision.finding.claim_status == ClaimStatus.BOUNDED_SUPPORT
    assert len(decision.reasons) == 1
    assert "INV-6" in decision.reasons[0]


# --- INV-3: unknown enum members are carried opaque --------------------------


def test_unknown_claim_status_is_carried_not_floored() -> None:
    # An unknown status matches no known-member check, so it is neither floored
    # up nor rejected — the read-side degrades it.
    decision = admit(
        _finding(subkind="decision", claim_status="speculative", evidence_kind="vibes")
    )
    assert decision.verdict == ACCEPT
    assert decision.finding is not None
    assert decision.finding.claim_status == "speculative"
    assert decision.finding.evidence_kind == "vibes"


def test_unknown_subkind_is_treated_as_non_scientific() -> None:
    # An unknown subkind is carried; it requires neither claim_status nor evidence.
    decision = admit(_finding(subkind="anecdote", claim_status=None, evidence_kind=None))
    assert decision.verdict == ACCEPT


# --- Decision shape ----------------------------------------------------------


def test_decision_is_immutable() -> None:
    decision = admit(_finding())
    assert isinstance(decision, Decision)
    try:
        decision.verdict = "x"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Decision must be frozen")
