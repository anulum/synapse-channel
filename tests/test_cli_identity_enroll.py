# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse identity enroll` and the hub's enrolment flags
"""The operator CLI for online enrolment, and the hub flags that enable it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from hub_e2e_helpers import running_hub
from synapse_channel import cli_processes
from synapse_channel.cli import build_parser
from synapse_channel.cli_identity import (
    _cmd_identity_enroll,
    _cmd_identity_revoke,
    _identity_enroll,
    _identity_revoke,
)
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from test_hub_identity_enrollment import OPERATOR, SEAT, Machines, _hub, _trust


def _factory(key: dict[str, Any]) -> Any:
    def build(name: str, callback: Any, **kwargs: Any) -> SynapseAgent:
        return SynapseAgent(name, callback, machine_identity=False, **key, **kwargs)

    return build


async def _enroll(uri: str, key: dict[str, Any], machines: Machines, **extra: Any) -> int:
    key_id, public = machines.public("seat")
    fields: dict[str, Any] = {
        "uri": uri,
        "operator": OPERATOR,
        "name": SEAT,
        "key_id": key_id,
        "public_key": public,
        "reason": "new seat",
        "expected_key_id": "",
        "expires_at": None,
        "token": None,
        "ready_timeout": 3.0,
        "result_timeout": 3.0,
        "json_output": False,
        "agent_factory": _factory(key),
    }
    fields.update(extra)
    return await _identity_enroll(**fields)


async def test_the_operator_cli_enrols_and_reports_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    operator = machines.kwargs("operator")
    async with running_hub(hub) as (_hub_ref, uri):
        assert await _enroll(uri, operator, machines, expires_at=4_000_000_000.0) == 0
        applied = capsys.readouterr().out
        assert await _enroll(uri, operator, machines, json_output=True) == 1
        refused = json.loads(capsys.readouterr().out)
        assert await _enroll(uri, operator, machines) == 1
        refused_text = capsys.readouterr().out
        rotate = await _enroll(uri, operator, machines, expected_key_id="none", result_timeout=0.0)
        silent = capsys.readouterr().out
        unproven = await _enroll(uri, {}, machines)
        unproven_text = capsys.readouterr().out
    assert hub.journal is not None
    hub.journal.close()
    assert "enrolled for 'PROJ/seat'" in applied and "(audit seq " in applied
    assert refused["applied"] is False and "already" in refused["detail"]
    assert refused_text.startswith("identity enrolment refused:")
    assert rotate == 2 and "no authoritative verdict" in silent
    assert unproven == 2 and OPERATOR in unproven_text


def test_the_enroll_command_parses_and_dispatches(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "identity",
            "enroll",
            SEAT,
            "--operator",
            OPERATOR,
            "--key-id",
            "k1",
            "--public-key",
            "AAAA",
            "--reason",
            "r",
            "--expires-at",
            "5",
            "--uri",
            "ws://127.0.0.1:9",
            "--ready-timeout",
            "0.2",
        ]
    )
    assert args.func is _cmd_identity_enroll
    assert (args.name, args.key_id, args.expected_key_id, args.expires_at) == (
        SEAT,
        "k1",
        "",
        5.0,
    )
    assert _cmd_identity_enroll(args) == 2  # nothing listens on port 9


def test_the_hub_flags_reach_the_hub(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    trust = _trust(tmp_path, machines, ("operator", OPERATOR))
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    args = build_parser().parse_args(
        [
            "hub",
            "--identity-enrollments",
            str(tmp_path / "enrolled.json"),
            "--identity-enrollment-namespace",
            "PROJ",
            "--identity-enrollment-namespace",
            "TEAM",
            "--identity-enrollment-rate",
            "3",
            "--identity-enrollment-window",
            "60",
        ]
    )
    namespace = _hub_ns(
        identity_trust=str(trust),
        require_identity_binding=True,
        db=str(tmp_path / "hub.db"),
        identity_enrollments=args.identity_enrollments,
        identity_enrollment_namespace=args.identity_enrollment_namespace,
        identity_enrollment_rate=args.identity_enrollment_rate,
        identity_enrollment_window=args.identity_enrollment_window,
    )
    assert cli_processes._cmd_hub(namespace, runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["identity_enrollment_path"] == str(tmp_path / "enrolled.json")
    assert captured["identity_enrollment_namespaces"] == ("PROJ", "TEAM")
    assert captured["identity_enrollment_rate"] == 3
    assert captured["identity_enrollment_window_seconds"] == 60.0
    defaults = build_parser().parse_args(["hub"])
    assert (defaults.identity_enrollments, defaults.identity_enrollment_namespace) == ("", [])


def test_the_hub_refuses_enrolment_without_a_journal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    machines = Machines(tmp_path / "machines")
    trust = _trust(tmp_path, machines, ("operator", OPERATOR))
    namespace = _hub_ns(
        identity_trust=str(trust),
        require_identity_binding=True,
        identity_enrollments=str(tmp_path / "enrolled.json"),
    )
    assert cli_processes._cmd_hub(namespace, runner=_close_runner) == 2
    assert "--identity-trust and --db" in capsys.readouterr().err


async def test_the_operator_cli_revokes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    operator = machines.kwargs("operator")
    key_id, _ = machines.public("seat")
    common: dict[str, Any] = {
        "uri": "",
        "operator": OPERATOR,
        "name": SEAT,
        "key_id": key_id,
        "reason": "retire",
        "token": None,
        "ready_timeout": 3.0,
        "result_timeout": 3.0,
        "json_output": False,
        "agent_factory": _factory(operator),
    }
    async with running_hub(hub) as (_hub_ref, uri):
        common["uri"] = uri
        assert await _enroll(uri, operator, machines) == 0
        capsys.readouterr()
        assert await _identity_revoke(**common) == 0
        applied = capsys.readouterr().out
        assert await _identity_revoke(**common) == 1
        refused = capsys.readouterr().out
    assert hub.journal is not None
    hub.journal.close()
    assert "revoked" in applied and "(audit seq " in applied
    assert refused.startswith("identity revocation refused:") and "already revoked" in refused


def test_the_revoke_command_parses_and_dispatches() -> None:
    args = build_parser().parse_args(
        [
            "identity",
            "revoke",
            SEAT,
            "--operator",
            OPERATOR,
            "--key-id",
            "k1",
            "--reason",
            "r",
            "--uri",
            "ws://127.0.0.1:9",
            "--ready-timeout",
            "0.2",
        ]
    )
    assert args.func is _cmd_identity_revoke
    assert (args.name, args.key_id, args.reason) == (SEAT, "k1", "r")
    assert _cmd_identity_revoke(args) == 2  # nothing listens on port 9
