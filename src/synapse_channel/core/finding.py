# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the finding record: the emit-time memory atom and its axes
"""The ``finding`` record — one honest memory atom authored at the hub edge.

A finding is a single assertion a producer wants remembered, carried opaquely by
the hub (it never indexes or interprets it) and journalled durably so a
downstream persistent-memory adapter can ingest it. The record places the
assertion on three *independent* axes — what kind of evidence backs it
(:class:`EvidenceKind`), how strong the standing of the claim is
(:class:`ClaimStatus`), and whether the reference was checked at source
(:class:`Verification`) — plus orthogonal provenance, a bi-temporal validity
window, and a lifecycle. The axes have sensible default bindings but stay
independent: producer-asserted testimony *defaults* to ``traceable-unchecked``
yet can be ``verified-at-source`` when re-checked this session.

Parsing is forward-tolerant: an unknown enum member is carried as-is (the
read-side degrades it, never up), missing optionals become ``None``, and a
malformed field becomes its empty form rather than raising — so the wire format
can evolve without a flag day. The emit gate
(:mod:`synapse_channel.core.emit_gate`) is the separate component that turns this
parsed record into an admit / floor / reject decision; this module only models
and stamps the record.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


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


class Verification:
    """Whether the supporting reference was checked at source."""

    VERIFIED_AT_SOURCE = "verified-at-source"
    TRACEABLE_UNCHECKED = "traceable-unchecked"
    UNTRACEABLE = "untraceable"


KNOWN_VERIFICATIONS = frozenset(
    {Verification.VERIFIED_AT_SOURCE, Verification.TRACEABLE_UNCHECKED, Verification.UNTRACEABLE}
)
"""The verification states the SDK names; any other string is carried opaquely."""


class Lifecycle:
    """Whether the atom is current, replaced, or withdrawn — orthogonal to status."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


KNOWN_LIFECYCLES = frozenset({Lifecycle.ACTIVE, Lifecycle.SUPERSEDED, Lifecycle.RETRACTED})
"""The lifecycle states the SDK names; any other string is carried opaquely."""


def _str(raw: Any) -> str:
    """Return ``raw`` stripped if it is a string, else the empty string."""
    return raw.strip() if isinstance(raw, str) else ""


def _opt_str(raw: Any) -> str | None:
    """Return a non-empty stripped string, or ``None`` for absent/blank/non-string."""
    value = _str(raw)
    return value or None


def _opt_int(raw: Any) -> int | None:
    """Return ``raw`` as an int, or ``None`` for a boolean or non-numeric value."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return int(raw)


def _opt_float(raw: Any) -> float | None:
    """Return ``raw`` as a float, or ``None`` for a boolean or non-numeric value."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _str_tuple(raw: Any) -> tuple[str, ...]:
    """Return a tuple of the non-blank strings in ``raw``, or ``()`` when not a list."""
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def default_verification(
    *, checked_this_session: bool, evidence_ref: str | None, source_ref: str
) -> str:
    """Derive the verification axis from the freshness and reference signals.

    Used only when the producer leaves ``verification`` unset; an explicitly
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
        return Verification.VERIFIED_AT_SOURCE
    if evidence_ref or source_ref:
        return Verification.TRACEABLE_UNCHECKED
    return Verification.UNTRACEABLE


@dataclass(frozen=True)
class Provenance:
    """Where a finding came from — its origin coordinates.

    ``actor`` and ``ts`` are overwritten with hub-attested values at the edge (see
    :meth:`Finding.attested`); ``project``, ``session``, and ``source_event_seq``
    are producer-supplied.

    Attributes
    ----------
    project : str
        The repository or project the finding belongs to.
    actor : str
        The producing identity; hub-attested from the connection, not self-reported.
    session : str
        The producer's session identifier.
    source_event_seq : int or None
        The hub-log sequence of the message that carried the finding, when known.
    ts : float or None
        Receive-time, in seconds; hub-attested when the producer leaves it unset.
    """

    project: str
    actor: str
    session: str
    source_event_seq: int | None
    ts: float | None

    @classmethod
    def from_dict(cls, raw: Any) -> Provenance | None:
        """Parse a provenance mapping, or ``None`` when ``raw`` is not a mapping."""
        if not isinstance(raw, dict):
            return None
        return cls(
            project=_str(raw.get("project")),
            actor=_str(raw.get("actor")),
            session=_str(raw.get("session")),
            source_event_seq=_opt_int(raw.get("source_event_seq")),
            ts=_opt_float(raw.get("ts")),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of this provenance."""
        return {
            "project": self.project,
            "actor": self.actor,
            "session": self.session,
            "source_event_seq": self.source_event_seq,
            "ts": self.ts,
        }


@dataclass(frozen=True)
class Validity:
    """The bi-temporal window over which a finding holds.

    Attributes
    ----------
    valid_from : float or None
        When the fact starts holding, in seconds; hub-anchored to receive-time
        when the producer leaves it unset.
    valid_to : float or None
        When the fact stops holding, in seconds, or ``None`` for an open window.
    """

    valid_from: float | None
    valid_to: float | None

    @classmethod
    def from_dict(cls, raw: Any) -> Validity | None:
        """Parse a validity mapping, or ``None`` when ``raw`` is not a mapping."""
        if not isinstance(raw, dict):
            return None
        return cls(
            valid_from=_opt_float(raw.get("valid_from")),
            valid_to=_opt_float(raw.get("valid_to")),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of this validity window."""
        return {"valid_from": self.valid_from, "valid_to": self.valid_to}


@dataclass(frozen=True)
class SourceCheck:
    """The producer-asserted re-check, with hub-attested origin.

    ``checked_this_session`` and ``source_ref`` are producer claims; ``by`` and
    ``at`` are stamped by the hub so a re-check cannot be back-dated or
    misattributed.

    Attributes
    ----------
    checked_this_session : bool
        Whether the producer re-verified the reference at source this session.
    source_ref : str
        The reference the producer checked.
    by : str
        Hub-attested producing identity; empty until stamped at the edge.
    at : float or None
        Hub-attested receive-time, in seconds; ``None`` until stamped at the edge.
    """

    checked_this_session: bool
    source_ref: str
    by: str
    at: float | None

    @classmethod
    def from_producer(cls, raw: Any) -> SourceCheck:
        """Parse the producer-asserted half; ``by``/``at`` stay empty until attested."""
        if not isinstance(raw, dict):
            return cls(checked_this_session=False, source_ref="", by="", at=None)
        return cls(
            checked_this_session=bool(raw.get("checked_this_session", False)),
            source_ref=_str(raw.get("source_ref")),
            by="",
            at=None,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of this re-check."""
        return {
            "checked_this_session": self.checked_this_session,
            "source_ref": self.source_ref,
            "by": self.by,
            "at": self.at,
        }


@dataclass(frozen=True)
class Finding:
    """One emit-time memory atom: an assertion placed on the three honesty axes.

    Construct from the wire with :meth:`from_dict` (forward-tolerant) and stamp
    the hub-attested fields with :meth:`attested` before journalling. The emit
    gate decides whether the record is admitted, floored, or rejected.

    Attributes
    ----------
    statement : str
        The assertion being remembered.
    subkind : str
        The episodic category (:class:`Subkind`); an unknown value is carried opaque.
    evidence_kind : str or None
        What backs the claim (:class:`EvidenceKind`); ``None`` when no basis is named.
    claim_status : str or None
        The epistemic standing (:class:`ClaimStatus`); ``None`` when unstated.
    verification : str
        Whether the reference was checked at source (:class:`Verification`).
    evidence_ref : str or None
        A reference to the evidence (file:line, commit, command output).
    provenance : Provenance or None
        Origin coordinates; ``None`` only on a malformed/absent record (rejected).
    validity : Validity or None
        Bi-temporal window; ``None`` only on a malformed/absent record (rejected).
    lifecycle : str
        Whether the atom is current, superseded, or retracted (:class:`Lifecycle`).
    supersedes : str or None
        Identifier of the atom this one replaces, if any.
    verified_at_source : SourceCheck
        The producer-asserted re-check with hub-attested origin.
    producer_confidence : float or None
        Advisory producer confidence; never gates recall.
    execution_substrate : str or None
        Where the result was produced, when relevant.
    entities : tuple[str, ...]
        Named entities the finding concerns (read-side routing hooks).
    tags : tuple[str, ...]
        Free-form tags (read-side hierarchy hooks).
    """

    statement: str
    subkind: str
    evidence_kind: str | None
    claim_status: str | None
    verification: str
    evidence_ref: str | None
    provenance: Provenance | None
    validity: Validity | None
    lifecycle: str
    supersedes: str | None
    verified_at_source: SourceCheck
    producer_confidence: float | None
    execution_substrate: str | None
    entities: tuple[str, ...]
    tags: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Finding:
        """Parse a finding from a wire mapping without raising.

        Missing optionals become ``None``, malformed fields become their empty
        form, and an unknown enum member is carried as-is. The verification axis
        is derived from the freshness and reference signals only when the producer
        leaves it unset.

        Parameters
        ----------
        raw : dict[str, Any]
            The decoded finding message body.

        Returns
        -------
        Finding
            The parsed record, ready for the emit gate.
        """
        evidence_ref = _opt_str(raw.get("evidence_ref"))
        source = SourceCheck.from_producer(raw.get("verified_at_source"))
        verification = _opt_str(raw.get("verification"))
        if verification is None:
            verification = default_verification(
                checked_this_session=source.checked_this_session,
                evidence_ref=evidence_ref,
                source_ref=source.source_ref,
            )
        return cls(
            statement=_str(raw.get("statement")),
            subkind=_str(raw.get("subkind")),
            evidence_kind=_opt_str(raw.get("evidence_kind")),
            claim_status=_opt_str(raw.get("claim_status")),
            verification=verification,
            evidence_ref=evidence_ref,
            provenance=Provenance.from_dict(raw.get("provenance")),
            validity=Validity.from_dict(raw.get("validity")),
            lifecycle=_opt_str(raw.get("lifecycle")) or Lifecycle.ACTIVE,
            supersedes=_opt_str(raw.get("supersedes")),
            verified_at_source=source,
            producer_confidence=_opt_float(raw.get("producer_confidence")),
            execution_substrate=_opt_str(raw.get("execution_substrate")),
            entities=_str_tuple(raw.get("entities")),
            tags=_str_tuple(raw.get("tags")),
        )

    def attested(self, *, by: str, at: float, project_fallback: str = "") -> Finding:
        """Return a copy with the hub-attested identity and time stamped in.

        The producing identity (``by``) and receive-time (``at``) come from the
        connection, not the producer, so they cannot be forged. ``provenance.actor``
        is overwritten with ``by``; ``provenance.ts`` and ``validity.valid_from``
        are anchored to ``at`` when the producer left them unset; ``provenance.project``
        falls back to ``project_fallback`` when blank; and ``verified_at_source``
        carries the attested ``by``/``at``.

        Parameters
        ----------
        by : str
            The hub-attested producing identity.
        at : float
            The hub-attested receive-time, in seconds.
        project_fallback : str, optional
            Project to record when the producer named none.

        Returns
        -------
        Finding
            A new record with the attested fields stamped in.
        """
        provenance = self.provenance
        if provenance is not None:
            provenance = replace(
                provenance,
                actor=by,
                ts=provenance.ts if provenance.ts is not None else at,
                project=provenance.project or project_fallback,
            )
        validity = self.validity
        if validity is not None and validity.valid_from is None:
            validity = replace(validity, valid_from=at)
        verified_at_source = replace(self.verified_at_source, by=by, at=at)
        return replace(
            self,
            provenance=provenance,
            validity=validity,
            verified_at_source=verified_at_source,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the whole record."""
        return {
            "statement": self.statement,
            "subkind": self.subkind,
            "evidence_kind": self.evidence_kind,
            "claim_status": self.claim_status,
            "verification": self.verification,
            "evidence_ref": self.evidence_ref,
            "provenance": self.provenance.as_dict() if self.provenance is not None else None,
            "validity": self.validity.as_dict() if self.validity is not None else None,
            "lifecycle": self.lifecycle,
            "supersedes": self.supersedes,
            "verified_at_source": self.verified_at_source.as_dict(),
            "producer_confidence": self.producer_confidence,
            "execution_substrate": self.execution_substrate,
            "entities": list(self.entities),
            "tags": list(self.tags),
        }
