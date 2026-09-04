# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict setup verification document tests

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.setup_authorization import build_setup_authorization
from synapse_channel.setup_contract import document_digest, setup_schema
from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile
from synapse_channel.setup_verification import (
    SetupVerificationError,
    SetupVerificationLedger,
    build_verification_authorization,
    build_verification_plan,
    default_verification_ledger_dir,
    load_application_receipt,
    load_historical_setup_authorization,
    load_verification_authorization,
    load_verification_plan,
    validate_application_receipt,
    validate_verification_authorization,
    validate_verification_plan,
)

NONCE = "verification_nonce_0123456789"


def _profile() -> SetupProfile:
    profile = get_setup_profile("local-single-user")
    assert profile is not None
    return profile


def inspection(*, waiter: str, hub_pid: int = 4321) -> dict[str, object]:
    profile = _profile()
    statuses = {item.requirement_id: "pass" for item in profile.requirements}
    statuses["waiter"] = waiter
    values: dict[str, object] = {
        "package": {"name": "synapse-channel", "version": "0.99.24"},
        "python": {"executable": sys.executable, "version": "3.12.0"},
        "platform": {"system": "Linux", "release": "test", "machine": "x86_64"},
        "executable": "/usr/bin/true",
        "identity": {"project": "DEMO", "identity": "DEMO/codex-one"},
        "hub": {"uri": "ws://localhost:8876"},
        "waiter": {"identity": "DEMO/codex-one-rx"},
        "service_manager": {
            "kind": "systemd-user",
            "executable": "/usr/bin/true",
            "hub_pid": hub_pid,
        },
    }
    checks = [
        {
            "id": item.requirement_id,
            "status": statuses[item.requirement_id],
            "required": item.required,
            "value": values[item.requirement_id],
            "detail": "Observed through the setup inspection contract.",
            "remedy": "",
        }
        for item in profile.requirements
    ]
    return {
        "schema_version": "synapse-setup.v1",
        "document_kind": "inspection",
        "profile": "local-single-user",
        "profile_version": 1,
        "read_only": True,
        "ready": waiter == "pass",
        "target": {
            "uri": "ws://localhost:8876",
            "project": "DEMO",
            "identity": "DEMO/codex-one",
        },
        "summary": {
            name: sum(check["status"] == name for check in checks)
            for name in ("pass", "warn", "fail", "unavailable")
        },
        "checks": checks,
    }


def setup_documents() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    plan = build_setup_plan(_profile(), inspection(waiter="fail"))
    authorization = build_setup_authorization(
        plan,
        confirm_digest=cast(str, plan["plan_digest"]),
        nonce="application_nonce_0123456789",
        expires_in=300,
        restart_pid=None,
        clock=lambda: 100.0,
    )
    receipt: dict[str, object] = {
        "schema_version": "synapse-setup.v1",
        "document_kind": "application_receipt",
        "profile": "local-single-user",
        "profile_version": 1,
        "plan_digest": plan["plan_digest"],
        "authorization_digest": authorization["authorization_digest"],
        "target": plan["target"],
        "started_at": 101,
        "completed_at": 102,
        "outcome": "applied",
        "ledger_state": "applied",
        "effects": [
            {
                "id": "establish_identity_waiter",
                "unit": "synapse-arm@DEMO-codex-one.service",
                "outcome": "applied",
            }
        ],
        "protected_processes": [{"pid": 2222, "before_alive": True, "after_alive": True}],
        "recovery": "not_required",
        "effect_receipt_digest": None,
    }
    receipt["receipt_digest"] = document_digest(receipt)
    verification_plan = build_verification_plan(
        plan,
        authorization,
        receipt,
        inspection(waiter="pass"),
    )
    verification_authorization = build_verification_authorization(
        verification_plan,
        confirm_digest=cast(str, verification_plan["verification_plan_digest"]),
        nonce=NONCE,
        expires_in=300,
        restart_pid=4321,
        clock=lambda: 200.0,
    )
    return plan, authorization, receipt, verification_plan, verification_authorization


def _write(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def test_verification_documents_are_bound_deterministic_and_schema_valid() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    plan, authorization, receipt, verification_plan, verification_authorization = setup_documents()
    assert validate_application_receipt(plan, authorization, receipt) == receipt
    assert validate_verification_plan(verification_plan) == verification_plan
    assert (
        validate_verification_authorization(
            verification_plan,
            verification_authorization,
            now=201,
        )
        == verification_authorization
    )
    jsonschema.validate(verification_plan, setup_schema())
    jsonschema.validate(verification_authorization, setup_schema())
    rebuilt = build_verification_plan(
        plan,
        authorization,
        receipt,
        inspection(waiter="pass"),
    )
    assert rebuilt == verification_plan


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "recovered"),
        ("ledger_state", "failed"),
        ("recovery", "complete"),
        ("effect_receipt_digest", "a" * 64),
        ("plan_digest", "a" * 64),
        ("authorization_digest", "b" * 64),
        ("target", {"uri": "ws://localhost:1", "project": "DEMO", "identity": "DEMO/x"}),
        ("started_at", -1),
        ("completed_at", 99),
        ("effects", "bad"),
        ("protected_processes", []),
        ("receipt_digest", "bad"),
    ],
)
def test_application_receipt_refuses_non_success_or_changed_evidence(
    field: str,
    value: object,
) -> None:
    plan, authorization, receipt, _verification_plan, _verification_authorization = (
        setup_documents()
    )
    changed = {**receipt, field: value}
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(plan, authorization, changed)


@pytest.mark.parametrize(
    "effects",
    [
        [{"id": "unknown", "unit": "unit", "outcome": "applied"}],
        [{"id": "establish_identity_waiter", "unit": "", "outcome": "applied"}],
        [{"id": "establish_identity_waiter", "unit": "unit", "outcome": "already_satisfied"}],
        [{"id": "establish_identity_waiter", "unit": "unit", "outcome": "failed"}],
        [
            {"id": "establish_identity_waiter", "unit": "unit", "outcome": "applied"},
            {"id": "establish_identity_waiter", "unit": "unit", "outcome": "applied"},
        ],
        [{"id": "establish_identity_waiter", "unit": "unit"}],
    ],
)
def test_application_receipt_refuses_invalid_effect_evidence(effects: object) -> None:
    plan, authorization, receipt, _verification_plan, _verification_authorization = (
        setup_documents()
    )
    changed = {**receipt, "effects": effects}
    changed["receipt_digest"] = document_digest(
        {key: value for key, value in changed.items() if key != "receipt_digest"}
    )
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(plan, authorization, changed)


@pytest.mark.parametrize(
    "processes",
    [
        "bad",
        [{"pid": 1, "before_alive": True, "after_alive": True}],
        [{"pid": True, "before_alive": True, "after_alive": True}],
        [{"pid": 2, "before_alive": False, "after_alive": True}],
        [{"pid": 2, "before_alive": True, "after_alive": False}],
        [{"pid": 2, "before_alive": True}],
        [
            {"pid": 2, "before_alive": True, "after_alive": True},
            {"pid": 2, "before_alive": True, "after_alive": True},
        ],
    ],
)
def test_application_receipt_refuses_invalid_process_evidence(processes: object) -> None:
    plan, authorization, receipt, _verification_plan, _verification_authorization = (
        setup_documents()
    )
    changed = {**receipt, "protected_processes": processes}
    changed["receipt_digest"] = document_digest(
        {key: value for key, value in changed.items() if key != "receipt_digest"}
    )
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(plan, authorization, changed)


def test_application_receipt_accepts_already_satisfied_effect() -> None:
    plan, authorization, receipt, _verification_plan, _verification_authorization = (
        setup_documents()
    )
    changed = {
        **receipt,
        "effects": [
            {
                "id": "establish_local_loopback_hub",
                "unit": "",
                "outcome": "already_satisfied",
            }
        ],
    }
    changed["receipt_digest"] = document_digest(
        {key: value for key, value in changed.items() if key != "receipt_digest"}
    )
    assert validate_application_receipt(plan, authorization, changed) == changed


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"read_only": False}, "invalid_verification_plan"),
        ({"profile": "other"}, "invalid_verification_plan"),
        ({"current_hub_pid": True}, "invalid_verification_plan"),
        ({"current_hub_pid": 1}, "invalid_verification_plan"),
        ({"required_checks": []}, "invalid_verification_plan"),
        ({"warnings": []}, "invalid_verification_plan"),
        ({"plan_digest": "bad"}, "invalid_verification_plan"),
        ({"verification_plan_digest": "a" * 64}, "invalid_verification_plan"),
    ],
)
def test_verification_plan_refuses_changed_contract(
    mutation: dict[str, object],
    code: str,
) -> None:
    _plan, _authorization, _receipt, verification_plan, _verification_authorization = (
        setup_documents()
    )
    with pytest.raises(SetupVerificationError, match=code):
        validate_verification_plan({**verification_plan, **mutation})


@pytest.mark.parametrize(
    "mutation",
    [
        {"ready": False},
        {"document_kind": "error"},
        {"target": {"uri": "ws://localhost:8876", "project": "DEMO", "identity": "DEMO/x"}},
        {"checks": []},
    ],
)
def test_verification_plan_requires_exact_ready_inspection(
    mutation: dict[str, object],
) -> None:
    plan, authorization, receipt, _verification_plan, _verification_authorization = (
        setup_documents()
    )
    with pytest.raises(SetupVerificationError, match="verification_target_changed"):
        build_verification_plan(
            plan,
            authorization,
            receipt,
            {**inspection(waiter="pass"), **mutation},
        )


def test_verification_authorization_refuses_bad_inputs_and_time() -> None:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    for kwargs, code in (
        ({"confirm_digest": "a" * 64}, "digest_mismatch"),
        ({"nonce": "short"}, "invalid_nonce"),
        ({"expires_in": 29}, "invalid_expiry"),
        ({"expires_in": True}, "invalid_expiry"),
        ({"restart_pid": 9999}, "authorization_mismatch"),
        ({"restart_pid": True}, "authorization_mismatch"),
    ):
        values: dict[str, object] = {
            "confirm_digest": verification_plan["verification_plan_digest"],
            "nonce": NONCE,
            "expires_in": 300,
            "restart_pid": 4321,
        }
        values.update(kwargs)
        with pytest.raises(SetupVerificationError, match=code):
            build_verification_authorization(verification_plan, **values)  # type: ignore[arg-type]
    with pytest.raises(SetupVerificationError, match="authorization_expired"):
        validate_verification_authorization(
            verification_plan,
            verification_authorization,
            now=500,
        )
    with pytest.raises(SetupVerificationError, match="invalid_verification_authorization"):
        validate_verification_authorization(
            verification_plan,
            {**verification_authorization, "issued_at": 300},
            now=201,
        )


def test_loaders_reject_symlinks_duplicates_oversize_and_non_objects(tmp_path: Path) -> None:
    plan, authorization, receipt, verification_plan, verification_authorization = setup_documents()
    paths = [tmp_path / name for name in ("auth.json", "receipt.json", "plan.json", "vauth.json")]
    for path, document in zip(
        paths,
        (authorization, receipt, verification_plan, verification_authorization),
        strict=True,
    ):
        _write(path, document)
    assert load_historical_setup_authorization(paths[0], plan=plan) == authorization
    assert load_application_receipt(paths[1], plan=plan, authorization=authorization) == receipt
    assert load_verification_plan(paths[2]) == verification_plan
    assert (
        load_verification_authorization(paths[3], verification_plan=verification_plan, now=201)
        == verification_authorization
    )

    bad = tmp_path / "bad.json"
    bad.write_text('{"a":1,"a":2}', encoding="utf-8")
    loaders: tuple[Callable[[], object], ...] = (
        lambda: load_verification_plan(bad),
        lambda: load_verification_authorization(bad, verification_plan=verification_plan, now=201),
    )
    for loader in loaders:
        with pytest.raises(SetupVerificationError):
            loader()
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(SetupVerificationError):
        load_verification_plan(bad)
    bad.write_bytes(b"{" + b" " * 65_536 + b"}")
    with pytest.raises(SetupVerificationError):
        load_verification_plan(bad)
    link = tmp_path / "link.json"
    link.symlink_to(paths[2])
    with pytest.raises(SetupVerificationError):
        load_verification_plan(link)


def test_default_ledger_directory_and_single_use_transitions(tmp_path: Path) -> None:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    assert default_verification_ledger_dir(env={"XDG_STATE_HOME": "/state"}) == Path(
        "/state/synapse-channel"
    )
    assert default_verification_ledger_dir(env={"HOME": "/home/demo"}) == Path(
        "/home/demo/.local/state/synapse-channel"
    )
    with pytest.raises(SetupVerificationError, match="verification_ledger_unavailable"):
        default_verification_ledger_dir(env={"XDG_STATE_HOME": "relative"})

    ledger_dir = tmp_path / "ledger"
    with SetupVerificationLedger(ledger_dir) as ledger:
        ledger.reserve(verification_plan, verification_authorization, now=201)
        with pytest.raises(SetupVerificationError, match="verification_authorization_replayed"):
            ledger.reserve(verification_plan, verification_authorization, now=201)
        ledger.finish(
            cast(str, verification_authorization["verification_authorization_digest"]),
            outcome="verified",
            receipt_digest="a" * 64,
        )
        with pytest.raises(SetupVerificationError, match="authorization_transition_invalid"):
            ledger.finish("missing", outcome="failed", receipt_digest="b" * 64)
        with pytest.raises(SetupVerificationError, match="authorization_transition_invalid"):
            ledger.finish("missing", outcome="other", receipt_digest="b" * 64)
    assert stat_mode(ledger_dir) == 0o700
    assert stat_mode(ledger_dir / "setup-verification-v1.sqlite3") == 0o600
    with sqlite3.connect(ledger_dir / "setup-verification-v1.sqlite3") as connection:
        (nonce_digest,) = connection.execute(
            "SELECT nonce_digest FROM verification_authorizations"
        ).fetchone()
    assert nonce_digest != NONCE
    assert len(nonce_digest) == 64


def test_verification_receipt_schema_requires_exact_check_order() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    receipt: dict[str, object] = {
        "schema_version": "synapse-setup.v1",
        "document_kind": "verification_receipt",
        "profile": "local-single-user",
        "profile_version": 1,
        "verification_plan_digest": verification_plan["verification_plan_digest"],
        "verification_authorization_digest": verification_authorization[
            "verification_authorization_digest"
        ],
        "plan_digest": verification_plan["plan_digest"],
        "authorization_digest": verification_plan["authorization_digest"],
        "application_receipt_digest": verification_plan["application_receipt_digest"],
        "receipt_digest": "a" * 64,
        "target": verification_plan["target"],
        "generation": verification_plan["generation"],
        "started_at": 201,
        "completed_at": 202,
        "outcome": "verified",
        "ledger_state": "verified",
        "canary_id": "b" * 32,
        "message_seq": 1,
        "chat_digest": "c" * 64,
        "ack_event_seq": 2,
        "ack_digest": "d" * 64,
        "hub_pid_before": 4321,
        "hub_pid_after": 4322,
        "protected_processes": [{"pid": 2222, "before_alive": True, "after_alive": True}],
        "checks": [
            {"id": check, "status": "pass"}
            for check in cast(list[str], verification_plan["required_checks"])
        ],
        "failure_code": None,
    }
    jsonschema.validate(receipt, setup_schema())
    changed = {**receipt, "checks": list(reversed(cast(list[object], receipt["checks"])))}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(changed, setup_schema())


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_ledger_refuses_unsafe_directory_and_leaf(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    unsafe = tmp_path / "unsafe"
    unsafe.symlink_to(actual, target_is_directory=True)
    with pytest.raises(SetupVerificationError, match="verification_ledger_unavailable"):
        with SetupVerificationLedger(unsafe):
            pass

    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    (safe / "setup-verification-v1.sqlite3").mkdir()
    with pytest.raises(SetupVerificationError, match="verification_ledger_unavailable"):
        with SetupVerificationLedger(safe):
            pass


def test_ledger_closed_and_database_errors_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    ledger = SetupVerificationLedger(tmp_path / "closed")
    with pytest.raises(SetupVerificationError, match="verification_ledger_unavailable"):
        ledger.reserve(verification_plan, verification_authorization, now=201)
    with pytest.raises(SetupVerificationError, match="verification_ledger_unavailable"):
        ledger.finish("a" * 64, outcome="failed", receipt_digest="b" * 64)

    def unavailable(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("unavailable")

    monkeypatch.setattr(sqlite3, "connect", unavailable)
    with pytest.raises(SetupVerificationError, match="verification_ledger_unavailable"):
        with SetupVerificationLedger(tmp_path / "broken"):
            pass


def test_application_receipt_rejects_malformed_ancestry_and_exact_digest() -> None:
    plan, authorization, receipt, _verification_plan, _verification_authorization = (
        setup_documents()
    )
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(plan, {**authorization, "issued_at": True}, receipt)
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(
            plan,
            {**authorization, "authorization_digest": "a" * 64},
            receipt,
        )
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(plan, authorization, {**receipt, "extra": True})

    changed = {**receipt, "receipt_digest": "a" * 64}
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(plan, authorization, changed)

    changed = {
        **receipt,
        "effects": [
            {
                "id": "establish_identity_waiter",
                "unit": 7,
                "outcome": "applied",
            }
        ],
    }
    changed["receipt_digest"] = document_digest(
        {key: value for key, value in changed.items() if key != "receipt_digest"}
    )
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(plan, authorization, changed)


def test_historical_authorization_and_inspection_shape_fail_closed(tmp_path: Path) -> None:
    plan, authorization, receipt, _verification_plan, _verification_authorization = (
        setup_documents()
    )
    auth_path = tmp_path / "authorization.json"
    _write(auth_path, {**authorization, "issued_at": False})
    with pytest.raises(SetupVerificationError, match="invalid_authorization"):
        load_historical_setup_authorization(auth_path, plan=plan)

    _write(auth_path, {**authorization, "authorization_digest": "a" * 64})
    with pytest.raises(SetupVerificationError, match="authorization_mismatch"):
        load_historical_setup_authorization(auth_path, plan=plan)

    ready = inspection(waiter="pass")
    cases = (
        {**ready, "checks": "bad"},
        {**ready, "checks": []},
        {
            **ready,
            "checks": [
                {
                    **cast(dict[str, object], item),
                    "value": {},
                }
                if isinstance(item, dict) and item.get("id") == "service_manager"
                else item
                for item in cast(list[object], ready["checks"])
            ],
        },
    )
    for candidate in cases:
        with pytest.raises(SetupVerificationError, match="verification_target_changed"):
            build_verification_plan(plan, authorization, receipt, candidate)


def test_verification_documents_reject_extra_fields_bad_clock_and_digest() -> None:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    with pytest.raises(SetupVerificationError, match="invalid_verification_plan"):
        validate_verification_plan({**verification_plan, "extra": True})

    for clock in (lambda: float("nan"), lambda: -1.0, lambda: float("inf")):
        with pytest.raises(SetupVerificationError, match="invalid_expiry"):
            build_verification_authorization(
                verification_plan,
                confirm_digest=cast(str, verification_plan["verification_plan_digest"]),
                nonce=f"{NONCE}_clock",
                expires_in=300,
                restart_pid=4321,
                clock=clock,
            )

    with pytest.raises(SetupVerificationError, match="invalid_verification_authorization"):
        validate_verification_authorization(
            verification_plan,
            {**verification_authorization, "extra": True},
            now=201,
        )
    with pytest.raises(SetupVerificationError, match="authorization_mismatch"):
        validate_verification_authorization(
            verification_plan,
            {**verification_authorization, "verification_authorization_digest": "a" * 64},
            now=201,
        )


def test_loader_rejects_non_finite_json_and_home_fallback(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"value":NaN}', encoding="utf-8")
    with pytest.raises(SetupVerificationError, match="invalid_verification_plan"):
        load_verification_plan(bad)
    assert default_verification_ledger_dir(env={}).is_absolute()


def test_ledger_database_operation_errors_are_bounded(tmp_path: Path) -> None:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    reserve_dir = tmp_path / "reserve"
    with SetupVerificationLedger(reserve_dir) as ledger:
        with sqlite3.connect(reserve_dir / "setup-verification-v1.sqlite3") as connection:
            connection.execute("DROP TABLE verification_authorizations")
        with pytest.raises(SetupVerificationError, match="verification_ledger_unavailable"):
            ledger.reserve(verification_plan, verification_authorization, now=201)

    finish_dir = tmp_path / "finish"
    with SetupVerificationLedger(finish_dir) as ledger:
        ledger.reserve(verification_plan, verification_authorization, now=201)
        with sqlite3.connect(finish_dir / "setup-verification-v1.sqlite3") as connection:
            connection.execute("DROP TABLE verification_authorizations")
        with pytest.raises(SetupVerificationError, match="verification_ledger_unavailable"):
            ledger.finish(
                cast(str, verification_authorization["verification_authorization_digest"]),
                outcome="failed",
                receipt_digest="a" * 64,
            )
