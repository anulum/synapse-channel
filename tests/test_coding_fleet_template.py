# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse new coding-fleet` scaffold

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import cli, cli_new
from synapse_channel.coding_fleet import _free_port, run_coding_agents_demo
from synapse_channel.coding_fleet_template import create_coding_fleet


def test_parser_routes_new_coding_fleet() -> None:
    args = cli.build_parser().parse_args(["new", "coding-fleet", "demo-workspace"])

    assert args.func is cli_new._cmd_new_coding_fleet
    assert args.path == "demo-workspace"
    assert args.force is False


def test_create_coding_fleet_writes_runnable_workspace(tmp_path: Path) -> None:
    target = tmp_path / "fleet"

    lines = create_coding_fleet(target)

    assert "created coding fleet workspace" in "\n".join(lines)
    assert (
        (target / "README.md").read_text(encoding="utf-8").startswith("# Synapse coding fleet demo")
    )
    assert "success: coding fleet demo completed" in (target / "README.md").read_text(
        encoding="utf-8"
    )
    assert "synapse_channel.coding_fleet" in (target / "run_demo.py").read_text(encoding="utf-8")
    assert (target / ".synapse" / "project").read_text(encoding="utf-8") == "coding-fleet\n"
    assert (target / "src" / "app" / "api.py").exists()
    assert (target / "tests" / "test_api.py").exists()


def test_create_coding_fleet_refuses_non_empty_directory(tmp_path: Path) -> None:
    target = tmp_path / "fleet"
    target.mkdir()
    (target / "notes.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not empty"):
        create_coding_fleet(target)

    assert (target / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_create_coding_fleet_force_keeps_unrelated_files(tmp_path: Path) -> None:
    target = tmp_path / "fleet"
    target.mkdir()
    (target / "notes.txt").write_text("keep me", encoding="utf-8")

    create_coding_fleet(target, force=True)

    assert (target / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert (target / "run_demo.py").exists()


def test_cmd_new_coding_fleet_prints_next_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "fleet"

    assert cli.main(["new", "coding-fleet", str(target)]) == 0

    out = capsys.readouterr().out
    assert "created coding fleet workspace" in out
    assert "python run_demo.py" in out


async def test_packaged_coding_fleet_demo_prevents_collisions() -> None:
    log = await run_coding_agents_demo(_free_port())

    assert any("claimed src/app/api.py" in line for line in log)
    assert any("refused" in line for line in log)
    assert any("disjoint scope, granted" in line for line in log)
    assert any("test-dev received:" in line for line in log)
    assert any("released" in line for line in log)
