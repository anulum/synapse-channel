# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the installed first-run demo path

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import cli, cli_demo
from synapse_channel.demo import _free_port, run_coordination_demo

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_repo_text(relative_path: str) -> str:
    """Read a repository text file for first-run documentation checks."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _single_spaced(text: str) -> str:
    """Normalize documentation whitespace for prose phrase checks."""
    return " ".join(text.split())


def test_parser_routes_demo_to_installed_first_run_command() -> None:
    args = cli.build_parser().parse_args(["demo"])

    assert args.func is cli_demo._cmd_demo


async def test_installed_demo_drives_core_coordination_flow() -> None:
    log = await run_coordination_demo(_free_port())

    assert any("Two agents are online" in line for line in log)
    assert any("ready set: ['BUILD']" in line for line in log)
    assert any("refused" in line for line in log)
    assert any("ready set: ['TEST']" in line for line in log)
    assert any("handed TEST off to PLANNER" in line for line in log)


def test_cmd_demo_prints_success_criterion(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["demo"]) == 0

    out = capsys.readouterr().out
    assert "SYNAPSE CHANNEL — first-run demo" in out
    assert "success: coordination demo completed" in out


def test_public_docs_explain_the_60_second_install_doctor_demo_path() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/quickstart.md"),
                _read_repo_text("docs/cli.md"),
                _read_repo_text("examples/README.md"),
            ]
        )
    )

    assert "First 60 seconds" in combined
    assert "python -m pip install synapse-channel" in combined
    assert "synapse doctor" in combined
    assert "synapse demo" in combined
    assert "success: coordination demo completed" in combined


async def test_demo_helpers_time_out_honestly(tmp_path: object) -> None:
    """The demo's waiters raise a named TimeoutError instead of hanging."""
    import pytest

    from synapse_channel import demo as demo_module

    inbox = demo_module.DemoInbox()
    with pytest.raises(TimeoutError, match="expected message did not arrive"):
        await inbox.wait_for(lambda _m: False, timeout=0.05)
    dead_port = demo_module._free_port()
    with pytest.raises(TimeoutError, match="did not start listening"):
        await demo_module._await_listening(dead_port, timeout=0.05)
