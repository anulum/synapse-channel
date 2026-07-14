# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the quickstart-coding CLI command

from __future__ import annotations

import argparse
import inspect
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_quickstart_coding
from synapse_channel.quickstart_coding import run_quickstart_coding


class TestCmdQuickstartCoding:
    """Cover the ``quickstart-coding`` command handler with an injected runner."""

    def test_explicit_path_is_coerced_and_flags_forwarded(self) -> None:
        recorded: dict[str, Any] = {}

        def _runner(path: Path | None, *, force: bool, keep: bool) -> int:
            recorded.update(path=path, force=force, keep=keep)
            return 0

        args = argparse.Namespace(path="/tmp/ws", force=True, keep=False)
        code = cli_quickstart_coding._cmd_quickstart_coding(args, runner=_runner)
        assert code == 0
        assert recorded["path"] == Path("/tmp/ws")
        assert isinstance(recorded["path"], Path)
        assert recorded["force"] is True
        assert recorded["keep"] is False

    def test_absent_path_is_passed_as_none(self) -> None:
        recorded: dict[str, Any] = {}

        def _runner(path: Path | None, *, force: bool, keep: bool) -> int:
            recorded["path"] = path
            return 0

        args = argparse.Namespace(path=None, force=False, keep=True)
        code = cli_quickstart_coding._cmd_quickstart_coding(args, runner=_runner)
        assert code == 0
        assert recorded["path"] is None

    def test_runner_return_code_is_propagated(self) -> None:
        def _runner(path: Path | None, *, force: bool, keep: bool) -> int:
            return 3

        args = argparse.Namespace(path=None, force=False, keep=False)
        assert cli_quickstart_coding._cmd_quickstart_coding(args, runner=_runner) == 3

    def test_file_exists_error_reports_and_returns_two(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _runner(path: Path | None, *, force: bool, keep: bool) -> int:
            raise FileExistsError("workspace is not empty")

        args = argparse.Namespace(path="/tmp/ws", force=False, keep=False)
        code = cli_quickstart_coding._cmd_quickstart_coding(args, runner=_runner)
        assert code == 2
        assert "synapse quickstart-coding: workspace is not empty" in capsys.readouterr().err

    def test_default_runner_is_run_quickstart_coding(self) -> None:
        default = (
            inspect.signature(cli_quickstart_coding._cmd_quickstart_coding)
            .parameters["runner"]
            .default
        )
        assert default is run_quickstart_coding


class TestAddParsers:
    """Cover ``quickstart-coding`` parser registration."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="synapse")
        subparsers = parser.add_subparsers()
        cli_quickstart_coding.add_parsers(subparsers)
        return parser

    def test_defaults_and_func_binding(self) -> None:
        args = self._parser().parse_args(["quickstart-coding"])
        assert args.func is cli_quickstart_coding._cmd_quickstart_coding
        assert args.path is None
        assert args.force is False
        assert args.keep is False

    def test_explicit_path_and_flags(self) -> None:
        args = self._parser().parse_args(["quickstart-coding", "/tmp/demo", "--force", "--keep"])
        assert args.path == "/tmp/demo"
        assert args.force is True
        assert args.keep is True
