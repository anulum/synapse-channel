# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — attention evidence from durable coordination and private quota facts
"""Project actionable attention evidence without granting approval or spending authority."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    STATE_REQUESTED,
    ApprovalReport,
    parse_approval_note,
)
from synapse_channel.core.entitlement_view import entitlement_view
from synapse_channel.core.entitlements import parse_time
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.review_feedback import (
    AuthorBinding,
    ReviewFinding,
    independent_decision,
    review_subject,
)


@dataclass(frozen=True)
class AttentionEvidence:
    """One source-backed alert candidate or source-backed resolution.

    Attributes
    ----------
    key : str
        Stable reason and subject identifier used for deduplication.
    kind : str
        Approval, delivery, recovery, stale quota or reset category.
    subject : str
        Opaque task, message or window identifier; never content or account label.
    severity : str
        ``critical``, ``warning`` or ``info``.
    state : str
        ``open``, ``expired`` or ``resolved``. Expiry is not approval.
    action : str
        Next operator action, expressed without private content.
    source_revision : str
        Durable event sequence or private-ledger revision.
    observed_at : float
        Source timestamp in Unix seconds.
    expires_at : float | None
        Alert review deadline, if one applies.
    """

    key: str
    kind: str
    subject: str
    severity: str
    state: str
    action: str
    source_revision: str
    observed_at: float
    expires_at: float | None


ATTENTION_EVENT_KINDS = frozenset(
    {
        EventKind.LEDGER_PROGRESS,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        EventKind.DELIVERY_RECEIPT_DEFERRED,
        EventKind.DELIVERY_RECEIPT_EXPIRED,
        EventKind.DEAD_LETTER_ESCALATION,
    }
)
"""Hub event kinds needed for an attention projection."""


def project_review_attention(
    rows: Sequence[tuple[ReviewFinding, AuthorBinding | None, float, int, bool]],
    approvals: ApprovalReport,
    *,
    reviewer_seat: str,
) -> tuple[AttentionEvidence, ...]:
    """Project private findings into generic, source-backed routing alerts.

    Parameters
    ----------
    rows : Sequence[tuple[ReviewFinding, AuthorBinding | None, float, int, bool]]
        Immutable finding, exact binding, observation time, decision sequence and route receipt.
    approvals : ApprovalReport
        Current hub decisions on exact finding subjects.
    reviewer_seat : str
        Exact independent review identity configured by the owner.

    Returns
    -------
    tuple[AttentionEvidence, ...]
        Review alerts without free-text or native-session disclosure.
    """
    evidence: list[AttentionEvidence] = []
    for finding, binding, observed_at, decision_seq, routed in rows:
        decision = independent_decision(finding, binding, approvals, reviewer_seat=reviewer_seat)
        if binding is None:
            severity, action = "critical", "Bind the exact author task and session"
        elif decision in {"awaiting_independent_decision", "not_independent"}:
            severity, action = "warning", "Request an independent decision on the exact review"
        else:
            severity, action = "warning", "Check current diff and route decided feedback"
        key = "review:" + hashlib.sha256(finding.key.encode()).hexdigest()
        revision = hashlib.sha256(
            f"{finding.source_sha256}:{decision}:{decision_seq}:{routed}".encode()
        ).hexdigest()
        evidence.append(
            AttentionEvidence(
                key=key,
                kind="review_feedback",
                subject=review_subject(finding, binding) if binding is not None else finding.key,
                severity="info" if routed else severity,
                state="resolved" if routed else "open",
                action="Inspect the routed review receipt" if routed else action,
                source_revision=revision,
                observed_at=observed_at,
                expires_at=None,
            )
        )
    return tuple(evidence)


def _approval_evidence(
    event: StoredEvent, now: float, ttl_seconds: float
) -> AttentionEvidence | None:
    if event.payload.get("kind") != APPROVAL_NOTE_KIND:
        return None
    fields = parse_approval_note(str(event.payload.get("text", "")))
    if fields is None:
        return None
    subject = fields["subject"]
    pending = fields["state"] == STATE_REQUESTED
    deadline = event.ts + ttl_seconds if pending else None
    state = "expired" if pending and deadline is not None and now >= deadline else "open"
    if not pending:
        state = "resolved"
    return AttentionEvidence(
        key=f"approval:{subject}",
        kind="approval",
        subject=subject,
        severity="critical" if state == "expired" else "warning",
        state=state,
        action=(
            "Review the pending approval; request a fresh decision if overdue"
            if pending
            else "Inspect the recorded approval decision"
        ),
        source_revision=str(event.seq),
        observed_at=event.ts,
        expires_at=deadline,
    )


def _delivery_evidence(event: StoredEvent) -> AttentionEvidence | None:
    sequence = event.payload.get("message_seq")
    if type(sequence) is not int or sequence < 1:
        return None
    key = f"delivery:{sequence}"
    if event.kind == EventKind.DELIVERY_RECEIPT_DEFERRED or (
        event.kind == EventKind.DELIVERY_RECEIPT_IMMEDIATE
        and event.payload.get("delivered") is True
    ):
        state, severity, kind, action = (
            "resolved",
            "info",
            "recovery",
            "Inspect the delivery acknowledgement",
        )
    elif event.kind == EventKind.DELIVERY_RECEIPT_EXPIRED or (
        event.kind == EventKind.DELIVERY_RECEIPT_IMMEDIATE
        and event.payload.get("delivered") is False
    ):
        state, severity, kind, action = (
            "open",
            "critical",
            "failed_delivery",
            "Inspect the delivery receipt and retry or reroute explicitly",
        )
    else:
        return None
    return AttentionEvidence(
        key=key,
        kind=kind,
        subject=str(sequence),
        severity=severity,
        state=state,
        action=action,
        source_revision=str(event.seq),
        observed_at=event.ts,
        expires_at=None,
    )


def project_hub_attention(
    events: Iterable[StoredEvent], *, now: float, approval_ttl_seconds: float = 86400.0
) -> tuple[AttentionEvidence, ...]:
    """Fold durable approval and delivery events into current attention evidence.

    Parameters
    ----------
    events : Iterable[StoredEvent]
        Hub events in ascending durable sequence order.
    now : float
        Wall time used only to label overdue requests; never to decide them.
    approval_ttl_seconds : float
        Time after which an undecided request is marked overdue.

    Returns
    -------
    tuple[AttentionEvidence, ...]
        Latest evidence per reason and subject, sorted by key.

    Raises
    ------
    ValueError
        If the review deadline is not positive.
    """
    if approval_ttl_seconds <= 0:
        raise ValueError("approval review deadline must be positive")
    latest: dict[str, AttentionEvidence] = {}
    for event in events:
        candidate: AttentionEvidence | None = None
        if event.kind == EventKind.LEDGER_PROGRESS:
            candidate = _approval_evidence(event, now, approval_ttl_seconds)
        elif event.kind in {
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            EventKind.DELIVERY_RECEIPT_EXPIRED,
        }:
            candidate = _delivery_evidence(event)
        elif event.kind == EventKind.DEAD_LETTER_ESCALATION:
            target = event.payload.get("target")
            if isinstance(target, str) and target:
                candidate = AttentionEvidence(
                    key=f"dead-letter:{target}",
                    kind="failed_delivery",
                    subject=target,
                    severity="critical",
                    state="open",
                    action="Inspect the dead-letter queue and recover the target",
                    source_revision=str(event.seq),
                    observed_at=event.ts,
                    expires_at=None,
                )
        if candidate is not None:
            latest[candidate.key] = candidate
    return tuple(latest[key] for key in sorted(latest))


def project_quota_attention(
    events: Sequence[Mapping[str, object]], *, at: datetime, stale_after_seconds: float = 86400.0
) -> tuple[AttentionEvidence, ...]:
    """Project owner-local quota freshness and renewal without account labels.

    Parameters
    ----------
    events : Sequence[Mapping[str, object]]
        Validated C04 private-ledger records.
    at : datetime
        Offset-aware evaluation time.
    stale_after_seconds : float
        Maximum accepted observation age.

    Returns
    -------
    tuple[AttentionEvidence, ...]
        Stale observation and renewal evidence for current windows.

    Raises
    ------
    ValueError
        If the age bound is not positive.
    """
    if stale_after_seconds <= 0:
        raise ValueError("stale age bound must be positive")
    report = entitlement_view(events, as_of=at, private=True)
    evidence: list[AttentionEvidence] = []
    for pool in report["pools"]:
        for window in pool["windows"]:
            if not window["current"]:
                continue
            window_id = str(window["window_id"])
            age = window["observation_age_seconds"]
            source_time = window["observation_time"]
            if age is None or float(age) > stale_after_seconds:
                observed = (
                    parse_time(source_time, "observation_time").timestamp()
                    if source_time is not None
                    else parse_time(window["recorded_at"], "recorded_at").timestamp()
                )
                evidence.append(
                    AttentionEvidence(
                        key=f"quota-stale:{window_id}",
                        kind="stale_data",
                        subject=window_id,
                        severity="warning",
                        state="open",
                        action="Refresh the allowance observation from its source",
                        source_revision=f"{window['revision']}:{source_time or 'unobserved'}",
                        observed_at=observed,
                        expires_at=None,
                    )
                )
            renewal = window["renewal_at"]
            if renewal is not None and window["remaining"] == "0":
                evidence.append(
                    AttentionEvidence(
                        key=f"quota-reset:{window_id}",
                        kind="quota_reset",
                        subject=window_id,
                        severity="warning",
                        state="open",
                        action="Wait for the recorded renewal, then verify the new balance",
                        source_revision=f"{window['revision']}:{source_time}",
                        observed_at=parse_time(renewal, "renewal_at").timestamp(),
                        expires_at=None,
                    )
                )
    return tuple(sorted(evidence, key=lambda item: item.key))
