# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound capability helpers
"""Outbound capability-card helpers for the reusable client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.capability import normalized_capability_card
from synapse_channel.core.capability_card_signing import sign_capability_card
from synapse_channel.core.protocol import MessageType

__all__ = ["AgentCapabilityMixin"]


class AgentCapabilityMixin:
    """Send capability advertisement envelopes."""

    async def advertise(
        self: _OutboundAgent,
        *,
        description: str = "",
        skills: tuple[str, ...] | list[str] = (),
        task_classes: tuple[str, ...] | list[str] = (),
        model: str = "",
        contracts: tuple[Mapping[str, Any], ...]
        | list[Mapping[str, Any]]
        | Mapping[str, Mapping[str, Any]]
        | None = None,
        meta: dict[str, Any] | None = None,
        manifest_digest: str = "",
    ) -> None:
        """Advertise this agent's capability card to the hub.

        Parameters
        ----------
        description : str, optional
            Human-readable capability summary.
        skills : tuple[str, ...] or list[str], optional
            Free-form skill tags.
        task_classes : tuple[str, ...] or list[str], optional
            Routing classes this agent can serve.
        model : str, optional
            Backing model or runtime label.
        contracts : tuple, list, mapping, or None, optional
            Declarative task-class contracts. Lists are forwarded as JSON arrays;
            mappings may either be one contract or a task-class keyed mapping.
        meta : dict[str, Any] or None, optional
            Additional descriptive metadata.
        manifest_digest : str, optional
            Digest of the package/tool manifest this advertisement describes.
        """
        if self._capability_card_key is None:
            extra: dict[str, Any] = {}
            if description:
                extra["description"] = description
            if skills:
                extra["skills"] = list(skills)
            if task_classes:
                extra["task_classes"] = list(task_classes)
            if model:
                extra["model"] = model
            if contracts:
                extra["contracts"] = (
                    dict(contracts) if isinstance(contracts, Mapping) else list(contracts)
                )
            if meta:
                extra["meta"] = meta
            if manifest_digest:
                extra["manifest_digest"] = manifest_digest
            await self.send_message(MessageType.ADVERTISE, target="System", **extra)
            return

        card = normalized_capability_card(
            self.name,
            description=description,
            skills=skills,
            task_classes=task_classes,
            model=model,
            project=self._capability_card_project,
            manifest_digest=manifest_digest,
            contracts=contracts,
            meta=meta,
        )
        self._capability_card_sequence += 1
        card = sign_capability_card(
            card,
            key_id=self._capability_card_key_id,
            private_key=self._capability_card_key,
            sequence=self._capability_card_sequence,
            lifetime_seconds=self._capability_card_lifetime_seconds,
        )
        extra = {
            key: card[key]
            for key in (
                "description",
                "skills",
                "task_classes",
                "model",
                "project",
                "manifest_digest",
                "contracts",
                "meta",
                "signature",
            )
            if card.get(key)
        }
        await self.send_message(MessageType.ADVERTISE, target="System", **extra)
