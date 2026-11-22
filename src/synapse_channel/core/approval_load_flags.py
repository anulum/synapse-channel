# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-side audit flags for fast approval decisions and oversized batches
"""Flag fast decisions and oversized batches from the journal alone, for an auditor's attention.

The flags select items for audit. They never refuse, never change a verdict
and never change a level. They are computed from journalled rows only:

- a ``state=requested`` approval note;
- the ``approval_load_admitted`` row the hub appends right after it, holding
  the thresholds the request was counted under;
- a later ``approved`` or ``rejected`` note on the same subject.

So a request admitted with no policy in force carries no thresholds and is
never flagged, and a policy change applies only to requests admitted after
it. Two flags exist:

``approval_fast_decision``
    A decision made less than the request's ``min_review_seconds`` after its
    latest request.
``approval_batch_oversized``
    Every request of a batch whose admitted requests, counted over the whole
    journal, exceed that request's ``max_batch``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    STATE_REQUESTED,
    approval_text_sha256,
    parse_approval_note,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent

FLAG_FAST_DECISION = "approval_fast_decision"
"""Flag kind of a decision made sooner than the request's minimum review time."""

FLAG_BATCH_OVERSIZED = "approval_batch_oversized"
"""Flag kind of a request in a batch larger than its maximum batch size."""


@dataclass(frozen=True)
class ApprovalLoadFlag:
    """One audit flag, with the journal rows that show it.

    Attributes
    ----------
    kind : str
        :data:`FLAG_FAST_DECISION` or :data:`FLAG_BATCH_OVERSIZED`.
    subject : str
        The approval subject flagged.
    seq : int
        The decision's sequence for a fast decision, the request's for a batch.
    ts : float
        That row's timestamp.
    detail : str
        What was measured against which threshold, without any note body.
    """

    kind: str
    subject: str
    seq: int
    ts: float
    detail: str


@dataclass
class _Request:
    """The latest request on one subject and the thresholds it was admitted under."""

    seq: int
    ts: float
    text_sha256: str
    min_review_seconds: float | None = None


@dataclass(frozen=True)
class _BatchMember:
    """One admitted request of a batch, judged by its own threshold."""

    subject: str
    seq: int
    ts: float
    max_batch: int


class ApprovalLoadFlags:
    """Fold journalled approval rows, in sequence order, into audit flags."""

    def __init__(self) -> None:
        """Start with no requests, decisions or batches seen."""
        self._requests: dict[str, _Request] = {}
        self._fast: dict[str, ApprovalLoadFlag] = {}
        self._batches: dict[str, list[_BatchMember]] = {}

    def observe_all(self, events: Iterable[StoredEvent]) -> None:
        """Observe every event of an ordered stream."""
        for event in events:
            self.observe(event)

    def observe(self, event: StoredEvent) -> None:
        """Observe one event; kinds other than approval notes and admissions are ignored."""
        if event.kind == EventKind.APPROVAL_LOAD_ADMITTED:
            self._admitted(event)
        elif event.kind == EventKind.LEDGER_PROGRESS:
            if event.payload.get("kind") == APPROVAL_NOTE_KIND:
                self._note(event)

    def flags(self) -> tuple[ApprovalLoadFlag, ...]:
        """Return every flag, fast decisions first, each family in subject order."""
        oversized: dict[str, ApprovalLoadFlag] = {}
        for batch, members in sorted(self._batches.items()):
            for member in members:
                if len(members) > member.max_batch:
                    oversized[member.subject] = ApprovalLoadFlag(
                        kind=FLAG_BATCH_OVERSIZED,
                        subject=member.subject,
                        seq=member.seq,
                        ts=member.ts,
                        detail=(
                            f"batch {batch} holds {len(members)} requests; "
                            f"max_batch is {member.max_batch}"
                        ),
                    )
        return (
            *(self._fast[key] for key in sorted(self._fast)),
            *(oversized[key] for key in sorted(oversized)),
        )

    def _note(self, event: StoredEvent) -> None:
        """Record a request, or judge a decision against its latest admitted request."""
        text = str(event.payload.get("text", ""))
        fields = parse_approval_note(text)
        if fields is None:
            return
        subject = fields["subject"]
        if fields["state"] == STATE_REQUESTED:
            self._requests[subject] = _Request(
                seq=event.seq,
                ts=event.ts,
                text_sha256=approval_text_sha256(text),
            )
            return
        request = self._requests.get(subject)
        if request is None or request.min_review_seconds is None:
            return
        elapsed = event.ts - request.ts
        if elapsed < request.min_review_seconds:
            self._fast[subject] = ApprovalLoadFlag(
                kind=FLAG_FAST_DECISION,
                subject=subject,
                seq=event.seq,
                ts=event.ts,
                detail=(
                    f"decided {elapsed:.3f} s after the request at seq {request.seq}; "
                    f"min_review_seconds is {request.min_review_seconds:g}"
                ),
            )

    def _admitted(self, event: StoredEvent) -> None:
        """Attach an admission row's thresholds to the request it follows."""
        payload = event.payload
        subject = str(payload.get("subject", ""))
        request = self._requests.get(subject)
        if (
            request is None
            or request.min_review_seconds is not None
            or request.text_sha256 != payload.get("text_sha256")
        ):
            return
        min_review = payload.get("min_review_seconds")
        max_batch = payload.get("max_batch")
        if not isinstance(min_review, int | float) or isinstance(min_review, bool):
            return
        if not isinstance(max_batch, int) or isinstance(max_batch, bool):
            return
        request.min_review_seconds = float(min_review)
        batch = payload.get("batch")
        if isinstance(batch, str) and batch:
            self._batches.setdefault(batch, []).append(
                _BatchMember(subject=subject, seq=request.seq, ts=request.ts, max_batch=max_batch)
            )
