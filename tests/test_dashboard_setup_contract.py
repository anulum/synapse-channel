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
from dataclasses import FrozenInstanceError
from typing import cast
from uuid import UUID, uuid1, uuid4

import pytest

from synapse_channel.dashboard_setup_contract import (
    MAX_SETUP_REQUEST_BYTES,
    SetupApplyRequest,
    SetupContractRefusal,
    SetupPlanRequest,
    SetupPosture,
    SetupProfileId,
    available_setup_profiles,
    build_setup_plan,
    evaluate_setup_posture,
    parse_setup_apply_request,
    parse_setup_plan_request,
)

REQUEST_ID = "4dc282e6-21eb-4f5c-89d8-ea824836ef65"
CONFIGURATION_GENERATION = "a" * 64
PLAN_ID = "plan_id_0123456789abcdef"
NONCE = "nonce_0123456789abcdef"


def encoded(document: object) -> bytes:
    """Encode one compact request fixture."""
    return json.dumps(document, separators=(",", ":")).encode()


def valid_plan_document() -> dict[str, object]:
    return {"version": 1, "profile": "local-ephemeral", "request_id": REQUEST_ID}


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


def test_profiles_are_stable_and_not_mutable_through_the_public_projection() -> None:
    profiles = available_setup_profiles()
    assert profiles == ("local-ephemeral", "local-durable-existing")
    assert isinstance(profiles, tuple)


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
    request = SetupPlanRequest(REQUEST_ID, "local-ephemeral")
    with pytest.raises(FrozenInstanceError):
        posture.feature_armed = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        request.profile = "local-durable-existing"  # type: ignore[misc]


def test_plan_request_accepts_only_the_exact_schema() -> None:
    result = parse_setup_plan_request(encoded(valid_plan_document()))
    assert result == SetupPlanRequest(REQUEST_ID, "local-ephemeral")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"", "body_size"),
        (b" " * (MAX_SETUP_REQUEST_BYTES + 1), "body_size"),
        (b"\xff", "invalid_json"),
        (b"{", "invalid_json"),
        (b'{"version":NaN,"profile":"local-ephemeral","request_id":"x"}', "invalid_json"),
        (b'{"version":1,"version":1,"profile":"local-ephemeral","request_id":"x"}', "invalid_json"),
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
    [None, 7, "not-a-uuid", str(uuid1()), str(UUID(REQUEST_ID)).upper()],
)
def test_plan_request_requires_a_canonical_v4_request_id(request_id: object) -> None:
    document = valid_plan_document()
    document["request_id"] = request_id
    assert error_code(parse_setup_plan_request(encoded(document))) == "invalid_request_id"


@pytest.mark.parametrize("profile", ["unknown", 7, ["local-ephemeral"]])
def test_plan_request_rejects_every_non_registry_profile(profile: object) -> None:
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


@pytest.mark.parametrize("profile", available_setup_profiles())
def test_plan_is_deterministic_token_only_and_profile_bound(
    profile: SetupProfileId,
) -> None:
    request = SetupPlanRequest(REQUEST_ID, profile)
    first = build_setup_plan(
        request,
        configuration_generation=CONFIGURATION_GENERATION,
        expires_at=1_784_000_000,
    )
    second = build_setup_plan(
        request,
        configuration_generation=CONFIGURATION_GENERATION,
        expires_at=1_784_000_000,
    )
    assert first == second
    assert first.digest == second.digest
    assert len(first.digest) == 64
    assert first.as_dict()["version"] == 1
    rendered = first.canonical_bytes().decode()
    assert rendered == second.canonical_bytes().decode()
    for forbidden in ("/home/", "~/", "--token", "Bearer ", "<", ">"):
        assert forbidden not in rendered
    assert all(effect.target != "" for effect in first.effects)


def test_profiles_produce_distinct_plans_and_durable_profile_only_verifies_store() -> None:
    ephemeral = build_setup_plan(
        SetupPlanRequest(REQUEST_ID, "local-ephemeral"),
        configuration_generation=CONFIGURATION_GENERATION,
        expires_at=1_784_000_000,
    )
    durable = build_setup_plan(
        SetupPlanRequest(REQUEST_ID, "local-durable-existing"),
        configuration_generation=CONFIGURATION_GENERATION,
        expires_at=1_784_000_000,
    )
    assert ephemeral.digest != durable.digest
    durable_effect = next(effect for effect in durable.effects if effect.kind == "durable_store")
    assert durable_effect.change == "verify_existing"
    assert all(effect.kind != "durable_store" for effect in ephemeral.effects)


@pytest.mark.parametrize("generation", ["", "A" * 64, "g" * 64])
def test_plan_rejects_invalid_server_configuration_generation(generation: str) -> None:
    with pytest.raises(ValueError, match="configuration generation"):
        build_setup_plan(
            SetupPlanRequest(REQUEST_ID, "local-ephemeral"),
            configuration_generation=generation,
            expires_at=1,
        )


@pytest.mark.parametrize("expires_at", [True, 0, -1, 1.5, "1"])
def test_plan_rejects_invalid_expiry(expires_at: object) -> None:
    with pytest.raises(ValueError, match="plan expiry"):
        build_setup_plan(
            SetupPlanRequest(str(uuid4()), "local-ephemeral"),
            configuration_generation=CONFIGURATION_GENERATION,
            expires_at=cast(int, expires_at),
        )
