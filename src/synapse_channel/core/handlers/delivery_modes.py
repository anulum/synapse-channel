# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — version-three session-bound delivery handlers
"""Route durable delivery intents without promoting transport to task outcome."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import DELIVERY_CONTROL, WOULD_ALLOW, Target, evaluate_access
from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.delivery_modes import (
    DeliveryIntent,
    DeliveryRefusal,
    DeliveryStage,
    parse_delivery_intent,
    select_delivery_mode,
)
from synapse_channel.core.delivery_persistence import DeliveryPersistence, StoredDelivery
from synapse_channel.core.hub_address import HUB_ADDRESS_SEPARATOR
from synapse_channel.core.lifecycle import TaskStatus
from synapse_channel.core.message_forward_origin import (
    forward_delivery_followup,
    forward_delivery_request,
)
from synapse_channel.core.protocol import MIN_DELIVERY_PROTOCOL_VERSION, MessageType

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import AclPolicy
    from synapse_channel.core.auth import TokenAuthenticator
    from synapse_channel.core.message_forward_origin import ForwardOriginContext

    class DeliveryModesContext(ForwardOriginContext, Protocol):
        """Capabilities consumed by delivery modes handlers and their callees."""

        @property
        def acl_policy(self) -> AclPolicy | None:
            """Return the acl policy used by this handler family."""
            ...

        @property
        def authenticator(self) -> TokenAuthenticator | None:
            """Return the authenticator used by this handler family."""
            ...

        @property
        def stable_delivery_hub_id(self) -> str | None:
            """Return the stable delivery hub id used by this handler family."""
            ...


logger = logging.getLogger("synapse.delivery")


def _utf8_size(value: str) -> int | None:
    """Return the encoded size, or reject an unpaired Unicode surrogate."""
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _correlation(data: dict[str, Any]) -> str:
    """Echo only a bounded printable request id in a private refusal."""
    value = data.get("request_id")
    if not isinstance(value, str) or (size := _utf8_size(value)) is None or size > 128:
        return ""
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return ""
    return value


async def _refuse(
    hub: DeliveryModesContext,
    websocket: Any,
    sender: str,
    data: dict[str, Any],
    refusal: DeliveryRefusal,
) -> None:
    """Send one stable reason code without echoing the request body or secrets."""
    await hub.send_json(
        websocket,
        hub.system(
            str(refusal),
            msg_type=MessageType.DELIVERY_REFUSED,
            target=sender,
            request_id=_correlation(data),
            reason_code=refusal.code,
            protocol_version=MIN_DELIVERY_PROTOCOL_VERSION,
        ),
    )


def _require_profile(hub: DeliveryModesContext, sender: str, data: dict[str, Any]) -> None:
    """Require a durable stable hub and a version-three registered peer."""
    if hub.clients.protocol_version_of(sender) < MIN_DELIVERY_PROTOCOL_VERSION:
        raise DeliveryRefusal("unsupported_protocol", "peer did not negotiate delivery version 3")
    require_delivery_profile(hub, data)


def require_delivery_profile(
    hub: DeliveryModesContext, data: dict[str, Any]
) -> DeliveryPersistence:
    """Require a version-three frame and a durable hub with a stable id.

    The connection half of the profile (the sender negotiated version three) is checked by
    the agent-facing handlers; a request forwarded by a peer hub arrives on the peer's
    connection and is checked here alone.

    Parameters
    ----------
    hub : DeliveryModesContext
        The hub asked to act on the delivery.
    data : dict[str, Any]
        The delivery frame.

    Returns
    -------
    DeliveryPersistence
        The hub's durable delivery store.

    Raises
    ------
    DeliveryRefusal
        ``unsupported_protocol`` for a frame that is not version three;
        ``unsupported_profile`` for a hub without a durable journal and stable id.
    """
    if data.get("protocol_version") != MIN_DELIVERY_PROTOCOL_VERSION or isinstance(
        data.get("protocol_version"), bool
    ):
        raise DeliveryRefusal("unsupported_protocol", "peer did not negotiate delivery version 3")
    if hub.journal is None or not hub.stable_delivery_hub_id:
        raise DeliveryRefusal("unsupported_profile", "delivery requires a durable stable hub")
    return hub.journal.delivery


def _ledger(hub: DeliveryModesContext) -> DeliveryPersistence:
    """Return the durable delivery store or a typed fail-closed refusal."""
    if hub.journal is None:
        raise DeliveryRefusal("unsupported_profile", "delivery requires a durable hub")
    return hub.journal.delivery


def _control_authorized(hub: DeliveryModesContext, intent: DeliveryIntent) -> bool:
    """Check always-on ACL and a live exact recipient claim for steer/interrupt."""
    if hub.authenticator is None or hub.acl_policy is None or not intent.task_id:
        return False
    claim = hub.state.claims.get(intent.task_id)
    if (
        claim is None
        or claim.owner != intent.target
        or claim.status != TaskStatus.CLAIMED
        or claim.lease_expires_at <= time.time()
    ):
        return False
    decision = evaluate_access(
        subject=intent.sender,
        project=project_of(intent.sender),
        permission=DELIVERY_CONTROL,
        target=Target("agent", intent.target),
        policy=hub.acl_policy,
    )
    return decision.decision == WOULD_ALLOW


def _status(
    hub: DeliveryModesContext, record: StoredDelivery, *, viewer: str | None = None
) -> dict[str, Any]:
    """Describe receiver, session, message and task stages as separate facts."""
    target = record.request["target"]
    incarnation = record.request["target_incarnation"]
    session = hub.clients.delivery_session(target)
    active_session = session is not None and session.incarnation == incarnation
    receiver_reachable = active_session and target in hub.clients.agent_sockets
    return hub.system(
        "Delivery state.",
        msg_type=MessageType.DELIVERY_STATUS,
        target=viewer or record.sender,
        operation_key=record.operation_key,
        request_id=record.request["request_id"],
        task_id=record.request["task_id"],
        selected_mode=record.selected_mode,
        quality=record.quality,
        stage=record.stage,
        cancel_requested=record.cancel_requested,
        receiver_reachable=receiver_reachable,
        active_session=active_session,
        boundary_delivered=record.boundary_delivered,
        explicitly_acknowledged=record.explicitly_acknowledged,
        task_completed=record.stage == "completed",
        latest_event_seq=record.latest_event_seq,
        protocol_version=MIN_DELIVERY_PROTOCOL_VERSION,
    )


def _offer(intent: DeliveryIntent, selected_mode: str, quality: str) -> dict[str, Any]:
    """Build the exact recipient frame retained in the durable notification row."""
    key = intent.operation_key
    return {
        "type": MessageType.DELIVERY_OFFER,
        "sender": intent.sender,
        "origin_hub": intent.origin_hub,
        "target": intent.target,
        "target_incarnation": intent.target_incarnation,
        "operation_key": key,
        "notification_id": f"delivery:{key}:1",
        "request_id": intent.request_id,
        "task_id": intent.task_id,
        "requested_mode": intent.mode,
        "selected_mode": selected_mode,
        "quality": quality,
        "deadline": intent.deadline,
        "body": intent.body,
        "protocol_version": MIN_DELIVERY_PROTOCOL_VERSION,
    }


async def _publish_queued_offer(hub: DeliveryModesContext, record: StoredDelivery) -> None:
    """Retry one stable offer only to its exact, unexpired recipient session."""
    if record.stage != "queued" or record.request["deadline"] <= time.time():
        return
    target = record.request["target"]
    session = hub.clients.delivery_session(target)
    websocket = hub.clients.agent_sockets.get(target)
    if (
        session is None
        or websocket is None
        or session.incarnation != record.request["target_incarnation"]
    ):
        return
    ledger = _ledger(hub)
    notification_id = f"delivery:{record.operation_key}:1"
    frame = ledger.notification(notification_id)
    if frame is not None:
        await hub.send_json(websocket, frame)
        ledger.mark_notification_delivered(notification_id)


async def handle_delivery_request(
    hub: DeliveryModesContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Admit, deduplicate and offer one session-bound intent through the real hub.

    A target of the form ``PROJECT/seat@HUB_ID`` is forwarded to that message peer by
    :func:`~synapse_channel.core.message_forward_origin.forward_delivery_request`.
    """
    try:
        _require_profile(hub, sender, data)
        if HUB_ADDRESS_SEPARATOR in str(data.get("target") or ""):
            await hub.send_json(websocket, await forward_delivery_request(hub, sender, data))
            return
        admission = await admit_delivery_request(
            hub, sender=sender, origin_hub=hub.hub_id, data=data
        )
        await hub.send_json(websocket, admission.status)
        if admission.expire_now:
            await expire_due_deliveries(hub)
    except DeliveryRefusal as refusal:
        await _refuse(hub, websocket, sender, data, refusal)


@dataclass(frozen=True)
class DeliveryAdmission:
    """The outcome of admitting one delivery intent.

    Attributes
    ----------
    status : dict[str, Any]
        The ``delivery_status`` frame for the requester.
    expire_now : bool
        Whether the deadline passed during admission, so due deliveries should be expired
        right after the status is sent.
    """

    status: dict[str, Any]
    expire_now: bool = False


async def admit_delivery_request(
    hub: DeliveryModesContext, *, sender: str, origin_hub: str, data: dict[str, Any]
) -> DeliveryAdmission:
    """Admit, deduplicate and offer one intent to a local recipient session.

    Parameters
    ----------
    hub : DeliveryModesContext
        The hub that hosts the recipient.
    sender : str
        The requester: a local seat, or ``seat@origin_hub`` for a peer's forward.
    origin_hub : str
        The hub the request entered the federation through: this hub's id for a local
        requester, the authenticated peer's id for a forward.
    data : dict[str, Any]
        The version-three request with ``target`` naming a local seat.

    Returns
    -------
    DeliveryAdmission
        The requester's status frame.

    Raises
    ------
    DeliveryRefusal
        For a malformed or conflicting request, an unavailable or changed recipient session,
        an unsupported mode, or task control that is not granted. A forwarded requester is
        never granted ``interrupt`` or ``steer``.
    """
    intent = parse_delivery_intent(data, sender=sender, origin_hub=origin_hub, now=time.time())
    ledger = _ledger(hub)
    previous = ledger.find_by_idempotency(sender, intent.idempotency_key)
    if previous is not None:
        if (
            previous.operation_key != intent.operation_key
            or previous.request_digest != intent.digest
        ):
            raise DeliveryRefusal("id_conflict", "request identity was reused with new content")
        await _publish_queued_offer(hub, previous)
        return DeliveryAdmission(status=_status(hub, previous))
    session = hub.clients.delivery_session(intent.target)
    if session is None:
        raise DeliveryRefusal("unavailable_recipient", "recipient has no active delivery session")
    if session.incarnation != intent.target_incarnation:
        raise DeliveryRefusal("stale_incarnation", "recipient session changed")
    selected_mode, quality = select_delivery_mode(intent, session.capabilities)
    if selected_mode in ("interrupt", "steer") and (
        origin_hub != hub.hub_id or not _control_authorized(hub, intent)
    ):
        raise DeliveryRefusal("unauthorised_requester", "active task control is not granted")
    offer = _offer(intent, selected_mode, quality)
    write = ledger.create(
        intent,
        selected_mode=selected_mode,
        quality=quality,
        offer=offer,
        receiving_hub=hub.hub_id,
    )
    if write.disposition == "conflict":
        raise DeliveryRefusal("id_conflict", "request identity was reused with new content")
    await _publish_queued_offer(hub, write.record)
    return DeliveryAdmission(
        status=_status(hub, write.record), expire_now=intent.deadline <= time.time()
    )


def _operation_key(data: dict[str, Any]) -> str:
    """Read one bounded digest-shaped operation key from an inbound frame."""
    key = data.get("operation_key")
    if not isinstance(key, str) or len(key) != 64:
        raise DeliveryRefusal("invalid_shape", "operation key is malformed")
    if any(char not in "0123456789abcdef" for char in key):
        raise DeliveryRefusal("invalid_shape", "operation key is malformed")
    return key


async def handle_delivery_status_request(
    hub: DeliveryModesContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Return a private status without confusing reachability with completion.

    A key issued by a peer hub for a forwarded request is answered by that peer through
    :func:`~synapse_channel.core.message_forward_origin.forward_delivery_followup`.
    """
    try:
        _require_profile(hub, sender, data)
        key = _operation_key(data)
        if hub.message_forward_ledger.remote_delivery(key) is not None:
            frame = await forward_delivery_followup(hub, sender, data, kind="delivery_status")
            await hub.send_json(websocket, frame)
            return
        await hub.send_json(
            websocket, await delivery_status_frame(hub, requester=sender, data=data)
        )
    except DeliveryRefusal as refusal:
        await _refuse(hub, websocket, sender, data, refusal)


async def delivery_status_frame(
    hub: DeliveryModesContext, *, requester: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Return the requester's private status for one locally admitted delivery.

    Parameters
    ----------
    hub : DeliveryModesContext
        The hub that admitted the delivery.
    requester : str
        The requester; only the delivery's sender or recipient may see it.
    data : dict[str, Any]
        The status request carrying ``operation_key``.

    Returns
    -------
    dict[str, Any]
        A ``delivery_status`` frame; an overdue open delivery is expired first.

    Raises
    ------
    DeliveryRefusal
        For a malformed or unknown key, or a requester that is neither party.
    """
    key = _operation_key(data)
    ledger = _ledger(hub)
    record = ledger.get(key)
    if record is None:
        raise DeliveryRefusal("unknown_request", "delivery request does not exist")
    if requester not in (record.sender, record.request["target"]):
        raise DeliveryRefusal("unauthorised_requester", "request is not visible to sender")
    if record.request["deadline"] <= time.time() and record.stage in (
        "queued",
        "boundary_delivered",
        "acknowledged",
    ):
        await expire_due_deliveries(hub)
        refreshed = ledger.get(key)
        if refreshed is not None:
            record = refreshed
    return _status(hub, record, viewer=requester)


def _mutation_id(data: dict[str, Any]) -> str:
    """Require one printable sender-chosen identity for an idempotent transition."""
    value = data.get("mutation_id")
    if not isinstance(value, str) or not value or (size := _utf8_size(value)) is None or size > 128:
        raise DeliveryRefusal("invalid_shape", "mutation_id is malformed")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise DeliveryRefusal("invalid_shape", "mutation_id is malformed")
    return value


def _evidence(data: dict[str, Any]) -> dict[str, str]:
    """Keep only bounded correlation and executor evidence, never transcript text."""
    raw = data.get("evidence", {})
    allowed = {
        "request_id",
        "task_id",
        "boundary",
        "receipt_id",
        "outcome_code",
        "executor_ref",
        "reason_code",
    }
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise DeliveryRefusal("invalid_shape", "delivery evidence shape is unsupported")
    evidence: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or (size := _utf8_size(value)) is None or size > 128:
            raise DeliveryRefusal("invalid_shape", "delivery evidence field is malformed")
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
            raise DeliveryRefusal("invalid_shape", "delivery evidence field is malformed")
        evidence[key] = value
    return evidence


def _mutation_digest(
    operation_key: str,
    mutation_id: str,
    stage: str,
    evidence: dict[str, str],
) -> str:
    """Hash semantic fields only so transport timestamps cannot defeat dedupe."""
    encoded = json.dumps(
        [operation_key, mutation_id, stage, evidence],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _recipient_record(hub: DeliveryModesContext, sender: str, key: str) -> StoredDelivery:
    """Bind a transition to the exact target identity and current incarnation."""
    record = _ledger(hub).get(key)
    if record is None:
        raise DeliveryRefusal("unknown_request", "delivery request does not exist")
    if record.request["target"] != sender:
        raise DeliveryRefusal("unauthorised_requester", "sender is not the recipient")
    session = hub.clients.delivery_session(sender)
    if session is None or session.incarnation != record.request["target_incarnation"]:
        raise DeliveryRefusal("stale_incarnation", "recipient session changed")
    return record


def _outcome_stage(data: dict[str, Any]) -> DeliveryStage:
    """Narrow an executor outcome to a supported explicit terminal stage."""
    raw = data.get("stage")
    if raw == "completed":
        return "completed"
    if raw == "failed":
        return "failed"
    if raw == "rejected":
        return "rejected"
    if raw == "cancelled":
        return "cancelled"
    if raw == "superseded":
        return "superseded"
    raise DeliveryRefusal("invalid_shape", "executor outcome stage is unsupported")


async def _notify_record(hub: DeliveryModesContext, record: StoredDelivery, audience: str) -> None:
    """Publish an already committed stable notification to its live audience."""
    ledger = _ledger(hub)
    notification_id = f"delivery:{record.operation_key}:{record.ordinal}"
    frame = ledger.notification(notification_id)
    websocket = hub.clients.agent_sockets.get(audience)
    if audience == record.request["target"]:
        session = hub.clients.delivery_session(audience)
        if session is None or session.incarnation != record.request["target_incarnation"]:
            return
    if (
        frame is not None
        and websocket is not None
        and hub.clients.protocol_version_of(audience) >= MIN_DELIVERY_PROTOCOL_VERSION
    ):
        await hub.send_json(websocket, frame)
        ledger.mark_notification_delivered(notification_id)


async def expire_due_deliveries(hub: DeliveryModesContext) -> int:
    """Commit elapsed deadlines, including queued requests after hub restart."""
    if hub.journal is None or not hub.stable_delivery_hub_id:
        return 0
    cursor = ""
    expired = 0
    while True:
        page = hub.journal.delivery.due_for_expiry(time.time(), after_key=cursor)
        if not page:
            return expired
        for record in page:
            cursor = record.operation_key
            evidence = {"reason_code": "deadline_elapsed"}
            mutation_id = "hub-deadline-expired"
            try:
                write = hub.journal.delivery.advance(
                    record.operation_key,
                    stage="expired",
                    mutation_id=mutation_id,
                    mutation_digest=_mutation_digest(
                        record.operation_key, mutation_id, "expired", evidence
                    ),
                    actor=hub.hub_id,
                    source="hub",
                    evidence=evidence,
                )
            except DeliveryRefusal as exc:
                logger.error(
                    "delivery expiry refused key=%s reason=%s", record.operation_key, exc.code
                )
                hub.journal.delivery.quarantine(record.operation_key, exc.code)
                continue
            if write.disposition == "inserted":
                expired += 1
                await _notify_record(hub, write.record, write.record.sender)


async def supersede_old_delivery_sessions(
    hub: DeliveryModesContext, *, target: str, incarnation: str
) -> int:
    """Terminally resolve unfinished work for a replaced recipient process."""
    if hub.journal is None or not hub.stable_delivery_hub_id:
        return 0
    cursor = ""
    superseded = 0
    while True:
        page = hub.journal.delivery.open_for_other_incarnations(
            target, incarnation, after_key=cursor
        )
        if not page:
            return superseded
        for record in page:
            cursor = record.operation_key
            evidence = {"reason_code": "recipient_session_replaced"}
            mutation_id = "hub-session-superseded"
            try:
                write = hub.journal.delivery.advance(
                    record.operation_key,
                    stage="superseded",
                    mutation_id=mutation_id,
                    mutation_digest=_mutation_digest(
                        record.operation_key, mutation_id, "superseded", evidence
                    ),
                    actor=hub.hub_id,
                    source="hub",
                    evidence=evidence,
                )
            except DeliveryRefusal as exc:
                logger.error(
                    "delivery supersession refused key=%s reason=%s", record.operation_key, exc.code
                )
                hub.journal.delivery.quarantine(record.operation_key, exc.code)
                continue
            if write.disposition == "inserted":
                superseded += 1
                await _notify_record(hub, write.record, write.record.sender)


async def delivery_expiry_loop(hub: DeliveryModesContext) -> None:
    """Sweep deadlines while the hub serves; cancellation ends with the server."""
    while True:
        await expire_due_deliveries(hub)
        await asyncio.sleep(5)


async def deliver_pending_delivery_notifications(
    hub: DeliveryModesContext, *, sender: str, websocket: Any
) -> None:
    """Replay bounded v3 notifications and exact-incarnation queued offers."""
    if hub.journal is None or hub.clients.protocol_version_of(sender) < 3:
        return
    cursor = 0
    while True:
        page = hub.journal.delivery.pending_notifications(sender, after_rowid=cursor)
        if not page:
            break
        for rowid, notification_id, frame in page:
            cursor = rowid
            if frame.get("type") == MessageType.DELIVERY_OFFER:
                continue
            if frame.get("target") == sender:
                current = hub.clients.delivery_session(sender)
                if current is None or current.incarnation != frame.get("target_incarnation"):
                    continue
            if hub.clients.agent_sockets.get(sender) is not websocket:
                return
            await hub.send_json(websocket, frame)
            hub.journal.delivery.mark_notification_delivered(notification_id)
    session = hub.clients.delivery_session(sender)
    if session is None:
        return
    await expire_due_deliveries(hub)
    after_seq = 0
    while True:
        offers = hub.journal.delivery.pending_for(sender, session.incarnation, after_seq=after_seq)
        if not offers:
            break
        for record in offers:
            after_seq = record.latest_event_seq
            if record.request["deadline"] <= time.time():
                await expire_due_deliveries(hub)
                continue
            if hub.clients.agent_sockets.get(sender) is not websocket:
                return
            offer_frame = hub.journal.delivery.notification(f"delivery:{record.operation_key}:1")
            if offer_frame is not None:
                await hub.send_json(websocket, offer_frame)
                hub.journal.delivery.mark_notification_delivered(offer_frame["notification_id"])


async def handle_delivery_stage(
    hub: DeliveryModesContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Commit a recipient boundary, explicit ACK, or outcome from its live session."""
    try:
        _require_profile(hub, sender, data)
        key = _operation_key(data)
        record = _recipient_record(hub, sender, key)
        if record.request["deadline"] <= time.time() and record.stage in (
            "queued",
            "boundary_delivered",
            "acknowledged",
        ):
            await expire_due_deliveries(hub)
            raise DeliveryRefusal("deadline_expired", "delivery deadline has elapsed")
        if (
            data.get("request_id") != record.request["request_id"]
            or data.get("task_id") != record.request["task_id"]
        ):
            raise DeliveryRefusal("invalid_shape", "stage correlation does not match request")
        msg_type = data.get("type")
        if msg_type == MessageType.DELIVERY_BOUNDARY:
            stage: DeliveryStage = "boundary_delivered"
        elif msg_type == MessageType.DELIVERY_ACK:
            stage = "acknowledged"
        elif msg_type == MessageType.DELIVERY_OUTCOME:
            stage = _outcome_stage(data)
        else:
            raise DeliveryRefusal("invalid_shape", "delivery stage verb is unsupported")
        evidence = _evidence(data)
        if stage == "boundary_delivered" and not evidence.get("boundary"):
            raise DeliveryRefusal("invalid_shape", "boundary evidence is required")
        if stage == "acknowledged" and not evidence.get("receipt_id"):
            raise DeliveryRefusal("invalid_shape", "recipient acknowledgement evidence is required")
        if stage in ("completed", "failed"):
            if not evidence.get("executor_ref") or not evidence.get("outcome_code"):
                raise DeliveryRefusal("invalid_shape", "executor outcome evidence is required")
            if (
                evidence.get("request_id", record.request["request_id"])
                != record.request["request_id"]
            ):
                raise DeliveryRefusal("invalid_shape", "outcome request correlation changed")
            if evidence.get("task_id", record.request["task_id"]) != record.request["task_id"]:
                raise DeliveryRefusal("invalid_shape", "outcome task correlation changed")
            evidence["request_id"] = record.request["request_id"]
            evidence["task_id"] = record.request["task_id"]
        mutation_id = _mutation_id(data)
        digest = _mutation_digest(key, mutation_id, stage, evidence)
        write = _ledger(hub).advance(
            key,
            stage=stage,
            mutation_id=mutation_id,
            mutation_digest=digest,
            actor=sender,
            source="recipient",
            evidence=evidence,
        )
        if write.disposition == "conflict":
            raise DeliveryRefusal("id_conflict", "mutation identity was reused with new content")
        await hub.send_json(websocket, _status(hub, write.record, viewer=sender))
        if write.disposition == "inserted":
            await _notify_record(hub, write.record, write.record.sender)
    except DeliveryRefusal as refusal:
        await _refuse(hub, websocket, sender, data, refusal)


async def handle_delivery_cancel(
    hub: DeliveryModesContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Record a sender cancellation request and notify the responsible executor.

    A key issued by a peer hub for a forwarded request is cancelled on that peer through
    :func:`~synapse_channel.core.message_forward_origin.forward_delivery_followup`.
    """
    try:
        _require_profile(hub, sender, data)
        key = _operation_key(data)
        if hub.message_forward_ledger.remote_delivery(key) is not None:
            frame = await forward_delivery_followup(hub, sender, data, kind="delivery_cancel")
            await hub.send_json(websocket, frame)
            return
        await hub.send_json(websocket, await cancel_delivery(hub, requester=sender, data=data))
    except DeliveryRefusal as refusal:
        await _refuse(hub, websocket, sender, data, refusal)


async def cancel_delivery(
    hub: DeliveryModesContext, *, requester: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Record the sender's cancellation of one locally admitted delivery.

    Parameters
    ----------
    hub : DeliveryModesContext
        The hub that admitted the delivery.
    requester : str
        The requester; only the delivery's sender may cancel it.
    data : dict[str, Any]
        The cancel request carrying ``operation_key`` and ``mutation_id``.

    Returns
    -------
    dict[str, Any]
        The requester's ``delivery_status`` frame after the cancellation request. A newly
        recorded request is also offered to the recipient.

    Raises
    ------
    DeliveryRefusal
        For a malformed or unknown key, a requester that is not the sender, or a reused
        mutation id with different content.
    """
    key = _operation_key(data)
    ledger = _ledger(hub)
    record = ledger.get(key)
    if record is None:
        raise DeliveryRefusal("unknown_request", "delivery request does not exist")
    if record.sender != requester:
        raise DeliveryRefusal("unauthorised_requester", "only sender can request cancellation")
    mutation_id = _mutation_id(data)
    digest = _mutation_digest(key, mutation_id, "cancel_requested", {})
    write = ledger.request_cancel(
        key, mutation_id=mutation_id, mutation_digest=digest, actor=requester
    )
    if write.disposition == "conflict":
        raise DeliveryRefusal("id_conflict", "mutation identity was reused with new content")
    if write.disposition == "inserted":
        await _notify_record(hub, write.record, write.record.request["target"])
    return _status(hub, write.record, viewer=requester)
