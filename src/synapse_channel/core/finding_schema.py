# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — finding domain schema constants and default bindings
"""Finding domain constants and default schema bindings."""

from __future__ import annotations


class Subkind:
    """What a finding is *about* — its episodic category."""

    CODEBASE_FACT = "codebase-fact"
    LESSON = "lesson"
    DECISION = "decision"
    DEAD_END = "dead-end"
    OUTCOME = "outcome"


KNOWN_SUBKINDS = frozenset(
    {Subkind.CODEBASE_FACT, Subkind.LESSON, Subkind.DECISION, Subkind.DEAD_END, Subkind.OUTCOME}
)
"""The subkinds the SDK names; any other string is carried opaquely."""

SCIENTIFIC_SUBKINDS = frozenset(
    {Subkind.CODEBASE_FACT, Subkind.LESSON, Subkind.DEAD_END, Subkind.OUTCOME}
)
"""Subkinds that assert a falsifiable claim and so must carry a ``claim_status``.

A ``decision`` records a choice rather than a claim about the world, so it is the
one known subkind exempt from the claim-status requirement.
"""

EVIDENCE_REQUIRED_SUBKINDS = frozenset({Subkind.CODEBASE_FACT, Subkind.LESSON, Subkind.DEAD_END})
"""Subkinds whose ``evidence_kind`` may not be null.

A null evidence basis is legitimate only for ``decision`` and ``outcome`` (a
decision has no measured basis; an outcome may record a result without one).
"""


class EvidenceKind:
    """What kind of evidence backs the assertion."""

    MEASURED = "measured"
    CURATED = "curated"
    FORMALLY_PROVEN = "formally-proven"
    FALSIFIED = "falsified"
    NOISE_LIMITED = "noise-limited"
    HARDWARE_VALIDATED = "hardware-validated"
    PRODUCER_ASSERTED = "producer-asserted"


KNOWN_EVIDENCE_KINDS = frozenset(
    {
        EvidenceKind.MEASURED,
        EvidenceKind.CURATED,
        EvidenceKind.FORMALLY_PROVEN,
        EvidenceKind.FALSIFIED,
        EvidenceKind.NOISE_LIMITED,
        EvidenceKind.HARDWARE_VALIDATED,
        EvidenceKind.PRODUCER_ASSERTED,
    }
)
"""The evidence kinds the SDK names; any other string is carried opaquely."""


class ClaimStatus:
    """The epistemic standing of the claim."""

    REFERENCE_VALIDATED = "reference-validated"
    BOUNDED_MODEL = "bounded-model"
    BOUNDED_SUPPORT = "bounded-support"
    VALIDATION_GAP = "validation-gap"
    EXTERNAL_DEPENDENCY_BLOCKED = "external-dependency-blocked"
    ROADMAP = "roadmap"
    TOOLCHAIN_GATED = "toolchain-gated"
    REFUTED = "refuted"


KNOWN_CLAIM_STATUSES = frozenset(
    {
        ClaimStatus.REFERENCE_VALIDATED,
        ClaimStatus.BOUNDED_MODEL,
        ClaimStatus.BOUNDED_SUPPORT,
        ClaimStatus.VALIDATION_GAP,
        ClaimStatus.EXTERNAL_DEPENDENCY_BLOCKED,
        ClaimStatus.ROADMAP,
        ClaimStatus.TOOLCHAIN_GATED,
        ClaimStatus.REFUTED,
    }
)
"""The claim statuses the SDK names; any other string is carried opaquely."""

BOUNDARY_FLOOR = ClaimStatus.BOUNDED_SUPPORT
"""The status a claim is floored to when it cannot honestly stand higher."""

ABOVE_BOUNDARY = frozenset({ClaimStatus.REFERENCE_VALIDATED, ClaimStatus.BOUNDED_MODEL})
"""Statuses stronger than the boundary; testimony and unevidenced claims floor here.

The remaining statuses (``validation-gap``, ``external-dependency-blocked``,
``roadmap``, ``toolchain-gated``, ``refuted``) already sit at or below the
boundary in epistemic strength, so the producer-assertion ceiling never lifts or
lowers them.
"""


class Freshness:
    """How recently the supporting reference was re-checked at source.

    The recency-of-re-check axis, orthogonal to *how* the claim is known
    (:class:`EvidenceKind`): a measured fact can be re-checked this session or
    left unverified for months. It gates validation — only a source-verified
    claim renders validated — so a reference that exists but was never re-checked
    cannot pass for one that was.
    """

    VERIFIED_AT_SOURCE = "verified-at-source"
    TRACEABLE_UNCHECKED = "traceable-unchecked"
    UNTRACEABLE = "untraceable"


KNOWN_FRESHNESS = frozenset(
    {Freshness.VERIFIED_AT_SOURCE, Freshness.TRACEABLE_UNCHECKED, Freshness.UNTRACEABLE}
)
"""The freshness states the SDK names; any other string is carried opaquely."""


class Lifecycle:
    """Whether the atom is current, replaced, or withdrawn — orthogonal to status."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


KNOWN_LIFECYCLES = frozenset({Lifecycle.ACTIVE, Lifecycle.SUPERSEDED, Lifecycle.RETRACTED})
"""The lifecycle states the SDK names; any other string is carried opaquely."""


def default_freshness(
    *, checked_this_session: bool, evidence_ref: str | None, source_ref: str
) -> str:
    """Derive the freshness axis from the re-check recency and reference signals.

    Used only when the producer leaves ``freshness`` unset; an explicitly
    supplied value is always carried as-is so the axes stay independent.

    Parameters
    ----------
    checked_this_session : bool
        Whether the producer re-checked the reference at source this session.
    evidence_ref : str or None
        The supporting reference, if any.
    source_ref : str
        The re-check reference recorded on ``verified_at_source``.

    Returns
    -------
    str
        ``verified-at-source`` when re-checked this session, ``traceable-unchecked``
        when a reference exists but was not re-checked, else ``untraceable``.
    """
    if checked_this_session:
        return Freshness.VERIFIED_AT_SOURCE
    if evidence_ref or source_ref:
        return Freshness.TRACEABLE_UNCHECKED
    return Freshness.UNTRACEABLE
