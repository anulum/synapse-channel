# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the shell hook CLI commands

from __future__ import annotations

import argparse
from typing import Any

import pytest

from synapse_channel import cli_shell
from synapse_channel.shell_integration import DEFAULT_PROVIDER_COMMANDS


class TestCmdShellHook:
    """Cover the ``shell-hook`` command handler with an injected renderer."""

    def test_explicit_providers_are_passed_as_a_tuple(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        recorded: dict[str, Any] = {}

        def _renderer(*, shell: str, provider_commands: tuple[str, ...]) -> str:
            recorded.update(shell=shell, provider_commands=provider_commands)
            return "export HOOK=1"

        args = argparse.Namespace(shell="zsh", provider=["codex", "claude"])
        code = cli_shell._cmd_shell_hook(args, renderer=_renderer)
        out = capsys.readouterr().out
        assert code == 0
        assert recorded["shell"] == "zsh"
        assert recorded["provider_commands"] == ("codex", "claude")
        # Printed verbatim with no trailing newline appended.
        assert out == "export HOOK=1"

    def test_absent_providers_fall_back_to_defaults(self) -> None:
        recorded: dict[str, Any] = {}

        def _renderer(*, shell: str, provider_commands: tuple[str, ...]) -> str:
            recorded["provider_commands"] = provider_commands
            return ""

        args = argparse.Namespace(shell="bash", provider=None)
        cli_shell._cmd_shell_hook(args, renderer=_renderer)
        assert recorded["provider_commands"] == DEFAULT_PROVIDER_COMMANDS

    def test_value_error_reports_and_returns_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        def _renderer(*, shell: str, provider_commands: tuple[str, ...]) -> str:
            raise ValueError("unsupported shell")

        args = argparse.Namespace(shell="powershell", provider=None)
        code = cli_shell._cmd_shell_hook(args, renderer=_renderer)
        assert code == 2
        assert "unsupported shell" in capsys.readouterr().out


class TestCmdInstallShellHook:
    """Cover the ``install-shell-hook`` command handler with an injected installer."""

    def test_success_prints_lines_and_forwards_arguments(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        recorded: dict[str, Any] = {}

        def _installer(*, shell: str, synapse_bin: str) -> list[str]:
            recorded.update(shell=shell, synapse_bin=synapse_bin)
            return ["wrote ~/.bashrc block", "reload your shell"]

        args = argparse.Namespace(shell="auto", synapse_bin="synapse")
        code = cli_shell._cmd_install_shell_hook(args, installer=_installer)
        out = capsys.readouterr().out
        assert code == 0
        assert recorded == {"shell": "auto", "synapse_bin": "synapse"}
        assert "wrote ~/.bashrc block" in out
        assert "reload your shell" in out

    def test_value_error_reports_and_returns_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        def _installer(*, shell: str, synapse_bin: str) -> list[str]:
            raise ValueError("no writable startup file")

        args = argparse.Namespace(shell="auto", synapse_bin="synapse")
        code = cli_shell._cmd_install_shell_hook(args, installer=_installer)
        assert code == 2
        assert "no writable startup file" in capsys.readouterr().out


class TestAddParsers:
    """Cover shell integration parser registration."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="synapse")
        subparsers = parser.add_subparsers()
        cli_shell.add_parsers(subparsers)
        return parser

    def test_shell_hook_defaults_and_func_binding(self) -> None:
        args = self._parser().parse_args(["shell-hook"])
        assert args.func is cli_shell._cmd_shell_hook
        assert args.shell == "bash"
        assert args.provider is None

    def test_shell_hook_provider_appends_and_shell_choice_validated(self) -> None:
        args = self._parser().parse_args(
            ["shell-hook", "--shell", "fish", "--provider", "codex", "--provider", "claude"]
        )
        assert args.shell == "fish"
        assert args.provider == ["codex", "claude"]
        with pytest.raises(SystemExit):
            self._parser().parse_args(["shell-hook", "--shell", "powershell"])

    def test_install_shell_hook_defaults_and_func_binding(self) -> None:
        args = self._parser().parse_args(["install-shell-hook"])
        assert args.func is cli_shell._cmd_install_shell_hook
        assert args.shell == "auto"
        assert args.synapse_bin == "synapse"
