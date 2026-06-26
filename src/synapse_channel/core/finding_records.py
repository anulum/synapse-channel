# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — finding record models and serialisation
"""Finding record dataclasses and tolerant wire parsing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from synapse_channel.core.finding_coercion import (
    _opt_float,
    _opt_int,
    _opt_str,
    _str,
    _str_tuple,
)
from synapse_channel.core.finding_schema import Lifecycle, default_freshness


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
        The episodic category; an unknown value is carried opaque.
    evidence_kind : str or None
        What backs the claim; ``None`` when no basis is named.
    claim_status : str or None
        The epistemic standing; ``None`` when unstated.
    freshness : str
        How recently the reference was re-checked at source.
    evidence_ref : str or None
        A reference to the evidence (file:line, commit, command output).
    provenance : Provenance or None
        Origin coordinates; ``None`` only on a malformed/absent record (rejected).
    validity : Validity or None
        Bi-temporal window; ``None`` only on a malformed/absent record (rejected).
    lifecycle : str
        Whether the atom is current, superseded, or retracted.
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
    freshness: str
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
        form, and an unknown enum member is carried as-is. The freshness axis
        is derived from the re-check recency and reference signals only when the
        producer leaves it unset.

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
        freshness = _opt_str(raw.get("freshness"))
        if freshness is None:
            freshness = default_freshness(
                checked_this_session=source.checked_this_session,
                evidence_ref=evidence_ref,
                source_ref=source.source_ref,
            )
        return cls(
            statement=_str(raw.get("statement")),
            subkind=_str(raw.get("subkind")),
            evidence_kind=_opt_str(raw.get("evidence_kind")),
            claim_status=_opt_str(raw.get("claim_status")),
            freshness=freshness,
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
            "freshness": self.freshness,
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
