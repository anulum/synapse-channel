# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A task construction and continuation helpers
"""Keep A2A task-shape mechanics outside the bridge orchestrator."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_errors import A2AValidationError


def render_message_text(message: Mapping[str, Any]) -> str:
    """Render supported A2A message parts into SYNAPSE chat text."""
    rendered: list[str] = []
    for part in message.get("parts", []):
        if not isinstance(part, dict):
            continue
        if "text" in part:
            rendered.append(str(part["text"]))
        elif "data" in part:
            rendered.append(json.dumps(part["data"], sort_keys=True))
        elif "url" in part:
            rendered.append(str(part["url"]))
        elif isinstance(part.get("file"), dict):
            file_part = part["file"]
            file_bits = [
                str(file_part[value])
                for value in ("name", "mimeType", "uri")
                if file_part.get(value)
            ]
            if file_bits:
                rendered.append(f"[file: {'; '.join(file_bits)}]")
        elif "raw" in part:
            rendered.append("[raw omitted]")
    return "\n".join(text for text in rendered if text).strip()


def resolve_target(message: Mapping[str, Any], *, default: str) -> str:
    """Resolve a message-level SYNAPSE target or return ``default``."""
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        target = metadata.get("target") or metadata.get("synapseTarget")
        if target:
            return str(target)
    return default


def build_working_task(
    message: JsonMap,
    *,
    task_id: str,
    context_id: str,
    target: str,
    now: float,
) -> JsonMap:
    """Build one new internal task record before it is forwarded."""
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": "TASK_STATE_SUBMITTED",
            "message": user_status_message(message),
        },
        "history": [message],
        "artifacts": [],
        "metadata": {
            "synapseTarget": target,
            "a2aTaskId": task_id,
            "a2aContextId": context_id,
            "createdAt": now,
            "updatedAt": now,
        },
    }


def prepare_continuation(task: JsonMap, message: JsonMap) -> JsonMap:
    """Return a history message bound to an existing task and context."""
    task_id = str(task.get("id") or "")
    context_id = str(task.get("contextId") or "")
    requested_context = message.get("contextId")
    if requested_context is not None and str(requested_context) != context_id:
        raise A2AValidationError("message.contextId does not match message.taskId")
    continued = dict(message)
    continued["taskId"] = task_id
    continued["contextId"] = context_id
    return continued


def user_status_message(message: Mapping[str, Any]) -> JsonMap:
    """Build the user message carried by a task status."""
    return {
        "messageId": str(message.get("messageId") or uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": list(message.get("parts", [])),
    }


def stored_task_target(task: Mapping[str, Any], *, default: str) -> str:
    """Return the original SYNAPSE target stored on ``task``."""
    metadata = task.get("metadata")
    if isinstance(metadata, dict):
        target = metadata.get("synapseTarget")
        if target:
            return str(target)
    return default
