# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A Agent Card projection for the SYNAPSE capability manifest
"""Agent2Agent (A2A) discovery projection for SYNAPSE.

The A2A bridge starts at discovery: an A2A client needs an Agent Card before it
can decide whether and how to call a peer. SYNAPSE already has live capability
cards, so this module maps the hub manifest into one A2A Agent Card without
adding any server dependency to the local-first hub.
"""

from __future__ import annotations

import re
from typing import Any

from synapse_channel import __version__

A2A_PROTOCOL_VERSION = "1.0"
"""A2A protocol version emitted by this projection."""

DEFAULT_INPUT_MODES = ("text/plain", "application/json")
"""Default input media types for the bridge card."""

DEFAULT_OUTPUT_MODES = ("text/plain", "application/json")
"""Default output media types for the bridge card."""

DEFAULT_DESCRIPTION = (
    "Local-first coordination bridge for coding-agent fleets: file-scope claims, "
    "presence, chat, a shared plan, and live capability discovery."
)
"""Default A2A Agent Card description."""

JsonMap = dict[str, Any]


def _unique_strings(values: object) -> list[str]:
    """Return stripped, de-duplicated strings from an arbitrary JSON value."""
    if not isinstance(values, list | tuple):
        return []
    seen: dict[str, None] = {}
    for value in values:
        text = str(value).strip()
        if text:
            seen.setdefault(text, None)
    return list(seen)


def _slug(value: str) -> str:
    """Convert ``value`` into a stable A2A skill identifier fragment."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "agent"


def skill_from_manifest_card(card: JsonMap) -> JsonMap:
    """Convert one SYNAPSE capability card into an A2A ``AgentSkill`` object.

    Parameters
    ----------
    card : dict[str, Any]
        One capability card from ``synapse manifest``.

    Returns
    -------
    dict[str, Any]
        A JSON-serialisable A2A ``AgentSkill`` mapping.
    """
    agent = str(card.get("agent") or "agent").strip() or "agent"
    task_classes = _unique_strings(card.get("task_classes"))
    skills = _unique_strings(card.get("skills"))
    tags = _unique_strings([*task_classes, *skills, "synapse"])
    description = str(card.get("description") or "").strip()
    if not description:
        description = f"SYNAPSE-advertised capability for {agent}."
    return {
        "id": f"synapse-{_slug(agent)}",
        "name": agent,
        "description": description,
        "tags": tags,
        "inputModes": list(DEFAULT_INPUT_MODES),
        "outputModes": list(DEFAULT_OUTPUT_MODES),
    }


def _fallback_skill() -> JsonMap:
    """Return the generic bridge skill used when no live manifest exists."""
    return {
        "id": "synapse-coordination",
        "name": "SYNAPSE coordination",
        "description": DEFAULT_DESCRIPTION,
        "tags": ["coordination", "claims", "presence", "blackboard", "synapse"],
        "examples": [
            "Claim files before a parallel coding agent edits them.",
            "Read the shared board before starting a dependent task.",
        ],
        "inputModes": list(DEFAULT_INPUT_MODES),
        "outputModes": list(DEFAULT_OUTPUT_MODES),
    }


def agent_card_from_manifest(
    manifest: list[JsonMap],
    *,
    endpoint_url: str,
    name: str = "SYNAPSE CHANNEL",
    description: str = DEFAULT_DESCRIPTION,
    documentation_url: str = "https://anulum.github.io/synapse-channel",
    provider_organization: str = "Anulum",
    provider_url: str = "https://www.anulum.li",
    protocol_binding: str = "HTTP+JSON",
    protocol_version: str = A2A_PROTOCOL_VERSION,
    bearer_auth: bool = False,
) -> JsonMap:
    """Build an A2A Agent Card from the live SYNAPSE capability manifest.

    Parameters
    ----------
    manifest : list[dict[str, Any]]
        Hub capability manifest entries.
    endpoint_url : str
        Absolute URL where the A2A bridge endpoint will receive requests.
    name, description : str, optional
        Human-facing A2A card identity.
    documentation_url : str, optional
        Public documentation URL.
    provider_organization, provider_url : str, optional
        A2A provider metadata.
    protocol_binding : str, optional
        A2A interface binding; defaults to ``HTTP+JSON``.
    protocol_version : str, optional
        A2A protocol version advertised by this interface.
    bearer_auth : bool, optional
        When true, declare HTTP Bearer authentication for bridge calls.

    Returns
    -------
    dict[str, Any]
        JSON-serialisable A2A Agent Card.
    """
    skills = [skill_from_manifest_card(card) for card in manifest]
    card: JsonMap = {
        "name": name,
        "description": description,
        "supportedInterfaces": [
            {
                "url": endpoint_url,
                "protocolBinding": protocol_binding,
                "protocolVersion": protocol_version,
            }
        ],
        "provider": {
            "organization": provider_organization,
            "url": provider_url,
        },
        "version": __version__,
        "documentationUrl": documentation_url,
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": list(DEFAULT_INPUT_MODES),
        "defaultOutputModes": list(DEFAULT_OUTPUT_MODES),
        "skills": skills or [_fallback_skill()],
    }
    if bearer_auth:
        card["securitySchemes"] = {
            "synapseBearer": {
                "httpAuthSecurityScheme": {
                    "scheme": "Bearer",
                    "description": "Bearer token accepted by the A2A bridge endpoint.",
                }
            }
        }
        card["securityRequirements"] = [{"synapseBearer": []}]
    return card
