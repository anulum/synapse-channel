# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — finding split compatibility contract

from __future__ import annotations

from typing import Any

from synapse_channel.core import emit_gate, finding
from synapse_channel.core.finding import ClaimStatus, EvidenceKind, Finding, Subkind
from synapse_channel.core.finding_coercion import (
    _opt_float,
    _opt_int,
    _opt_str,
    _str,
    _str_tuple,
)
from synapse_channel.core.finding_records import Finding as RecordsFinding
from synapse_channel.core.finding_records import Provenance, SourceCheck, Validity
from synapse_channel.core.finding_schema import (
    ABOVE_BOUNDARY,
    BOUNDARY_FLOOR,
    EVIDENCE_REQUIRED_SUBKINDS,
    KNOWN_CLAIM_STATUSES,
    KNOWN_EVIDENCE_KINDS,
    KNOWN_FRESHNESS,
    KNOWN_LIFECYCLES,
    KNOWN_SUBKINDS,
    SCIENTIFIC_SUBKINDS,
    Freshness,
    Lifecycle,
    default_freshness,
)
from synapse_channel.core.finding_schema import (
    ClaimStatus as SchemaClaimStatus,
)
from synapse_channel.core.finding_schema import (
    EvidenceKind as SchemaEvidenceKind,
)
from synapse_channel.core.finding_schema import (
    Subkind as SchemaSubkind,
)


def _raw_finding(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "statement": "finding split preserves the public record contract",
        "subkind": Subkind.CODEBASE_FACT,
        "evidence_kind": EvidenceKind.MEASURED,
        "claim_status": ClaimStatus.REFERENCE_VALIDATED,
        "evidence_ref": "tests/test_finding_refactor_structure.py",
        "provenance": {"project": "SYNAPSE-CHANNEL", "session": "finding-split"},
        "validity": {"valid_from": 1.0},
        "verified_at_source": {
            "checked_this_session": True,
            "source_ref": "focused refactor regression",
        },
        "entities": ["Finding"],
        "tags": ["refactor"],
    }
    raw.update(overrides)
    return raw


def test_compatibility_module_reexports_record_models_from_owner_module() -> None:
    assert finding.Finding is RecordsFinding
    assert Finding is RecordsFinding
    assert finding.Provenance is Provenance
    assert finding.Validity is Validity
    assert finding.SourceCheck is SourceCheck


def test_compatibility_module_reexports_schema_members_from_owner_module() -> None:
    assert finding.Subkind is SchemaSubkind
    assert finding.EvidenceKind is SchemaEvidenceKind
    assert finding.ClaimStatus is SchemaClaimStatus
    assert finding.Freshness is Freshness
    assert finding.Lifecycle is Lifecycle
    assert finding.KNOWN_SUBKINDS is KNOWN_SUBKINDS
    assert finding.SCIENTIFIC_SUBKINDS is SCIENTIFIC_SUBKINDS
    assert finding.EVIDENCE_REQUIRED_SUBKINDS is EVIDENCE_REQUIRED_SUBKINDS
    assert finding.KNOWN_EVIDENCE_KINDS is KNOWN_EVIDENCE_KINDS
    assert finding.KNOWN_CLAIM_STATUSES is KNOWN_CLAIM_STATUSES
    assert finding.BOUNDARY_FLOOR is BOUNDARY_FLOOR
    assert finding.ABOVE_BOUNDARY is ABOVE_BOUNDARY
    assert finding.KNOWN_FRESHNESS is KNOWN_FRESHNESS
    assert finding.KNOWN_LIFECYCLES is KNOWN_LIFECYCLES
    assert finding.default_freshness is default_freshness


def test_compatibility_module_reexports_coercion_helpers_from_owner_module() -> None:
    assert finding._str is _str
    assert finding._opt_str is _opt_str
    assert finding._opt_int is _opt_int
    assert finding._opt_float is _opt_float
    assert finding._str_tuple is _str_tuple


def test_finding_round_trip_and_emit_gate_still_work_through_compatibility_import() -> None:
    parsed = finding.Finding.from_dict(_raw_finding())

    assert parsed.as_dict() == {
        "statement": "finding split preserves the public record contract",
        "subkind": Subkind.CODEBASE_FACT,
        "evidence_kind": EvidenceKind.MEASURED,
        "claim_status": ClaimStatus.REFERENCE_VALIDATED,
        "freshness": Freshness.VERIFIED_AT_SOURCE,
        "evidence_ref": "tests/test_finding_refactor_structure.py",
        "provenance": {
            "project": "SYNAPSE-CHANNEL",
            "actor": "",
            "session": "finding-split",
            "source_event_seq": None,
            "ts": None,
        },
        "validity": {"valid_from": 1.0, "valid_to": None},
        "lifecycle": Lifecycle.ACTIVE,
        "supersedes": None,
        "verified_at_source": {
            "checked_this_session": True,
            "source_ref": "focused refactor regression",
            "by": "",
            "at": None,
        },
        "producer_confidence": None,
        "execution_substrate": None,
        "entities": ["Finding"],
        "tags": ["refactor"],
    }
    assert emit_gate.admit(parsed).verdict == emit_gate.ACCEPT
