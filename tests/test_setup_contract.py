# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — machine-readable setup document contract tests

from __future__ import annotations

import json

import pytest

from synapse_channel.setup_contract import (
    SETUP_SCHEMA_VERSION,
    SetupCheck,
    SetupRequirement,
    canonical_json,
    setup_error_document,
    setup_schema,
)


def test_contract_records_project_to_exact_fields() -> None:
    requirement = SetupRequirement("hub", "Hub answers.", True, "probe", "Start hub.")
    check = SetupCheck("hub", "pass", True, {"uri": "ws://localhost:8876"}, "Hub answers.")

    assert requirement.as_dict() == {
        "id": "hub",
        "description": "Hub answers.",
        "required": True,
        "evidence_source": "probe",
        "remedy": "Start hub.",
    }
    assert check.as_dict()["status"] == "pass"


def test_canonical_json_is_compact_sorted_and_unicode_safe() -> None:
    document = {"z": "Šotek", "a": 1}
    assert canonical_json(document) == '{"a":1,"z":"Šotek"}'
    assert json.loads(canonical_json(document)) == document


def test_packaged_schema_is_a_valid_draft_2020_12_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = setup_schema()
    validator = jsonschema.validators.validator_for(schema)
    validator.check_schema(schema)
    schema_id = schema["$id"]
    assert isinstance(schema_id, str)
    assert schema_id.endswith("synapse-setup-v1.schema.json")


@pytest.mark.parametrize("command", ["spec", "inspect", "plan"])
def test_unknown_profile_error_is_stable_and_schema_valid(command: str) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    document = setup_error_document(
        command=command,  # type: ignore[arg-type]
        profile="future-profile",
        code="unknown_profile",
    )

    jsonschema.validate(document, setup_schema())
    assert document["schema_version"] == SETUP_SCHEMA_VERSION
    assert document["code"] == "unknown_profile"
    assert "future-profile" not in str(document["message"])
