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
from typing import Any

import pytest

from synapse_channel import cli, cli_setup


def test_setup_parser_routes_both_read_only_operations() -> None:
    spec = cli.build_parser().parse_args(
        ["setup", "spec", "--profile", "local-single-user", "--json"]
    )
    inspect = cli.build_parser().parse_args(
        ["setup", "inspect", "--profile", "local-single-user", "--json"]
    )
    assert spec.func is cli_setup._cmd_spec
    assert inspect.func is cli_setup._cmd_inspect


def test_setup_parser_exposes_no_secret_or_mutating_options() -> None:
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
