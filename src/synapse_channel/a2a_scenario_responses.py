# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A structured Message/Artifact scenario responses
"""Pure builders for A2A direct Message and structured Artifact send responses.

The official A2A TCK residual probes (HTTP+JSON MUST profile, commit
``5996b79``) identify scenarios through ``messageId`` prefixes of the form
``tck-<scenario>-<session>``. Operators and in-repo tests may also request a
scenario via ``message.metadata.a2aScenario`` /
``message.metadata.synapseScenario`` or ``configuration.a2aScenario``.

These helpers are deliberately free of I/O so unit tests and the production
``A2ABridge.send_message`` path share one encoding.
"""

from __future__ import annotations

import base64
import re
import uuid
from collections.abc import Mapping
from enum import Enum
from typing import Any

from synapse_channel.a2a import JsonMap

# Official TCK residual names used in messageId prefixes (tck-<name>-session).
_TCK_MESSAGE_ID = re.compile(
    r"^tck-(?P<name>artifact-text|artifact-file-url|artifact-file|artifact-data|"
    r"message-response)(?:-|$)"
)

# Explicit metadata / configuration keys (stable for non-TCK callers).
_SCENARIO_KEYS = ("a2aScenario", "synapseScenario", "scenario")

# Content fixed by the official TCK assertions (DM-ART-001 / DM-MSG-001).
TCK_TEXT_ARTIFACT_BODY = "Generated text content"
"""Exact text the TCK expects in a text-part artifact."""

TCK_DIRECT_MESSAGE_BODY = "Direct message response"
"""Exact text the TCK expects for a direct Message send response."""

TCK_FILE_NAME = "output.txt"
"""Exact filename the TCK expects on file artifacts."""

TCK_FILE_MEDIA_TYPE = "text/plain"
"""Exact media type the TCK expects on file artifacts."""

TCK_FILE_URL = "https://example.test/tck/output.txt"
"""Stable HTTPS URL used for file-URL artifact parts."""

TCK_DATA_ARTIFACT_BODY: dict[str, Any] = {"key": "value", "count": 42}
"""Exact structured data the TCK expects in a data-part artifact."""

TCK_FILE_RAW_BYTES = b"Generated file content"
"""Raw file payload encoded as base64 for raw file-part artifacts."""


class ScenarioKind(str, Enum):
    """Named send-response profiles the bridge can answer immediately."""

    ARTIFACT_TEXT = "artifact-text"
    ARTIFACT_FILE = "artifact-file"
    ARTIFACT_FILE_URL = "artifact-file-url"
    ARTIFACT_DATA = "artifact-data"
    MESSAGE_RESPONSE = "message-response"


_NAME_TO_KIND: dict[str, ScenarioKind] = {kind.value: kind for kind in ScenarioKind}


def resolve_scenario(
    message: Mapping[str, Any],
    configuration: Mapping[str, Any] | None = None,
) -> ScenarioKind | None:
    """Return a structured-response scenario for one send payload, if any.

    Resolution order:

    1. ``message.metadata`` keys ``a2aScenario`` / ``synapseScenario`` / ``scenario``
    2. ``configuration`` keys with the same names
    3. Official TCK ``messageId`` prefix ``tck-<name>-…``

    Parameters
    ----------
    message : Mapping[str, Any]
        A2A user message object from ``message:send``.
    configuration : Mapping[str, Any] or None, optional
        Optional send-time configuration object.

    Returns
    -------
    ScenarioKind or None
        Matched scenario, or ``None`` when the bridge should use the default
        asynchronous Task / SYNAPSE-forward path.
    """
    metadata = message.get("metadata")
    if isinstance(metadata, Mapping):
        kind = _kind_from_mapping(metadata)
        if kind is not None:
            return kind
    if isinstance(configuration, Mapping):
        kind = _kind_from_mapping(configuration)
        if kind is not None:
            return kind
    message_id = str(message.get("messageId") or "")
    match = _TCK_MESSAGE_ID.match(message_id)
    if match is None:
        return None
    return _NAME_TO_KIND.get(match.group("name"))


def build_direct_message_response(message: Mapping[str, Any]) -> JsonMap:
    """Build a SendMessageResponse that is a direct Message (not a Task).

    Parameters
    ----------
    message : Mapping[str, Any]
        Original user message (used for context/task id binding when present).

    Returns
    -------
    dict[str, Any]
        ``{"message": <Message>}`` with the TCK-expected text part.
    """
    agent_message: JsonMap = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_AGENT",
        "parts": [{"text": TCK_DIRECT_MESSAGE_BODY, "mediaType": "text/plain"}],
    }
    context_id = message.get("contextId")
    if context_id is not None:
        agent_message["contextId"] = str(context_id)
    task_id = message.get("taskId")
    if task_id is not None:
        agent_message["taskId"] = str(task_id)
    return {"message": agent_message}


def build_scenario_artifact(kind: ScenarioKind) -> JsonMap:
    """Build one Artifact matching the official TCK residual for ``kind``.

    Parameters
    ----------
    kind : ScenarioKind
        Artifact scenario (not ``MESSAGE_RESPONSE``).

    Returns
    -------
    dict[str, Any]
        A2A Artifact with ``artifactId`` and the expected part shape.

    Raises
    ------
    ValueError
        If ``kind`` is not an artifact scenario.
    """
    if kind is ScenarioKind.MESSAGE_RESPONSE:
        raise ValueError("MESSAGE_RESPONSE does not produce an artifact")
    if kind is ScenarioKind.ARTIFACT_TEXT:
        parts: list[JsonMap] = [
            {"text": TCK_TEXT_ARTIFACT_BODY, "mediaType": "text/plain"},
        ]
        name = "TCK text artifact"
    elif kind is ScenarioKind.ARTIFACT_FILE:
        parts = [
            {
                "raw": base64.b64encode(TCK_FILE_RAW_BYTES).decode("ascii"),
                "filename": TCK_FILE_NAME,
                "mediaType": TCK_FILE_MEDIA_TYPE,
            }
        ]
        name = "TCK raw file artifact"
    elif kind is ScenarioKind.ARTIFACT_FILE_URL:
        parts = [
            {
                "url": TCK_FILE_URL,
                "filename": TCK_FILE_NAME,
                "mediaType": TCK_FILE_MEDIA_TYPE,
            }
        ]
        name = "TCK file URL artifact"
    elif kind is ScenarioKind.ARTIFACT_DATA:
        parts = [{"data": dict(TCK_DATA_ARTIFACT_BODY)}]
        name = "TCK data artifact"
    else:  # pragma: no cover - exhaustive enum
        raise ValueError(f"unsupported scenario: {kind!r}")
    return {
        "artifactId": f"tck-{kind.value}-{uuid.uuid4().hex[:12]}",
        "name": name,
        "description": f"Immediate structured response for scenario {kind.value}",
        "parts": parts,
    }


def build_completed_task_with_artifact(
    message: Mapping[str, Any],
    *,
    kind: ScenarioKind,
    task_id: str,
    context_id: str,
    target: str,
    now: float,
) -> JsonMap:
    """Build a completed Task carrying the structured artifact for ``kind``.

    Parameters
    ----------
    message : Mapping[str, Any]
        Original user message retained in history.
    kind : ScenarioKind
        Artifact scenario.
    task_id, context_id : str
        Task identifiers.
    target : str
        SYNAPSE target recorded in metadata (not contacted for scenarios).
    now : float
        Unix timestamp for created/updated metadata.

    Returns
    -------
    dict[str, Any]
        Completed internal task record ready for store projection.
    """
    artifact = build_scenario_artifact(kind)
    agent_message: JsonMap = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_AGENT",
        "parts": [
            {
                "text": f"Scenario {kind.value} completed with structured artifact.",
                "mediaType": "text/plain",
            }
        ],
    }
    history_message = dict(message)
    history_message.setdefault("messageId", str(uuid.uuid4()))
    history_message.setdefault("role", "ROLE_USER")
    history_message["taskId"] = task_id
    history_message["contextId"] = context_id
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": "TASK_STATE_COMPLETED",
            "message": agent_message,
        },
        "history": [history_message, agent_message],
        "artifacts": [artifact],
        "metadata": {
            "synapseTarget": target,
            "a2aTaskId": task_id,
            "a2aContextId": context_id,
            "a2aScenario": kind.value,
            "createdAt": now,
            "updatedAt": now,
        },
    }


def _kind_from_mapping(mapping: Mapping[str, Any]) -> ScenarioKind | None:
    """Resolve a scenario name from an explicit metadata/configuration mapping."""
    for key in _SCENARIO_KEYS:
        raw = mapping.get(key)
        if raw is None:
            continue
        name = str(raw).strip().lower().replace("_", "-")
        if name in _NAME_TO_KIND:
            return _NAME_TO_KIND[name]
    return None
