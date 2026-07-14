# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the installed first-run demo command

from __future__ import annotations

import argparse
import inspect

import pytest

from synapse_channel import cli_demo
from synapse_channel.demo import run_installed_demo


class TestCmdDemo:
    """Cover the ``demo`` command handler with an injected runner."""

    def test_prints_markers_and_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        calls: list[str] = []

        def _runner() -> list[str]:
            calls.append("ran")
            return ["line one", "line two"]

        code = cli_demo._cmd_demo(argparse.Namespace(), demo_runner=_runner)
        out = capsys.readouterr().out
        assert code == 0
        assert calls == ["ran"]
        assert "=== SYNAPSE CHANNEL — first-run demo ===" in out
        assert "success: coordination demo completed" in out

    def test_runner_exceptions_propagate(self) -> None:
        def _boom() -> list[str]:
            raise RuntimeError("demo failed")

        with pytest.raises(RuntimeError, match="demo failed"):
            cli_demo._cmd_demo(argparse.Namespace(), demo_runner=_boom)

    def test_default_runner_is_the_installed_demo(self) -> None:
        default = inspect.signature(cli_demo._cmd_demo).parameters["demo_runner"].default
        assert default is run_installed_demo


class TestAddParsers:
    """Cover registration of the ``demo`` subcommand."""

    def test_demo_command_is_registered_and_bound(self) -> None:
        parser = argparse.ArgumentParser(prog="synapse")
        subparsers = parser.add_subparsers()
        cli_demo.add_parsers(subparsers)
        args = parser.parse_args(["demo"])
        assert args.func is cli_demo._cmd_demo
