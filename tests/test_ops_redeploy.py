# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for release redeploy operations checklists

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from _platform_caps import requires_linux
from synapse_channel import cli, cli_doctor
from synapse_channel.client.diagnostics import Diagnosis
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
    identity = args[-1]
    escaped = identity.replace("/", "-")
    unit = template.replace("@.service", f"@{escaped}.service")
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"{unit}\n", stderr="")


def _run_rendered_restart(
    tmp_path: Path, *, authorized_pid: int, live_pid: int
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run a rendered restart against a fake systemctl, never the live service."""
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        """#!/bin/sh
if [ "$1" = "--user" ] && [ "$2" = "show" ]; then
    printf '%s\\n' "$FAKE_MAIN_PID"
    exit 0
fi
printf '%s\\n' "$*" >> "$SYSTEMCTL_LOG"
""",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    log = tmp_path / "systemctl.log"
    env = dict(os.environ)
    env.update(
        {
            "FAKE_MAIN_PID": str(live_pid),
            "PATH": f"{tmp_path}:{env['PATH']}",
            "SYSTEMCTL_LOG": str(log),
            "XDG_RUNTIME_DIR": str(tmp_path),
        }
    )
    restart = build_redeploy_checklist(
        project="repo",
        identity="repo/codex-main",
        authorized_hub_pid=authorized_pid,
        escape_runner=_fake_systemd_escape,
    )[2]
    assert shlex.split(restart.command)[0] == "flock"
    completed = subprocess.run(  # noqa: S603  # nosec B603
        ["/bin/sh", "-c", restart.command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed, log


def test_build_redeploy_checklist_withholds_disruption_by_default() -> None:
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
        "Live-session disruption preflight",
        "Roster reconnect",
        "Durable state replay",
        "Git hook wiring",
    ]
    rendered_commands = "\n".join(item.command for item in checklist)
    assert "/opt/synapse/bin/synapse --version" in rendered_commands
    assert "systemctl --user restart" not in rendered_commands
    assert "systemctl --user show --property=ActiveState,MainPID,NRestarts" in rendered_commands
    assert "synapse who --project=repo" in rendered_commands
    assert 'sqlite3 -- "${HOME}"/synapse/hub.db' in rendered_commands
    assert "synapse git-hook test" in rendered_commands


def test_authorized_redeploy_is_pid_guarded_and_uniquely_locked() -> None:
    checklist = build_redeploy_checklist(
        project="repo",
        identity="repo/codex-main",
        hub_uri="ws://127.0.0.1:8876",
        authorized_hub_pid=4242,
        escape_runner=_fake_systemd_escape,
    )

    restart = checklist[2]
    assert restart.label == "Authorised locked service restart"
    assert "flock --exclusive --nonblock" in restart.command
    assert "XDG_RUNTIME_DIR is required" in restart.command
    assert "synapse-channel-redeploy.lock" in restart.command
    assert "MainPID --value -- synapse-hub.service" in restart.command
    assert '"$1"' in restart.command
    assert restart.command.endswith(" sh 4242")
    assert "synapse-hub.service" in restart.command
    assert "synapse-presence@repo.service" in restart.command
    assert "synapse-arm@repo-codex-main.service" in restart.command


@requires_linux
def test_authorized_redeploy_runs_only_against_the_exact_fake_pid(tmp_path: Path) -> None:
    completed, log = _run_rendered_restart(tmp_path, authorized_pid=4242, live_pid=4242)

    assert completed.returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == [
        "--user restart -- synapse-hub.service synapse-presence@repo.service "
        "synapse-arm@repo-codex-main.service",
        "--user status --no-pager -- synapse-hub.service synapse-presence@repo.service "
        "synapse-arm@repo-codex-main.service",
    ]


@requires_linux
def test_authorized_redeploy_pid_mismatch_has_no_fake_mutation(tmp_path: Path) -> None:
    completed, log = _run_rendered_restart(tmp_path, authorized_pid=4242, live_pid=4243)

    assert completed.returncode != 0
    assert not log.exists()


@requires_linux
def test_authorized_redeploy_lock_contention_has_no_fake_mutation(tmp_path: Path) -> None:
    fcntl = pytest.importorskip("fcntl")
    lock_path = tmp_path / "synapse-channel-redeploy.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed, log = _run_rendered_restart(tmp_path, authorized_pid=4242, live_pid=4242)

    assert completed.returncode != 0
    assert not log.exists()


@pytest.mark.parametrize("pid", [-1, 0, 1])
def test_authorized_redeploy_refuses_non_process_pid(pid: int) -> None:
    with pytest.raises(ValueError, match="greater than one"):
        build_redeploy_checklist(
            project="repo",
            identity="repo/codex-main",
            authorized_hub_pid=pid,
            escape_runner=_fake_systemd_escape,
        )


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
    assert "restart commands remain withheld" in text
    assert "systemctl --user restart" not in text


def test_parser_doctor_redeploy_checklist_flag() -> None:
    args = cli.build_parser().parse_args(["doctor", "--redeploy-checklist"])
    assert args.func is cli_doctor._cmd_doctor
    assert args.redeploy_checklist is True
    assert args.redeploy_authorize_restart_pid is None


def test_parser_doctor_redeploy_authorization_binds_exact_pid() -> None:
    args = cli.build_parser().parse_args(
        [
            "doctor",
            "--redeploy-checklist",
            "--redeploy-authorize-restart-pid",
            "4242",
        ]
    )
    assert args.redeploy_authorize_restart_pid == 4242


@pytest.mark.parametrize("pid", ["-1", "0", "1", "not-a-pid"])
def test_parser_doctor_redeploy_authorization_refuses_invalid_pid(pid: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "doctor",
                "--redeploy-checklist",
                "--redeploy-authorize-restart-pid",
                pid,
            ]
        )


@requires_linux
def test_cmd_doctor_prints_redeploy_checklist(capsys: pytest.CaptureFixture[str]) -> None:
    async def diagnose(**_: object) -> tuple[int, list[str], list[Diagnosis]]:
        return (0, ["synapse doctor: all clear"], [])

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
    assert "synapse who --project=repo" in out
    assert 'sqlite3 -- "${HOME}"/synapse/hub.db' in out
    assert "systemctl --user restart" not in out


def test_cmd_doctor_refuses_restart_authorization_without_checklist(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = cli.build_parser().parse_args(["doctor", "--redeploy-authorize-restart-pid", "4242"])

    assert cli_doctor._cmd_doctor(args) == 2
    assert "requires --redeploy-checklist" in capsys.readouterr().err


@requires_linux
def test_cmd_doctor_only_renders_authorized_restart(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def diagnose(**_: object) -> tuple[int, list[str], list[Diagnosis]]:
        return (0, ["synapse doctor: all clear"], [])

    args = cli.build_parser().parse_args(
        [
            "doctor",
            "--project",
            "repo",
            "--id",
            "codex-main",
            "--redeploy-checklist",
            "--redeploy-authorize-restart-pid",
            "4242",
        ]
    )

    assert cli_doctor._cmd_doctor(args, diagnose_runner=diagnose) == 0
    out = capsys.readouterr().out
    assert "flock --exclusive --nonblock" in out
    assert "systemctl --user restart" in out
    assert out.rstrip().endswith("claim-aware post-commit/post-merge hook path still resolves")


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
    assert "restart commands are withheld by default" in docs
    assert "--redeploy-authorize-restart-pid" in docs
    assert "Every new release tag" in docs
    assert "unrelated running application" in docs
