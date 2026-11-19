# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests: which cut seats a restore starts, and with what
"""The restore plan: candidates, what holds a row back, the resume table and the order."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Collection, Iterable
from typing import Any

import pytest

from synapse_channel.restore_plan import (
    CAUSE_UNKNOWN,
    CWD_GONE,
    ELSEWHERE,
    ENDED,
    LIVE,
    NEEDS_ATTENTION,
    NEEDS_PROFILE,
    NO_HANDLE,
    OTHER_BOOT,
    RESUME_FORMS,
    START,
    TMUX_IN_USE,
    Entry,
    Scope,
    failures,
    last_rows,
    plan,
    previous_boot,
)
from synapse_channel.session_ledger import Row, fold

HOST = "ws"
NOW, PREV, OLD = "boot-now", "boot-prev", "boot-old"
SID = "11111111-2222-3333-4444-555555555555"


def _start(
    boot: str,
    pid: int,
    identity: str | None = "P/a",
    *,
    launcher: str = "seat-hold",
    vendor: str | None = "claude",
    host: str = HOST,
    cwd: str | None = "/work",
    profile: str | None = None,
    priority: int | None = None,
    started_at: str = "2026-11-18T10:00:00Z",
) -> dict[str, Any]:
    return {
        "event": "start",
        "row_id": f"{boot}:{pid}:7",
        "host": host,
        "boot_id": boot,
        "identity": identity,
        "project": identity.partition("/")[0] if identity else None,
        "vendor": vendor,
        "pid": pid,
        "pid_starttime": 7,
        "cwd": cwd,
        "started_at": started_at,
        "launcher": launcher,
        "holder": None,
        "tmux": None,
        "permission_profile": profile,
        "restore_priority": priority,
    }


def _native(
    row_id: str, native: object = SID, source: str = "claude_sessions_file"
) -> dict[str, Any]:
    return {
        "event": "observe",
        "row_id": row_id,
        "native_session_id": native,
        "native_session_id_source": source,
    }


def _end(row_id: str, kind: str) -> dict[str, Any]:
    event = "end" if kind in ("exit", "signal") else "ended_unobserved"
    return {"event": event, "row_id": row_id, "end_kind": kind}


def _seat(boot: str, pid: int, end: str | None, **start: Any) -> list[dict[str, Any]]:
    """A row with a native id and, when given, its end."""
    opened = _start(boot, pid, **start)
    events = [opened, _native(opened["row_id"])]
    return events + ([] if end is None else [_end(opened["row_id"], end)])


def _plan(
    events: Iterable[dict[str, Any]],
    *,
    scope: Scope | None = None,
    live: Collection[str] = (),
    alive: Callable[[Row], bool] = lambda _row: False,
    cwd_ok: bool = True,
    taken: Collection[str] = (),
) -> list[Entry]:
    return plan(
        fold(events),
        scope or Scope(HOST, NOW, PREV),
        live=live,
        alive=alive,
        cwd_exists=lambda _cwd: cwd_ok,
        tmux_taken=lambda name: name in taken,
    )


def _states(entries: list[Entry]) -> dict[str, str]:
    return {entry.identity: entry.state for entry in entries}


# --- candidates ----------------------------------------------------------------------


def test_a_reboot_cut_row_starts_again_with_its_vendors_resume_command() -> None:
    (entry,) = _plan(_seat(PREV, 10, "boot_cut", priority=3))
    assert entry.state == START
    assert entry.command == ("claude", "--resume", SID)
    assert (entry.cwd, entry.session, entry.project) == ("/work", "synapse-P_a", "P")
    assert (entry.row_id, entry.vendor, entry.priority) == (f"{PREV}:10:7", "claude", 3)
    assert entry.as_dict()["command"] == ("claude", "--resume", SID)


@pytest.mark.parametrize("launcher", ["seat-hold", "worker-session", "observed"])
def test_an_open_row_of_the_previous_boot_was_cut_whoever_launched_it(launcher: str) -> None:
    (entry,) = _plan(_seat(PREV, 10, None, launcher=launcher))
    assert entry.state == START


@pytest.mark.parametrize("kind", ["exit", "signal", "something-new", ""])
def test_a_real_or_unknown_end_is_never_started(kind: str) -> None:
    assert _states(_plan(_seat(PREV, 10, kind))) == {"P/a": ENDED}


@pytest.mark.parametrize("launcher", ["seat-hold", "worker-session"])
def test_a_launcher_that_vanished_in_this_boot_starts_again(launcher: str) -> None:
    assert _states(_plan(_seat(NOW, 10, "observed_gone", launcher=launcher))) == {"P/a": START}
    # Its open row whose process is gone vanished the same way, before any snapshot saw it.
    assert _states(_plan(_seat(NOW, 10, None, launcher=launcher))) == {"P/a": START}


def test_a_bare_agents_end_is_started_only_when_the_run_names_it() -> None:
    # The owner's /exit and a crash look the same to the collector.
    events = _seat(NOW, 10, "observed_gone", launcher="observed")
    assert _states(_plan(events)) == {"P/a": CAUSE_UNKNOWN}
    named = Scope(HOST, NOW, PREV, only=frozenset({"P/a"}))
    assert _states(_plan(events, scope=named)) == {"P/a": START}


def test_a_row_cut_in_an_older_boot_needs_its_boot_named() -> None:
    events = _seat(OLD, 10, "boot_cut")
    assert _states(_plan(events)) == {"P/a": OTHER_BOOT}
    assert _states(_plan(events, scope=Scope(HOST, NOW, OLD, by_default=False))) == {"P/a": START}


def test_a_named_boot_does_not_read_this_boots_vanished_launchers() -> None:
    events = _seat(NOW, 10, "observed_gone")
    assert _states(_plan(events, scope=Scope(HOST, NOW, OLD, by_default=False))) == {
        "P/a": OTHER_BOOT
    }


def test_with_no_earlier_boot_only_this_boots_vanished_launchers_are_read() -> None:
    events = _seat(NOW, 10, "observed_gone") + _seat(NOW, 11, "boot_cut", identity="P/b")
    assert _states(_plan(events, scope=Scope(HOST, NOW, None))) == {
        "P/a": START,
        "P/b": OTHER_BOOT,
    }


def test_another_hosts_row_is_elsewhere() -> None:
    assert _states(_plan(_seat(PREV, 10, "boot_cut", host="ml350"))) == {"P/a": ELSEWHERE}


def test_an_identity_the_snapshot_sees_running_is_live() -> None:
    assert _states(_plan(_seat(PREV, 10, "boot_cut"), live={"P/a"})) == {"P/a": LIVE}


def test_an_open_row_of_this_boot_is_live_while_its_process_runs() -> None:
    events = _seat(NOW, 10, None) + _seat(NOW, 11, None, identity="P/b", launcher="observed")
    assert _states(_plan(events, alive=lambda _row: True)) == {"P/a": LIVE, "P/b": LIVE}
    # Gone: a launcher row starts, an observed row needs the identity named.
    assert _states(_plan(events)) == {"P/a": START, "P/b": CAUSE_UNKNOWN}


def test_two_failed_restores_need_attention_until_the_run_names_the_row() -> None:
    events = _seat(PREV, 10, "boot_cut")
    failing = {"event": "observe", "row_id": f"{PREV}:10:7", "restore": {"failures": 2}}
    assert _states(_plan([*events, failing])) == {"P/a": NEEDS_ATTENTION}
    named = Scope(HOST, NOW, PREV, only=frozenset({"P/a"}))
    assert _states(_plan([*events, failing], scope=named)) == {"P/a": START}
    once = {**failing, "restore": {"failures": 1}}
    (entry,) = _plan([*events, once])
    assert (entry.state, entry.failures) == (START, 1)


@pytest.mark.parametrize("recorded", [None, "two", {"failures": "2"}, {"failures": None}])
def test_an_unreadable_failure_count_counts_as_none(recorded: object) -> None:
    row = Row("r", observed={"restore": recorded})
    assert failures(row) == 0


def test_a_row_without_an_identity_is_never_a_candidate() -> None:
    assert _plan(_seat(PREV, 10, "boot_cut", identity=None)) == []
    assert _plan(_seat(PREV, 10, "boot_cut", identity="")) == []


def test_the_last_row_of_an_identity_decides() -> None:
    events = _seat(PREV, 10, "boot_cut") + _seat(NOW, 11, "exit")
    assert _states(_plan(events)) == {"P/a": ENDED}
    rows = fold(events)
    assert last_rows(rows.values())["P/a"].row_id == f"{NOW}:11:7"


def test_only_restricts_the_run_to_the_named_identities() -> None:
    events = _seat(PREV, 10, "boot_cut") + _seat(PREV, 11, "boot_cut", identity="P/b")
    named = Scope(HOST, NOW, PREV, only=frozenset({"P/b"}))
    assert _states(_plan(events, scope=named)) == {"P/b": START}


# --- what holds a candidate back -----------------------------------------------------


@pytest.mark.parametrize(
    ("native", "source", "vendor"),
    [
        (None, "claude_sessions_file", "claude"),  # no native id
        (SID, "carried", "claude"),  # carried over, not read from this session
        (SID, "claude_sessions_file", "opencode"),  # a vendor without a resume form
        (SID, "claude_sessions_file", None),
        ("--dangerously-skip-permissions", "argv_resume_flag", "claude"),  # never a flag
        ("-r", "argv_resume_flag", "claude"),
        ("a b", "argv_resume_flag", "claude"),
        ("x" * 129, "argv_resume_flag", "claude"),
        (7, "argv_resume_flag", "claude"),
    ],
)
def test_a_seat_without_a_usable_resume_handle_is_not_started(
    native: object, source: str, vendor: str | None
) -> None:
    opened = _start(PREV, 10, vendor=vendor)
    events = [opened, _native(opened["row_id"], native, source), _end(opened["row_id"], "boot_cut")]
    (entry,) = _plan(events)
    assert (entry.state, entry.command) == (NO_HANDLE, ())


def test_a_native_id_read_at_the_start_is_used_when_nothing_was_observed() -> None:
    opened = {**_start(PREV, 10, vendor="codex"), "native_session_id": SID}
    (entry,) = _plan([opened, _end(opened["row_id"], "boot_cut")])
    assert entry.command == ("codex", "resume", SID)


def test_a_permission_profile_holds_the_row_until_it_can_be_applied() -> None:
    assert _states(_plan(_seat(PREV, 10, "boot_cut", profile="safe"))) == {"P/a": NEEDS_PROFILE}


@pytest.mark.parametrize("cwd", ["/gone", None])
def test_a_working_directory_that_is_gone_or_unknown_holds_the_row(cwd: str | None) -> None:
    entries = _plan(_seat(PREV, 10, "boot_cut", cwd=cwd), cwd_ok=cwd is None)
    assert _states(entries) == {"P/a": CWD_GONE}


def test_a_tmux_name_in_use_holds_the_row_and_the_rows_own_session_is_kept() -> None:
    events = _seat(PREV, 10, "boot_cut")
    placed = {"event": "observe", "row_id": f"{PREV}:10:7", "tmux": {"session": "work-1"}}
    (entry,) = _plan([*events, placed], taken={"work-1"})
    assert (entry.state, entry.session) == (TMUX_IN_USE, "work-1")
    (default,) = _plan(events, taken={"work-1"})
    assert (default.state, default.session) == (START, "synapse-P_a")
    blank = {**placed, "tmux": {"session": ""}}
    assert _plan([*events, blank])[0].session == "synapse-P_a"


# --- order ---------------------------------------------------------------------------


def test_starts_come_first_by_priority_then_in_the_order_they_started() -> None:
    events = (
        _seat(PREV, 1, "boot_cut", identity="P/late", started_at="2026-11-18T12:00:00Z")
        + _seat(PREV, 2, "boot_cut", identity="P/early", started_at="2026-11-18T09:00:00Z")
        + _seat(PREV, 3, "boot_cut", identity="P/second", priority=2)
        + _seat(PREV, 4, "exit", identity="P/done", priority=0)
        + _seat(PREV, 5, "boot_cut", identity="P/first", priority=1)
        + _seat(PREV, 6, "boot_cut", identity="P/zero", priority=0)
    )
    order = [entry.identity for entry in _plan(events)]
    assert order == ["P/zero", "P/first", "P/second", "P/early", "P/late", "P/done"]


def test_the_previous_boot_is_the_last_boot_before_this_one() -> None:
    rows = fold(_seat(OLD, 1, "boot_cut") + _seat(PREV, 2, "boot_cut") + _seat(NOW, 3, None))
    assert previous_boot(rows.values(), NOW) == PREV
    assert previous_boot(fold(_seat(NOW, 3, None)).values(), NOW) is None


# --- the resume table ----------------------------------------------------------------

PERMISSION_WORDS = ("permission", "dangerous", "yolo", "full-auto", "bypass", "skip", "trust")


def test_no_resume_form_carries_a_permission_flag() -> None:
    for vendor, form in RESUME_FORMS.items():
        assert form[0] == vendor
        assert not [word for word in PERMISSION_WORDS for part in form if word in part.lower()]


def _cli_knows(binary: str, word: str) -> bool:
    """Return whether the installed CLI lists ``word``: a flag in its help, or a subcommand."""
    probe = ["--help"] if word.startswith("-") else [word, "--help"]
    shown = subprocess.run([binary, *probe], capture_output=True, text=True, timeout=60)
    text = shown.stdout + shown.stderr
    # A flag must be listed; a subcommand must answer and take the session id as its argument.
    return shown.returncode == 0 and (
        word in text if word.startswith("-") else "SESSION_ID" in text
    )


@pytest.mark.parametrize("vendor", sorted(RESUME_FORMS))
def test_each_resume_form_exists_in_the_installed_cli(vendor: str) -> None:
    """The table's flag is in the CLI's help; its subcommand answers its own help.

    A form the CLI does not know would start a fresh, unresumed session. A
    made-up form of the same kind must fail the same check, or the check
    proves nothing.
    """
    binary, word = RESUME_FORMS[vendor]
    found = shutil.which(binary)
    if found is None:
        pytest.skip(f"{vendor} is not installed on this host")
    assert _cli_knows(found, word)
    made_up = "--no-such-resume-form" if word.startswith("-") else "no-such-resume-command"
    assert not _cli_knows(found, made_up)
