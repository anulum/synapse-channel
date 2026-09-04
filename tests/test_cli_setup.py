# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — machine-readable setup CLI tests

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_setup


def test_setup_parser_routes_every_setup_operation() -> None:
    spec = cli.build_parser().parse_args(
        ["setup", "spec", "--profile", "local-single-user", "--json"]
    )
    inspect = cli.build_parser().parse_args(
        ["setup", "inspect", "--profile", "local-single-user", "--json"]
    )
    plan = cli.build_parser().parse_args(
        ["setup", "plan", "--profile", "local-single-user", "--json"]
    )
    authorize = cli.build_parser().parse_args(
        [
            "setup",
            "authorize",
            "--plan",
            "plan.json",
            "--confirm-digest",
            "a" * 64,
            "--nonce",
            "0123456789abcdefghijkl",
            "--json",
        ]
    )
    apply = cli.build_parser().parse_args(
        [
            "setup",
            "apply",
            "--plan",
            "plan.json",
            "--authorization",
            "authorization.json",
            "--confirm-digest",
            "a" * 64,
            "--protect-pid",
            "1234",
            "--json",
        ]
    )
    assert spec.func is cli_setup._cmd_spec
    assert inspect.func is cli_setup._cmd_inspect
    assert plan.func is cli_setup._cmd_plan
    assert authorize.func is cli_setup._cmd_authorize
    assert apply.func is cli_setup._cmd_apply
    assert apply.protect_pid == [1234]


def test_setup_parser_exposes_no_secret_or_unbounded_mutation_options() -> None:
    parser = cli.build_parser(command="setup")
    help_text = parser.format_help()
    assert parser._subparsers is not None
    setup_action = parser._subparsers._group_actions[0]
    assert isinstance(setup_action, argparse._SubParsersAction)
    setup = setup_action.choices["setup"]
    setup_help = setup.format_help()
    assert setup._subparsers is not None
    nested_action = setup._subparsers._group_actions[0]
    assert isinstance(nested_action, argparse._SubParsersAction)
    nested = nested_action.choices
    all_help = help_text + setup_help + "".join(item.format_help() for item in nested.values())
    for forbidden in ("--token", "--token-file", "--apply", "--fix", "--start"):
        assert forbidden not in all_help


def test_spec_json_is_deterministic_and_parseable(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["setup", "spec", "--profile", "local-single-user", "--json"]
    assert cli.main(argv) == 0
    first = capsys.readouterr().out
    assert cli.main(argv) == 0
    second = capsys.readouterr().out
    assert first == second
    assert json.loads(first)["document_kind"] == "spec"


def test_unknown_profile_returns_stable_json_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["setup", "spec", "--profile", "future", "--json"]) == 2
    document = json.loads(capsys.readouterr().out)
    assert document["code"] == "unknown_profile"
    assert document["profile"] == "future"


def test_human_spec_and_error_render_without_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["setup", "spec", "--profile", "local-single-user"]) == 0
    output = capsys.readouterr().out
    assert "SYNAPSE setup spec: local-single-user v1" in output
    assert "package (required)" in output
    assert "service_manager (optional)" in output

    assert cli.main(["setup", "spec", "--profile", "future"]) == 2
    assert "setup error [unknown_profile]" in capsys.readouterr().err


def test_human_inspection_renders_ready_and_not_ready(
    capsys: pytest.CaptureFixture[str],
) -> None:
    base: dict[str, object] = {
        "document_kind": "inspection",
        "profile": "local-single-user",
        "profile_version": 1,
        "checks": [
            {"id": "hub", "status": "pass", "detail": "Hub answers."},
        ],
    }
    cli_setup._print_document({**base, "ready": True}, as_json=False)
    assert "result: ready (read-only)" in capsys.readouterr().out
    cli_setup._print_document({**base, "ready": False}, as_json=False)
    assert "result: not ready (read-only)" in capsys.readouterr().out


def test_human_plan_renders_digest_and_effects(capsys: pytest.CaptureFixture[str]) -> None:
    cli_setup._print_document(
        {
            "document_kind": "plan",
            "profile": "local-single-user",
            "profile_version": 1,
            "can_apply": True,
            "plan_digest": "a" * 64,
            "effects": [
                {
                    "id": "establish_identity_waiter",
                    "disposition": "planned",
                    "authority": "operator_confirmation",
                    "disruption": "service_start",
                }
            ],
        },
        as_json=False,
    )
    output = capsys.readouterr().out
    assert "1 proposed effect(s); applicable" in output
    assert f"digest: {'a' * 64}" in output
    assert "planned establish_identity_waiter" in output


def test_human_authorization_is_explicitly_output_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_setup._print_document(
        {
            "document_kind": "authorization",
            "profile": "local-single-user",
            "profile_version": 1,
            "authorization_digest": "a" * 64,
            "plan_digest": "b" * 64,
            "expires_at": 1234,
        },
        as_json=False,
    )
    output = capsys.readouterr().out
    assert "single use; pass with its exact plan" in output
    assert f"digest: {'a' * 64}" in output


def test_human_application_receipt_reports_outcome_and_ledger(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_setup._print_document(
        {
            "document_kind": "application_receipt",
            "profile": "local-single-user",
            "profile_version": 1,
            "outcome": "recovered",
            "receipt_digest": "a" * 64,
            "ledger_state": "recovered",
        },
        as_json=False,
    )
    output = capsys.readouterr().out
    assert "outcome: recovered" in output
    assert "ledger: recovered" in output


def test_unknown_inspect_profile_returns_stable_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(
        profile="future",
        uri="ws://localhost:8876",
        project=None,
        id=None,
        json=True,
    )
    assert cli_setup._cmd_inspect(args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "unknown_profile"


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:8876",
        "ws://user:secret@localhost:8876",
        "ws://localhost:8876/?token=secret",
        "ws://localhost:99999",
    ],
)
def test_inspect_refuses_non_websocket_and_secret_bearing_uris(
    uri: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(
        profile="local-single-user",
        uri=uri,
        project=None,
        id=None,
        json=True,
    )
    assert cli_setup._cmd_inspect(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "invalid_uri"
    assert "secret" not in output


def test_inspect_exit_code_tracks_readiness(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def inspect_stub(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {
            "document_kind": "inspection",
            "profile": "local-single-user",
            "profile_version": 1,
            "ready": False,
        }

    monkeypatch.setattr(cli_setup, "inspect_setup", inspect_stub)
    args = argparse.Namespace(
        profile="local-single-user",
        uri="ws://localhost:8876",
        project=None,
        id=None,
        json=True,
    )
    assert cli_setup._cmd_inspect(args) == 1
    assert json.loads(capsys.readouterr().out)["ready"] is False


def test_inspect_failure_returns_bounded_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def inspect_stub(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise RuntimeError("Bearer secret")

    monkeypatch.setattr(cli_setup, "inspect_setup", inspect_stub)
    args = argparse.Namespace(
        profile="local-single-user",
        uri="ws://localhost:8876",
        project=None,
        id=None,
        json=True,
    )
    assert cli_setup._cmd_inspect(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "inspection_failed"
    assert "secret" not in output


def test_plan_builds_from_read_only_inspection_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def inspect_stub(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {"inspection": "fixture"}

    expected = {
        "schema_version": "synapse-setup.v1",
        "document_kind": "plan",
        "profile": "local-single-user",
        "profile_version": 1,
        "ready": False,
        "can_apply": False,
        "effects": [],
        "plan_digest": "b" * 64,
    }
    monkeypatch.setattr(cli_setup, "inspect_setup", inspect_stub)
    monkeypatch.setattr(cli_setup, "build_setup_plan", lambda *_args: expected)
    args = argparse.Namespace(
        profile="local-single-user",
        uri="ws://localhost:8876",
        project="DEMO",
        id="one",
        json=True,
    )
    assert cli_setup._cmd_plan(args) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_plan_refuses_unknown_profile_and_invalid_uri(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(
        profile="future",
        uri="ws://localhost:8876",
        project=None,
        id=None,
        json=True,
    )
    assert cli_setup._cmd_plan(args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "unknown_profile"

    args.profile = "local-single-user"
    args.uri = "ws://user:secret@localhost:8876"
    assert cli_setup._cmd_plan(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "invalid_uri"
    assert "secret" not in output


def test_plan_failure_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def inspect_stub(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise RuntimeError("Bearer secret")

    monkeypatch.setattr(cli_setup, "inspect_setup", inspect_stub)
    args = argparse.Namespace(
        profile="local-single-user",
        uri="ws://localhost:8876",
        project=None,
        id=None,
        json=True,
    )
    assert cli_setup._cmd_plan(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "planning_failed"
    assert "secret" not in output


def test_authorize_emits_document_from_validated_plan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = {"profile": "local-single-user", "plan_digest": "a" * 64}
    expected = {
        "document_kind": "authorization",
        "profile": "local-single-user",
        "profile_version": 1,
        "authorization_digest": "b" * 64,
    }
    monkeypatch.setattr(cli_setup, "load_setup_plan", lambda _path: plan)
    monkeypatch.setattr(cli_setup, "build_setup_authorization", lambda *_args, **_kwargs: expected)
    args = argparse.Namespace(
        plan=Path("plan.json"),
        confirm_digest="a" * 64,
        nonce="0123456789abcdefghijkl",
        expires_in=300,
        authorize_restart_pid=None,
        json=True,
    )
    assert cli_setup._cmd_authorize(args) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_authorize_returns_stable_bounded_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from synapse_channel.setup_authorization import SetupAuthorizationError

    def refused(_path: object) -> dict[str, object]:
        raise SetupAuthorizationError("invalid_plan")

    monkeypatch.setattr(cli_setup, "load_setup_plan", refused)
    args = argparse.Namespace(
        plan=Path("Bearer-secret.json"),
        confirm_digest="a" * 64,
        nonce="0123456789abcdefghijkl",
        expires_in=300,
        authorize_restart_pid=None,
        json=True,
    )
    assert cli_setup._cmd_authorize(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "invalid_plan"
    assert "secret" not in output

    monkeypatch.setattr(cli_setup, "load_setup_plan", lambda _path: {"profile": 7})
    monkeypatch.setattr(
        cli_setup,
        "build_setup_authorization",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SetupAuthorizationError("invalid_plan")),
    )
    assert cli_setup._cmd_authorize(args) == 2
    assert json.loads(capsys.readouterr().out)["profile"] == "unknown"

    monkeypatch.setattr(cli_setup, "load_setup_plan", lambda _path: {"profile": "safe"})
    monkeypatch.setattr(
        cli_setup,
        "build_setup_authorization",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Bearer secret")),
    )
    assert cli_setup._cmd_authorize(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "authorization_failed"
    assert "secret" not in output


def test_apply_missing_plan_uses_the_real_public_cli_error_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "Bearer-secret-plan.json"
    assert (
        cli.main(
            [
                "setup",
                "apply",
                "--plan",
                str(missing),
                "--authorization",
                str(tmp_path / "authorization.json"),
                "--confirm-digest",
                "a" * 64,
                "--json",
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "invalid_plan"
    assert "secret" not in output


def test_apply_cli_projects_success_and_recovery_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = {"profile": "local-single-user", "plan_digest": "a" * 64}
    authorization = {"authorization_digest": "b" * 64}
    monkeypatch.setattr(cli_setup, "load_setup_plan", lambda _path: plan)
    monkeypatch.setattr(
        cli_setup,
        "load_setup_authorization",
        lambda _path, **_kwargs: authorization,
    )

    async def result(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "document_kind": "application_receipt",
            "profile": "local-single-user",
            "profile_version": 1,
            "outcome": "applied",
            "receipt_digest": "c" * 64,
            "ledger_state": "applied",
        }

    monkeypatch.setattr(cli_setup, "apply_setup", result)
    args = argparse.Namespace(
        plan=Path("plan.json"),
        authorization=Path("authorization.json"),
        confirm_digest="a" * 64,
        protect_pid=[1234],
        receipt=None,
        json=True,
    )
    assert cli_setup._cmd_apply(args) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "applied"

    async def recovered(*_args: object, **_kwargs: object) -> dict[str, object]:
        document = await result()
        document["outcome"] = "recovered"
        document["ledger_state"] = "recovered"
        return document

    monkeypatch.setattr(cli_setup, "apply_setup", recovered)
    assert cli_setup._cmd_apply(args) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "recovered"


def test_apply_cli_bounds_executor_authorization_and_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from synapse_channel.setup_authorization import SetupAuthorizationError
    from synapse_channel.setup_executor import SetupExecutionError

    plan = {"profile": "local-single-user", "plan_digest": "a" * 64}
    authorization = {"authorization_digest": "b" * 64}
    monkeypatch.setattr(cli_setup, "load_setup_plan", lambda _path: plan)
    monkeypatch.setattr(
        cli_setup,
        "load_setup_authorization",
        lambda _path, **_kwargs: authorization,
    )
    args = argparse.Namespace(
        plan=Path("plan.json"),
        authorization=Path("authorization.json"),
        confirm_digest="a" * 64,
        protect_pid=[],
        receipt=None,
        json=True,
    )

    async def execution_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise SetupExecutionError("application_target_changed")

    monkeypatch.setattr(cli_setup, "apply_setup", execution_error)
    assert cli_setup._cmd_apply(args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "application_target_changed"

    receipt = {
        "document_kind": "application_receipt",
        "profile": "local-single-user",
        "profile_version": 1,
        "outcome": "applied",
        "receipt_digest": "c" * 64,
        "ledger_state": "applied",
    }

    async def receipt_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise SetupExecutionError("application_receipt_unavailable", receipt=receipt)

    monkeypatch.setattr(cli_setup, "apply_setup", receipt_error)
    assert cli_setup._cmd_apply(args) == 2
    assert json.loads(capsys.readouterr().out)["outcome"] == "applied"

    def authorization_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise SetupAuthorizationError("authorization_expired")

    monkeypatch.setattr(cli_setup, "load_setup_authorization", authorization_error)
    assert cli_setup._cmd_apply(args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "authorization_expired"

    monkeypatch.setattr(cli_setup, "load_setup_plan", lambda _path: {"profile": 7})
    monkeypatch.setattr(
        cli_setup,
        "load_setup_authorization",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Bearer secret")),
    )
    assert cli_setup._cmd_apply(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "application_effect_failed"
    assert "secret" not in output
