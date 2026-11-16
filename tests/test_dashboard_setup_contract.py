# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure setup contract and fail-closed planning tests
"""Prove setup policy and schemas before any effect adapter or route exists."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import cast
from uuid import UUID, uuid1, uuid4

import pytest

import setup_plan_fixture as fixture
from synapse_channel import setup_planner
from synapse_channel.dashboard_setup_contract import (
    MAX_SETUP_REQUEST_BYTES,
    SetupApplyRequest,
    SetupContractRefusal,
    SetupPlanEffect,
    SetupPlanRequest,
    SetupPosture,
    build_setup_plan,
    evaluate_setup_posture,
    parse_setup_apply_request,
    parse_setup_plan_request,
    setup_generation_digest,
)
from synapse_channel.setup_contract import document_digest

REQUEST_ID = "4dc282e6-21eb-4f5c-89d8-ea824836ef65"
PLAN_ID = "plan_id_0123456789abcdef"
NONCE = "nonce_0123456789abcdef"


def encoded(document: object) -> bytes:
    """Encode one compact request fixture."""
    return json.dumps(document, separators=(",", ":")).encode()


def valid_plan_document() -> dict[str, object]:
    return {"version": 1, "profile": "local-single-user", "request_id": REQUEST_ID}


def valid_apply_document() -> dict[str, object]:
    return {
        "version": 1,
        "request_id": REQUEST_ID,
        "plan_id": PLAN_ID,
        "plan_digest": "b" * 64,
        "confirmation_nonce": NONCE,
        "confirm": True,
    }


def error_code(result: object) -> str:
    assert isinstance(result, SetupContractRefusal)
    return result.code


@pytest.mark.parametrize(
    ("posture", "advertised", "reason"),
    [
        (SetupPosture(False, True, False, True, True), False, "unarmed"),
        (SetupPosture(True, False, False, True, True), False, "non_loopback"),
        (SetupPosture(True, True, True, True, True), False, "compatibility_access"),
        (SetupPosture(True, True, False, False, True), False, "access_file_required"),
        (
            SetupPosture(True, True, False, True, False),
            False,
            "receipt_store_unavailable",
        ),
        (SetupPosture(True, True, False, True, True), True, "ready"),
    ],
)
def test_setup_posture_is_deny_by_default(
    posture: SetupPosture,
    advertised: bool,
    reason: str,
) -> None:
    decision = evaluate_setup_posture(posture)
    assert decision.advertised is advertised
    assert decision.reason == reason


def test_setup_posture_and_requests_are_immutable() -> None:
    posture = SetupPosture(True, True, False, True, True)
    request = SetupPlanRequest(REQUEST_ID, "local-single-user")
    # The field names are held in variables: a frozen dataclass refuses every set.
    for frozen, field in ((posture, "feature_armed"), (request, "profile")):
        with pytest.raises(FrozenInstanceError):
            setattr(frozen, field, False)


def test_plan_request_accepts_only_the_exact_schema() -> None:
    result = parse_setup_plan_request(encoded(valid_plan_document()))
    assert result == SetupPlanRequest(REQUEST_ID, "local-single-user")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"", "body_size"),
        (b" " * (MAX_SETUP_REQUEST_BYTES + 1), "body_size"),
        (b"\xff", "invalid_json"),
        (b"{", "invalid_json"),
        (b'{"version":NaN,"profile":"local-single-user","request_id":"x"}', "invalid_json"),
        (
            b'{"version":1,"version":1,"profile":"local-single-user","request_id":"x"}',
            "invalid_json",
        ),
        (b"[]", "invalid_fields"),
        (encoded({"version": 1}), "invalid_fields"),
        (encoded({**valid_plan_document(), "extra": True}), "invalid_fields"),
    ],
)
def test_plan_request_rejects_bad_transport_and_field_shapes(body: bytes, code: str) -> None:
    assert error_code(parse_setup_plan_request(body)) == code


@pytest.mark.parametrize("version", [True, 2, "1"])
def test_plan_request_requires_literal_contract_version(version: object) -> None:
    document = valid_plan_document()
    document["version"] = version
    assert error_code(parse_setup_plan_request(encoded(document))) == "invalid_version"


@pytest.mark.parametrize(
    "request_id",
    [
        None,
        7,
        "not-a-uuid",
        pytest.param(str(uuid1()), id="uuid-v1"),
        str(UUID(REQUEST_ID)).upper(),
    ],
)
def test_plan_request_requires_a_canonical_v4_request_id(request_id: object) -> None:
    document = valid_plan_document()
    document["request_id"] = request_id
    assert error_code(parse_setup_plan_request(encoded(document))) == "invalid_request_id"


@pytest.mark.parametrize(
    "profile", ["unknown", 7, ["local-single-user"], "local-ephemeral", "local-durable-existing"]
)
def test_plan_request_rejects_every_profile_the_command_line_does_not_serve(
    profile: object,
) -> None:
    document = valid_plan_document()
    document["profile"] = profile
    assert error_code(parse_setup_plan_request(encoded(document))) == "unknown_profile"


def test_apply_request_accepts_only_explicit_confirmation() -> None:
    result = parse_setup_apply_request(encoded(valid_apply_document()))
    assert result == SetupApplyRequest(REQUEST_ID, PLAN_ID, "b" * 64, NONCE)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("version", False, "invalid_version"),
        ("request_id", "wrong", "invalid_request_id"),
        ("plan_id", "short", "invalid_plan_id"),
        ("plan_id", 7, "invalid_plan_id"),
        ("plan_digest", "B" * 64, "invalid_plan_digest"),
        ("plan_digest", 7, "invalid_plan_digest"),
        ("confirmation_nonce", "spaces are refused here", "invalid_confirmation_nonce"),
        ("confirmation_nonce", None, "invalid_confirmation_nonce"),
        ("confirm", False, "confirmation_required"),
        ("confirm", 1, "confirmation_required"),
    ],
)
def test_apply_request_refuses_each_invalid_boundary(
    field: str,
    value: object,
    code: str,
) -> None:
    document = valid_apply_document()
    document[field] = value
    assert error_code(parse_setup_apply_request(encoded(document))) == code


def test_apply_request_reuses_strict_transport_and_exact_field_gates() -> None:
    assert error_code(parse_setup_apply_request(b"")) == "body_size"
    assert error_code(parse_setup_apply_request(b"not-json")) == "invalid_json"
    document = valid_apply_document()
    document["extra"] = "refused"
    assert error_code(parse_setup_apply_request(encoded(document))) == "invalid_fields"


def _request() -> SetupPlanRequest:
    return SetupPlanRequest(REQUEST_ID, "local-single-user")


def test_a_plan_is_bound_to_the_command_line_plan_and_holds_no_path() -> None:
    cli = fixture.cli_plan(fixture.HUB_AND_WAITER_DOWN, hub_pid=4321)
    first = build_setup_plan(_request(), cli_plan=cli, expires_at=1_784_000_000)
    second = build_setup_plan(_request(), cli_plan=deepcopy(cli), expires_at=1_784_000_000)

    assert first == second
    assert first.cli_plan_digest == cli["plan_digest"]
    generation = cli["generation"]
    assert isinstance(generation, dict)
    assert first.configuration_generation == document_digest(generation)
    assert first.configuration_generation == setup_generation_digest(cli)
    document = first.as_dict()
    assert document["cli_plan_digest"] == cli["plan_digest"]
    assert document["profile"] == "local-single-user"
    assert document["effects"] == [
        {
            "id": "establish_local_loopback_hub",
            "disposition": "planned",
            "authority": "operator_restart_authority",
            "disruption": "service_start",
            "reversible": True,
            "process_id": 4321,
        },
        {
            "id": "establish_identity_waiter",
            "disposition": "planned",
            "authority": "operator_confirmation",
            "disruption": "service_start",
            "reversible": True,
            "process_id": None,
        },
    ]
    rendered = first.canonical_bytes().decode()
    for forbidden in ("/home/", "/usr/", "ws://", "DEMO", "trigger_check", "observed_status"):
        assert forbidden not in rendered


def test_the_plan_digest_covers_the_command_line_digest() -> None:
    plan = build_setup_plan(_request(), cli_plan=fixture.cli_plan(), expires_at=1_784_000_000)
    other = build_setup_plan(
        _request(), cli_plan=fixture.cli_plan(fixture.HUB_AND_WAITER_DOWN), expires_at=1_784_000_000
    )

    assert plan.effects == ()
    assert plan.digest != other.digest
    assert plan.cli_plan_digest != other.cli_plan_digest


def test_two_command_line_plans_that_differ_only_in_their_inspection_give_two_digests() -> None:
    profile = fixture.profile()
    seen = fixture.inspection(fixture.HUB_AND_WAITER_DOWN)
    seen_again = deepcopy(seen)
    checks = cast(list[dict[str, object]], seen_again["checks"])
    checks[0] = {**checks[0], "detail": "Observed again, a moment later."}
    first_cli = setup_planner.build_setup_plan(profile, seen)
    second_cli = setup_planner.build_setup_plan(profile, seen_again)

    first = build_setup_plan(_request(), cli_plan=first_cli, expires_at=1)
    second = build_setup_plan(_request(), cli_plan=second_cli, expires_at=1)

    assert first.as_dict() | {"cli_plan_digest": ""} == second.as_dict() | {"cli_plan_digest": ""}
    assert first.cli_plan_digest != second.cli_plan_digest
    assert first.digest != second.digest


def test_a_blocked_effect_is_kept_so_the_person_sees_what_cannot_be_applied() -> None:
    cli = fixture.cli_plan({"package": "fail"})

    plan = build_setup_plan(_request(), cli_plan=cli, expires_at=1)

    assert plan.effects == (
        SetupPlanEffect(
            "install_synapse_package", "blocked", "unsupported", "environment_change", False, None
        ),
    )


def _tampered(change: dict[str, object]) -> dict[str, object]:
    """Return a command-line plan with its first effect changed and its digest matching again."""
    cli = fixture.cli_plan(fixture.HUB_AND_WAITER_DOWN, hub_pid=4321)
    effects = cast(list[dict[str, object]], cli["effects"])
    effects[0] = {**effects[0], **change}
    unsigned = {key: value for key, value in cli.items() if key != "plan_digest"}
    return {**unsigned, "plan_digest": document_digest(unsigned)}


@pytest.mark.parametrize(
    "change",
    [
        {"id": "format_the_disk"},
        {"id": 7},
        {"disposition": "maybe"},
        {"authority": ["operator_confirmation"]},
        {"disruption": "reboot"},
        {"disruption": None},
        {"reversible": "yes"},
        {"process_id": None},
        {"process_id": 1},
        {"process_id": 2_147_483_648},
        {"process_id": True},
        {"authority": "operator_confirmation"},
    ],
)
def test_an_effect_outside_the_command_line_vocabulary_is_refused(
    change: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="outside the setup vocabulary"):
        build_setup_plan(_request(), cli_plan=_tampered(change), expires_at=1)


def test_a_restart_without_its_authority_is_refused() -> None:
    cli = fixture.cli_plan(fixture.HUB_AND_WAITER_DOWN)
    effects = cast(list[dict[str, object]], cli["effects"])
    effects[1] = {**effects[1], "process_id": 4321}
    unsigned = {key: value for key, value in cli.items() if key != "plan_digest"}

    with pytest.raises(ValueError, match="outside the setup vocabulary"):
        build_setup_plan(
            _request(),
            cli_plan={**unsigned, "plan_digest": document_digest(unsigned)},
            expires_at=1,
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"plan_digest": "a" * 64}, "does not match its digest"),
        ({"plan_digest": None}, "does not match its digest"),
        ({"profile": "local-ephemeral"}, "not of the requested profile"),
        ({"profile_version": 0}, "profile version"),
        ({"profile_version": True}, "profile version"),
        ({"effects": {}}, "no effect list"),
        ({"generation": "paths"}, "no generation"),
    ],
)
def test_a_command_line_plan_that_cannot_be_bound_is_refused(
    change: dict[str, object], message: str
) -> None:
    cli = {**fixture.cli_plan(fixture.HUB_AND_WAITER_DOWN), **change}

    with pytest.raises(ValueError, match=message):
        build_setup_plan(_request(), cli_plan=cli, expires_at=1)


def test_an_effect_that_is_not_an_object_is_refused() -> None:
    cli = fixture.cli_plan(fixture.HUB_AND_WAITER_DOWN)
    unsigned = {key: value for key, value in cli.items() if key != "plan_digest"}
    unsigned["effects"] = ["establish_identity_waiter"]

    with pytest.raises(ValueError, match="not an object"):
        build_setup_plan(
            _request(),
            cli_plan={**unsigned, "plan_digest": document_digest(unsigned)},
            expires_at=1,
        )


@pytest.mark.parametrize("expires_at", [True, 0, -1, 1.5, "1"])
def test_plan_rejects_invalid_expiry(expires_at: object) -> None:
    with pytest.raises(ValueError, match="plan expiry"):
        build_setup_plan(
            SetupPlanRequest(str(uuid4()), "local-single-user"),
            cli_plan=fixture.cli_plan(),
            expires_at=cast(int, expires_at),
        )
