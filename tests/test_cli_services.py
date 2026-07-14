# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the setup and worker-session CLI commands

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_services


def test_default_project_is_the_current_directory_name() -> None:
    assert cli_services._default_project() == Path.cwd().name


def _init_namespace(
    *,
    project: str | None = "proj",
    identity: str | None = "id",
    install: bool = False,
    start: bool = False,
    synapse_bin: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        project=project,
        identity=identity,
        install_user_services=install,
        start_user_services=start,
        synapse_bin=synapse_bin,
    )


class TestCmdInit:
    """Cover the ``synapse init`` dispatch branches."""

    def test_suggestions_are_printed_when_nothing_is_requested(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        recorded: dict[str, Any] = {}
        installer_calls: list[dict[str, Any]] = []

        def _suggest(**kwargs: Any) -> list[str]:
            recorded.update(kwargs)
            return ["suggest-1", "suggest-2"]

        def _install(**kwargs: Any) -> list[str]:
            installer_calls.append(kwargs)
            return []

        code = cli_services._cmd_init(
            _init_namespace(),
            service_installer=_install,
            suggestion_builder=_suggest,
        )
        out = capsys.readouterr().out
        assert code == 0
        assert installer_calls == []  # nothing requested -> installer untouched
        assert "not installed automatically" in out
        assert "suggest-1" in out
        assert "suggest-2" in out
        assert recorded == {"project": "proj", "identity": "id", "synapse_bin": None}

    def test_install_path_skips_validation_without_a_bin(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        validated: list[str] = []
        monkeypatch.setattr(
            cli_services,
            "validate_systemd_executable",
            lambda value: validated.append(value),
        )
        recorded: dict[str, Any] = {}

        def _install(**kwargs: Any) -> list[str]:
            recorded.update(kwargs)
            return ["installed hub"]

        code = cli_services._cmd_init(
            _init_namespace(install=True),
            service_installer=_install,
            suggestion_builder=lambda **_: [],
        )
        assert code == 0
        assert "installed hub" in capsys.readouterr().out
        assert validated == []  # synapse_bin is None -> no validation call
        assert recorded == {
            "project": "proj",
            "identity": "id",
            "synapse_bin": None,
            "start": False,
        }

    def test_start_path_validates_the_supplied_bin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        validated: list[str] = []
        monkeypatch.setattr(
            cli_services,
            "validate_systemd_executable",
            lambda value: validated.append(value),
        )
        recorded: dict[str, Any] = {}

        def _install(**kwargs: Any) -> list[str]:
            recorded.update(kwargs)
            return []

        code = cli_services._cmd_init(
            _init_namespace(start=True, synapse_bin="/usr/bin/synapse"),
            service_installer=_install,
            suggestion_builder=lambda **_: [],
        )
        assert code == 0
        assert validated == ["/usr/bin/synapse"]
        assert recorded["start"] is True
        assert recorded["synapse_bin"] == "/usr/bin/synapse"

    def test_project_and_identity_fall_back_to_resolver(self) -> None:
        recorded: dict[str, Any] = {}

        def _suggest(**kwargs: Any) -> list[str]:
            recorded.update(kwargs)
            return []

        cli_services._cmd_init(
            _init_namespace(project=None, identity=None),
            service_installer=lambda **_: [],
            suggestion_builder=_suggest,
            project_resolver=lambda: "resolved",
        )
        assert recorded["project"] == "resolved"
        assert recorded["identity"] == "resolved"

    def test_value_error_reports_and_returns_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        def _install(**kwargs: Any) -> list[str]:
            raise ValueError("bad executable")

        code = cli_services._cmd_init(
            _init_namespace(install=True),
            service_installer=_install,
            suggestion_builder=lambda **_: [],
        )
        assert code == 2
        assert "synapse init: bad executable" in capsys.readouterr().out


def _worker_namespace(command: list[str], **overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "command": command,
        "identity": "P/ux",
        "project": None,
        "uri": "ws://hub.test:8876",
        "syn_bin": "syn",
        "token": None,
        "no_arm": False,
        "terminal_tmux": "auto",
        "tmux_bin": "tmux",
        "synapse_bin": "synapse",
        "tmux_session": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestCmdWorkerSession:
    """Cover the ``synapse worker-session`` dispatch branches."""

    def test_leading_double_dash_is_stripped_and_kwargs_forwarded(self) -> None:
        recorded: dict[str, Any] = {}

        def _runner(**kwargs: Any) -> int:
            recorded.update(kwargs)
            return 0

        args = _worker_namespace(["--", "opencode", "run"], token_file="/keys/t")
        code = cli_services._cmd_worker_session(args, session_runner=_runner)
        assert code == 0
        assert recorded["command"] == ["opencode", "run"]
        assert recorded["identity"] == "P/ux"
        assert recorded["token_file"] == "/keys/t"
        assert recorded["arm"] is True

    def test_command_without_separator_is_passed_through(self) -> None:
        recorded: dict[str, Any] = {}

        def _runner(**kwargs: Any) -> int:
            recorded.update(kwargs)
            return 7

        args = _worker_namespace(["provider"], no_arm=True)
        code = cli_services._cmd_worker_session(args, session_runner=_runner)
        assert code == 7
        assert recorded["command"] == ["provider"]
        assert recorded["token_file"] is None  # getattr default when absent
        assert recorded["arm"] is False

    def test_empty_command_returns_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = cli_services._cmd_worker_session(_worker_namespace([]), session_runner=lambda **_: 0)
        assert code == 2
        assert "worker-session requires a provider command after --" in capsys.readouterr().out

    def test_only_separator_becomes_empty_and_returns_two(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = cli_services._cmd_worker_session(
            _worker_namespace(["--"]), session_runner=lambda **_: 0
        )
        assert code == 2
        assert "worker-session requires a provider command after --" in capsys.readouterr().out

    def test_runner_value_error_reports_and_returns_two(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _runner(**kwargs: Any) -> int:
            raise ValueError("cannot arm sidecar")

        code = cli_services._cmd_worker_session(
            _worker_namespace(["provider"]), session_runner=_runner
        )
        assert code == 2
        assert "cannot arm sidecar" in capsys.readouterr().out


class TestAddParsers:
    """Cover setup and worker-session parser registration."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="synapse")
        subparsers = parser.add_subparsers()
        cli_services.add_parsers(subparsers)
        return parser

    def test_init_defaults_and_func_binding(self) -> None:
        args = self._parser().parse_args(["init"])
        assert args.func is cli_services._cmd_init
        assert args.project is None
        assert args.identity is None
        assert args.install_user_services is False
        assert args.start_user_services is False
        assert args.synapse_bin is None

    def test_worker_session_defaults_and_func_binding(self) -> None:
        args = self._parser().parse_args(["worker-session", "--identity", "P/ux", "--", "opencode"])
        assert args.func is cli_services._cmd_worker_session
        assert args.identity == "P/ux"
        assert args.terminal_tmux == "auto"
        assert args.syn_bin == "syn"
        assert args.no_arm is False
        assert "opencode" in args.command

    def test_worker_session_requires_identity(self) -> None:
        with pytest.raises(SystemExit):
            self._parser().parse_args(["worker-session"])

    def test_terminal_tmux_choice_is_validated(self) -> None:
        with pytest.raises(SystemExit):
            self._parser().parse_args(
                ["worker-session", "--identity", "P/ux", "--terminal-tmux", "maybe"]
            )
