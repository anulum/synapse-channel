# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for release redeploy operations checklists

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from synapse_channel import cli, cli_doctor
from synapse_channel.ops_redeploy import build_redeploy_checklist, render_redeploy_checklist


def _fake_systemd_escape(
    args: list[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Return deterministic escaped systemd unit names for tests."""
    del capture_output, text, check
    template = args[1].removeprefix("--template=")
    identity = args[2]
    escaped = identity.replace("/", "-")
    unit = template.replace("@.service", f"@{escaped}.service")
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"{unit}\n", stderr="")


def test_build_redeploy_checklist_contains_all_release_restart_checks() -> None:
    checklist = build_redeploy_checklist(
        project="repo",
        identity="repo/codex-main",
        hub_uri="ws://127.0.0.1:8876",
        db_path=Path("~/synapse/hub.db"),
        synapse_bin="/opt/synapse/bin/synapse",
        escape_runner=_fake_systemd_escape,
    )

    labels = [item.label for item in checklist]
    assert labels == [
        "Package and executable",
        "Hub service restart",
        "Presence daemon restart",
        "Wake listener restart",
        "Roster reconnect",
        "Durable state replay",
        "Git hook wiring",
    ]
    rendered_commands = "\n".join(item.command for item in checklist)
    assert "/opt/synapse/bin/synapse --version" in rendered_commands
    assert "systemctl --user restart synapse-hub.service" in rendered_commands
    assert "synapse-presence@repo.service" in rendered_commands
    assert "synapse-arm@repo-codex-main.service" in rendered_commands
    assert "synapse who --project repo" in rendered_commands
    assert "sqlite3 ~/synapse/hub.db" in rendered_commands
    assert "synapse git-hook test" in rendered_commands


def test_render_redeploy_checklist_is_operator_copyable() -> None:
    lines = render_redeploy_checklist(
        build_redeploy_checklist(
            project="repo",
            identity="repo/codex-main",
            escape_runner=_fake_systemd_escape,
        )
    )

    text = "\n".join(lines)
    assert lines[0] == "synapse doctor: release redeploy checklist"
    assert "[1] Package and executable" in text
    assert "expected: installed command reports the release version" in text
    assert "expected: active claims and waiters are visible after restart" in text


def test_parser_doctor_redeploy_checklist_flag() -> None:
    args = cli.build_parser().parse_args(["doctor", "--redeploy-checklist"])
    assert args.func is cli_doctor._cmd_doctor
    assert args.redeploy_checklist is True


def test_cmd_doctor_prints_redeploy_checklist(capsys: pytest.CaptureFixture[str]) -> None:
    async def diagnose(**_: object) -> tuple[int, list[str]]:
        return (0, ["synapse doctor: all clear"])

    args = cli.build_parser().parse_args(
        [
            "doctor",
            "--project",
            "repo",
            "--id",
            "codex-main",
            "--redeploy-checklist",
            "--db-path",
            "~/synapse/hub.db",
            "--synapse-bin",
            "/opt/synapse/bin/synapse",
        ]
    )

    assert cli_doctor._cmd_doctor(args, diagnose_runner=diagnose) == 0
    out = capsys.readouterr().out
    assert "synapse doctor: release redeploy checklist" in out
    assert "/opt/synapse/bin/synapse --version" in out
    assert "synapse who --project repo" in out
    assert "sqlite3 ~/synapse/hub.db" in out


def test_redeploy_checklist_is_documented() -> None:
    docs = "\n".join(
        [
            Path("README.md").read_text(encoding="utf-8"),
            Path("docs/cli.md").read_text(encoding="utf-8"),
            Path("docs/deployment.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse doctor --redeploy-checklist" in docs
    assert "package, service, roster, durable-state, and git-hook checks" in docs
    assert "does not restart services by itself" in docs
