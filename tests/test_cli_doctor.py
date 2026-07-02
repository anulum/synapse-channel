# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse doctor` CLI command

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_doctor
from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.core.hub import SynapseHub

# --- parser ------------------------------------------------------------------


def test_parser_doctor_defaults() -> None:
    args = cli.build_parser().parse_args(["doctor"])
    assert args.func is cli_doctor._cmd_doctor
    assert args.uri.endswith(":8876")
    assert args.project is None
    assert args.id is None
    assert args.send_name is None


def test_parser_doctor_has_token_file_companion() -> None:
    args = cli.build_parser().parse_args(["doctor", "--token-file", "/tmp/tok"])
    assert args.token_file == "/tmp/tok"


def test_parser_doctor_fix_flags() -> None:
    args = cli.build_parser().parse_args(
        ["doctor", "--fix", "--install-user-services", "--identity", "repo/ux"]
    )
    assert args.fix is True
    assert args.install_user_services is True
    assert args.identity == "repo/ux"


# --- _diagnose logic ---------------------------------------------------------


async def test_diagnose_reachable_with_waiter_passes() -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        waiter = await connect_agent("demorepo-rx", uri)
        try:
            code, lines, _ = await cli_doctor._diagnose(
                uri=uri,
                project="demorepo",
                agent_id=None,
                token=None,
            )
        finally:
            await close_agents(waiter)
    text = "\n".join(lines)
    assert "[ok] hub:" in text
    assert "[ok] waiter:" in text
    assert code == 0


async def test_diagnose_reachable_without_waiter_warns() -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        code, lines, _ = await cli_doctor._diagnose(
            uri=uri,
            project="demorepo",
            agent_id=None,
            token=None,
        )
    text = "\n".join(lines)
    assert "no waiter 'demorepo-rx'" in text
    assert code == 0  # a missing waiter warns but does not fail


async def test_diagnose_unreachable_fails() -> None:
    code, lines, diagnoses = await cli_doctor._diagnose(
        uri=f"ws://127.0.0.1:{_free_port()}",
        project="demorepo",
        agent_id=None,
        token=None,
        ready_timeout=0.1,
    )
    text = "\n".join(lines)
    assert "did not answer" in text
    assert "[warn] waiter:" in text  # unreachable also blocks the waiter check
    assert code == 1
    assert [d.check for d in diagnoses if d.status == "fail"] == ["hub"]


async def test_diagnose_flags_off_loopback_without_token_and_disk_pressure(
    tmp_path: Path,
) -> None:
    async def no_roster(**_: Any) -> list[str] | None:
        return []

    code, lines, _ = await cli_doctor._diagnose(
        uri="ws://10.0.0.5:8876",
        project="demorepo",
        agent_id=None,
        token=None,
        roster_probe=no_roster,
        disk_path=tmp_path,
        disk_warn_used_percent=0.0,
        disk_warn_free_mib=1,
    )
    assert code == 0
    assert any("off loopback with no token" in line for line in lines)
    assert any("[warn] disk:" in line for line in lines)
    assert any(str(tmp_path) in line for line in lines)


async def test_diagnose_warns_on_hyphen_send_identity() -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        _, lines, _ = await cli_doctor._diagnose(
            uri=uri,
            project="demorepo",
            agent_id=None,
            token=None,
            send_name="demorepo-keeper",
        )
    assert any("hyphen child" in line for line in lines)


# --- dispatch ----------------------------------------------------------------


def _doctor_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "uri": "ws://h",
        "project": None,
        "id": None,
        "token": None,
        "send_name": None,
        "disk_path": "/",
        "disk_warn_used_percent": 95.0,
        "disk_warn_free_mib": 1024,
        "fix": False,
        "install_user_services": False,
        "start_user_services": False,
        "identity": None,
        "synapse_bin": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _fail(check: str) -> Diagnosis:
    return Diagnosis(check=check, status="fail", detail="nope")


def _warn(check: str) -> Diagnosis:
    return Diagnosis(check=check, status="warn", detail="wobbly")


def _pass(check: str) -> Diagnosis:
    return Diagnosis(check=check, status="pass", detail="fine")


def test_cmd_doctor_prints_lines_and_returns_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (1, ["[FAIL] hub: nope", "synapse doctor: FAILED"], [_fail("hub")])

    ns = _doctor_ns()
    assert cli_doctor._cmd_doctor(ns, diagnose_runner=diagnose) == 1
    assert "synapse doctor: FAILED" in capsys.readouterr().out


# --- --fix auto-repair ---------------------------------------------------------


def test_service_repairable_checks_selects_hub_fail_and_waiter_warn() -> None:
    diagnoses = [_pass("identity"), _fail("hub"), _warn("waiter"), _warn("disk")]
    assert cli_doctor.service_repairable_checks(diagnoses, uri="ws://localhost:8876") == [
        "hub",
        "waiter",
    ]
    assert cli_doctor.service_repairable_checks(diagnoses, uri="ws://127.0.0.1:8876") == [
        "hub",
        "waiter",
    ]


def test_service_repairable_checks_is_empty_off_the_default_local_hub() -> None:
    diagnoses = [_fail("hub"), _warn("waiter")]
    # local systemd units cannot repair a remote or non-default hub
    assert cli_doctor.service_repairable_checks(diagnoses, uri="ws://10.0.0.5:8876") == []
    assert cli_doctor.service_repairable_checks(diagnoses, uri="ws://localhost:9999") == []


def test_service_repairable_checks_is_empty_when_everything_passes() -> None:
    diagnoses = [_pass("hub"), _pass("waiter"), _warn("disk"), _fail("identity")]
    assert cli_doctor.service_repairable_checks(diagnoses, uri="ws://localhost:8876") == []


def test_cmd_doctor_fix_repairs_the_default_hub_and_rechecks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--fix installs+starts the services, re-runs the checks, and reports the new state."""
    runs: list[int] = []
    installed: dict[str, Any] = {}

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        runs.append(1)
        if len(runs) == 1:
            return (1, ["[FAIL] hub: down", "synapse doctor: FAILED"], [_fail("hub")])
        return (0, ["[ok] hub: answered", "synapse doctor: all clear"], [_pass("hub")])

    def install_services(**kwargs: Any) -> list[str]:
        installed.update(kwargs)
        return ["ok: systemctl --user enable --now synapse-hub.service"]

    ns = _doctor_ns(uri="ws://localhost:8876", project="repo", identity="repo/ux", fix=True)
    assert (
        cli_doctor._cmd_doctor(ns, diagnose_runner=diagnose, service_installer=install_services)
        == 0
    )
    out = capsys.readouterr().out
    assert "[fix] auto-repairing hub" in out
    assert "[fix] re-check:" in out
    assert "synapse doctor: all clear" in out
    assert installed["start"] is True
    assert installed["project"] == "repo"
    assert installed["identity"] == "repo/ux"
    assert len(runs) == 2  # the exit code comes from the post-repair run


def test_cmd_doctor_fix_exit_code_reflects_a_repair_that_did_not_take(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (1, ["[FAIL] hub: down"], [_fail("hub")])

    ns = _doctor_ns(uri="ws://localhost:8876", project="repo", fix=True)
    assert (
        cli_doctor._cmd_doctor(ns, diagnose_runner=diagnose, service_installer=lambda **_: []) == 1
    )
    assert "[fix] re-check:" in capsys.readouterr().out


def test_cmd_doctor_fix_never_touches_a_non_default_hub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Against a remote hub --fix explains the gate and prints manual guidance only."""
    installed: list[dict[str, Any]] = []

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (1, ["[FAIL] hub: down"], [_fail("hub")])

    def install_services(**kwargs: Any) -> list[str]:
        installed.append(kwargs)
        return []

    ns = _doctor_ns(uri="ws://10.0.0.5:8876", project="repo", fix=True)
    assert (
        cli_doctor._cmd_doctor(ns, diagnose_runner=diagnose, service_installer=install_services)
        == 1
    )
    out = capsys.readouterr().out
    assert installed == []
    assert "not the default local hub" in out
    assert "synapse-arm@.service" in out  # manual setup commands still offered


def test_cmd_doctor_fix_reports_nothing_to_repair_when_healthy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    installed: list[dict[str, Any]] = []

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (0, ["synapse doctor: all clear"], [_pass("hub"), _pass("waiter")])

    def install_services(**kwargs: Any) -> list[str]:
        installed.append(kwargs)
        return []

    ns = _doctor_ns(project="repo", identity="repo/ux", synapse_bin="/bin/synapse", fix=True)
    assert (
        cli_doctor._cmd_doctor(ns, diagnose_runner=diagnose, service_installer=install_services)
        == 0
    )
    out = capsys.readouterr().out
    assert installed == []
    assert "[fix] nothing to auto-repair" in out


def test_cmd_doctor_installs_user_services(
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (0, ["synapse doctor: all clear"], [])

    def install_services(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["wrote synapse-hub.service", "ok: systemctl --user daemon-reload"]

    ns = _doctor_ns(
        project="repo",
        install_user_services=True,
        start_user_services=True,
        identity="repo/ux",
        synapse_bin="/bin/synapse",
    )

    assert (
        cli_doctor._cmd_doctor(
            ns,
            diagnose_runner=diagnose,
            service_installer=install_services,
        )
        == 0
    )
    assert captured == {
        "project": "repo",
        "identity": "repo/ux",
        "synapse_bin": "/bin/synapse",
        "start": True,
    }
    assert "systemctl --user daemon-reload" in capsys.readouterr().out


def test_main_routes_to_doctor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    port = _free_port()
    assert cli.main(["doctor", "--uri", f"ws://127.0.0.1:{port}", "--project", "repo"]) == 1
    assert "did not answer" in capsys.readouterr().out


# --- --json -------------------------------------------------------------------


def test_cmd_doctor_json_reports_every_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (0, ["unused text report"], [_pass("hub"), _warn("disk")])

    ns = _doctor_ns(json=True)
    assert cli_doctor._cmd_doctor(ns, diagnose_runner=diagnose) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["healthy"] is True
    assert payload["diagnoses"] == [
        {"check": "hub", "status": "pass", "detail": "fine", "remedy": ""},
        {"check": "disk", "status": "warn", "detail": "wobbly", "remedy": ""},
    ]


def test_cmd_doctor_json_mirrors_a_failing_exit(capsys: pytest.CaptureFixture[str]) -> None:
    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (1, ["unused"], [_fail("hub")])

    ns = _doctor_ns(json=True)
    assert cli_doctor._cmd_doctor(ns, diagnose_runner=diagnose) == 1
    assert json.loads(capsys.readouterr().out)["healthy"] is False


def test_cmd_doctor_json_refuses_mutating_flags(capsys: pytest.CaptureFixture[str]) -> None:
    ns = _doctor_ns(json=True, fix=True, start_user_services=True)
    assert cli_doctor._cmd_doctor(ns) == 2
    err = capsys.readouterr().err
    assert "plain diagnostic" in err
    assert "--fix" in err and "--start-user-services" in err


def test_parser_accepts_doctor_json() -> None:
    args = cli.build_parser().parse_args(["doctor", "--json"])
    assert args.json is True
