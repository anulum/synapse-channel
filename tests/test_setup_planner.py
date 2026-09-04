# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deterministic read-only setup planner tests

from __future__ import annotations

from copy import deepcopy

import pytest

from synapse_channel.setup_contract import document_digest, setup_schema
from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile


def _profile() -> SetupProfile:
    profile = get_setup_profile("local-single-user")
    assert profile is not None
    return profile


def inspection_document(
    overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    """Return one complete schema-valid inspection fixture."""
    profile = _profile()
    statuses = {requirement.requirement_id: "pass" for requirement in profile.requirements}
    statuses.update(overrides or {})
    checks = [
        {
            "id": requirement.requirement_id,
            "status": statuses[requirement.requirement_id],
            "required": requirement.required,
            "value": {},
            "detail": "Observed fixture.",
            "remedy": "",
        }
        for requirement in profile.requirements
    ]
    ready = all(
        statuses[requirement.requirement_id] == "pass"
        for requirement in profile.requirements
        if requirement.required
    )
    return {
        "schema_version": "synapse-setup.v1",
        "document_kind": "inspection",
        "profile": "local-single-user",
        "profile_version": 1,
        "read_only": True,
        "ready": ready,
        "target": {
            "uri": "ws://localhost:8876",
            "project": "DEMO",
            "identity": "DEMO/claude-one",
        },
        "summary": {
            status: sum(check["status"] == status for check in checks)
            for status in ("pass", "warn", "fail", "unavailable")
        },
        "checks": checks,
    }


def test_ready_plan_is_deterministic_digest_bound_and_non_executable() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    inspection = inspection_document()

    first = build_setup_plan(_profile(), inspection)
    second = build_setup_plan(_profile(), deepcopy(inspection))

    assert first == second
    assert first["ready"] is True
    assert first["read_only"] is True
    assert first["can_apply"] is False
    assert first["effects"] == []
    assert first["authority_required"] == []
    assert first["warnings"] == ["apply_not_available"]
    assert first["target"] == inspection["target"]
    assert first["inspection_digest"] == document_digest(inspection)
    unsigned = {key: value for key, value in first.items() if key != "plan_digest"}
    assert first["plan_digest"] == document_digest(unsigned)
    jsonschema.validate(first, setup_schema())


def test_unmet_checks_map_only_to_allowlisted_effects_and_authorities() -> None:
    inspection = inspection_document({"identity": "fail", "hub": "fail", "waiter": "warn"})
    plan = build_setup_plan(_profile(), inspection)
    effects = plan["effects"]
    assert isinstance(effects, list)

    assert [effect["id"] for effect in effects] == [
        "configure_coordination_identity",
        "establish_local_loopback_hub",
        "establish_identity_waiter",
    ]
    assert all(effect["disposition"] == "planned" for effect in effects)
    assert plan["authority_required"] == [
        "operator_confirmation",
        "operator_restart_authority",
    ]
    assert plan["ready"] is False


def test_unavailable_and_unsupported_effects_fail_closed() -> None:
    inspection = inspection_document({"platform": "warn", "hub": "unavailable"})
    plan = build_setup_plan(_profile(), inspection)
    effects = plan["effects"]
    assert isinstance(effects, list)
    by_id = {effect["id"]: effect for effect in effects}

    assert by_id["select_supported_platform"]["authority"] == "unsupported"
    assert by_id["select_supported_platform"]["disposition"] == "blocked"
    assert by_id["establish_local_loopback_hub"]["disposition"] == "blocked"
    assert plan["authority_required"] == []
    assert plan["warnings"] == ["apply_not_available", "manual_remediation_required"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "future"),
        ("document_kind", "spec"),
        ("profile", "future"),
        ("profile_version", 2),
        ("read_only", False),
    ],
)
def test_planner_rejects_an_inspection_for_another_contract(
    field: str,
    value: object,
) -> None:
    inspection = inspection_document()
    inspection[field] = value
    with pytest.raises(ValueError, match="does not match"):
        build_setup_plan(_profile(), inspection)


def test_planner_rejects_non_list_and_non_object_checks() -> None:
    inspection = inspection_document()
    inspection["checks"] = "not-a-list"
    with pytest.raises(ValueError, match="must be a list"):
        build_setup_plan(_profile(), inspection)

    inspection["checks"] = ["not-an-object"]
    with pytest.raises(ValueError, match="must be an object"):
        build_setup_plan(_profile(), inspection)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 7),
        ("id", "unknown"),
        ("status", "unknown"),
        ("required", "yes"),
        ("required", False),
    ],
)
def test_planner_rejects_malformed_check_fields(field: str, value: object) -> None:
    inspection = inspection_document()
    checks = inspection["checks"]
    assert isinstance(checks, list)
    checks[0][field] = value
    with pytest.raises(ValueError, match="do not match"):
        build_setup_plan(_profile(), inspection)


def test_planner_rejects_duplicate_missing_and_contradictory_checks() -> None:
    inspection = inspection_document()
    checks = inspection["checks"]
    assert isinstance(checks, list)
    checks[1]["id"] = checks[0]["id"]
    with pytest.raises(ValueError, match="do not match"):
        build_setup_plan(_profile(), inspection)

    inspection = inspection_document()
    checks = inspection["checks"]
    assert isinstance(checks, list)
    checks.pop()
    with pytest.raises(ValueError, match="missing"):
        build_setup_plan(_profile(), inspection)

    inspection = inspection_document({"hub": "fail"})
    inspection["ready"] = True
    with pytest.raises(ValueError, match="contradicts"):
        build_setup_plan(_profile(), inspection)


@pytest.mark.parametrize(
    "target",
    [
        None,
        {"uri": "http://localhost", "project": "DEMO", "identity": "DEMO/one"},
        {"uri": "ws://user:secret@localhost", "project": "DEMO", "identity": "DEMO/one"},
        {"uri": "ws://localhost", "project": "bad project", "identity": "DEMO/one"},
    ],
)
def test_planner_refuses_an_unsafe_or_incomplete_target(target: object) -> None:
    inspection = inspection_document()
    inspection["target"] = target
    with pytest.raises(ValueError, match="target"):
        build_setup_plan(_profile(), inspection)
