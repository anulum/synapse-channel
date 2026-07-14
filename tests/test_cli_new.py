# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the workspace scaffold CLI commands

from __future__ import annotations

import argparse
import inspect
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_new
from synapse_channel.coding_fleet_template import DEFAULT_WORKSPACE, create_coding_fleet


class TestCmdNewCodingFleet:
    """Cover the ``new coding-fleet`` command handler with an injected creator."""

    def test_success_prints_lines_and_returns_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        recorded: dict[str, Any] = {}

        def _creator(path: Path, *, force: bool) -> list[str]:
            recorded["path"] = path
            recorded["force"] = force
            return ["created a", "created b"]

        args = argparse.Namespace(path="/tmp/ws", force=True)
        code = cli_new._cmd_new_coding_fleet(args, creator=_creator)
        captured = capsys.readouterr()
        assert code == 0
        assert recorded["path"] == Path("/tmp/ws")
        assert isinstance(recorded["path"], Path)
        assert recorded["force"] is True
        assert captured.out.splitlines() == ["created a", "created b"]

    def test_file_exists_error_reports_to_stderr_and_returns_two(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _creator(path: Path, *, force: bool) -> list[str]:
            raise FileExistsError("directory is not empty")

        args = argparse.Namespace(path="/tmp/ws", force=False)
        code = cli_new._cmd_new_coding_fleet(args, creator=_creator)
        captured = capsys.readouterr()
        assert code == 2
        assert "synapse new coding-fleet: directory is not empty" in captured.err
        assert captured.out == ""

    def test_default_creator_is_create_coding_fleet(self) -> None:
        default = inspect.signature(cli_new._cmd_new_coding_fleet).parameters["creator"].default
        assert default is create_coding_fleet


class TestAddParsers:
    """Cover ``synapse new`` scaffold parser registration."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="synapse")
        subparsers = parser.add_subparsers()
        cli_new.add_parsers(subparsers)
        return parser

    def test_defaults_and_func_binding(self) -> None:
        args = self._parser().parse_args(["new", "coding-fleet"])
        assert args.func is cli_new._cmd_new_coding_fleet
        assert args.new_command == "coding-fleet"
        assert args.path == DEFAULT_WORKSPACE
        assert args.force is False

    def test_explicit_path_and_force(self) -> None:
        args = self._parser().parse_args(["new", "coding-fleet", "/tmp/demo", "--force"])
        assert args.path == "/tmp/demo"
        assert args.force is True

    def test_new_requires_a_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            self._parser().parse_args(["new"])
