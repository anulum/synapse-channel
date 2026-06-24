# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `syn` agent-ergonomic layer

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from synapse_channel import cli, ergonomics
from synapse_channel.ergonomics import (
    Identity,
    arm_argv,
    board_argv,
    inbox_argv,
    is_plausible_project,
    name_lines,
    resolve_identity,
    say_argv,
)

# --- identity resolution (the CWD footgun fix) -------------------------------


def test_explicit_project_flag_wins() -> None:
    ident = resolve_identity(project="SCPN-CONTROL", env={}, cwd_basename="anulum")
    assert ident.project == "SCPN-CONTROL"
    assert ident.identity == "SCPN-CONTROL"
    assert ident.source == "flag"


def test_flag_with_id_builds_a_multi_agent_identity() -> None:
    ident = resolve_identity(project="quantum", agent_id="7f3a", env={})
    assert ident.identity == "quantum/claude-7f3a"
    assert ident.waiter_name == "quantum/claude-7f3a-rx"


def test_custom_type_in_multi_agent_identity() -> None:
    ident = resolve_identity(project="quantum", agent_id="2b40", agent_type="codex", env={})
    assert ident.identity == "quantum/codex-2b40"


def test_syn_project_env_over_cwd() -> None:
    ident = resolve_identity(env={"SYN_PROJECT": "REMANENTIA"}, cwd_basename="anulum")
    assert ident.project == "REMANENTIA"
    assert ident.source == "env"


def test_syn_identity_env_supplies_full_identity() -> None:
    ident = resolve_identity(env={"SYN_IDENTITY": "quantum/codex-2b40"}, cwd_basename="anulum")
    assert ident.project == "quantum"  # first segment
    assert ident.identity == "quantum/codex-2b40"  # verbatim
    assert ident.source == "env"


def test_explicit_id_overrides_syn_identity() -> None:
    ident = resolve_identity(
        agent_id="9999", env={"SYN_IDENTITY": "quantum/codex-2b40"}, cwd_basename="x"
    )
    assert ident.identity == "quantum/claude-9999"


def test_cwd_is_the_last_resort() -> None:
    ident = resolve_identity(env={}, cwd_basename="SYNAPSE-CHANNEL", home_basename="anulum")
    assert ident.project == "SYNAPSE-CHANNEL"
    assert ident.source == "cwd"
    assert ident.plausible is True


def test_home_directory_identity_is_flagged_implausible() -> None:
    ident = resolve_identity(env={}, cwd_basename="anulum", home_basename="anulum")
    assert ident.plausible is False


# --- plausibility ------------------------------------------------------------


def test_is_plausible_project() -> None:
    assert is_plausible_project("SCPN-CONTROL", home_basename="anulum") is True
    assert is_plausible_project("anulum", home_basename="anulum") is False
    assert is_plausible_project("tmp", home_basename="anulum") is False
    assert is_plausible_project("", home_basename="anulum") is False


# --- argv builders -----------------------------------------------------------


def _ident(project: str = "SCPN-CONTROL", identity: str | None = None) -> Identity:
    return Identity(project=project, identity=identity or project, source="flag", plausible=True)


def test_arm_argv_is_directed_only_and_distinct_by_default() -> None:
    argv = arm_argv(_ident())
    assert argv == ["wait", "--name", "SCPN-CONTROL-rx", "--for", "SCPN-CONTROL", "--directed-only"]


def test_arm_argv_broadcasts_drops_directed_only_and_keeps_extra() -> None:
    argv = arm_argv(_ident(), directed_only=False, extra=["--timeout", "5"])
    assert "--directed-only" not in argv
    assert argv[-2:] == ["--timeout", "5"]


def test_say_argv_sends_as_the_bare_project_not_the_waiter() -> None:
    argv = say_argv(_ident(), "REMANENTIA,CEO", "hello")
    assert argv == ["send", "--name", "SCPN-CONTROL", "--target", "REMANENTIA,CEO", "hello"]


def test_inbox_argv_is_project_scoped_and_cursored() -> None:
    argv = inbox_argv(_ident(), feed="/h/feed.ndjson", cursor="/h/SCPN-CONTROL.cursor")
    assert argv == [
        "relay",
        "/h/feed.ndjson",
        "--project",
        "SCPN-CONTROL",
        "--cursor",
        "/h/SCPN-CONTROL.cursor",
    ]


def test_board_argv() -> None:
    assert board_argv(_ident()) == ["board", "--name", "SCPN-CONTROL"]


def test_name_lines_reports_plausible_and_implausible() -> None:
    ok = name_lines(_ident())
    assert any("plausible: yes" in line for line in ok)
    bad = name_lines(Identity("anulum", "anulum", "cwd", plausible=False))
    assert any("accidental" in line for line in bad)


# --- cwd basename + syn home -------------------------------------------------


def test_cwd_basename_uses_git_toplevel() -> None:
    assert ergonomics._cwd_basename(runner=lambda cmd: "/work/MyRepo") == "MyRepo"


def test_cwd_basename_falls_back_to_cwd_when_git_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "cwd", staticmethod(lambda: Path("/work/elsewhere")))
    assert ergonomics._cwd_basename(runner=lambda cmd: "") == "elsewhere"


def test_cwd_basename_falls_back_when_git_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: Sequence[str]) -> str:
        raise OSError("no git")

    monkeypatch.setattr(Path, "cwd", staticmethod(lambda: Path("/work/repoX")))
    assert ergonomics._cwd_basename(runner=boom) == "repoX"


def test_cwd_basename_default_runner_returns_a_string() -> None:
    # The real subprocess path (no injected runner) returns the repo or CWD basename.
    assert isinstance(ergonomics._cwd_basename(), str)


def test_syn_home_prefers_override() -> None:
    assert ergonomics._syn_home({"SYN_HOME": "/custom/syn"}) == Path("/custom/syn")


def test_syn_home_defaults_under_home() -> None:
    assert ergonomics._syn_home({"HOME": "/home/u"}) == Path("/home/u/synapse")


# --- the `syn` dispatcher ----------------------------------------------------


@pytest.fixture
def captured_cli(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_main(argv: Sequence[str] | None = None) -> int:
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    monkeypatch.setattr(ergonomics, "_cwd_basename", lambda: "SYNAPSE-CHANNEL")
    monkeypatch.setattr(os, "environ", {"HOME": "/home/u"})
    return calls


def test_main_without_a_verb_prints_help_and_returns_2(
    captured_cli: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert ergonomics.main([]) == 2
    assert "syn" in capsys.readouterr().out


def test_main_name_prints_identity_without_calling_the_cli(
    captured_cli: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert ergonomics.main(["name"]) == 0
    assert "project:  SYNAPSE-CHANNEL" in capsys.readouterr().out
    assert captured_cli == []  # name never reaches the package CLI


def test_main_arm_builds_a_directed_only_waiter(captured_cli: list[list[str]]) -> None:
    assert ergonomics.main(["arm"]) == 0
    assert captured_cli[0] == [
        "wait",
        "--name",
        "SYNAPSE-CHANNEL-rx",
        "--for",
        "SYNAPSE-CHANNEL",
        "--directed-only",
    ]


def test_main_arm_broadcasts_and_passthrough(captured_cli: list[list[str]]) -> None:
    assert ergonomics.main(["arm", "--broadcasts", "--timeout", "5"]) == 0
    argv = captured_cli[0]
    assert "--directed-only" not in argv
    assert argv[-2:] == ["--timeout", "5"]


def test_main_say_routes_target_and_message(captured_cli: list[list[str]]) -> None:
    assert ergonomics.main(["say", "CEO", "ack"]) == 0
    assert captured_cli[0] == ["send", "--name", "SYNAPSE-CHANNEL", "--target", "CEO", "ack"]


def test_main_say_without_a_message_is_a_usage_error(
    captured_cli: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert ergonomics.main(["say", "CEO"]) == 2
    assert "usage" in capsys.readouterr().err
    assert captured_cli == []


def test_main_inbox_is_project_scoped(captured_cli: list[list[str]]) -> None:
    assert ergonomics.main(["inbox"]) == 0
    assert captured_cli[0] == [
        "relay",
        "/home/u/synapse/feed.ndjson",
        "--project",
        "SYNAPSE-CHANNEL",
        "--cursor",
        "/home/u/synapse/SYNAPSE-CHANNEL.cursor",
    ]


def test_main_board(captured_cli: list[list[str]]) -> None:
    assert ergonomics.main(["board"]) == 0
    assert captured_cli[0] == ["board", "--name", "SYNAPSE-CHANNEL"]


def test_main_warns_on_an_implausible_identity(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "main", lambda argv=None: 0)
    monkeypatch.setattr(ergonomics, "_cwd_basename", lambda: "anulum")
    monkeypatch.setattr(os, "environ", {"HOME": "/home/anulum"})
    ergonomics.main(["arm"])
    assert "looks accidental" in capsys.readouterr().err


# --- alias entry points ------------------------------------------------------


def test_aliases_dispatch_to_their_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: Sequence[str] | None = None) -> int:
        seen.append(list(argv or []))
        return 0

    monkeypatch.setattr(ergonomics, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["syn-wait", "--timeout", "5"])
    assert ergonomics.alias_arm() == 0
    monkeypatch.setattr(sys, "argv", ["syn-say", "CEO", "hi"])
    assert ergonomics.alias_say() == 0
    monkeypatch.setattr(sys, "argv", ["syn-name"])
    assert ergonomics.alias_name() == 0
    monkeypatch.setattr(sys, "argv", ["syn-inbox"])
    assert ergonomics.alias_inbox() == 0
    monkeypatch.setattr(sys, "argv", ["syn-board"])
    assert ergonomics.alias_board() == 0
    assert seen == [["arm", "--timeout", "5"], ["say", "CEO", "hi"], ["name"], ["inbox"], ["board"]]
