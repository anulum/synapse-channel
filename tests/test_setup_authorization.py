# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded setup authorization envelope tests

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from synapse_channel.setup_authorization import (
    MAX_PLAN_BYTES,
    SetupAuthorizationError,
    build_setup_authorization,
    load_setup_authorization,
    load_setup_plan,
    validate_setup_authorization,
    validate_setup_plan,
)
from synapse_channel.setup_contract import document_digest, setup_schema
from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile

NONCE = "0123456789abcdefghijkl"


def _profile() -> SetupProfile:
    profile = get_setup_profile("local-single-user")
    assert profile is not None
    return profile


def _inspection(
    overrides: dict[str, str] | None = None,
    *,
    hub_pid: int | None = None,
) -> dict[str, object]:
    profile = _profile()
    statuses = {item.requirement_id: "pass" for item in profile.requirements}
    statuses.update(overrides or {})
    checks = [
        {
            "id": item.requirement_id,
            "status": statuses[item.requirement_id],
            "required": item.required,
            "value": {},
            "detail": "Observed fixture.",
            "remedy": "",
        }
        for item in profile.requirements
    ]
    service_manager = next(check for check in checks if check["id"] == "service_manager")
    service_manager["value"] = {
        "kind": "systemd-user",
        "executable": "/usr/bin/systemctl",
        "hub_pid": hub_pid or 0,
    }
    values = {
        "package": {"name": "synapse-channel", "version": "0.99.24"},
        "python": {"executable": "/usr/bin/python3", "version": "3.12.0"},
        "platform": {"system": "Linux", "release": "test", "machine": "x86_64"},
        "executable": "/usr/bin/synapse",
    }
    for check in checks:
        if check["id"] in values:
            check["value"] = values[check["id"]]
    return {
        "schema_version": "synapse-setup.v1",
        "document_kind": "inspection",
        "profile": "local-single-user",
        "profile_version": 1,
        "read_only": True,
        "ready": all(
            statuses[item.requirement_id] == "pass"
            for item in profile.requirements
            if item.required
        ),
        "target": {
            "uri": "ws://localhost:8876",
            "project": "DEMO",
            "identity": "DEMO/codex-one",
        },
        "summary": {
            status: sum(check["status"] == status for check in checks)
            for status in ("pass", "warn", "fail", "unavailable")
        },
        "checks": checks,
    }


def _plan(
    overrides: dict[str, str] | None = None,
    *,
    hub_pid: int | None = None,
) -> dict[str, object]:
    return build_setup_plan(_profile(), _inspection(overrides, hub_pid=hub_pid))


def _write_plan(path: Path, plan: dict[str, object]) -> None:
    path.write_text(json.dumps(plan), encoding="utf-8")


def _assert_code(code: str, call: object) -> None:
    assert isinstance(call, SetupAuthorizationError)
    assert call.code == code


def test_authorization_is_deterministic_schema_valid_and_non_executable() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    plan = _plan({"waiter": "fail"})
    kwargs = {
        "confirm_digest": plan["plan_digest"],
        "nonce": NONCE,
        "expires_in": 300,
        "restart_pid": None,
        "clock": lambda: 1_788_520_000.9,
    }

    first = build_setup_authorization(plan, **kwargs)  # type: ignore[arg-type]
    second = build_setup_authorization(deepcopy(plan), **kwargs)  # type: ignore[arg-type]

    assert first == second
    assert first["read_only"] is True
    assert first["can_apply"] is True
    assert first["target"] == plan["target"]
    assert first["issued_at"] == 1_788_520_000
    assert first["expires_at"] == 1_788_520_300
    assert first["authority_granted"] == ["operator_confirmation"]
    assert first["restart_authority"] is None
    assert first["consumption_required"] is True
    unsigned = {key: value for key, value in first.items() if key != "authorization_digest"}
    assert first["authorization_digest"] == document_digest(unsigned)
    jsonschema.validate(first, setup_schema())


def test_authorization_binds_restart_authority_to_one_exact_pid() -> None:
    plan = _plan({"hub": "fail"}, hub_pid=4321)
    document = build_setup_authorization(
        plan,
        confirm_digest=str(plan["plan_digest"]),
        nonce=NONCE,
        expires_in=30,
        restart_pid=4321,
        clock=lambda: 10.0,
    )
    assert document["authority_granted"] == ["operator_restart_authority"]
    assert document["restart_authority"] == {"pid": 4321}
    assert validate_setup_authorization(plan, document, now=11) == document

    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=str(plan["plan_digest"]),
            nonce=NONCE,
            expires_in=30,
            restart_pid=4322,
            clock=lambda: 10.0,
        )
    _assert_code("authorization_mismatch", caught.value)

    changed = deepcopy(document)
    changed["restart_authority"] = {"pid": 4322}
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_authorization(plan, changed, now=11)
    _assert_code("authorization_mismatch", caught.value)


@pytest.mark.parametrize("digest", ["A" * 64, "0" * 64, "short"])
def test_authorization_refuses_unconfirmed_digest(digest: str) -> None:
    plan = _plan()
    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=digest,
            nonce=NONCE,
            expires_in=300,
            restart_pid=None,
        )
    _assert_code("digest_mismatch", caught.value)


@pytest.mark.parametrize("nonce", ["short", "space is never allowed!!!", "x" * 129])
def test_authorization_refuses_invalid_nonce(nonce: str) -> None:
    plan = _plan()
    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=str(plan["plan_digest"]),
            nonce=nonce,
            expires_in=300,
            restart_pid=None,
        )
    _assert_code("invalid_nonce", caught.value)


@pytest.mark.parametrize("expires_in", [True, 29, 901])
def test_authorization_refuses_invalid_lifetime(expires_in: int) -> None:
    plan = _plan()
    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=str(plan["plan_digest"]),
            nonce=NONCE,
            expires_in=expires_in,
            restart_pid=None,
        )
    _assert_code("invalid_expiry", caught.value)


def test_authorization_refuses_blocked_plan_before_any_authority() -> None:
    plan = _plan({"hub": "unavailable"})
    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=str(plan["plan_digest"]),
            nonce=NONCE,
            expires_in=300,
            restart_pid=4321,
        )
    _assert_code("plan_blocked", caught.value)


@pytest.mark.parametrize("pid", [None, 1, 2_147_483_648])
def test_authorization_requires_a_valid_pid_for_restart_authority(pid: int | None) -> None:
    plan = _plan({"hub": "fail"}, hub_pid=4321)
    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=str(plan["plan_digest"]),
            nonce=NONCE,
            expires_in=300,
            restart_pid=pid,
        )
    _assert_code("restart_authority_required", caught.value)


def test_authorization_refuses_restart_pid_that_widens_scope() -> None:
    plan = _plan({"waiter": "fail"})
    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=str(plan["plan_digest"]),
            nonce=NONCE,
            expires_in=300,
            restart_pid=4321,
        )
    _assert_code("unexpected_restart_authority", caught.value)


def test_authorization_refuses_invalid_clock_range() -> None:
    plan = _plan({"waiter": "fail"})
    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=str(plan["plan_digest"]),
            nonce=NONCE,
            expires_in=300,
            restart_pid=None,
            clock=lambda: -1.0,
        )
    _assert_code("invalid_expiry", caught.value)

    with pytest.raises(SetupAuthorizationError) as caught:
        build_setup_authorization(
            plan,
            confirm_digest=str(plan["plan_digest"]),
            nonce=NONCE,
            expires_in=300,
            restart_pid=None,
            clock=lambda: float("inf"),
        )
    _assert_code("invalid_expiry", caught.value)


def test_plan_loader_reads_one_regular_bounded_file(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    plan = _plan()
    _write_plan(path, plan)
    assert load_setup_plan(path) == plan


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b"not-json",
        b'{"schema_version":"synapse-setup.v1","schema_version":"duplicate"}',
        b'{"number":NaN}',
        b"\xff",
    ],
)
def test_plan_loader_refuses_invalid_json(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(payload)
    with pytest.raises(SetupAuthorizationError) as caught:
        load_setup_plan(path)
    _assert_code("invalid_plan", caught.value)


def test_plan_loader_refuses_missing_oversize_symlink_and_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (MAX_PLAN_BYTES + 1))
    real = tmp_path / "real.json"
    _write_plan(real, _plan())
    link = tmp_path / "link.json"
    link.symlink_to(real)

    for path in (missing, oversized, link, tmp_path):
        with pytest.raises(SetupAuthorizationError) as caught:
            load_setup_plan(path)
        _assert_code("invalid_plan", caught.value)


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda plan: plan.update(extra=True), "invalid_plan"),
        (lambda plan: plan.update(profile_version=2), "invalid_plan"),
        (lambda plan: plan.update(profile=7), "invalid_plan"),
        (lambda plan: plan.update(inspection_digest="short"), "invalid_plan"),
        (lambda plan: plan.update(profile_digest="0" * 64), "invalid_plan"),
        (
            lambda plan: plan.update(
                target={"uri": "http://bad", "project": "P", "identity": "P/a"}
            ),
            "invalid_plan",
        ),
        (lambda plan: plan.update(authority_required=["operator_confirmation"]), "invalid_plan"),
        (lambda plan: plan.update(effects="not-a-list"), "invalid_plan"),
        (lambda plan: plan.update(warnings=[]), "invalid_plan"),
        (lambda plan: plan.update(ready=False), "invalid_plan"),
        (lambda plan: plan.update(plan_digest="0" * 64), "invalid_plan"),
    ],
)
def test_plan_validator_rejects_tampering(mutator: object, expected: str) -> None:
    plan = _plan()
    assert callable(mutator)
    mutator(plan)
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_plan(plan)
    _assert_code(expected, caught.value)


def test_plan_validator_refuses_a_digest_consistent_apply_disposition_lie() -> None:
    plan = _plan({"waiter": "fail"})
    plan["can_apply"] = False
    unsigned = {key: item for key, item in plan.items() if key != "plan_digest"}
    plan["plan_digest"] = document_digest(unsigned)
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_plan(plan)
    _assert_code("invalid_plan", caught.value)


def test_plan_validator_rejects_effect_tampering() -> None:
    base = _plan({"identity": "fail"})
    cases = []
    for field, value in (
        ("id", "unknown"),
        ("trigger_check", "hub"),
        ("observed_status", "pass"),
        ("disposition", "planned"),
        ("process_id", 4321),
    ):
        plan = deepcopy(base)
        effects = plan["effects"]
        assert isinstance(effects, list)
        effects[0][field] = value
        unsigned = {key: item for key, item in plan.items() if key != "plan_digest"}
        plan["plan_digest"] = document_digest(unsigned)
        cases.append(plan)

    for plan in cases:
        with pytest.raises(SetupAuthorizationError) as caught:
            validate_setup_plan(plan)
        _assert_code("invalid_plan", caught.value)

    plan = deepcopy(base)
    plan["effects"] = ["not-an-object"]
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_plan(plan)
    _assert_code("invalid_plan", caught.value)

    plan = deepcopy(base)
    effects = plan["effects"]
    assert isinstance(effects, list)
    effects.append(deepcopy(effects[0]))
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_plan(plan)
    _assert_code("invalid_plan", caught.value)


def test_plan_validator_refuses_valid_but_nondefault_local_target() -> None:
    plan = _plan()
    target = plan["target"]
    assert isinstance(target, dict)
    target["uri"] = "ws://localhost:9999"
    unsigned = {key: item for key, item in plan.items() if key != "plan_digest"}
    plan["plan_digest"] = document_digest(unsigned)

    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_plan(plan)
    _assert_code("invalid_plan", caught.value)


def test_plan_validator_refuses_invalid_hub_process_id() -> None:
    plan = _plan({"hub": "fail"}, hub_pid=4321)
    effects = plan["effects"]
    assert isinstance(effects, list)
    effects[0]["process_id"] = 1
    unsigned = {key: item for key, item in plan.items() if key != "plan_digest"}
    plan["plan_digest"] = document_digest(unsigned)

    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_plan(plan)
    _assert_code("invalid_plan", caught.value)


def test_repeated_authority_is_listed_once() -> None:
    plan = _plan({"hub": "fail", "waiter": "fail"})
    assert plan["authority_required"] == ["operator_confirmation"]
    assert validate_setup_plan(plan) == plan


def test_authorization_validation_and_loader_bind_plan_time_and_digest(tmp_path: Path) -> None:
    plan = _plan({"waiter": "fail"})
    authorization = build_setup_authorization(
        plan,
        confirm_digest=str(plan["plan_digest"]),
        nonce=NONCE,
        expires_in=300,
        restart_pid=None,
        clock=lambda: 100.0,
    )
    assert validate_setup_authorization(plan, authorization, now=101) == authorization
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(authorization), encoding="utf-8")
    assert load_setup_authorization(path, plan=plan, now=101) == authorization

    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_authorization(plan, authorization, now=400)
    _assert_code("authorization_expired", caught.value)

    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_authorization(plan, authorization, now=99)
    _assert_code("invalid_authorization", caught.value)

    changed = deepcopy(authorization)
    changed["target"] = {
        "uri": "ws://127.0.0.1:8876",
        "project": "DEMO",
        "identity": "DEMO/codex-one",
    }
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_authorization(plan, changed, now=101)
    _assert_code("authorization_mismatch", caught.value)


def test_authorization_validator_refuses_structure_token_and_scope_tampering() -> None:
    plan = _plan({"waiter": "fail"})
    authorization = build_setup_authorization(
        plan,
        confirm_digest=str(plan["plan_digest"]),
        nonce=NONCE,
        expires_in=300,
        restart_pid=None,
        clock=lambda: 100.0,
    )

    malformed = deepcopy(authorization)
    malformed["extra"] = True
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_authorization(plan, malformed, now=101)
    _assert_code("invalid_authorization", caught.value)

    malformed = deepcopy(authorization)
    malformed["confirmation_nonce"] = "short"
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_authorization(plan, malformed, now=101)
    _assert_code("invalid_authorization", caught.value)

    widened = deepcopy(authorization)
    widened["restart_authority"] = {"pid": 4321}
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_authorization(plan, widened, now=101)
    _assert_code("authorization_mismatch", caught.value)

    changed = deepcopy(authorization)
    changed["authorization_digest"] = "0" * 64
    with pytest.raises(SetupAuthorizationError) as caught:
        validate_setup_authorization(plan, changed, now=101)
    _assert_code("authorization_mismatch", caught.value)


def test_authorization_loader_refuses_malformed_or_symlink_input(tmp_path: Path) -> None:
    plan = _plan({"waiter": "fail"})
    malformed = tmp_path / "authorization.json"
    malformed.write_text('{"duplicate":1,"duplicate":2}', encoding="utf-8")
    with pytest.raises(SetupAuthorizationError) as caught:
        load_setup_authorization(malformed, plan=plan, now=100)
    _assert_code("invalid_authorization", caught.value)

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(SetupAuthorizationError) as caught:
        load_setup_authorization(link, plan=plan, now=100)
    _assert_code("invalid_authorization", caught.value)
