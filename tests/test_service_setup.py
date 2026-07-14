# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for user service setup helpers

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from synapse_channel.service_setup import (
    escaped_instance,
    install_arm_service,
    install_user_services,
    render_arm_unit,
    render_hub_unit,
    render_presence_unit,
    service_suggestions,
)
from synapse_channel.terminal_text import terminal_text


def test_render_arm_unit_uses_non_llm_synapse_arm() -> None:
    unit = render_arm_unit(synapse_bin="/usr/bin/synapse")
    assert "ExecStart=/usr/bin/synapse arm" in unit
    assert "--directed-only" in unit
    assert "--mailbox" in unit
    assert "Restart=always" in unit
    assert "Wants=synapse-hub.service" not in unit
    assert "After=synapse-hub.service" in unit


def test_render_arm_unit_adds_remote_uri_and_token_file() -> None:
    unit = render_arm_unit(
        synapse_bin="/usr/bin/synapse",
        uri="wss://hub.example:8876",
        token_file="/home/user/.config/synapse/token",
    )

    assert "--uri=wss://hub.example:8876" in unit
    assert "--token-file=/home/user/.config/synapse/token" in unit


def test_render_arm_unit_escapes_systemd_percent_specifiers() -> None:
    unit = render_arm_unit(
        synapse_bin="/usr/bin/synapse",
        uri="wss://hub.example/%2F",
        token_file="/home/user/token%name",
    )

    assert "--uri=wss://hub.example/%%2F" in unit
    assert "--token-file=/home/user/token%%name" in unit


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("/bin/synapse\nExecStart=/bin/false", "non-empty systemd token"),
        ("bad'path", "non-empty systemd token"),
        ("bad\\path", "non-empty systemd token"),
        ("bad$PATH", "non-empty systemd token"),
        *((f"{prefix}/usr/bin/synapse", "ExecStart control prefix") for prefix in "@-:+!|"),
    ],
)
def test_rendered_units_reject_ambiguous_systemd_tokens(value: str, match: str) -> None:
    for renderer in (render_hub_unit, render_presence_unit, render_arm_unit):
        with pytest.raises(ValueError, match=match):
            renderer(synapse_bin=value)


def test_rendered_units_never_order_after_their_install_target() -> None:
    # A unit that is WantedBy=default.target and also ordered
    # After=default.target creates a boot ordering cycle; systemd breaks it by
    # deleting dependent start jobs, so presence/arm never start at boot.
    units = {
        "hub": render_hub_unit(synapse_bin="/bin/synapse"),
        "presence": render_presence_unit(synapse_bin="/bin/synapse"),
        "arm": render_arm_unit(synapse_bin="/bin/synapse"),
    }
    for name, unit in units.items():
        assert "WantedBy=default.target" in unit, name
        assert "After=default.target" not in unit, name


def test_checked_in_deploy_templates_have_no_boot_ordering_cycle() -> None:
    deploy_dir = Path(__file__).resolve().parents[1] / "deploy"
    for filename in (
        "synapse-hub.service",
        "synapse-presence@.service",
        "synapse-arm@.service",
    ):
        template = (deploy_dir / filename).read_text(encoding="utf-8")
        directives = "\n".join(
            line for line in template.splitlines() if not line.lstrip().startswith("#")
        )
        assert "WantedBy=default.target" in directives, filename
        assert "After=default.target" not in directives, filename


def test_checked_in_arm_template_matches_generated_runtime_contract() -> None:
    template = (Path(__file__).resolve().parents[1] / "deploy" / "synapse-arm@.service").read_text(
        encoding="utf-8"
    )

    assert "After=synapse-hub.service" in template
    assert "Wants=synapse-hub.service" not in template
    assert "--directed-only --mailbox --uri=ws://localhost:8876" in template
    assert "Restart=always" in template


def test_install_arm_service_writes_only_waiter_unit(tmp_path: Path) -> None:
    result = install_arm_service(
        identity="repo/ux",
        synapse_bin="/bin/synapse",
        home=tmp_path,
    )

    unit_dir = tmp_path / ".config" / "systemd" / "user"
    assert result.ok is True
    assert sorted(path.name for path in unit_dir.iterdir()) == ["synapse-arm@.service"]
    assert any(
        "systemd-escape --template=synapse-arm@.service -- repo/ux" in line for line in result.lines
    )
    # Still no hub or presence unit — but the sandboxed unit's ReadWritePaths
    # targets must exist before the first start, so the data dirs are created.
    assert (tmp_path / "synapse").is_dir()
    assert (tmp_path / ".local" / "share" / "synapse").is_dir()

    hostile = "repo/ux\x1b]52;c;YQ==\x07\nforged\u202e"
    hostile_result = install_arm_service(
        identity=hostile,
        synapse_bin="/bin/synapse",
        home=tmp_path,
    )
    hostile_command = hostile_result.lines[-1].removeprefix("run: ")
    assert all(control not in hostile_command for control in ("\x1b", "\x07", "\n", "\u202e"))
    _assert_copyable_service_command_quotes_identity(
        hostile_command, hostile, tmp_path, expected_identity=terminal_text(hostile)
    )


def test_install_arm_service_start_enables_exact_escaped_instance(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        stdout = "synapse-arm@repo-ux.service\n" if args[0] == "systemd-escape" else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    result = install_arm_service(
        identity="repo/ux",
        synapse_bin="/bin/synapse",
        start=True,
        home=tmp_path,
        runner=runner,
    )

    assert result.ok is True
    assert commands == [
        ["systemd-escape", "--template=synapse-arm@.service", "--", "repo/ux"],
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "synapse-arm@repo-ux.service"],
    ]
    assert result.lines[-1] == ("ok: systemctl --user enable --now synapse-arm@repo-ux.service")


def test_install_arm_service_fails_closed_when_escape_fails(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="bad identity")

    result = install_arm_service(
        identity="repo/ux",
        start=True,
        home=tmp_path,
        runner=runner,
    )

    assert result.ok is False
    assert commands == [["systemd-escape", "--template=synapse-arm@.service", "--", "repo/ux"]]
    assert result.lines[-1].endswith("— bad identity")


def test_install_arm_service_rejects_empty_escaped_instance(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="\n", stderr="")

    result = install_arm_service(
        identity="repo/ux",
        start=True,
        home=tmp_path,
        runner=runner,
    )

    assert result.ok is False
    assert result.lines[-1] == "failed: systemd-escape returned an empty unit name"


def test_install_arm_service_reports_systemctl_failure(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(
                args, 0, stdout="synapse-arm@repo.service\n", stderr=""
            )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="no user bus")

    result = install_arm_service(
        identity="repo",
        start=True,
        home=tmp_path,
        runner=runner,
    )

    assert result.ok is False
    assert result.lines[-1] == "failed: systemctl --user daemon-reload — no user bus"


def test_install_arm_service_reports_enable_failure(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(
                args, 0, stdout="synapse-arm@repo.service\n", stderr=""
            )
        if args[-1] == "daemon-reload":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="enable refused")

    result = install_arm_service(
        identity="repo",
        start=True,
        home=tmp_path,
        runner=runner,
    )

    assert result.ok is False
    assert result.lines[-2:] == (
        "ok: systemctl --user daemon-reload",
        "failed: systemctl --user enable --now synapse-arm@repo.service — enable refused",
    )


def test_install_arm_service_normalizes_missing_command(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("systemd-escape not installed")

    result = install_arm_service(
        identity="repo",
        start=True,
        home=tmp_path,
        runner=runner,
    )

    assert result.ok is False
    assert "systemd-escape not installed" in result.lines[-1]


@pytest.mark.parametrize(
    "synapse_bin",
    ["/bin/synapse\nExecStart=/bin/false", *(f"{prefix}/bin/synapse" for prefix in "@-:+!|")],
)
def test_install_arm_service_rejects_unit_directive_injection(
    tmp_path: Path, synapse_bin: str
) -> None:
    result = install_arm_service(
        identity="repo",
        synapse_bin=synapse_bin,
        home=tmp_path,
    )

    assert result.ok is False
    assert "failed to write" in result.lines[-1]
    assert not (tmp_path / ".config").exists()


def test_install_arm_service_requires_nonempty_identity(tmp_path: Path) -> None:
    result = install_arm_service(identity="  ", home=tmp_path)

    assert result == install_arm_service(identity="", home=tmp_path)
    assert result.ok is False
    assert result.lines == ("identity must not be empty",)
    assert not (tmp_path / ".config").exists()


def test_service_suggestions_include_hub_presence_and_arm() -> None:
    lines = service_suggestions(project="repo", identity="repo/ux", synapse_bin="/bin/synapse")
    text = "\n".join(lines)
    assert "synapse-hub.service" in text
    assert "synapse-presence@.service" in text
    assert "synapse-arm@.service" in text
    assert "/bin/synapse" in text


def _assert_copyable_service_command_quotes_identity(
    command: str,
    identity: str,
    tmp_path: Path,
    *,
    expected_identity: str | None = None,
) -> None:
    capture = tmp_path / "systemd-escape-argv"
    script = """
systemctl() { :; }
systemd-escape() {
  printf '%s\n' "$@" > "$CAPTURE"
  printf 'escaped.service'
}
"""
    completed = subprocess.run(
        ["bash", "-c", f"{script}\n{command}"],
        capture_output=True,
        text=True,
        check=False,
        env={"CAPTURE": str(capture)},
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert capture.read_text(encoding="utf-8").splitlines()[-1] == (
        identity if expected_identity is None else expected_identity
    )


def test_service_suggestions_shell_quote_untrusted_identity(tmp_path: Path) -> None:
    identity = "repo'$(printf INJECTED >&2)\x1b]52;c;YQ==\x07\nforged\u202e"
    command = service_suggestions(
        project="safe",
        identity=identity,
        synapse_bin="/bin/synapse",
    )[5]

    assert all(control not in command for control in ("\x1b", "\x07", "\n", "\u202e"))
    _assert_copyable_service_command_quotes_identity(
        command, identity, tmp_path, expected_identity=terminal_text(identity)
    )


def test_install_user_services_writes_three_units(tmp_path: Path) -> None:
    lines = install_user_services(
        project="repo",
        identity="repo/ux",
        synapse_bin="/bin/synapse",
        home=tmp_path,
    )
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    assert (unit_dir / "synapse-hub.service").exists()
    assert (unit_dir / "synapse-presence@.service").exists()
    assert (unit_dir / "synapse-arm@.service").exists()
    assert any("systemctl --user enable --now synapse-hub.service" in line for line in lines)

    for index, prefix in enumerate("@-:+!|"):
        rejected_home = tmp_path / f"rejected-{index}"
        with pytest.raises(ValueError, match="ExecStart control prefix"):
            install_user_services(
                project="repo",
                identity="repo/ux",
                synapse_bin=f"{prefix}/bin/synapse",
                home=rejected_home,
            )
        assert not rejected_home.exists()


def test_install_user_services_shell_quotes_untrusted_project(tmp_path: Path) -> None:
    project = "repo'$(printf INJECTED >&2)\x1b]52;c;YQ==\x07\nforged\u202e"
    lines = install_user_services(
        project=project,
        identity="safe",
        synapse_bin="/bin/synapse",
        home=tmp_path,
    )
    command = next(
        line.removeprefix("run: ")
        for line in lines
        if line.startswith("run: ") and "synapse-presence" in line
    )

    assert all(control not in command for control in ("\x1b", "\x07", "\n", "\u202e"))
    _assert_copyable_service_command_quotes_identity(
        command, project, tmp_path, expected_identity=terminal_text(project)
    )


def test_install_user_services_start_runs_systemctl(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(args, 0, stdout="escaped.service\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    install_user_services(
        project="repo",
        identity="repo/ux",
        synapse_bin="/bin/synapse",
        start=True,
        home=tmp_path,
        runner=runner,
    )
    assert ["systemctl", "--user", "daemon-reload"] in commands
    assert ["systemctl", "--user", "enable", "--now", "synapse-hub.service"] in commands
    assert ["systemctl", "--user", "enable", "--now", "escaped.service"] in commands


def test_install_user_services_start_terminates_escape_options(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(args, 0, stdout="escaped.service\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    install_user_services(
        project="--version",
        identity="--help",
        synapse_bin="/bin/synapse",
        start=True,
        home=tmp_path,
        runner=runner,
    )

    assert commands[:2] == [
        ["systemd-escape", "--template=synapse-presence@.service", "--", "--version"],
        ["systemd-escape", "--template=synapse-arm@.service", "--", "--help"],
    ]


def test_install_user_services_reports_systemctl_failures(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        if args[0] == "systemd-escape":
            return subprocess.CompletedProcess(args, 0, stdout="escaped.service\n", stderr="")
        if args[-1] == "synapse-hub.service":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="unit failed")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    lines = install_user_services(
        project="repo",
        identity="repo/ux",
        synapse_bin="/bin/synapse",
        start=True,
        home=tmp_path,
        runner=runner,
    )

    assert any(
        "failed: systemctl --user enable --now synapse-hub.service" in line for line in lines
    )
    assert any("unit failed" in line for line in lines)


def test_escaped_instance_falls_back_when_systemd_escape_fails() -> None:
    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="missing")

    assert (
        escaped_instance("repo/ux", template="synapse-arm@.service", runner=runner)
        == "synapse-arm@repo-ux.service"
    )


def test_escaped_instance_falls_back_for_non_template_unit() -> None:
    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="missing")

    assert (
        escaped_instance("repo/ux", template="synapse-arm.service", runner=runner)
        == "synapse-arm.service-repo-ux"
    )
