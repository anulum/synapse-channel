# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — map authoritative mutations to the durable event log
"""Translate hub state changes into durable events and define the journal vocabulary.

This module is the write side of the bridge between the in-memory coordination
state and the append-only :class:`~synapse_channel.core.persistence.EventStore`.
Each authoritative mutation — a claim, release, task update, resource offer, or
chat message — is recorded as one event; the read side
(:mod:`synapse_channel.core.journal_replay`) replays the log back and rebuilds
live hub state after a restart.

Claim and task-update events carry the full claim snapshot, so replay overwrites
the projected claim directly instead of re-deriving it through the claim
algorithm — the persisted epoch and lease are reconstructed faithfully. Lease
durability (``durable=True``) is used for the claim/release/update path; the
high-volume chat and the soft resource offers use ordinary commits.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from synapse_channel.core.delivery_persistence import (
    DELIVERY_ACCEPTED,
    DELIVERY_OWNER_BOUND,
    DELIVERY_QUEUED,
    DELIVERY_TRANSITION,
)
from synapse_channel.core.delivery_persistence import (
    DELIVERY_CANCEL_REQUESTED as DELIVERY_CANCEL_REQUESTED_KIND,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND
from synapse_channel.core.ledger import LedgerTask, ProgressNote
from synapse_channel.core.message_auth_key_watch import (
    MESSAGE_KEY_AUDIT_OVERFLOW_KIND,
    MESSAGE_KEY_EXPIRED_KIND,
    MESSAGE_KEY_RETIRING_ADMISSION_KIND,
    MESSAGE_KEY_SET_APPLIED_KIND,
    MESSAGE_KEY_SET_REJECTED_KIND,
)
from synapse_channel.core.multihub_equivocation import (
    QUARANTINE_CAUSES,
    FederationQuarantine,
    valid_fingerprint,
)
from synapse_channel.core.notification_events import NOTIFICATION_FIELD, notification_metadata
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import ResourceOffer, TaskClaim
from synapse_channel.core.task_causality import TaskCausalParent, task_event_payload
from synapse_channel.core.token_audit import (
    ADMITTED_KIND,
    AUDIT_OVERFLOW_KIND,
    METRICS_AUDIT_OVERFLOW_KIND,
    METRICS_EXPIRED_KIND,
    METRICS_RETIRING_ADMISSION_KIND,
    METRICS_SET_APPLIED_KIND,
    METRICS_SET_REJECTED_KIND,
    REFUSED_KIND,
    SESSION_ENDED_KIND,
    SET_APPLIED_KIND,
    SET_REJECTED_KIND,
)


class EventKind:
    """Durable event kind tags written to the log."""

    CLAIM = "claim"
    CLAIM_DENIAL = "claim_denial"
    GUARD_DENIAL = "guard_denial"
    NATIVE_MESSAGE = "native_message"
    RELEASE = "release"
    TASK_UPDATE = "task_update"
    CHECKPOINT = "checkpoint"
    HANDOFF = "handoff"
    RESOURCE = "resource"
    CHAT = "chat"
    LEDGER_TASK = "ledger_task"
    LEDGER_PROGRESS = "ledger_progress"
    RECALL = "recall"
    FINDING = "finding"
    IDEMPOTENCY = "idempotency"
    SANDBOX_RUN = "sandbox_run"
    OPERATOR_RELAY = "operator_relay"
    DEAD_LETTER_ESCALATION = "dead_letter_escalation"
    DEAD_LETTER_FORWARDING = "dead_letter_forwarding"
    DELIVERY_RECEIPT_REQUESTED = "delivery_receipt_requested"
    DELIVERY_RECEIPT_IMMEDIATE = "delivery_receipt_immediate"
    DELIVERY_RECEIPT_DEFERRED = "delivery_receipt_deferred"
    DELIVERY_RECEIPT_EXPIRED = "delivery_receipt_expired"
    DELIVERY_INTENT_ACCEPTED = DELIVERY_ACCEPTED
    DELIVERY_INTENT_QUEUED = DELIVERY_QUEUED
    DELIVERY_INTENT_TRANSITION = DELIVERY_TRANSITION
    DELIVERY_CANCEL_REQUESTED = DELIVERY_CANCEL_REQUESTED_KIND
    DELIVERY_RECEIVING_HUB_BOUND = DELIVERY_OWNER_BOUND
    MAILBOX_WATERMARK = "mailbox_watermark"
    MAILBOX_HANDOVER = "mailbox_handover"
    ROLE_BINDING = "role_binding"
    OPERATION_DECLARED = "operation_declared"
    OPERATION_OUTCOME = "operation_outcome"
    OPERATION_VERIFIED = "operation_verified"
    OPERATION_ATTEMPT_ANSWERED = "operation_attempt_answered"
    OPERATION_SEALED = "operation_sealed"
    OPERATION_STOPPED = "operation_stopped"
    OPERATION_RESUMED = "operation_resumed"
    OPERATION_STOP_ANSWERED = "operation_stop_answered"
    OPERATION_STOP_READBACK = "operation_stop_readback"
    OPERATION_STOP_STATE = "operation_stop_state"
    OPERATION_LEVEL = "operation_level"
    OPERATION_LEVEL_ANSWERED = "operation_level_answered"
    APPROVAL_KIND = "approval_kind"
    LEVEL_CRITERIA = "level_criteria"
    LEVEL_JUDGEMENT = "level_judgement"
    OPERATION_TRIGGER = "operation_trigger"
    IDENTITY_PIN_RECLAIM = "identity_pin_reclaim"
    IDENTITY_ENROLLMENT = "identity_enrollment"
    ENTITLEMENT_ADVERT = "entitlement_advert"
    VERDICT_STALE = "verdict_stale"
    VERDICT_RECORD_REFUSED = "verdict_record_refused"
    VERDICT_GATE_BYPASS = "verdict_gate_bypass"
    APPROVAL_LOAD_POLICY = "approval_load_policy"
    APPROVAL_LOAD_ADMITTED = "approval_load_admitted"
    MULTIHUB_PARTITION = "multihub_partition"
    MULTIHUB_HEAL = "multihub_heal"
    MULTIHUB_EQUIVOCATION = "multihub_equivocation"
    MULTIHUB_EQUIVOCATION_RECOVERY = "multihub_equivocation_recovery"
    FEED_ACK = "feed_ack"
    FEED_BIND = "feed_bind"
    COMPACTION_FLOOR = "compaction_floor"
    CONNECT_TOKEN_ADMITTED = ADMITTED_KIND
    CONNECT_TOKEN_REFUSED = REFUSED_KIND
    CONNECT_TOKEN_SET_APPLIED = SET_APPLIED_KIND
    CONNECT_TOKEN_SET_REJECTED = SET_REJECTED_KIND
    CONNECT_TOKEN_SESSION_ENDED = SESSION_ENDED_KIND
    CONNECT_TOKEN_AUDIT_OVERFLOW = AUDIT_OVERFLOW_KIND
    METRICS_TOKEN_SET_APPLIED = METRICS_SET_APPLIED_KIND
    METRICS_TOKEN_SET_REJECTED = METRICS_SET_REJECTED_KIND
    METRICS_TOKEN_EXPIRED = METRICS_EXPIRED_KIND
    METRICS_TOKEN_RETIRING_ADMISSION = METRICS_RETIRING_ADMISSION_KIND
    METRICS_TOKEN_AUDIT_OVERFLOW = METRICS_AUDIT_OVERFLOW_KIND
    MESSAGE_AUTH_KEY_SET_APPLIED = MESSAGE_KEY_SET_APPLIED_KIND
    MESSAGE_AUTH_KEY_SET_REJECTED = MESSAGE_KEY_SET_REJECTED_KIND
    MESSAGE_AUTH_KEY_EXPIRED = MESSAGE_KEY_EXPIRED_KIND
    MESSAGE_AUTH_KEY_RETIRING_ADMISSION = MESSAGE_KEY_RETIRING_ADMISSION_KIND
    MESSAGE_AUTH_KEY_AUDIT_OVERFLOW = MESSAGE_KEY_AUDIT_OVERFLOW_KIND
    CORRUPT = CORRUPT_EVENT_KIND


class UnsupportedProtectedWriteHistoryError(SynapseError, RuntimeError):
    """Replay cannot reconstruct custody from a protected-write history event.

    Attributes
    ----------
    code : str
        Stable classification returned by ``error_code``.
    """

    code = "unsupported_protected_write_history"


MEMORY_KINDS = frozenset(
    {
        EventKind.RECALL,
        EventKind.FINDING,
        EventKind.CHECKPOINT,
        EventKind.HANDOFF,
    }
)
"""The durable event kinds the persistent-memory read-side ingests.

The query-stream (``recall``), the authored atoms (``finding``), and the
highest-signal episodic state (``checkpoint``/``handoff``) — the subset of the
log a downstream memory adapter reads through the seq-cursored ingest seam. The
pure coordination kinds (``claim``/``release``/``task_update``/``resource``/the
ledger kinds) are excluded; ``chat`` is filtered read-side, not here.
"""


def record_claim(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a claim or renewal."""
    store.append(EventKind.CLAIM, claim.as_persisted_dict(), durable=True)


def record_claim_denial(
    store: EventStore,
    *,
    claimant: str,
    task_id: str,
    reason_code: str,
    worktree: str,
    paths: list[str],
) -> int:
    """Append bounded, content-minimized evidence for a refused claim.

    The audit row deliberately excludes the request note, Git metadata, raw task
    id, and raw paths.  A caller that already knows the attempted identifiers can
    correlate them by digest without turning the evidence log into a second copy
    of repository or prompt content.
    """
    claimant_text = str(claimant)
    scope = json.dumps(
        {"paths": [str(path) for path in paths], "worktree": str(worktree)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = {
        "claimant": claimant_text[:256],
        "claimant_sha256": hashlib.sha256(claimant_text.encode("utf-8")).hexdigest(),
        "claimant_truncated": len(claimant_text) > 256,
        "decision": "deny",
        "path_count": len(paths),
        "reason_code": str(reason_code),
        "scope_sha256": hashlib.sha256(scope).hexdigest(),
        "task_id_sha256": hashlib.sha256(str(task_id).encode("utf-8")).hexdigest(),
    }
    return store.append(EventKind.CLAIM_DENIAL, payload, durable=True)


def record_guard_denial(store: EventStore, evidence: Mapping[str, Any]) -> int:
    """Append one validated, digest-only guard refusal as durable evidence."""
    return store.append(EventKind.GUARD_DENIAL, dict(evidence), durable=True)


def record_release(
    store: EventStore,
    task_id: str,
    *,
    owner: str | None = None,
    via: str | None = None,
) -> None:
    """Append a durable event marking a task released.

    An owner release writes exactly ``{"task_id": ...}``. A sidecar release
    also names ``owner`` and ``via``. Callers pass both extra fields or neither.
    """
    payload: dict[str, str] = {"task_id": task_id}
    if owner is not None and via is not None:
        payload["owner"] = owner
        payload["via"] = via
    store.append(EventKind.RELEASE, payload, durable=True)


def record_task_update(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event with the post-update claim snapshot."""
    store.append(EventKind.TASK_UPDATE, claim.as_persisted_dict(), durable=True)


RELAY_DIRECTION_IN = "in"
"""Audit ``direction`` on the hub that *applies* a relayed action (the owning hub)."""

RELAY_DIRECTION_OUT = "out"
"""Audit ``direction`` on the hub that *originates* a relay and forwards it to the owner."""

DEAD_LETTER_DIRECTION_IN = "in"
"""Audit ``direction`` on the owning hub that *receives* a forwarded dead-letter pointer."""

DEAD_LETTER_DIRECTION_OUT = "out"
"""Audit ``direction`` on the origin hub that *forwards* a dead-letter pointer to the owner."""


def record_operator_relay(store: EventStore, provenance: Mapping[str, Any]) -> None:
    """Append a durable audit event for a governed cross-hub operator relay.

    This kind is **audit-only**: :func:`~synapse_channel.core.journal_replay.replay` skips it,
    because the state change a
    relay makes (for a release relay, the freed lease) is journalled by the underlying
    coordination event (a ``release``) that reconstructs state on its own. The relay
    event exists so the durable log records the cross-hub *provenance* a coordination
    event never carries — which action was relayed, into which namespace, the verified
    peer it arrived from, and the operator and origin hub it asserts — so a force-release
    performed on one hub's authority by another is attributable after the fact.

    A relay leaves an audit trail on **both** hubs it touches, distinguished by the
    ``direction`` field: :data:`RELAY_DIRECTION_OUT` on the origin hub that forwarded it
    (recording the local requester and the destination owner) and
    :data:`RELAY_DIRECTION_IN` on the owning hub that applied it (recording the verified
    peer and the previous holder), so the two sides of one relay reconcile in the log.

    Parameters
    ----------
    store : EventStore
        The recording hub's durable event store.
    provenance : Mapping[str, Any]
        The relay's audit fields (action, namespace, task id, ``direction``, asserted
        operator and origin hub, whether it was applied, and a detail; plus the verified
        peer and previous owner inbound, or the local requester and destination outbound).
    """
    store.append(EventKind.OPERATOR_RELAY, dict(provenance), durable=True)


def record_operator_release(store: EventStore, task_id: str, provenance: Mapping[str, Any]) -> None:
    """Atomically append a force-release projection and its relay provenance.

    Replay needs the ordinary ``release`` row, while auditors need the adjacent
    ``operator_relay`` row. They describe one governed mutation and therefore
    must never commit independently.
    """
    store.append_batch(
        (
            (EventKind.RELEASE, {"task_id": task_id}),
            (EventKind.OPERATOR_RELAY, dict(provenance)),
        ),
        durable=True,
    )


def record_identity_pin_reclaim(store: EventStore, provenance: Mapping[str, Any]) -> int:
    """Append the mandatory durable audit event for an applied pin reclaim.

    This event is audit-only: the identity-pin JSON file is the durable state
    projection, while replay deliberately ignores this kind. The event records
    who removed which exact key, why, whether the live/lease gates were
    overridden, and whether a live socket was evicted, so a break-glass action
    is never confused with passive expiry or a first-use pin.

    Returns
    -------
    int
        Monotonic journal sequence of the audit event.
    """
    return store.append(EventKind.IDENTITY_PIN_RECLAIM, dict(provenance), durable=True)


def record_entitlement_advert(store: EventStore, record: Mapping[str, Any]) -> int:
    """Append one redacted pool advertisement as a durable, audit-only row.

    Replay ignores this kind; it is never broadcast to seats. Fleet mirrors read
    it from the replicated log (journal-only advertisement).

    Returns
    -------
    int
        Monotonic journal sequence of the row.
    """
    return store.append(EventKind.ENTITLEMENT_ADVERT, dict(record), durable=True)


def record_verdict_stale(store: EventStore, record: Mapping[str, Any]) -> int:
    """Append the durable fact that a correction turned one verdict stale.

    The row names the verdict, its subject, author and reviewer, and the
    correction that staled it. It is the source of truth for the notification:
    a directed message to an offline seat may dead-letter, this row does not.

    Returns
    -------
    int
        Monotonic journal sequence of the row.
    """
    return store.append(EventKind.VERDICT_STALE, dict(record), durable=True)


def record_approval_load_policy(store: EventStore, record: Mapping[str, Any]) -> int:
    """Append the approval-load policy in force, once per hub lifetime at its first use.

    The row holds the canonical policy and its SHA-256, so an offline reader
    knows which thresholds every later ``approval_load_admitted`` row names.

    Returns
    -------
    int
        Monotonic journal sequence of the row.
    """
    return store.append(EventKind.APPROVAL_LOAD_POLICY, dict(record), durable=True)


def record_verdict_record_refused(store: EventStore, record: Mapping[str, Any]) -> int:
    """Append the durable audit row for a refused evidence, verdict or correction note.

    A refused note never enters the progress log; this row records who tried,
    which kind, and why, without the refused body.

    Returns
    -------
    int
        Monotonic journal sequence of the row.
    """
    return store.append(EventKind.VERDICT_RECORD_REFUSED, dict(record), durable=True)


def record_verdict_gate_bypass(store: EventStore, record: Mapping[str, Any]) -> int:
    """Append the durable row for an approval admitted while the verdict gate is off.

    The row names the approver and subject and what the gate would have
    decided, so a switched-off gate never hides a stale or missing verdict.

    Returns
    -------
    int
        Monotonic journal sequence of the row.
    """
    return store.append(EventKind.VERDICT_GATE_BYPASS, dict(record), durable=True)


def record_identity_enrollment(store: EventStore, provenance: Mapping[str, Any]) -> int:
    """Append the mandatory durable audit event for an identity-key enrolment.

    Audit-only, like the pin reclaim: the hub-owned enrolment store is the
    durable state and replay ignores this kind. One request writes an
    ``approved`` event before the store changes, then ``applied`` or
    ``not_applied``; a refused request writes ``denied`` with its reason.

    Returns
    -------
    int
        Monotonic journal sequence of the audit event.
    """
    return store.append(EventKind.IDENTITY_ENROLLMENT, dict(provenance), durable=True)


def record_multihub_ownership_transitions(
    store: EventStore, transitions: list[tuple[str, Mapping[str, Any]]]
) -> tuple[int, ...]:
    """Append one observation round's partition/heal transitions atomically.

    These rows are audit-only: coordination replay skips them, while the standing
    multi-hub watch folds them separately to restore unresolved partition
    suspicion after a restart.  A round is one durable transaction so an
    operator never observes only half of a simultaneous heal/partition change.
    """
    return store.append_batch(transitions, durable=True)


def record_multihub_equivocation(store: EventStore, quarantine: FederationQuarantine) -> int:
    """Append bounded digest-only evidence and an active peer quarantine."""
    return store.append(
        EventKind.MULTIHUB_EQUIVOCATION,
        _bounded_multihub_quarantine_payload(quarantine),
        ts=quarantine.detected_at,
        durable=True,
    )


def record_multihub_equivocation_recovery(
    store: EventStore,
    *,
    peer_id: str,
    recovered_at: float,
    recovered_by: str,
    reason: str,
    new_log_generation: str,
) -> int:
    """Append an explicit operator recovery without retaining its free-text reason."""
    for label, value in (
        ("peer_id", peer_id),
        ("recovered_by", recovered_by),
        ("reason", reason),
        ("new_log_generation", new_log_generation),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"multi-hub recovery requires a non-empty {label}")
    if (
        isinstance(recovered_at, bool)
        or not isinstance(recovered_at, (int, float))
        or not math.isfinite(recovered_at)
    ):
        raise ValueError("multi-hub recovery time must be finite")
    reason_text = str(reason)
    try:
        reason_bytes = reason_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("multi-hub recovery reason must be valid UTF-8") from exc
    generation = str(new_log_generation)
    payload = {
        **_bounded_identity("peer_id", peer_id),
        **_bounded_identity("recovered_by", recovered_by),
        **_bounded_identity("new_log_generation", generation),
        "reason_sha256": hashlib.sha256(reason_bytes).hexdigest(),
        "recovered_at": float(recovered_at),
        "explicit_recovery": True,
        "status": "recovered",
    }
    return store.append(
        EventKind.MULTIHUB_EQUIVOCATION_RECOVERY,
        payload,
        ts=float(recovered_at),
        durable=True,
    )


def _bounded_multihub_quarantine_payload(
    quarantine: FederationQuarantine,
) -> dict[str, object]:
    """Return the persisted quarantine shape without raw event content."""
    if not valid_fingerprint(quarantine.accepted_fingerprint) or not valid_fingerprint(
        quarantine.conflicting_fingerprint
    ):
        raise ValueError("multi-hub quarantine fingerprints must be lowercase SHA-256")
    if not quarantine.peer_id.strip():
        raise ValueError("multi-hub quarantine requires a peer id")
    if (
        isinstance(quarantine.seq, bool)
        or not isinstance(quarantine.seq, int)
        or quarantine.seq < 1
    ):
        raise ValueError("multi-hub quarantine sequence must be a positive integer")
    if (
        isinstance(quarantine.detected_at, bool)
        or not isinstance(quarantine.detected_at, (int, float))
        or not math.isfinite(quarantine.detected_at)
    ):
        raise ValueError("multi-hub quarantine detection time must be finite")
    if quarantine.cause not in QUARANTINE_CAUSES:
        raise ValueError("multi-hub quarantine cause is not a recorded cause")
    return {
        **_bounded_identity("peer_id", quarantine.peer_id),
        **_bounded_identity("observer_id", quarantine.observer_id),
        "seq": quarantine.seq,
        "accepted_fingerprint": quarantine.accepted_fingerprint,
        "conflicting_fingerprint": quarantine.conflicting_fingerprint,
        "detected_at": quarantine.detected_at,
        "cause": quarantine.cause,
        "status": "quarantined",
    }


def _bounded_identity(field: str, value: str) -> dict[str, object]:
    """Keep an operator label bounded while retaining its exact digest."""
    text = str(value)
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"multi-hub {field} must be valid UTF-8") from exc
    bounded = encoded[:256].decode("utf-8", errors="ignore")
    return {
        field: bounded,
        f"{field}_sha256": hashlib.sha256(encoded).hexdigest(),
        f"{field}_truncated": bounded != text,
    }


def record_dead_letter_escalation(store: EventStore, provenance: Mapping[str, Any]) -> None:
    """Append a durable audit event for a dead-letter blackhole crossing its escalation threshold.

    This kind is **audit-only**: :func:`~synapse_channel.core.journal_replay.replay` skips it,
    because it records no coordination state
    change — the dead-letter ledger is rebuilt from live sends, not the log — only the durable fact
    that a target's undelivered count reached an escalation point, so an operator reviewing the log
    can see when a blackhole was flagged and how large it had grown.

    Parameters
    ----------
    store : EventStore
        The recording hub's durable event store.
    provenance : Mapping[str, Any]
        The escalation fields: the target, its undelivered count, the most recent sender, and the
        threshold that fired.
    """
    store.append(EventKind.DEAD_LETTER_ESCALATION, dict(provenance), durable=True)


def record_dead_letter_forwarding(store: EventStore, provenance: Mapping[str, Any]) -> None:
    """Append a durable audit event for a dead-letter blackhole forwarded to its owning peer hub.

    Like :func:`record_dead_letter_escalation` this kind is **audit-only**:
    :func:`~synapse_channel.core.journal_replay.replay` skips
    it, because it records no coordination state change — only the durable, attributable fact that
    this hub resolved a blackholed target to a peer's domain and handed the peer a pointer to the
    gap (never a message body). It is written whether or not a live forwarder delivered the signal,
    so a forward that could not reach the peer is still reviewable as "recorded but not delivered".

    A forward leaves an audit trail on **both** hubs it touches, distinguished by the ``direction``
    field: :data:`DEAD_LETTER_DIRECTION_OUT` on the origin hub that resolved the target to a peer
    and forwarded the pointer, and :data:`DEAD_LETTER_DIRECTION_IN` on the owning hub that received
    it (recording the verified sending ``peer``), so the two sides of one forward reconcile in the
    log.

    Parameters
    ----------
    store : EventStore
        The recording hub's durable event store.
    provenance : Mapping[str, Any]
        The forwarding fields: the target, its undelivered count, the origin and owner hub ids, and
        the ``direction``; plus the verified sending ``peer`` on the owning (inbound) side.
    """
    store.append(EventKind.DEAD_LETTER_FORWARDING, dict(provenance), durable=True)


def record_delivery_receipt_requested(store: EventStore, receipt: Mapping[str, Any]) -> None:
    """Append the durable fact that a sender requested a delivery receipt.

    The payload names the sender, addressed target, per-hub message id, and durable
    chat sequence. It is audit-only — replay skips it — but it gives operators the
    first edge in the receipt lifecycle before the immediate or deferred verdicts.
    """
    store.append(EventKind.DELIVERY_RECEIPT_REQUESTED, dict(receipt), durable=True)


def record_delivery_receipt_immediate(store: EventStore, receipt: Mapping[str, Any]) -> None:
    """Append the immediate delivery receipt verdict returned to the sender."""
    store.append(EventKind.DELIVERY_RECEIPT_IMMEDIATE, dict(receipt), durable=True)


def record_delivery_receipt_deferred(store: EventStore, receipt: Mapping[str, Any]) -> None:
    """Append a deferred receipt emitted when a mailbox replay is acked."""
    store.append(EventKind.DELIVERY_RECEIPT_DEFERRED, dict(receipt), durable=True)


def record_delivery_receipt_expired(store: EventStore, receipt: Mapping[str, Any]) -> None:
    """Append a pending receipt expiry when the bounded live window evicts it."""
    store.append(EventKind.DELIVERY_RECEIPT_EXPIRED, dict(receipt), durable=True)


def record_mailbox_watermark(
    store: EventStore,
    *,
    identity: str,
    through_seq: int,
    source: str,
) -> None:
    """Append a receiver-acknowledged mailbox cursor.

    This uses the chat path's normal durability: losing the newest watermark to
    a power failure causes safe replay/recount, never deletion of an unseen body.
    """
    store.append(
        EventKind.MAILBOX_WATERMARK,
        {
            "identity": identity,
            "through_seq": int(through_seq),
            "source": source,
        },
    )


def record_feed_ack(
    store: EventStore,
    *,
    consumer: str,
    hub_id: str,
    journal_instance: str,
    acked_seq: int,
) -> None:
    """Append a feed consumer's acknowledgement, durably, before the hub answers it.

    The ack is the consumer's own statement that it has ingested every feed row
    through ``acked_seq`` of ``journal_instance``. Replay ignores it; the feed
    ledger reads the highest one back (:class:`~synapse_channel.core.feed.FeedLedger`).
    It is journalled ``durable`` because it is what protects the unread rows
    from compaction.
    """
    store.append(
        EventKind.FEED_ACK,
        {
            "consumer": consumer,
            "hub_id": hub_id,
            "journal_instance": journal_instance,
            "acked_seq": int(acked_seq),
        },
        durable=True,
    )


def record_feed_bind(
    store: EventStore,
    *,
    consumer: str,
    hub_id: str,
    journal_instance: str,
    after_seq: int,
    proof_kind: str,
    proof_seq: int | None,
    payload_sha256: str,
) -> None:
    """Append a verified legacy binding of a feed consumer, durably.

    The record names the cursor the consumer was bound to and the proof that
    bound it: the proof row's feed kind, its sequence when the consumer kept one,
    and its payload digest. Replay ignores it; it is the audit of a one-time
    migration act.
    """
    store.append(
        EventKind.FEED_BIND,
        {
            "consumer": consumer,
            "hub_id": hub_id,
            "journal_instance": journal_instance,
            "after_seq": int(after_seq),
            "proof_kind": proof_kind,
            "proof_seq": proof_seq,
            "payload_sha256": payload_sha256,
        },
        durable=True,
    )


def record_compaction_floor(
    store: EventStore,
    *,
    journal_instance: str,
    requested_floor: int,
    applied_floor: int,
    past_feed_acks: bool,
    held_by: tuple[tuple[str, int], ...],
    passed_consumers: tuple[tuple[str, int], ...],
    removed: int,
    released: tuple[str, ...] = (),
) -> None:
    """Append the floor one compaction applied, durably.

    ``held_by`` names the feed consumers whose acknowledgements lay below the
    requested floor and held it; ``passed_consumers`` names those an explicit
    override or release compacted past, with their acknowledgements, and
    ``released`` the consumers the operator released by name. The feed reports
    the highest applied floor of the journal instance, so a consumer whose
    cursor lies below it is told that rows may be gone. Compaction records the
    floor before it deletes, so ``removed`` counts the rows the run then
    deletes.
    """
    store.append(
        EventKind.COMPACTION_FLOOR,
        {
            "journal_instance": journal_instance,
            "requested_floor": int(requested_floor),
            "applied_floor": int(applied_floor),
            "past_feed_acks": bool(past_feed_acks),
            "held_by": [[consumer, int(acked)] for consumer, acked in held_by],
            "passed_consumers": [[consumer, int(acked)] for consumer, acked in passed_consumers],
            "released": list(released),
            "removed": int(removed),
        },
        durable=True,
    )


def record_role_binding(store: EventStore, *, identity: str, roles: tuple[str, ...]) -> None:
    """Append the role set a connection bound, when it differs from its last binding.

    A role binding is a coordination record, not an authority grant: it says which
    ``<project>/<role>`` names the hub accepted for the connection at that time, so a
    restarted hub and an offline reader know which roles have mailboxes and which roles
    a name that has since gone quiet last held.
    """
    store.append(EventKind.ROLE_BINDING, {"identity": identity, "roles": list(roles)})


def record_checkpoint(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a resume checkpoint saved on a claim.

    The payload is the full post-checkpoint claim snapshot, so
    :func:`~synapse_channel.core.journal_replay.replay`
    reconstructs the claim — including its durable ``checkpoint`` — exactly as a
    ``claim`` event would. The distinct kind lets the persistent-memory read-side
    pick out resume summaries (the highest-signal episodic memory) without
    re-deriving them from generic claim snapshots; coordination replay treats it
    identically to a claim.
    """
    store.append(EventKind.CHECKPOINT, claim.as_persisted_dict(), durable=True)


def record_handoff(store: EventStore, claim: TaskClaim, *, actor: str = "") -> None:
    """Append a durable event capturing a task handed to another agent.

    The payload is the full post-handoff claim snapshot — now owned by the
    recipient — so :func:`~synapse_channel.core.journal_replay.replay` reconstructs ownership
    exactly as a ``claim``
    event would. The distinct kind lets the read-side trace ownership transfers
    apart from ordinary claims; coordination replay treats it identically.

    Parameters
    ----------
    store : EventStore
        Authoritative durable native journal.
    claim : TaskClaim
        Committed candidate recipient claim.
    actor : str, optional
        Authenticated prior owner. When supplied for external project-scoped
        work, routing metadata is appended inside this same durable event.
    """
    payload = claim.as_persisted_dict()
    notification = notification_metadata("handoffs", actor, claim.owner, payload)
    if notification is not None:
        payload[NOTIFICATION_FIELD] = notification
    store.append(EventKind.HANDOFF, payload, durable=True)


def record_resource(store: EventStore, offer: ResourceOffer) -> None:
    """Append a (non-durable) event capturing a resource offer."""
    store.append(
        EventKind.RESOURCE,
        {
            "agent": offer.agent,
            "kind": offer.kind,
            "name": offer.name,
            "capacity": offer.capacity,
            "meta": offer.meta,
            "offered_at": offer.offered_at,
        },
    )


def record_sandbox_run(store: EventStore, receipt: dict[str, Any]) -> None:
    """Append a WASM sandbox run receipt to the durable log as an attestation.

    The receipt is already a bounded, digest-only record — tool id, content
    digest, input/output digests, exit token, fuel used, granted capabilities,
    and any containment reason — so persisting it makes every sandboxed
    execution auditable through the same event query and replay path as a
    claim or a release: an operator can later prove which tool ran under which
    grants and how it exited, without the tool's inputs or outputs ever
    entering the log.
    """
    store.append(EventKind.SANDBOX_RUN, dict(receipt), durable=True)


def record_chat(store: EventStore, message: dict[str, Any]) -> int:
    """Append a (non-durable) event capturing a broadcast chat message.

    Returns the durable ``seq`` the chat was journalled under, so the hub can
    stamp it on the outgoing frame as the resume cursor a reconnecting client
    replays its missed directed backlog from.
    """
    return store.append(EventKind.CHAT, message)


def record_ledger_task(
    store: EventStore,
    task: LedgerTask,
    *,
    causal_parent: TaskCausalParent | None = None,
    notification: dict[str, object] | None = None,
) -> None:
    """Append a task snapshot and its optional metadata in one durable event.

    Parameters
    ----------
    store : EventStore
        Authoritative durable native journal.
    task : LedgerTask
        Candidate task snapshot to publish only after persistence.
    causal_parent : TaskCausalParent or None
        Content-bound observed parent, kept outside the public task record.
    notification : dict[str, object] or None
        Server-generated assignment pointer, kept outside the task record.
    """
    store.append(
        EventKind.LEDGER_TASK,
        task_event_payload(
            task.as_dict(), causal_parent, notification=notification, origin=task.assignment_origin
        ),
        durable=True,
    )


def record_ledger_progress(store: EventStore, note: ProgressNote) -> None:
    """Append a (non-durable) event capturing one progress note."""
    store.append(EventKind.LEDGER_PROGRESS, note.as_dict())


def ledger_progress_events(
    note: ProgressNote, admitted: Mapping[str, Any] | None
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Return the rows one progress note writes: the note, then its approval-load row if any.

    An approval request admitted under an approval-load policy is journalled
    together with the ``approval_load_admitted`` row naming the limits it
    counted against and the thresholds in force, so both are one transaction:
    a restart never finds the note without its row, nor the row without its note.
    """
    rows: list[tuple[str, dict[str, Any]]] = [(EventKind.LEDGER_PROGRESS, note.as_dict())]
    if admitted is not None:
        rows.append((EventKind.APPROVAL_LOAD_ADMITTED, dict(admitted)))
    return tuple(rows)


def record_ledger_progress_admitted(
    store: EventStore, note: ProgressNote, admitted: Mapping[str, Any]
) -> None:
    """Append an admitted approval request and its approval-load row in one durable transaction."""
    store.append_batch(ledger_progress_events(note, admitted), durable=True)


def record_recall(store: EventStore, record: dict[str, Any]) -> None:
    """Append a (non-durable) event capturing one recall query-stream log.

    The record is memory telemetry — the fleet's actual lookups, captured so a
    downstream persistent-memory layer can calibrate recall against the real query
    distribution rather than activity-weighted noise. It is not coordination state,
    so :func:`~synapse_channel.core.journal_replay.replay` ignores it; it is committed at
    ``NORMAL`` durability (a lost
    log on an OS crash is statistically harmless, like a dropped chat line).

    Parameters
    ----------
    store : EventStore
        The event log to append to.
    record : dict[str, Any]
        The recall event body: the query and its outcome, with the producing
        identity and time already hub-attested by the handler.
    """
    store.append(EventKind.RECALL, record)


def record_finding(store: EventStore, record: dict[str, Any]) -> None:
    """Append one finding to the durable memory spine.

    A finding is an authored memory atom — a fact, lesson, decision, dead-end, or
    outcome the producer wants remembered — that has already passed the emit gate
    (:mod:`synapse_channel.core.emit_gate`) and been stamped with its hub-attested
    origin. Unlike recall telemetry, a finding is the durable record a
    persistent-memory adapter ingests, so it is committed at ``FULL`` durability
    (``durable=True``): it must survive an OS crash, like the lease path. It is
    not coordination state, so :func:`~synapse_channel.core.journal_replay.replay` skips it when
    rebuilding the hub's
    working state.

    Parameters
    ----------
    store : EventStore
        The event log to append to.
    record : dict[str, Any]
        The serialised, gate-admitted, hub-attested finding (see
        :meth:`synapse_channel.core.finding.Finding.as_dict`).
    """
    store.append(EventKind.FINDING, record, durable=True)


def record_idempotency(store: EventStore, key: str, response: dict[str, Any]) -> None:
    """Append a durable record of an idempotency key and the response it replays.

    The idempotency cache makes a retried mutation a no-op by replaying the
    original response; held only in memory it is lost on a hub restart, so a
    reconnecting agent's retry would re-apply the mutation. Journalling the
    ``key``/``response`` pair lets :func:`~synapse_channel.core.journal_replay.replay` rebuild the
    cache, so the
    at-most-once guarantee survives a restart. It is committed at ``FULL``
    durability to match the lease mutations it protects: a guard weaker than the
    mutation would let a retry re-apply after an OS crash.

    Parameters
    ----------
    store : EventStore
        The event log to append to.
    key : str
        The client-supplied idempotency key.
    response : dict[str, Any]
        The response message to replay for a future duplicate of ``key``.
    """
    store.append(EventKind.IDEMPOTENCY, {"key": key, "response": response}, durable=True)
