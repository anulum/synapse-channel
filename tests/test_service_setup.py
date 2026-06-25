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

from synapse_channel.service_setup import (
    escaped_instance,
    install_user_services,
    render_arm_unit,
    service_suggestions,
)


def test_render_arm_unit_uses_non_llm_synapse_arm() -> None:
    unit = render_arm_unit(synapse_bin="/usr/bin/synapse")
    assert "ExecStart=/usr/bin/synapse arm" in unit
    assert "--directed-only" in unit
    assert "Restart=always" in unit


def test_service_suggestions_include_hub_presence_and_arm() -> None:
    lines = service_suggestions(project="repo", identity="repo/ux", synapse_bin="/bin/synapse")
    text = "\n".join(lines)
    assert "synapse-hub.service" in text
    assert "synapse-presence@.service" in text
    assert "synapse-arm@.service" in text
    assert "/bin/synapse" in text


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


def test_escaped_instance_falls_back_when_systemd_escape_fails() -> None:
    def runner(
        args: list[str], *, capture_output: bool = False, text: bool = False, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="missing")

    assert (
        escaped_instance("repo/ux", template="synapse-arm@.service", runner=runner)
        == "synapse-arm@repo-ux.service"
    )
