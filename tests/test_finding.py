# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the finding record model (parse, default, attest)

from __future__ import annotations

from typing import Any

from synapse_channel.core.finding import (
    Finding,
    Freshness,
    Lifecycle,
    Provenance,
    SourceCheck,
    Validity,
    default_freshness,
)


def _raw(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "statement": "K_nm correlates with directed coupling at r=0.951",
        "subkind": "codebase-fact",
        "evidence_kind": "measured",
        "claim_status": "reference-validated",
        "evidence_ref": "experiments/k_nm.py:88",
        "provenance": {
            "project": "SCPN-CONTROL",
            "session": "s1",
            "source_event_seq": 7,
            "ts": 5.0,
        },
        "validity": {"valid_from": 2.0, "valid_to": 9.0},
        "verified_at_source": {"checked_this_session": True, "source_ref": "r=0.951 run"},
        "producer_confidence": 0.9,
        "entities": ["K_nm"],
        "tags": ["correlation", "scpn"],
    }
    base.update(overrides)
    return base


# --- field parsing -----------------------------------------------------------


def test_from_dict_parses_every_axis() -> None:
    f = Finding.from_dict(_raw())
    assert f.statement == "K_nm correlates with directed coupling at r=0.951"
    assert f.subkind == "codebase-fact"
    assert f.evidence_kind == "measured"
    assert f.claim_status == "reference-validated"
    assert f.freshness == Freshness.VERIFIED_AT_SOURCE  # checked_this_session True
    assert f.evidence_ref == "experiments/k_nm.py:88"
    assert f.provenance == Provenance("SCPN-CONTROL", "", "s1", 7, 5.0)
    assert f.validity == Validity(2.0, 9.0)
    assert f.lifecycle == Lifecycle.ACTIVE
    assert f.verified_at_source == SourceCheck(True, "r=0.951 run", "", None)
    assert f.producer_confidence == 0.9
    assert f.entities == ("K_nm",)
    assert f.tags == ("correlation", "scpn")


def test_from_dict_is_forward_tolerant_for_missing_and_malformed() -> None:
    f = Finding.from_dict(
        {
            "statement": "  trimmed  ",
            "subkind": 123,  # malformed -> empty
            "evidence_kind": "",  # blank -> None
            "claim_status": None,  # absent -> None
            "evidence_ref": 7,  # malformed -> None
            "provenance": "nope",  # not a mapping -> None
            "validity": ["nope"],  # not a mapping -> None
            "producer_confidence": "high",  # non-numeric -> None
            "source_event_seq_unused": True,
            "entities": ["a", 2, "", "  b  "],  # keep only non-blank strings
            "tags": "notalist",  # not a list -> ()
        }
    )
    assert f.statement == "trimmed"
    assert f.subkind == ""
    assert f.evidence_kind is None
    assert f.claim_status is None
    assert f.evidence_ref is None
    assert f.provenance is None
    assert f.validity is None
    assert f.producer_confidence is None
    assert f.entities == ("a", "b")
    assert f.tags == ()


def test_from_dict_keeps_unknown_enum_members_opaque() -> None:
    f = Finding.from_dict(
        _raw(evidence_kind="vibes", claim_status="speculative", lifecycle="frozen")
    )
    assert f.evidence_kind == "vibes"
    assert f.claim_status == "speculative"
    assert f.lifecycle == "frozen"


def test_lifecycle_defaults_to_active_when_absent() -> None:
    # ``_raw`` never sets a lifecycle, so the default binding applies.
    assert Finding.from_dict(_raw()).lifecycle == Lifecycle.ACTIVE


def test_source_event_seq_rejects_boolean() -> None:
    # A stray ``true`` must not be read as the integer 1.
    f = Finding.from_dict(_raw(provenance={"project": "p", "source_event_seq": True}))
    assert f.provenance is not None
    assert f.provenance.source_event_seq is None


def test_from_dict_rejects_non_finite_numbers_without_crashing() -> None:
    # A non-finite number reaching a finding field must become None, not crash the
    # handler: int(inf) raises OverflowError and int(nan) raises ValueError, which
    # previously escaped and dropped the sender's connection. The values are built
    # directly here — the bounded frame loader now rejects the NaN/Infinity tokens at
    # the boundary, so this exercises Finding.from_dict's own defence in depth for a
    # non-finite value arriving by any path that bypasses that loader.
    data = {
        "statement": "s",
        "provenance": {"project": "p", "source_event_seq": float("inf"), "ts": float("nan")},
        "validity": {"valid_from": float("nan"), "valid_to": float("inf")},
        "producer_confidence": float("-inf"),
    }
    f = Finding.from_dict(data)  # must not raise
    assert f.provenance is not None
    assert f.provenance.source_event_seq is None
    assert f.provenance.ts is None
    assert f.validity is not None
    assert f.validity.valid_from is None
    assert f.validity.valid_to is None
    assert f.producer_confidence is None


def test_producer_confidence_rejects_a_double_overflowing_integer() -> None:
    # A JSON integer too large for a double must not raise OverflowError on the
    # float() conversion; it is not a usable confidence and becomes None.
    f = Finding.from_dict(_raw(producer_confidence=10**400))
    assert f.producer_confidence is None


# --- freshness default binding --------------------------------------------


def test_explicit_freshness_overrides_the_default() -> None:
    f = Finding.from_dict(_raw(freshness="untraceable"))
    assert f.freshness == "untraceable"


def test_default_freshness_checked_this_session() -> None:
    assert (
        default_freshness(checked_this_session=True, evidence_ref=None, source_ref="")
        == Freshness.VERIFIED_AT_SOURCE
    )


def test_default_freshness_traceable_from_evidence_ref() -> None:
    assert (
        default_freshness(checked_this_session=False, evidence_ref="f.py:1", source_ref="")
        == Freshness.TRACEABLE_UNCHECKED
    )


def test_default_freshness_traceable_from_source_ref_only() -> None:
    assert (
        default_freshness(checked_this_session=False, evidence_ref=None, source_ref="run-42")
        == Freshness.TRACEABLE_UNCHECKED
    )


def test_default_freshness_untraceable_without_any_reference() -> None:
    assert (
        default_freshness(checked_this_session=False, evidence_ref=None, source_ref="")
        == Freshness.UNTRACEABLE
    )


def test_from_dict_derives_traceable_when_only_evidence_ref_present() -> None:
    raw = _raw(verified_at_source={"checked_this_session": False, "source_ref": ""})
    f = Finding.from_dict({k: v for k, v in raw.items() if k != "freshness"})
    assert f.freshness == Freshness.TRACEABLE_UNCHECKED


# --- attestation -------------------------------------------------------------


def test_attested_stamps_origin_and_anchors_unset_timestamps() -> None:
    raw = _raw(
        provenance={"project": "", "session": "s1"},  # no project, no ts
        validity={},  # no valid_from
        verified_at_source={"checked_this_session": False, "source_ref": "ref"},
    )
    f = Finding.from_dict(raw)
    stamped = f.attested(by="SCPN-CONTROL/claude-1", at=42.0, project_fallback="SCPN-CONTROL")
    assert stamped.provenance is not None and stamped.validity is not None
    assert stamped.provenance.actor == "SCPN-CONTROL/claude-1"  # hub-attested, not self-reported
    assert stamped.provenance.ts == 42.0  # anchored: producer left it unset
    assert stamped.provenance.project == "SCPN-CONTROL"  # filled from the fallback
    assert stamped.validity.valid_from == 42.0  # anchored to receive-time
    assert stamped.verified_at_source.by == "SCPN-CONTROL/claude-1"
    assert stamped.verified_at_source.at == 42.0


def test_attested_keeps_producer_supplied_timestamps_and_project() -> None:
    f = Finding.from_dict(_raw())  # provenance.ts=5.0, project set, validity.valid_from=2.0
    stamped = f.attested(by="A", at=99.0, project_fallback="FALLBACK")
    assert stamped.provenance is not None and stamped.validity is not None
    assert stamped.provenance.ts == 5.0  # producer's value is preserved
    assert stamped.provenance.project == "SCPN-CONTROL"  # not overwritten by the fallback
    assert stamped.validity.valid_from == 2.0  # producer's value is preserved
    assert stamped.provenance.actor == "A"  # identity is always overwritten


def test_attested_tolerates_a_record_without_provenance_or_validity() -> None:
    f = Finding.from_dict({"statement": "x", "subkind": "decision"})
    stamped = f.attested(by="A", at=1.0)
    assert stamped.provenance is None
    assert stamped.validity is None
    assert stamped.verified_at_source.by == "A"  # the re-check origin is still stamped


# --- serialisation -----------------------------------------------------------


def test_as_dict_round_trips_through_from_dict() -> None:
    # Round-trip the producer-supplied record: attestation deliberately drops the
    # hub-attested ``by``/``at`` (they are re-stamped at the edge), so the
    # idempotent surface is the pre-attestation record.
    f = Finding.from_dict(_raw())
    again = Finding.from_dict(f.as_dict())
    assert again == f


def test_as_dict_serialises_absent_provenance_and_validity_as_null() -> None:
    f = Finding.from_dict({"statement": "x", "subkind": "decision"})
    snap = f.as_dict()
    assert snap["provenance"] is None
    assert snap["validity"] is None
    assert snap["verified_at_source"] == {
        "checked_this_session": False,
        "source_ref": "",
        "by": "",
        "at": None,
    }
