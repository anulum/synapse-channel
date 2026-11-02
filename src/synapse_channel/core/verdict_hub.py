# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-side admission of verdict records and the approval gate
"""Admit evidence, verdict and correction notes, and gate approvals on fresh verdicts.

The hub calls these helpers before it appends a progress note. They read the
already-recorded notes from the durable journal, never from the bounded
in-memory board, so authority and staleness survive restarts and pruning.

* :func:`admission_refusal` returns why a new record must be refused (malformed
  body, an evidence declaration or verdict in a namespace the author may not
  write, or a correction by a seat that neither declared the object in that
  namespace nor is allow-listed there), or ``None``.
* :func:`newly_stale` lists the verdicts a newly accepted correction turns
  stale, so the hub can journal one ``verdict_stale`` event per pair and notify
  the verdict's author and reviewer.
* :func:`approval_refusal` refuses approving a subject that names a SHA-256
  object of the approver's project namespace whose verdicts do not pass
  :func:`~synapse_channel.core.verdict_staleness.gate_decision`.

Authority: a seat may declare objects and record verdicts in its own project
namespace (the part of its name before ``/``) and in any namespace whose
allow-list names it; only the object's declarers there and allow-listed seats
may correct it. Objects are identified by (namespace, SHA-256), so records in
one namespace never affect another.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.verdict_records import (
    VERDICT_RECORD_KINDS,
    VerdictRecordError,
    validate_record_body,
)
from synapse_channel.core.verdict_staleness import (
    STATE_STALE,
    CorrectionPolicy,
    GateDecision,
    RecordedNote,
    VerdictState,
    fold_verdicts,
    gate_decision,
)

_SUBJECT_DIGEST = re.compile(r"(?:sha256:)?([0-9a-f]{64})\Z")


@dataclass(frozen=True)
class VerdictHubPolicy:
    """Hub configuration for verdict records and the approval gate.

    ``verdict_authority`` maps a namespace to the seats allowed to declare
    objects, record verdicts and correct objects there beyond its own project
    seats. ``gate_enabled`` is
    the recorded per-hub switch; ``allow_unrecorded_inputs`` lets an accepted
    verdict without inputs pass the gate.
    """

    verdict_authority: Mapping[str, frozenset[str]] = field(default_factory=dict)
    gate_enabled: bool = True
    allow_unrecorded_inputs: bool = False

    def correction_policy(self) -> CorrectionPolicy:
        """Return the declaration and correction rules this hub enforces."""

        def allowed(namespace: str, seat: str) -> bool:
            return seat in self.verdict_authority.get(namespace, frozenset())

        def may_write(namespace: str, seat: str) -> bool:
            return seat.split("/", 1)[0] == namespace or allowed(namespace, seat)

        return CorrectionPolicy(may_write=may_write, may_correct=allowed)


def parse_verdict_authority(specs: Iterable[str]) -> dict[str, frozenset[str]]:
    """Parse repeated ``NAMESPACE=SEAT`` options into a per-namespace verdict allow-list.

    Raises
    ------
    ValueError
        When an entry lacks ``=`` or names an empty namespace or seat.
    """
    allowed: dict[str, set[str]] = {}
    for spec in specs:
        namespace, separator, seat = spec.partition("=")
        if not separator or not namespace.strip() or not seat.strip():
            raise ValueError(f"verdict authority must be NAMESPACE=SEAT, got {spec!r}")
        allowed.setdefault(namespace.strip(), set()).add(seat.strip())
    return {namespace: frozenset(seats) for namespace, seats in allowed.items()}


def recorded_notes(store: EventStore) -> list[RecordedNote]:
    """Return every evidence, verdict and correction note in the journal, in order."""
    notes: list[RecordedNote] = []
    for event in store.iter_events(kinds=(EventKind.LEDGER_PROGRESS,)):
        kind = str(event.payload.get("kind") or "")
        if kind not in VERDICT_RECORD_KINDS:
            continue
        notes.append(
            RecordedNote(
                seq=event.seq,
                author=str(event.payload.get("author") or ""),
                kind=kind,
                text=str(event.payload.get("text") or ""),
            )
        )
    return notes


def _candidate(notes: list[RecordedNote], author: str, kind: str, text: str) -> RecordedNote:
    following = max((note.seq for note in notes), default=0) + 1
    return RecordedNote(seq=following, author=author, kind=kind, text=text)


def admission_refusal(
    notes: list[RecordedNote], policy: VerdictHubPolicy, *, author: str, kind: str, text: str
) -> str | None:
    """Return why a new verdict-family note must be refused, or ``None`` to admit it."""
    try:
        validate_record_body(kind, text)
    except VerdictRecordError as exc:
        return f"malformed {kind} record: {exc}"
    candidate = _candidate(notes, author, kind, text)
    fold = fold_verdicts([*notes, candidate], policy.correction_policy())
    for seq, reason in fold.refused:
        if seq == candidate.seq:
            return reason
    return None


def newly_stale(
    notes: list[RecordedNote], policy: VerdictHubPolicy, *, author: str, kind: str, text: str
) -> tuple[VerdictState, ...]:
    """Return the verdicts that the admitted note turns stale, oldest first."""
    rules = policy.correction_policy()
    before = {state.seq: state.state for state in fold_verdicts(notes, rules).verdicts}
    after = fold_verdicts([*notes, _candidate(notes, author, kind, text)], rules)
    return tuple(
        state
        for state in after.verdicts
        if state.state == STATE_STALE and before.get(state.seq) != STATE_STALE
    )


def subject_digest(subject: str) -> str | None:
    """Return the SHA-256 an approval subject names, or ``None`` for other subjects."""
    match = _SUBJECT_DIGEST.match(subject.strip())
    return match.group(1) if match else None


def approver_namespace(approver: str) -> str:
    """Return the project namespace whose objects ``approver`` approves."""
    return approver.split("/", 1)[0]


def approval_refusal(
    notes: Iterable[RecordedNote], policy: VerdictHubPolicy, subject: str, *, approver: str
) -> GateDecision | None:
    """Return the failed gate for ``approver`` approving ``subject``, or ``None``.

    The subject is an object of the approver's project namespace. Subjects that
    do not name a SHA-256 object, and hubs whose gate is switched off, are never
    refused here.
    """
    digest = subject_digest(subject)
    if digest is None or not policy.gate_enabled:
        return None
    decision = gate_decision(
        fold_verdicts(notes, policy.correction_policy()),
        approver_namespace(approver),
        digest,
        allow_unrecorded_inputs=policy.allow_unrecorded_inputs,
    )
    return None if decision.passed else decision
