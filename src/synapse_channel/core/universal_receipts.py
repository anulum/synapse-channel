# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — universal receipt projections from durable events
"""Project receipt-bearing durable events into one read-side receipt shape.

SYNAPSE emits receipts through several independent mechanisms: release evidence
as blackboard assessment notes, delivery-receipt audit events, sandbox run
attestations, approval notes, governed operator relays, and cross-hub audit
events. This module keeps those write paths stable and gives dashboards,
event-query, and cockpit clients one JSON shape to consume.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, parse_approval_note
from synapse_channel.core.delivery_receipts import (
    DELIVERY_RECEIPT_EVENT_KINDS,
    receipt_event_to_json,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent

RELEASE_RECEIPT_PREFIX = "release receipt:"
"""Progress-note prefix used by release evidence receipts."""

RECEIPT_PROGRESS_KINDS = frozenset(
    {
        "a2a-validation",
        "a2a_validation",
        APPROVAL_NOTE_KIND,
        "policy",
        "postmortem",
        "verification",
    }
)
"""Structured progress-note kinds that project as universal receipts."""

UNIVERSAL_RECEIPT_EVENT_KINDS = DELIVERY_RECEIPT_EVENT_KINDS | {
    EventKind.DEAD_LETTER_FORWARDING,
    EventKind.LEDGER_PROGRESS,
    EventKind.OPERATOR_RELAY,
    EventKind.SANDBOX_RUN,
}
"""Event kinds worth loading when building the universal receipt view."""


@dataclass(frozen=True, slots=True)
class UniversalReceipt:
    """One receipt-like fact in the durable coordination log.

    Attributes
    ----------
    seq : int
        Event-log sequence that anchors the receipt.
    ts : float
        Event timestamp.
    receipt_id : str
        Stable local identifier derived from the event sequence and receipt kind.
    kind : str
        Receipt family, such as ``claim``, ``delivery``, ``sandbox-run``,
        ``policy``, ``approval``, ``operator-relay``, ``cross-hub``,
        ``a2a-validation``, or ``postmortem``.
    subject : str
        Task, target, tool, or namespace the receipt concerns.
    actor : str
        Best available actor for the receipt.
    status : str
        Family-specific status normalized to a short token.
    summary : str
        Human-readable one-line summary.
    source_event_kind : str
        Original event kind that produced the projection.
    payload : Mapping[str, object]
        Receipt-specific fields, preserved without message bodies.
    """

    seq: int
    ts: float
    receipt_id: str
    kind: str
    subject: str
    actor: str
    status: str
    summary: str
    source_event_kind: str
    payload: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-compatible receipt mapping."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "receipt_id": self.receipt_id,
            "kind": self.kind,
            "subject": self.subject,
            "actor": self.actor,
            "status": self.status,
            "summary": self.summary,
            "source_event_kind": self.source_event_kind,
            "payload": dict(self.payload),
        }


def universal_receipt_from_event(event: StoredEvent) -> UniversalReceipt | None:
    """Project one stored event to a universal receipt when it carries one.

    Parameters
    ----------
    event : StoredEvent
        Durable event read from the hub event store.

    Returns
    -------
    UniversalReceipt or None
        Receipt projection, or ``None`` when the event is not receipt-bearing.
    """
    if event.kind in DELIVERY_RECEIPT_EVENT_KINDS:
        return _delivery_receipt(event)
    if event.kind == EventKind.SANDBOX_RUN:
        return _sandbox_receipt(event)
    if event.kind == EventKind.OPERATOR_RELAY:
        return _operator_relay_receipt(event)
    if event.kind == EventKind.DEAD_LETTER_FORWARDING:
        return _cross_hub_receipt(event)
    if event.kind == EventKind.LEDGER_PROGRESS:
        return _progress_receipt(event)
    return None


def universal_receipts_from_events(events: Iterable[StoredEvent]) -> tuple[UniversalReceipt, ...]:
    """Return universal receipts projected from ``events`` in event order."""
    receipts: list[UniversalReceipt] = []
    for event in events:
        receipt = universal_receipt_from_event(event)
        if receipt is not None:
            receipts.append(receipt)
    return tuple(receipts)


def universal_receipt_to_json(receipt: UniversalReceipt) -> dict[str, object]:
    """Return ``receipt`` as a JSON-compatible mapping."""
    return receipt.to_dict()


def format_universal_receipt(receipt: UniversalReceipt) -> str:
    """Render one universal receipt as a compact operator line."""
    bits = [
        f"seq={receipt.seq}",
        f"kind={receipt.kind}",
        f"status={receipt.status}",
    ]
    if receipt.subject:
        bits.append(f"subject={receipt.subject}")
    if receipt.actor:
        bits.append(f"actor={receipt.actor}")
    if receipt.summary:
        bits.append(f"summary={receipt.summary}")
    return " ".join(bits)


def universal_receipt_matches(receipt: UniversalReceipt, selector: str) -> bool:
    """Return whether ``receipt`` belongs to ``selector``.

    ``all`` and an empty selector match every receipt. Other selectors match the
    receipt subject, actor, status, kind, or any string value in its payload.
    """
    name = selector.strip()
    if not name or name == "all":
        return True
    if name in {receipt.subject, receipt.actor, receipt.status, receipt.kind}:
        return True
    return _payload_contains(receipt.payload, name)


def _delivery_receipt(event: StoredEvent) -> UniversalReceipt:
    """Project one delivery-receipt audit event."""
    payload = receipt_event_to_json(event)
    phase = _text(payload, "phase")
    status = _delivery_status(payload, phase)
    subject = _text(payload, "target")
    sender = _text(payload, "sender")
    message_seq = payload.get("message_seq")
    summary = f"{sender} -> {subject} {status}"
    if message_seq is not None:
        summary += f" at message_seq={message_seq}"
    return _receipt(
        event,
        kind="delivery",
        subject=subject,
        actor=sender,
        status=status,
        summary=summary,
        payload=payload,
    )


def _sandbox_receipt(event: StoredEvent) -> UniversalReceipt:
    """Project one sandbox run attestation."""
    payload = _object_payload(event.payload)
    tool_id = _text(payload, "tool_id")
    exit_status = _text(payload, "exit") or "unknown"
    reason = _text(payload, "reason")
    summary = f"{tool_id or 'sandbox tool'} exited {exit_status}"
    if reason:
        summary += f": {reason}"
    return _receipt(
        event,
        kind="sandbox-run",
        subject=tool_id,
        actor="",
        status=exit_status,
        summary=summary,
        payload=payload,
    )


def _operator_relay_receipt(event: StoredEvent) -> UniversalReceipt:
    """Project one governed operator relay audit event."""
    payload = _object_payload(event.payload)
    status = _operator_status(payload)
    action = _text(payload, "action")
    task_id = _text(payload, "task_id")
    namespace = _text(payload, "namespace")
    subject = task_id or namespace
    actor = _first_text(payload, ("operator", "requester", "agent", "approver"))
    summary = " ".join(part for part in (action, subject, status) if part)
    return _receipt(
        event,
        kind="operator-relay",
        subject=subject,
        actor=actor,
        status=status,
        summary=summary,
        payload=payload,
    )


def _cross_hub_receipt(event: StoredEvent) -> UniversalReceipt:
    """Project one cross-hub audit pointer as a receipt."""
    payload = _object_payload(event.payload)
    delivered = bool(payload.get("delivered", False))
    status = "delivered" if delivered else "recorded"
    target = _text(payload, "target")
    owner_hub = _text(payload, "owner_hub_id")
    origin_hub = _text(payload, "origin_hub_id")
    summary = f"{origin_hub or 'hub'} -> {owner_hub or 'hub'} {status}".strip()
    return _receipt(
        event,
        kind="cross-hub",
        subject=target,
        actor=origin_hub,
        status=status,
        summary=summary,
        payload=payload,
    )


def _progress_receipt(event: StoredEvent) -> UniversalReceipt | None:
    """Project structured progress notes that carry receipt semantics."""
    payload = _object_payload(event.payload)
    note_kind = _normalize_kind(_text(payload, "kind"))
    text = _text(payload, "text")
    if note_kind == APPROVAL_NOTE_KIND:
        return _approval_receipt(event, payload, text)
    if text.lower().startswith(RELEASE_RECEIPT_PREFIX):
        return _release_receipt(event, payload, text)
    if note_kind in {_normalize_kind(kind) for kind in RECEIPT_PROGRESS_KINDS}:
        return _generic_progress_receipt(event, payload, note_kind, text)
    return None


def _approval_receipt(
    event: StoredEvent, payload: Mapping[str, object], text: str
) -> UniversalReceipt | None:
    """Project one approval progress note."""
    fields = parse_approval_note(text)
    if fields is None:
        return None
    reason = fields["reason"]
    status = fields["state"]
    subject = fields["subject"]
    summary = f"{subject} {status}"
    if reason:
        summary += f": {reason}"
    return _receipt(
        event,
        kind="approval",
        subject=subject,
        actor=_text(payload, "author"),
        status=status,
        summary=summary,
        payload={"text": text, **fields},
    )


def _release_receipt(
    event: StoredEvent, payload: Mapping[str, object], text: str
) -> UniversalReceipt:
    """Project one release-receipt assessment note."""
    details = _parse_release_note(text)
    status = _text(details, "epistemic_status") or "recorded"
    return _receipt(
        event,
        kind="claim",
        subject=_text(payload, "task_id"),
        actor=_text(payload, "author"),
        status=status,
        summary=text.removeprefix(RELEASE_RECEIPT_PREFIX).strip(),
        payload={"text": text, **details},
    )


def _generic_progress_receipt(
    event: StoredEvent, payload: Mapping[str, object], note_kind: str, text: str
) -> UniversalReceipt:
    """Project policy, verification, A2A-validation, and postmortem notes."""
    status = _first_text(payload, ("status", "state", "verdict")) or "recorded"
    subject = _text(payload, "task_id") or _text(payload, "subject")
    return _receipt(
        event,
        kind=note_kind,
        subject=subject,
        actor=_text(payload, "author"),
        status=status,
        summary=text,
        payload=payload,
    )


def _receipt(
    event: StoredEvent,
    *,
    kind: str,
    subject: str,
    actor: str,
    status: str,
    summary: str,
    payload: Mapping[str, object],
) -> UniversalReceipt:
    """Build one receipt projection with a stable event-derived id."""
    clean_kind = _normalize_kind(kind)
    return UniversalReceipt(
        seq=event.seq,
        ts=event.ts,
        receipt_id=f"{clean_kind}:{event.seq}",
        kind=clean_kind,
        subject=subject,
        actor=actor,
        status=status or "recorded",
        summary=summary,
        source_event_kind=event.kind,
        payload=payload,
    )


def _delivery_status(payload: Mapping[str, object], phase: str) -> str:
    """Return a short status token for a delivery receipt event."""
    if phase == "requested":
        return "requested"
    if bool(payload.get("expired", False)):
        return "expired"
    delivered = payload.get("delivered")
    if delivered is True:
        return "delivered"
    if delivered is False:
        return "undelivered"
    return phase or "recorded"


def _operator_status(payload: Mapping[str, object]) -> str:
    """Return a short status token for an operator relay receipt."""
    status = _text(payload, "status")
    if status:
        return status
    if bool(payload.get("applied", False)):
        return "applied"
    if bool(payload.get("pending", False)):
        return "pending"
    return "refused"


def _parse_release_note(text: str) -> dict[str, object]:
    """Parse the key/value tail of a release-receipt progress note."""
    tail = text.removeprefix(RELEASE_RECEIPT_PREFIX).strip()
    details: dict[str, object] = {}
    for part in tail.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key:
            details[key] = value.strip()
    return details


def _normalize_kind(value: str) -> str:
    """Normalize receipt-family names for the public read model."""
    return value.strip().lower().replace("_", "-")


def _object_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Return a plain object mapping from an event payload."""
    return dict(payload)


def _text(payload: Mapping[str, object], key: str) -> str:
    """Return one payload field as text, or an empty string."""
    value = payload.get(key)
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _first_text(payload: Mapping[str, object], keys: Iterable[str]) -> str:
    """Return the first non-empty text field among ``keys``."""
    for key in keys:
        value = _text(payload, key)
        if value:
            return value
    return ""


def _payload_contains(payload: Mapping[str, object], needle: str) -> bool:
    """Return whether a selector matches any string nested in ``payload``."""
    for value in payload.values():
        if isinstance(value, str) and value == needle:
            return True
        if isinstance(value, list) and needle in {str(item) for item in value}:
            return True
    return False
