# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — compatibility imports for finding records and schema
"""Compatibility import surface for finding records and schema helpers.

The implementation lives in focused internal modules:
``finding_schema`` for domain constants and freshness defaults,
``finding_coercion`` for tolerant field coercion, and ``finding_records`` for
the record dataclasses. Existing imports from ``synapse_channel.core.finding``
remain supported.
"""

from __future__ import annotations

from synapse_channel.core.finding_coercion import (
    _opt_float,
    _opt_int,
    _opt_str,
    _str,
    _str_tuple,
)
from synapse_channel.core.finding_records import Finding, Provenance, SourceCheck, Validity
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
    ClaimStatus,
    EvidenceKind,
    Freshness,
    Lifecycle,
    Subkind,
    default_freshness,
)

__all__ = [
    "ABOVE_BOUNDARY",
    "BOUNDARY_FLOOR",
    "EVIDENCE_REQUIRED_SUBKINDS",
    "KNOWN_CLAIM_STATUSES",
    "KNOWN_EVIDENCE_KINDS",
    "KNOWN_FRESHNESS",
    "KNOWN_LIFECYCLES",
    "KNOWN_SUBKINDS",
    "SCIENTIFIC_SUBKINDS",
    "ClaimStatus",
    "EvidenceKind",
    "Finding",
    "Freshness",
    "Lifecycle",
    "Provenance",
    "SourceCheck",
    "Subkind",
    "Validity",
    "_opt_float",
    "_opt_int",
    "_opt_str",
    "_str",
    "_str_tuple",
    "default_freshness",
]
