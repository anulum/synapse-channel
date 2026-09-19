# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — explicit version-three delivery client methods
"""Send negotiated delivery intents without silently degrading on old hubs."""

from __future__ import annotations

import time
from typing import Any, Protocol

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.delivery_modes import DeliveryRefusal, parse_delivery_intent
from synapse_channel.core.protocol import MIN_DELIVERY_PROTOCOL_VERSION, MessageType


class _DeliveryAgent(_OutboundAgent, Protocol):
    """Connected agent fields needed to validate a version-three request."""

    hub_id: str

    def _require_delivery_hub(self) -> None:
        """Refuse a disconnected or older hub before emitting a v3 verb."""

    async def _send_delivery_message(self, msg_type: str, **extra: Any) -> None:
        """Send one v3 frame or report a lost connection to its caller."""


class AgentDeliveryMixin:
    """Typed entrypoints for requests, status, cancellation and recipient evidence."""

    def _require_delivery_hub(self: _DeliveryAgent) -> None:
        if self.connection is None or self.hub_protocol_version is None:
            raise DeliveryRefusal("unavailable_hub", "delivery requires a connected hub")
        if self.hub_protocol_version < MIN_DELIVERY_PROTOCOL_VERSION:
            raise DeliveryRefusal("unsupported_protocol", "hub does not support delivery v3")
        if self.hub_id in ("", "unknown"):
            raise DeliveryRefusal("unsupported_profile", "hub has no stable identity")

    async def _send_delivery_message(self: _DeliveryAgent, msg_type: str, **extra: Any) -> None:
        """Never interpret a disconnected legacy no-op send as delivery."""
        self._require_delivery_hub()
        connection = self.connection
        if connection is None:
            raise ConnectionError("delivery connection closed before send")
        await self.send_message(msg_type, **extra)
        if self.connection is not connection:
            raise ConnectionError("delivery connection changed during send")

    async def request_delivery(
        self: _DeliveryAgent,
        *,
        target: str,
        target_incarnation: str,
        mode: str,
        body: str,
        deadline: float,
        request_id: str,
        idempotency_key: str,
        task_id: str = "",
        allowed_fallbacks: tuple[str, ...] = (),
    ) -> str:
        """Submit one exact-session intent and return its deterministic operation key.

        Read ``target_incarnation`` and capabilities from a v3 WHO snapshot.
        A retry must reuse every semantic field and both caller-chosen ids.
        The response arrives through the normal inbound callback.
        """
        self._require_delivery_hub()
        frame: dict[str, Any] = {
            "protocol_version": MIN_DELIVERY_PROTOCOL_VERSION,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "target": target,
            "target_incarnation": target_incarnation,
            "mode": mode,
            "allowed_fallbacks": list(allowed_fallbacks),
            "task_id": task_id,
            "body": body,
            "deadline": deadline,
        }
        intent = parse_delivery_intent(
            frame, sender=self.name, origin_hub=self.hub_id, now=time.time()
        )
        await self._send_delivery_message(MessageType.DELIVERY_REQUEST, **frame)
        return intent.operation_key

    async def request_delivery_status(self: _DeliveryAgent, operation_key: str) -> None:
        """Query the durable stage; the callback receives a delivery_status frame."""
        self._require_delivery_hub()
        await self._send_delivery_message(
            MessageType.DELIVERY_STATUS_REQUEST,
            target="System",
            protocol_version=MIN_DELIVERY_PROTOCOL_VERSION,
            operation_key=operation_key,
        )

    async def cancel_delivery(
        self: _DeliveryAgent, operation_key: str, *, mutation_id: str
    ) -> None:
        """Request cancellation without claiming the executor has stopped."""
        self._require_delivery_hub()
        await self._send_delivery_message(
            MessageType.DELIVERY_CANCEL,
            target="System",
            protocol_version=MIN_DELIVERY_PROTOCOL_VERSION,
            operation_key=operation_key,
            mutation_id=mutation_id,
        )

    async def report_delivery_stage(
        self: _DeliveryAgent,
        operation_key: str,
        *,
        request_id: str,
        task_id: str,
        mutation_id: str,
        stage: str,
        evidence: dict[str, str],
    ) -> None:
        """Report a boundary, explicit ACK or executor outcome with correlation."""
        self._require_delivery_hub()
        if stage == "boundary_delivered":
            msg_type = MessageType.DELIVERY_BOUNDARY
        elif stage == "acknowledged":
            msg_type = MessageType.DELIVERY_ACK
        elif stage in ("completed", "failed", "rejected", "cancelled", "superseded"):
            msg_type = MessageType.DELIVERY_OUTCOME
        else:
            raise DeliveryRefusal("invalid_shape", "unsupported recipient stage")
        await self._send_delivery_message(
            msg_type,
            target="System",
            protocol_version=MIN_DELIVERY_PROTOCOL_VERSION,
            operation_key=operation_key,
            request_id=request_id,
            task_id=task_id,
            mutation_id=mutation_id,
            stage=stage,
            evidence=evidence,
        )
