# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `syn` agent-ergonomic layer

from __future__ import annotations

import importlib
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

import synapse_channel.ack as ack_module
from synapse_channel import ergonomics
from synapse_channel.ergonomics import (
    Identity,
    arm_argv,
    board_argv,
    inbox_argv,
    is_plausible_project,
    name_lines,
    resolve_identity,
    say_argv,
    who_argv,
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


def test_syn_project_and_identity_env_keep_full_identity() -> None:
    ident = resolve_identity(
        env={"SYN_PROJECT": "quantum", "SYN_IDENTITY": "quantum/codex-2b40"},
        cwd_basename="anulum",
    )
    assert ident.project == "quantum"
    assert ident.identity == "quantum/codex-2b40"
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
    assert argv == ["arm", "--name", "SCPN-CONTROL-rx", "--for", "SCPN-CONTROL", "--directed-only"]


def test_arm_argv_broadcasts_drops_directed_only_and_keeps_extra() -> None:
    argv = arm_argv(_ident(), directed_only=False, extra=["--timeout", "5"])
    assert "--directed-only" not in argv
    assert argv[-2:] == ["--timeout", "5"]


def test_say_argv_sends_as_the_full_identity_by_default() -> None:
    argv = say_argv(_ident(identity="SCPN-CONTROL/coordinator"), "REMANENTIA,CEO", "hello")
    assert argv == [
        "send",
        "--name",
        "SCPN-CONTROL/coordinator",
        "--target",
        "REMANENTIA,CEO",
        "hello",
    ]


def test_say_argv_can_send_as_the_bare_project() -> None:
    argv = say_argv(
        _ident(identity="SCPN-CONTROL/coordinator"), "REMANENTIA,CEO", "hello", as_project=True
    )
    assert argv == ["send", "--name", "SCPN-CONTROL", "--target", "REMANENTIA,CEO", "hello"]


def test_ask_argv_waits_and_requires_recipient_by_default() -> None:
    argv = ergonomics.ask_argv(
        _ident(identity="SCPN-CONTROL/codex-1"),
        "SCPN-CONTROL/tester",
        "status?",
        wait_seconds=15.0,
    )
    assert argv == [
        "send",
        "--name",
        "SCPN-CONTROL/codex-1",
        "--target",
        "SCPN-CONTROL/tester",
        "--wait-seconds",
        "15",
        "--require-recipient",
        "status?",
    ]


def test_ask_argv_can_skip_recipient_requirement_and_keep_extra() -> None:
    argv = ergonomics.ask_argv(
        _ident(identity="SCPN-CONTROL/codex-1"),
        "all",
        "status?",
        wait_seconds=2.5,
        require_recipient=False,
        extra=["--receipt-timeout", "1"],
    )
    assert "--require-recipient" not in argv
    assert argv[-3:] == ["--receipt-timeout", "1", "status?"]


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


def test_who_argv_uses_identity_as_the_subject() -> None:
    assert who_argv(_ident(identity="SCPN-CONTROL/codex-1"), extra=["--me"]) == [
        "who",
        "--name",
        "SCPN-CONTROL/codex-1",
        "--me",
    ]


def test_name_lines_reports_plausible_and_implausible() -> None:
    ok = name_lines(_ident())
    assert any("plausible: yes" in line for line in ok)
    bad = name_lines(Identity("anulum", "anulum", "cwd", plausible=False))
    assert any("accidental" in line for line in bad)


# --- cwd basename + syn home -------------------------------------------------


def test_cwd_basename_uses_git_toplevel() -> None:
    assert ergonomics._cwd_basename(runner=lambda cmd: "/work/MyRepo") == "MyRepo"


def test_cwd_basename_falls_back_to_cwd_when_git_blank(tmp_path: Path) -> None:
    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert ergonomics._cwd_basename(runner=lambda cmd: "") == tmp_path.name
    finally:
        os.chdir(old_cwd)


def test_cwd_basename_falls_back_when_git_errors(tmp_path: Path) -> None:
    def boom(cmd: Sequence[str]) -> str:
        raise OSError("no git")

    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert ergonomics._cwd_basename(runner=boom) == tmp_path.name
    finally:
        os.chdir(old_cwd)


def test_cwd_basename_default_runner_returns_a_string() -> None:
    # The real subprocess path (no injected runner) returns the repo or CWD basename.
    assert isinstance(ergonomics._cwd_basename(), str)


def test_syn_home_prefers_override() -> None:
    assert ergonomics._syn_home({"SYN_HOME": "/custom/syn"}) == Path("/custom/syn")


def test_syn_home_defaults_under_home() -> None:
    assert ergonomics._syn_home({"HOME": "/home/u"}) == Path("/home/u/synapse")


# --- the `syn` dispatcher ----------------------------------------------------


class CapturedCalls(list[list[str]]):
    """Collected CLI dispatches for ergonomics tests."""

    def dispatch(self, argv: Sequence[str] | None = None) -> int:
        """Record an argv vector and report success."""
        self.append(list(argv or []))
        return 0


@pytest.fixture
def captured_cli() -> CapturedCalls:
    return CapturedCalls()


def _dispatch(captured_cli: CapturedCalls) -> ergonomics.CliDispatcher:
    return captured_cli.dispatch


def test_main_without_a_verb_prints_help_and_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert ergonomics.main([]) == 2
    assert "syn" in capsys.readouterr().out


def test_main_name_prints_identity_without_calling_the_cli(
    captured_cli: CapturedCalls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ergonomics.main(["name"], env={"HOME": "/home/u"}, cwd_basename="SYNAPSE-CHANNEL") == 0
    assert "project:  SYNAPSE-CHANNEL" in capsys.readouterr().out
    assert captured_cli == []  # name never reaches the package CLI


def test_main_arm_builds_a_directed_only_waiter(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["arm"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == [
        "arm",
        "--name",
        "SYNAPSE-CHANNEL-rx",
        "--for",
        "SYNAPSE-CHANNEL",
        "--directed-only",
    ]


def test_main_arm_broadcasts_and_passthrough(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["arm", "--broadcasts", "--timeout", "5"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    argv = captured_cli[0]
    assert "--directed-only" not in argv
    assert argv[-2:] == ["--timeout", "5"]


def test_main_say_routes_target_and_message(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["say", "CEO", "ack"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == ["send", "--name", "SYNAPSE-CHANNEL", "--target", "CEO", "ack"]


def test_main_say_uses_syn_identity_for_exact_replies(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["say", "CEO", "ack"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/coordinator"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == [
        "send",
        "--name",
        "SYNAPSE-CHANNEL/coordinator",
        "--target",
        "CEO",
        "ack",
    ]


def test_main_say_as_project_keeps_shared_project_sender(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["say", "--as-project", "CEO", "ack"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/coordinator"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == ["send", "--name", "SYNAPSE-CHANNEL", "--target", "CEO", "ack"]


def test_main_say_without_a_message_is_a_usage_error(
    captured_cli: CapturedCalls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        ergonomics.main(
            ["say", "CEO"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 2
    )
    assert "usage" in capsys.readouterr().err
    assert captured_cli == []


def test_main_say_as_project_without_target_and_message_is_a_usage_error(
    captured_cli: CapturedCalls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        ergonomics.main(
            ["say", "--as-project", "CEO"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 2
    )
    assert "usage" in capsys.readouterr().err
    assert captured_cli == []


def test_main_ask_routes_target_message_wait_and_receipt(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["ask", "--wait", "15", "CEO", "status?"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == [
        "send",
        "--name",
        "SYNAPSE-CHANNEL/codex-1",
        "--target",
        "CEO",
        "--wait-seconds",
        "15",
        "--require-recipient",
        "status?",
    ]


def test_main_ask_can_disable_recipient_requirement(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["ask", "--no-require-recipient", "all", "status?"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert "--require-recipient" not in captured_cli[0]
    assert captured_cli[0][-1] == "status?"


def test_main_ask_keeps_extra_send_options(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["ask", "--receipt-timeout", "1", "CEO", "status?", "--priority"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == [
        "send",
        "--name",
        "SYNAPSE-CHANNEL/codex-1",
        "--target",
        "CEO",
        "--wait-seconds",
        "30",
        "--require-recipient",
        "--receipt-timeout",
        "1",
        "--priority",
        "status?",
    ]


def test_main_ask_keeps_extra_flag_without_value(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["ask", "--priority", "--receipt-timeout", "1", "CEO", "status?"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0][-4:] == ["--priority", "--receipt-timeout", "1", "status?"]


def test_main_ask_wait_without_seconds_is_a_usage_error(
    captured_cli: CapturedCalls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        ergonomics.main(
            ["ask", "--wait"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 2
    )
    assert "usage" in capsys.readouterr().err
    assert captured_cli == []


def test_main_ask_wait_needs_number(
    captured_cli: CapturedCalls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        ergonomics.main(
            ["ask", "--wait", "soon", "CEO", "status?"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 2
    )
    assert "--wait needs a number" in capsys.readouterr().err
    assert captured_cli == []


def test_main_ask_without_a_message_is_a_usage_error(
    captured_cli: CapturedCalls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        ergonomics.main(
            ["ask", "CEO"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 2
    )
    assert "usage" in capsys.readouterr().err
    assert captured_cli == []


def test_main_inbox_is_project_scoped(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["inbox"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == [
        "relay",
        "/home/u/synapse/feed.ndjson",
        "--project",
        "SYNAPSE-CHANNEL",
        "--cursor",
        "/home/u/synapse/SYNAPSE-CHANNEL.cursor",
    ]


def test_main_board(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["board"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == ["board", "--name", "SYNAPSE-CHANNEL"]


def test_main_who_me_uses_resolved_identity(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["who", "--me"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert captured_cli[0] == ["who", "--name", "SYNAPSE-CHANNEL/codex-1", "--me"]


def test_main_reap_uses_resolved_identity() -> None:
    seen: list[tuple[Identity, list[str]]] = []

    def reap_runner(identity: Identity, rest: Sequence[str]) -> int:
        seen.append((identity, list(rest)))
        return 0

    assert (
        ergonomics.main(
            ["reap", "--pid", "1234"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            reap_runner=reap_runner,
        )
        == 0
    )
    assert seen == [
        (
            Identity(
                project="SYNAPSE-CHANNEL",
                identity="SYNAPSE-CHANNEL/codex-1",
                source="env",
                plausible=True,
            ),
            ["--pid", "1234"],
        )
    ]


def test_main_locks_uses_resolved_identity() -> None:
    seen: list[tuple[Identity, list[str]]] = []

    def locks_runner(identity: Identity, rest: Sequence[str]) -> int:
        seen.append((identity, list(rest)))
        return 0

    assert (
        ergonomics.main(
            ["locks", "--all"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            locks_runner=locks_runner,
        )
        == 0
    )
    assert seen == [
        (
            Identity(
                project="SYNAPSE-CHANNEL",
                identity="SYNAPSE-CHANNEL/codex-1",
                source="env",
                plausible=True,
            ),
            ["--all"],
        )
    ]


def test_main_ack_uses_resolved_identity() -> None:
    seen: list[tuple[Identity, list[str]]] = []

    def ack_runner(identity: Identity, rest: Sequence[str]) -> int:
        seen.append((identity, list(rest)))
        return 0

    assert (
        ergonomics.main(
            ["ack", "BUILD", "--evidence", "pytest"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            ack_runner=ack_runner,
        )
        == 0
    )
    assert seen == [
        (
            Identity(
                project="SYNAPSE-CHANNEL",
                identity="SYNAPSE-CHANNEL/codex-1",
                source="env",
                plausible=True,
            ),
            ["BUILD", "--evidence", "pytest"],
        )
    ]


def test_main_ack_default_runner_dispatches_ack_module(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Identity, list[str]]] = []

    def ack_main(identity: Identity, rest: Sequence[str] | None = None) -> int:
        seen.append((identity, list(rest or [])))
        return 0

    monkeypatch.setattr(ack_module, "main", ack_main)

    assert (
        ergonomics.main(
            ["ack", "BUILD", "--evidence", "pytest"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
        )
        == 0
    )
    assert seen == [
        (
            Identity(
                project="SYNAPSE-CHANNEL",
                identity="SYNAPSE-CHANNEL/codex-1",
                source="env",
                plausible=True,
            ),
            ["BUILD", "--evidence", "pytest"],
        )
    ]


def test_main_commit_uses_resolved_identity() -> None:
    seen: list[tuple[Identity, list[str]]] = []

    def commit_runner(identity: Identity, rest: Sequence[str]) -> int:
        seen.append((identity, list(rest)))
        return 0

    assert (
        ergonomics.main(
            ["commit", "README.md", "-m", "docs"],
            env={"HOME": "/home/u", "SYN_IDENTITY": "SYNAPSE-CHANNEL/codex-1"},
            cwd_basename="SYNAPSE-CHANNEL",
            commit_runner=commit_runner,
        )
        == 0
    )
    assert seen == [
        (
            Identity(
                project="SYNAPSE-CHANNEL",
                identity="SYNAPSE-CHANNEL/codex-1",
                source="env",
                plausible=True,
            ),
            ["README.md", "-m", "docs"],
        )
    ]


def test_main_warns_on_an_implausible_identity(capsys: pytest.CaptureFixture[str]) -> None:
    def dispatch(argv: list[str] | None = None) -> int:
        return 0

    ergonomics.main(
        ["arm"],
        env={"HOME": "/home/anulum"},
        cwd_basename="anulum",
        dispatcher=dispatch,
    )
    assert "looks accidental" in capsys.readouterr().err


# --- alias entry points ------------------------------------------------------


def test_aliases_dispatch_to_their_verb() -> None:
    seen: list[list[str]] = []

    def dispatch(argv: Sequence[str]) -> int:
        seen.append(list(argv or []))
        return 0

    assert ergonomics.alias_arm(["--timeout", "5"], dispatcher=dispatch) == 0
    assert ergonomics.alias_say(["CEO", "hi"], dispatcher=dispatch) == 0
    assert ergonomics.alias_ask(["CEO", "status?"], dispatcher=dispatch) == 0
    assert ergonomics.alias_name([], dispatcher=dispatch) == 0
    assert ergonomics.alias_inbox([], dispatcher=dispatch) == 0
    assert ergonomics.alias_board([], dispatcher=dispatch) == 0
    assert ergonomics.alias_reap(["--pid", "1234"], dispatcher=dispatch) == 0
    assert ergonomics.alias_locks(["--all"], dispatcher=dispatch) == 0
    assert ergonomics.alias_ack(["BUILD", "--evidence", "pytest"], dispatcher=dispatch) == 0
    assert ergonomics.alias_commit(["README.md", "-m", "docs"], dispatcher=dispatch) == 0
    assert seen == [
        ["arm", "--timeout", "5"],
        ["say", "CEO", "hi"],
        ["ask", "CEO", "status?"],
        ["name"],
        ["inbox"],
        ["board"],
        ["reap", "--pid", "1234"],
        ["locks", "--all"],
        ["ack", "BUILD", "--evidence", "pytest"],
        ["commit", "README.md", "-m", "docs"],
    ]


def test_syn_ask_is_packaged_and_documented() -> None:
    try:
        toml_parser = importlib.import_module("tomllib")
    except ModuleNotFoundError:  # pragma: no cover
        toml_parser = importlib.import_module("tomli")

    root = Path(__file__).resolve().parents[1]
    pyproject = toml_parser.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    readme = (root / "README.md").read_text(encoding="utf-8")
    cli_docs = (root / "docs" / "cli.md").read_text(encoding="utf-8")
    recipes = (root / "docs" / "recipes.md").read_text(encoding="utf-8")

    assert scripts["syn-ask"] == "synapse_channel.ergonomics:alias_ask"
    assert 'syn ask CEO "status?"' in readme
    assert "syn ask <target> <message>" in cli_docs
    assert 'syn ask test-dev "status?"' in recipes


# --- multi-identity inbox (--as / $SYN_ALIASES) --------------------------------------


def test_split_as_names_reads_flags_in_both_forms() -> None:
    names = ergonomics.split_as_names(
        ["--as", "SYNAPSE-CHANNEL/coordinator", "--as=OTHER", "noise"], env={}
    )
    assert names == ["SYNAPSE-CHANNEL/coordinator", "OTHER"]


def test_split_as_names_falls_back_to_the_env_aliases() -> None:
    names = ergonomics.split_as_names([], env={"SYN_ALIASES": "A/coord, B ,, "})
    assert names == ["A/coord", "B"]


def test_explicit_as_flags_beat_the_env_aliases() -> None:
    names = ergonomics.split_as_names(["--as", "X"], env={"SYN_ALIASES": "A,B"})
    assert names == ["X"]


def test_split_as_names_drops_blanks_and_dangling_flag() -> None:
    assert ergonomics.split_as_names(["--as"], env={}) == []
    assert ergonomics.split_as_names(["--as", "  "], env={}) == []


def test_aliased_inbox_argv_scopes_projects_and_exact_names(tmp_path: Path) -> None:
    project = ergonomics.aliased_inbox_argv("ACME", feed="/h/feed.ndjson", home=tmp_path)
    assert project == [
        "relay",
        "/h/feed.ndjson",
        "--project",
        "ACME",
        "--cursor",
        str(tmp_path / "ACME.cursor"),
    ]

    exact = ergonomics.aliased_inbox_argv("ACME/coordinator", feed="/h/feed.ndjson", home=tmp_path)
    assert exact == [
        "relay",
        "/h/feed.ndjson",
        "--for",
        "ACME/coordinator",
        "--cursor",
        str(tmp_path / "ACME__coordinator.cursor"),
    ]


def test_main_inbox_drains_every_as_identity_under_its_own_cursor(
    captured_cli: CapturedCalls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        ergonomics.main(
            ["inbox", "--as", "SYNAPSE-CHANNEL/coordinator", "--as", "ACME"],
            env={"HOME": "/home/u"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert len(captured_cli) == 3
    assert captured_cli[0][:4] == [
        "relay",
        "/home/u/synapse/feed.ndjson",
        "--project",
        "SYNAPSE-CHANNEL",
    ]
    assert captured_cli[1] == [
        "relay",
        "/home/u/synapse/feed.ndjson",
        "--for",
        "SYNAPSE-CHANNEL/coordinator",
        "--cursor",
        "/home/u/synapse/SYNAPSE-CHANNEL__coordinator.cursor",
    ]
    assert captured_cli[2][2:4] == ["--project", "ACME"]
    out = capsys.readouterr().out
    assert "--- inbox as SYNAPSE-CHANNEL/coordinator ---" in out
    assert "--- inbox as ACME ---" in out


def test_main_inbox_env_aliases_apply_without_flags(captured_cli: CapturedCalls) -> None:
    assert (
        ergonomics.main(
            ["inbox"],
            env={"HOME": "/home/u", "SYN_ALIASES": "SYNAPSE-CHANNEL/coordinator"},
            cwd_basename="SYNAPSE-CHANNEL",
            dispatcher=_dispatch(captured_cli),
        )
        == 0
    )
    assert len(captured_cli) == 2
    assert captured_cli[1][2:4] == ["--for", "SYNAPSE-CHANNEL/coordinator"]
