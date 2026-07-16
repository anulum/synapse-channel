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
import sys
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


def test_parser_accepts_mcp_config_trust_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "doctor",
            "--mcp-config",
            "/operator/mcp.json",
            "--mcp-config-trust-bundle",
            "/operator/trust.json",
            "--allow-repo-mcp-config",
        ]
    )
    assert args.mcp_config == "/operator/mcp.json"
    assert args.mcp_config_trust_bundle == "/operator/trust.json"
    assert args.allow_repo_mcp_config is True


@pytest.mark.parametrize(
    "flag_args",
    [
        ["--mcp-config-trust-bundle", "/operator/trust.json"],
        ["--allow-repo-mcp-config"],
    ],
)
def test_doctor_mcp_dependent_flags_require_config(
    flag_args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    args = cli.build_parser().parse_args(["doctor", *flag_args])

    async def should_not_run(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        raise AssertionError("diagnostics must not run for an invalid flag combination")

    assert cli_doctor._cmd_doctor(args, diagnose_runner=should_not_run) == 2
    assert "requires --mcp-config" in capsys.readouterr().err


def test_parser_doctor_fix_flags() -> None:
    args = cli.build_parser().parse_args(
        ["doctor", "--fix", "--install-user-services", "--identity", "repo/ux"]
    )
    assert args.fix is True
    assert args.install_user_services is True
    assert args.identity == "repo/ux"


def test_parser_accepts_doctor_federation_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "doctor",
            "--federation-peer",
            "alpha=ws://peer-a",
            "--federation-cursor",
            "alpha=42",
            "--federation-path",
            "alpha=tls-passthrough",
            "--federation-store",
            "/tmp/federation.json",
            "--federation-token",
            "secret",
            "--federation-skew-warn-seconds",
            "2.5",
            "--federation-cert-warn-days",
            "14",
        ]
    )
    assert args.federation_peer == ["alpha=ws://peer-a"]
    assert args.federation_cursor == ["alpha=42"]
    assert args.federation_path == ["alpha=tls-passthrough"]
    assert args.federation_store == "/tmp/federation.json"
    assert args.federation_token == "secret"
    assert args.federation_skew_warn_seconds == 2.5
    assert args.federation_cert_warn_days == 14


# --- _diagnose logic ---------------------------------------------------------


async def test_diagnose_reachable_with_waiter_passes() -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        waiter = await connect_agent("demorepo-rx", uri)
        try:
            code, lines, _ = await cli_doctor._diagnose(
                feed_tail_reader=lambda _env: [],
                cursor_names_reader=lambda _env: [],
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
            feed_tail_reader=lambda _env: [],
            cursor_names_reader=lambda _env: [],
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
        feed_tail_reader=lambda _env: [],
        cursor_names_reader=lambda _env: [],
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
        feed_tail_reader=lambda _env: [],
        cursor_names_reader=lambda _env: [],
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
            feed_tail_reader=lambda _env: [],
            cursor_names_reader=lambda _env: [],
            uri=uri,
            project="demorepo",
            agent_id=None,
            token=None,
            send_name="demorepo-keeper",
        )
    assert any("hyphen child" in line for line in lines)


async def test_diagnose_reports_outbound_mcp_config_trust(tmp_path: Path) -> None:
    async def no_roster(**_: Any) -> list[str]:
        return []

    executable = tmp_path / "mcp-server"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "servers": [
                    {
                        "name": "echo",
                        "command": str(executable),
                        "cwd": str(tmp_path),
                        "allowed_tools": ["echo"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)

    code, lines, diagnoses = await cli_doctor._diagnose(
        uri="ws://127.0.0.1:8876",
        project="demorepo",
        agent_id=None,
        token=None,
        roster_probe=no_roster,
        feed_tail_reader=lambda _env: [],
        cursor_names_reader=lambda _env: [],
        mcp_config=config,
    )

    assert code == 0
    assert any("[warn] mcp-config:" in line and "unsigned manifest" in line for line in lines)
    assert next(item for item in diagnoses if item.check == "mcp-config").status == "warn"


async def test_diagnose_fails_closed_on_loose_mcp_config(tmp_path: Path) -> None:
    async def no_roster(**_: Any) -> list[str]:
        return []

    config = tmp_path / "mcp.json"
    config.write_text('{"version":1,"servers":[]}', encoding="utf-8")
    config.chmod(0o644)

    code, lines, _diagnoses = await cli_doctor._diagnose(
        uri="ws://127.0.0.1:8876",
        project="demorepo",
        agent_id=None,
        token=None,
        roster_probe=no_roster,
        feed_tail_reader=lambda _env: [],
        cursor_names_reader=lambda _env: [],
        mcp_config=config,
    )

    assert code == 1
    assert any("[FAIL] mcp-config:" in line for line in lines)


async def test_diagnose_appends_federation_diagnoses(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    async def no_roster(**_: Any) -> list[str] | None:
        return []

    async def federation_runner(**kwargs: Any) -> list[Diagnosis]:
        captured.update(kwargs)
        return [Diagnosis(check="federation-peer:alpha", status="warn", detail="lag=2")]

    code, lines, diagnoses = await cli_doctor._diagnose(
        feed_tail_reader=lambda _env: [],
        cursor_names_reader=lambda _env: [],
        uri="ws://127.0.0.1:8876",
        project="demorepo",
        agent_id="coordinator",
        token="main-token",
        roster_probe=no_roster,
        federation_peers=("alpha=ws://peer",),
        federation_cursors=("alpha=3",),
        federation_paths=("alpha=tls-passthrough",),
        federation_store=tmp_path / "federation.json",
        federation_token="peer-token",
        federation_skew_warn_seconds=2.0,
        federation_cert_warn_days=9,
        federation_diagnose_runner=federation_runner,
    )

    assert code == 0
    assert any("[warn] federation-peer:alpha: lag=2" in line for line in lines)
    assert diagnoses[-1].check == "federation-peer:alpha"
    assert captured["peer_specs"] == ("alpha=ws://peer",)
    assert captured["cursor_specs"] == ("alpha=3",)
    assert captured["path_specs"] == ("alpha=tls-passthrough",)
    assert captured["local_id"].startswith("demorepo/")
    assert captured["local_id"].endswith("coordinator-doctor")
    assert captured["token"] == "peer-token"
    assert captured["store_path"] == tmp_path / "federation.json"
    assert captured["skew_warn_seconds"] == 2.0
    assert captured["cert_warn_days"] == 9


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
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    code = cli.main(
        [
            "doctor",
            "--uri",
            "ws://127.0.0.1:1",
            "--install-user-services",
            "--synapse-bin",
            "+/usr/bin/synapse",
        ]
    )
    captured_io = capsys.readouterr()
    assert code == 2
    assert "ExecStart control prefix" in captured_io.err
    assert "Traceback" not in captured_io.out + captured_io.err
    assert not home.exists()


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


async def test_diagnose_surfaces_directed_messages_nobody_reads() -> None:
    """The addressee check rides the doctor: unread directed traffic warns."""
    lines = [json.dumps({"v": 1, "ty": "chat", "s": "A", "to": "GHOST/coordinator", "p": "hi"})]
    async with running_hub(SynapseHub()) as (_, uri):
        code, report, _ = await cli_doctor._diagnose(
            feed_tail_reader=lambda _env: lines,
            cursor_names_reader=lambda _env: [],
            uri=uri,
            project="demorepo",
            agent_id=None,
            token=None,
        )
    text = "\n".join(report)
    assert "GHOST/coordinator (1 msg)" in text
    assert "syn inbox --as=GHOST/coordinator" in text
    assert code == 0  # a blackhole warns; it does not fail the doctor


def test_feed_tail_and_cursor_readers_use_the_syn_home(tmp_path: Path) -> None:
    home = tmp_path / "synapse"
    home.mkdir()
    (home / "feed.ndjson").write_text("line-1\nline-2\n", encoding="utf-8")
    (home / "ACME.cursor").write_text("0", encoding="utf-8")
    (home / "ACME__coordinator.cursor").write_text("0", encoding="utf-8")
    env = {"SYN_HOME": str(home)}

    assert cli_doctor._read_feed_tail(env) == ["line-1", "line-2"]
    assert sorted(cli_doctor._read_cursor_names(env)) == ["ACME", "ACME__coordinator"]

    absent = {"SYN_HOME": str(tmp_path / "nowhere")}
    assert cli_doctor._read_feed_tail(absent) == []
    assert cli_doctor._read_cursor_names(absent) == []


def test_cursor_reader_swallows_an_unreadable_home(monkeypatch: pytest.MonkeyPatch) -> None:
    # pathlib.glob answers [] for a missing home; this guards the uglier
    # case - a home directory the process cannot iterate (permissions)
    import synapse_channel.ergonomics as ergonomics_module

    class _HostileHome:
        def glob(self, _pattern: str) -> list[Path]:
            raise OSError("permission denied")

    monkeypatch.setattr(ergonomics_module, "syn_home", lambda _env: _HostileHome())
    assert cli_doctor._read_cursor_names({}) == []


# --- --notify-cmd ---------------------------------------------------------------


def test_finding_lines_render_non_pass_verdicts_with_remedy() -> None:
    diagnoses = [
        _pass("identity"),
        Diagnosis(check="hub", status="fail", detail="no answer", remedy="synapse hub"),
        Diagnosis(check="waiter", status="warn", detail="not armed", remedy="syn-wait"),
    ]

    lines = cli_doctor.finding_lines(diagnoses)

    assert lines == [
        "fail hub: no answer | remedy: synapse hub",
        "warn waiter: not armed | remedy: syn-wait",
    ]


def test_finding_lines_are_empty_for_a_healthy_report() -> None:
    assert cli_doctor.finding_lines([_pass("hub"), _pass("waiter")]) == []


def test_run_doctor_notify_pipes_findings_and_uri_to_the_sink(tmp_path: Path) -> None:
    capture = tmp_path / "captured.txt"
    sink = (
        "import os,sys,pathlib; pathlib.Path(sys.argv[1]).write_text("
        "sys.stdin.read() + os.environ['SYNAPSE_DOCTOR_URI'], encoding='utf-8')"
    )

    cli_doctor.run_doctor_notify(
        f'{sys.executable} -c "{sink}" {capture}',
        ["fail hub: no answer | remedy: synapse hub"],
        uri="ws://h:1",
    )

    assert capture.read_text(encoding="utf-8") == (
        "fail hub: no answer | remedy: synapse hub\nws://h:1"
    )


def test_run_doctor_notify_reports_a_missing_sink_without_raising(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_doctor.run_doctor_notify("/definitely/not/a/binary", ["fail hub: x | remedy: y"], uri="u")
    assert "notify command failed" in capsys.readouterr().err


def test_run_doctor_notify_reports_an_unparsable_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_doctor.run_doctor_notify("sink 'unbalanced", ["fail hub: x | remedy: y"], uri="u")
    assert "notify command failed" in capsys.readouterr().err


def test_run_doctor_notify_reports_a_nonzero_sink_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_doctor.run_doctor_notify(
        f'{sys.executable} -c "raise SystemExit(3)"',
        ["fail hub: x | remedy: y"],
        uri="u",
    )
    assert "notify command exited 3" in capsys.readouterr().err


def test_cmd_doctor_notify_fires_on_findings(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, list[str], str]] = []

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (1, ["[FAIL] hub: nope"], [_fail("hub"), _pass("waiter")])

    ns = _doctor_ns(notify_cmd="pager")
    code = cli_doctor._cmd_doctor(
        ns,
        diagnose_runner=diagnose,
        notify_runner=lambda cmd, findings, *, uri: calls.append((cmd, findings, uri)),
    )

    assert code == 1
    assert calls == [("pager", ["fail hub: nope | remedy: "], "ws://h")]
    capsys.readouterr()


def test_cmd_doctor_notify_stays_silent_when_healthy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (0, ["synapse doctor: all clear"], [_pass("hub")])

    ns = _doctor_ns(notify_cmd="pager")
    code = cli_doctor._cmd_doctor(
        ns,
        diagnose_runner=diagnose,
        notify_runner=lambda cmd, *_a, **_k: calls.append(cmd),
    )

    assert code == 0
    assert calls == []
    capsys.readouterr()


def test_cmd_doctor_notify_composes_with_json(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[list[str]] = []

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (1, ["[FAIL] hub: nope"], [_fail("hub")])

    ns = _doctor_ns(notify_cmd="pager", json=True)
    code = cli_doctor._cmd_doctor(
        ns,
        diagnose_runner=diagnose,
        notify_runner=lambda _cmd, findings, *, uri: calls.append(findings),
    )

    assert code == 1
    # stdout stays exactly one JSON document; the sink got the findings
    document = json.loads(capsys.readouterr().out)
    assert document["healthy"] is False
    assert calls == [["fail hub: nope | remedy: "]]


def test_cmd_doctor_notify_reports_the_state_after_a_fix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repaired fleet sends nothing — the sink sees post-repair reality."""
    runs: list[int] = []
    calls: list[list[str]] = []

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        runs.append(1)
        if len(runs) == 1:
            return (1, ["[FAIL] hub: down"], [_fail("hub")])
        return (0, ["[ok] hub: answered"], [_pass("hub")])

    ns = _doctor_ns(
        uri="ws://localhost:8876", project="r", identity="r/x", fix=True, notify_cmd="pager"
    )
    code = cli_doctor._cmd_doctor(
        ns,
        diagnose_runner=diagnose,
        service_installer=lambda **_: ["ok"],
        notify_runner=lambda _cmd, findings, *, uri: calls.append(findings),
    )

    assert code == 0
    assert len(runs) == 2
    assert calls == []  # healthy after the repair: nothing to page
    capsys.readouterr()


def test_cmd_doctor_notify_pages_a_repair_that_did_not_take(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []

    async def diagnose(**_: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return (
            1,
            ["[FAIL] hub: still down"],
            [Diagnosis(check="hub", status="fail", detail="still down", remedy="look")],
        )

    ns = _doctor_ns(
        uri="ws://localhost:8876", project="r", identity="r/x", fix=True, notify_cmd="pager"
    )
    code = cli_doctor._cmd_doctor(
        ns,
        diagnose_runner=diagnose,
        service_installer=lambda **_: ["ok"],
        notify_runner=lambda _cmd, findings, *, uri: calls.append(findings),
    )

    assert code == 1
    assert calls == [["fail hub: still down | remedy: look"]]
    capsys.readouterr()
