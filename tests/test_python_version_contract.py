# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — supported Python and stable enum compatibility contracts
"""Keep supported-interpreter declarations and existing string conversions aligned."""

from __future__ import annotations

import ast
import importlib
import json
from enum import Enum
from pathlib import Path

import pytest
import tomllib

_ROOT = Path(__file__).resolve().parents[1]

_STRING_ENUMS = (
    (
        "synapse_channel.a2a_push_delivery",
        "PushDeliveryState",
        "RETRY_SCHEDULED",
        "retry-scheduled",
    ),
    ("synapse_channel.a2a_push_delivery", "PushFailureClass", "TIMEOUT", "timeout"),
    (
        "synapse_channel.a2a_scenario_responses",
        "ScenarioKind",
        "ARTIFACT_TEXT",
        "artifact-text",
    ),
    ("synapse_channel.core.aef_chain", "AefChainCode", "CHAIN_CONTINUOUS", "CHAIN_CONTINUOUS"),
    ("synapse_channel.core.aef_countersign", "CountersignVerdict", "VALID", "VALID"),
    ("synapse_channel.core.aef_sth", "AefSthVerdict", "STH_COMPLETE", "STH_COMPLETE"),
    ("synapse_channel.core.aef_verdict", "AefVerdictCode", "VALID", "VALID"),
    (
        "synapse_channel.core.aef_verification",
        "AefInclusionVerdict",
        "INCLUSION_VALID",
        "INCLUSION_VALID",
    ),
    (
        "synapse_channel.core.capability_card_trust",
        "CapabilityCardHistoryResult",
        "ACCEPTED",
        "accepted",
    ),
    (
        "synapse_channel.core.capability_card_verification",
        "CapabilityCardVerificationResult",
        "VALID",
        "valid",
    ),
    ("synapse_channel.core.execution_outcome", "OutcomeState", "NOT_STARTED", "not_started"),
    ("synapse_channel.core.frame_signature", "FrameSignatureResult", "VALID", "valid"),
    ("synapse_channel.core.frame_signature_gate", "GateVerdict", "ADMIT", "admit"),
    (
        "synapse_channel.core.event_row_recovery",
        "CorruptEventReason",
        "INVALID_TIMESTAMP",
        "invalid_timestamp",
    ),
    ("synapse_channel.core.message_auth", "VerificationResult", "OK", "ok"),
    ("synapse_channel.core.message_auth", "SignedEventVerificationResult", "VALID", "valid"),
    ("synapse_channel.core.message_auth_durable", "SequenceFloorMode", "OFF", "off"),
    ("synapse_channel.core.message_auth_durable", "DurableAdmitResult", "ACCEPTED", "accepted"),
    ("synapse_channel.core.namespace_ownership", "OwnershipOutcome", "LOCAL", "local"),
    ("synapse_channel.core.tls", "MTLSVerificationResult", "VALID", "valid"),
    ("synapse_channel.participants.modes", "ConversationMode", "COLLOQUY", "colloquy"),
    ("synapse_channel.participants.participant", "ParticipantChannel", "MCP", "mcp"),
    ("synapse_channel.participants.provider_route_policy", "PriceKind", "PRICED", "priced"),
    (
        "synapse_channel.participants.provider_route_policy",
        "EvidenceStatus",
        "CURRENT",
        "current",
    ),
    (
        "synapse_channel.participants.session_advisor",
        "SessionSignal",
        "COMPACT_SOON",
        "compact-soon",
    ),
)
"""Every string enum of the package: module, type, one member and its wire value."""


@pytest.mark.parametrize("module_name,type_name,member_name,wire_value", _STRING_ENUMS)
def test_public_enum_string_format_and_wire_values_remain_stable(
    module_name: str, type_name: str, member_name: str, wire_value: str
) -> None:
    enum_type = getattr(importlib.import_module(module_name), type_name)
    member = getattr(enum_type, member_name)
    assert isinstance(member, Enum)
    assert isinstance(member, str)
    assert member.value == wire_value
    assert str(member) == f"{type_name}.{member_name}"
    assert format(member) == f"{type_name}.{member_name}"
    assert format(member, ">60") == f"{type_name}.{member_name}".rjust(60)
    assert json.dumps({"result": member}) == json.dumps({"result": wire_value})


def test_every_string_enum_of_the_package_is_in_the_table() -> None:
    """A string enum added to the package must be pinned above before it ships."""
    found = set()
    for path in sorted((_ROOT / "src" / "synapse_channel").rglob("*.py")):
        module = ".".join(path.relative_to(_ROOT / "src").with_suffix("").parts)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                bases = {ast.unparse(base) for base in node.bases}
                if {"str", "Enum"} <= bases or "StrEnum" in bases:
                    found.add((module, node.name))
    assert found == {(module, name) for module, name, _member, _value in _STRING_ENUMS}


def test_python_floor_matches_packaging_and_type_checking_contracts() -> None:
    for relative in ("pyproject.toml", "integrations/github-app/pyproject.toml"):
        data = tomllib.loads((_ROOT / relative).read_text(encoding="utf-8"))
        assert data["project"]["requires-python"] == ">=3.11"
        assert data["tool"]["mypy"]["python_version"] == "3.11"
        assert "Programming Language :: Python :: 3.10" not in data["project"].get(
            "classifiers", []
        )
