# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound task mutation helpers
"""Outbound task mutation helpers for the reusable client."""

from __future__ import annotations

from typing import Any

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.receipts import build_release_receipt

__all__ = ["AgentTaskMutationMixin"]


class AgentTaskMutationMixin:
    """Send task lease, lifecycle, checkpoint, handoff, and wait envelopes."""

    async def claim(
        self: _OutboundAgent,
        task_id: str,
        note: str = "",
        ttl_seconds: float | None = None,
        *,
        worktree: str = "",
        paths: tuple[str, ...] | list[str] = (),
        path_identity: dict[str, object] | None = None,
        idem_key: str | None = None,
        git: dict[str, Any] | None = None,
    ) -> None:
        """Request a task lease with optional display and canonical path scopes.

        ``paths`` remain human-readable wire values. Git-aware callers may add
        the aligned, versioned ``path_identity`` returned by the local resolver;
        callers that omit it retain legacy literal-path comparison. The hub
        validates supplied identity data before mutating task state.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "note": note}
        if ttl_seconds is not None:
            extra["ttl_seconds"] = float(ttl_seconds)
        if worktree:
            extra["worktree"] = worktree
        if paths:
            extra["paths"] = list(paths)
        if path_identity is not None:
            extra["path_identity"] = path_identity
        if idem_key:
            extra["idem_key"] = idem_key
        if git:
            extra["git"] = git
        await self.send_message(
            MessageType.CLAIM, target="System", payload=task_id.strip(), **extra
        )

    async def release(
        self: _OutboundAgent,
        task_id: str,
        *,
        epoch: int | None = None,
        idem_key: str | None = None,
        evidence: tuple[str, ...] | list[str] = (),
        artifacts: tuple[str, ...] | list[str] = (),
        known_failures: tuple[str, ...] | list[str] = (),
        changed_files: tuple[str, ...] | list[str] = (),
        generated_artifacts: tuple[str, ...] | list[str] = (),
        approvals: tuple[str, ...] | list[str] = (),
        confidence: str = "",
        freshness_seconds: float | None = None,
    ) -> None:
        """Release a task lease, optionally attaching closeout evidence."""
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        receipt = build_release_receipt(
            task_id=task_id,
            owner=self.name,
            evidence=evidence,
            artifacts=artifacts,
            known_failures=known_failures,
            changed_files=changed_files,
            generated_artifacts=generated_artifacts,
            approvals=approvals,
            confidence=confidence,
            freshness_seconds=freshness_seconds,
        )
        for key in (
            "evidence",
            "artifacts",
            "known_failures",
            "changed_files",
            "generated_artifacts",
            "approvals",
        ):
            if receipt[key]:
                extra[key] = receipt[key]
        if receipt.get("confidence"):
            extra["confidence"] = receipt["confidence"]
        if "freshness_seconds" in receipt:
            extra["freshness_seconds"] = receipt["freshness_seconds"]
        await self.send_message(
            MessageType.RELEASE, target="System", payload=task_id.strip(), **extra
        )

    async def update_task(
        self: _OutboundAgent,
        task_id: str,
        *,
        status: str | None = None,
        note: str | None = None,
        data_ref: str | None = None,
        epoch: int | None = None,
        expected_version: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Update an owned task's status, note, or artefact reference."""
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if status is not None:
            extra["status"] = status
        if note is not None:
            extra["note"] = note
        if data_ref is not None:
            extra["data_ref"] = data_ref
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if expected_version is not None:
            extra["expected_version"] = int(expected_version)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(
            MessageType.TASK_UPDATE, target="System", payload=task_id.strip(), **extra
        )

    async def handoff(
        self: _OutboundAgent,
        task_id: str,
        to_agent: str,
        *,
        note: str | None = None,
        epoch: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Hand an owned task to another agent in one atomic step."""
        extra: dict[str, Any] = {"task_id": task_id.strip(), "to_agent": to_agent.strip()}
        if note is not None:
            extra["note"] = note
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(
            MessageType.HANDOFF, target="System", payload=task_id.strip(), **extra
        )

    async def save_checkpoint(
        self: _OutboundAgent,
        task_id: str,
        checkpoint: str,
        *,
        epoch: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Save a resume checkpoint on an owned task."""
        extra: dict[str, Any] = {"task_id": task_id.strip(), "checkpoint": checkpoint}
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(MessageType.CHECKPOINT, target="System", **extra)

    async def request_wait(self: _OutboundAgent, task_id: str) -> None:
        """Register an advisory wait for a task another agent holds."""
        await self.send_message(
            MessageType.WAIT_REQUEST,
            target="System",
            payload=task_id.strip(),
            task_id=task_id.strip(),
        )
