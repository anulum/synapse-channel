# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A Agent Card projection

from __future__ import annotations

from synapse_channel import __version__
from synapse_channel.a2a import agent_card_from_manifest, skill_from_manifest_card


def test_skill_from_manifest_card_maps_capability_fields() -> None:
    skill = skill_from_manifest_card(
        {
            "agent": "FAST/worker-1",
            "description": " answers quick coordination requests ",
            "skills": ["chat", "chat", "handoff"],
            "task_classes": ["rule", "chat"],
            "contracts": [
                {
                    "task_class": "chat",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "string"},
                    "preconditions": [],
                    "postconditions": ["answer returned"],
                }
            ],
        }
    )

    assert skill == {
        "id": "synapse-fast-worker-1",
        "name": "FAST/worker-1",
        "description": "answers quick coordination requests",
        "tags": ["rule", "chat", "handoff", "synapse"],
        "inputModes": ["text/plain", "application/json"],
        "outputModes": ["text/plain", "application/json"],
        "metadata": {
            "synapse": {
                "contracts": [
                    {
                        "task_class": "chat",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "string"},
                        "preconditions": [],
                        "postconditions": ["answer returned"],
                    }
                ]
            }
        },
    }


def test_skill_from_manifest_card_falls_back_for_empty_fields() -> None:
    skill = skill_from_manifest_card(
        {
            "agent": "   ",
            "description": "",
            "skills": "chat",
            "task_classes": None,
        }
    )

    assert skill["id"] == "synapse-agent"
    assert skill["name"] == "agent"
    assert skill["description"] == "SYNAPSE-advertised capability for agent."
    assert skill["tags"] == ["synapse"]


def test_skill_from_manifest_card_ignores_blank_list_entries() -> None:
    skill = skill_from_manifest_card(
        {
            "agent": "worker",
            "description": "capability",
            "skills": ["", " chat ", "chat"],
            "task_classes": [" ", "rule"],
        }
    )

    assert skill["tags"] == ["rule", "chat", "synapse"]


def test_agent_card_from_manifest_emits_required_a2a_fields() -> None:
    card = agent_card_from_manifest(
        [
            {
                "agent": "FAST",
                "description": "quick worker",
                "skills": ["chat"],
                "task_classes": ["rule"],
            }
        ],
        endpoint_url="https://example.test/a2a/v1",
        name="Synapse A2A",
        bearer_auth=True,
    )

    assert card["name"] == "Synapse A2A"
    assert card["version"] == __version__
    assert card["supportedInterfaces"] == [
        {
            "url": "https://example.test/a2a/v1",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }
    ]
    assert card["provider"] == {"organization": "Anulum", "url": "https://www.anulum.li"}
    assert card["capabilities"] == {
        "streaming": False,
        "pushNotifications": False,
        "extendedAgentCard": False,
    }
    assert card["defaultInputModes"] == ["text/plain", "application/json"]
    assert card["defaultOutputModes"] == ["text/plain", "application/json"]
    assert card["skills"][0]["id"] == "synapse-fast"
    assert card["securitySchemes"]["synapseBearer"]["httpAuthSecurityScheme"]["scheme"] == "Bearer"
    assert card["securityRequirements"] == [{"synapseBearer": []}]


def test_agent_card_without_bearer_auth_omits_security_fields() -> None:
    card = agent_card_from_manifest(
        [{"agent": "agent", "description": "capability"}],
        endpoint_url="https://example.test/a2a/v1",
        provider_organization="Example",
        provider_url="https://example.test",
        protocol_binding="JSON-RPC",
        protocol_version="2.0",
    )

    assert card["provider"] == {"organization": "Example", "url": "https://example.test"}
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSON-RPC"
    assert card["supportedInterfaces"][0]["protocolVersion"] == "2.0"
    assert "securitySchemes" not in card
    assert "securityRequirements" not in card


def test_agent_card_from_empty_manifest_keeps_generic_coordination_skill() -> None:
    card = agent_card_from_manifest([], endpoint_url="https://example.test/a2a/v1")

    assert [skill["id"] for skill in card["skills"]] == ["synapse-coordination"]
    assert "file-scope claims" in card["skills"][0]["description"]
