# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strong Zed X11 ownership contracts
"""Verify exact Zed WM_CLASS, title, PID, process-group, and deadline checks."""

from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

from e2e.opencode_editors import zed_x11

_PINNED_ZED_X11_REGEX = r"^dev\.zed\.Zed$"


def _completed(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Return a typed X11 subprocess result."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_required_executable_accepts_only_an_absolute_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="required executable is unavailable"):
        zed_x11.required_executable("xdotool")
    monkeypatch.setattr(shutil, "which", lambda _name: "bin/xdotool")
    with pytest.raises(RuntimeError, match="required executable is unavailable"):
        zed_x11.required_executable("xdotool")
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/xdotool")
    assert zed_x11.required_executable("xdotool") == "/usr/bin/xdotool"


def test_remaining_timeout_uses_phase_remainder_and_command_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    assert zed_x11._remaining_timeout(12.5) == 2.5
    assert zed_x11._remaining_timeout(30.0) == 10.0
    with pytest.raises(RuntimeError, match="deadline expired"):
        zed_x11._remaining_timeout(10.0)


def test_xdotool_uses_absolute_timeout_and_normalises_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], float]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs["timeout"]
        assert isinstance(timeout, float)
        calls.append((command, timeout))
        return subprocess.CompletedProcess(command, 0, "123\n", "")

    monkeypatch.setattr(zed_x11, "required_executable", lambda _name: "/usr/bin/xdotool")
    monkeypatch.setattr(time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(subprocess, "run", run)
    assert zed_x11._run_xdotool("search", deadline=3.0).stdout == "123\n"
    assert calls == [(["/usr/bin/xdotool", "search"], 2.0)]

    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["xdotool"], 2.0, output=b"partial")

    monkeypatch.setattr(subprocess, "run", timeout)
    result = zed_x11._run_xdotool("search", deadline=3.0)
    assert (result.returncode, result.stdout, result.stderr) == (
        124,
        "",
        "xdotool command timed out",
    )


def test_checked_xdotool_accepts_success_and_rejects_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(zed_x11, "_run_xdotool", lambda *_args, **_kwargs: _completed())
    zed_x11.checked_xdotool("focus", "windowfocus", "123", deadline=1.0)
    monkeypatch.setattr(
        zed_x11,
        "_run_xdotool",
        lambda *_args, **_kwargs: _completed(2, "fallback", ""),
    )
    with pytest.raises(RuntimeError, match="could not focus: fallback"):
        zed_x11.checked_xdotool("focus", "windowfocus", "123", deadline=1.0)


def test_focus_window_for_input_proves_the_exact_current_xid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[tuple[str, tuple[str, ...], float]] = []
    monkeypatch.setattr(
        zed_x11,
        "checked_xdotool",
        lambda action, *args, deadline: actions.append((action, args, deadline)),
    )
    monkeypatch.setattr(
        zed_x11,
        "_run_xdotool",
        lambda *args, **_kwargs: (
            _completed(stdout="123\n")
            if args == ("getwindowfocus", "-f")
            else _completed(2, stderr="unexpected")
        ),
    )
    zed_x11.focus_window_for_input("123", deadline=7.0)
    assert actions == [
        ("focus the Zed input target", ("windowfocus", "--sync", "123"), 7.0),
    ]


@pytest.mark.parametrize(
    "result",
    [
        _completed(2, stderr="display unavailable"),
        _completed(stdout="123\n", stderr="warning"),
        _completed(stdout="invalid\n"),
        _completed(stdout="0\n"),
        _completed(stdout="0123\n"),
        _completed(stdout="456\n"),
    ],
)
def test_focus_window_for_input_rejects_unproved_focus(
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str],
) -> None:
    monkeypatch.setattr(zed_x11, "checked_xdotool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(zed_x11, "_run_xdotool", lambda *_args, **_kwargs: result)
    with pytest.raises(RuntimeError, match="could not prove Zed input focus"):
        zed_x11.focus_window_for_input("123", deadline=7.0)


def test_window_id_parser_accepts_only_exact_visible_search_shapes() -> None:
    selector = ("--class", _PINNED_ZED_X11_REGEX)
    assert zed_x11._window_ids(_completed(1), selector=selector) == ()
    assert zed_x11._window_ids(_completed(stdout="123\n123\n"), selector=selector) == ("123",)


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (_completed(2, stderr="display unavailable"), "could not search"),
        (_completed(stderr="warning"), "unclassifiable"),
        (_completed(), "unclassifiable"),
        (_completed(stdout="invalid\n"), "malformed"),
        (_completed(stdout="0\n"), "malformed"),
    ],
)
def test_window_id_parser_rejects_transport_and_identifier_failures(
    result: subprocess.CompletedProcess[str],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        zed_x11._window_ids(result, selector=("--class", _PINNED_ZED_X11_REGEX))


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (_completed(1, stderr="gone"), "classify the Zed window title"),
        (_completed(stdout="project\nsecond\n"), "classify the Zed window title"),
        (_completed(stdout=""), "classify the Zed window title"),
    ],
)
def test_window_title_parser_rejects_noncanonical_results(
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str],
    message: str,
) -> None:
    monkeypatch.setattr(zed_x11, "_run_xdotool", lambda *_args, **_kwargs: result)
    with pytest.raises(RuntimeError, match=message):
        zed_x11._required_window_title("123", deadline=1.0)


def test_window_title_and_pid_parsers_accept_pinned_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter((_completed(stdout="project\n"), _completed(stdout="42\n")))
    monkeypatch.setattr(zed_x11, "_run_xdotool", lambda *_args, **_kwargs: next(results))
    assert zed_x11._required_window_title("123", deadline=1.0) == "project"
    assert zed_x11._required_window_pid("123", deadline=1.0) == 42
    assert zed_x11._title_matches_project("project", "project") is True
    assert zed_x11._title_matches_project("project — README.md", "project") is True
    assert zed_x11._title_matches_project("project-copy", "project") is False


@pytest.mark.parametrize(
    "result",
    [
        _completed(1, stderr="gone"),
        _completed(stdout="1\n"),
        _completed(stdout="042\n"),
        _completed(stdout="invalid\n"),
    ],
)
def test_window_pid_parser_rejects_noncanonical_results(
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str],
) -> None:
    monkeypatch.setattr(zed_x11, "_run_xdotool", lambda *_args, **_kwargs: result)
    with pytest.raises(RuntimeError, match="classify the Zed window process"):
        zed_x11._required_window_pid("123", deadline=1.0)


def test_process_group_parser_normalises_owner_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "getpgid", lambda _pid: 77)
    assert zed_x11._required_process_group(42) == 77

    def vanished(_pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", vanished)
    with pytest.raises(RuntimeError, match="owner exited"):
        zed_x11._required_process_group(42)

    def denied(_pid: int) -> int:
        raise PermissionError

    monkeypatch.setattr(os, "getpgid", denied)
    with pytest.raises(RuntimeError, match="could not be read"):
        zed_x11._required_process_group(42)


def test_bounded_sleep_requires_the_complete_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    zed_x11.bounded_sleep(2.0, 0.25)
    assert sleeps == [0.25]
    with pytest.raises(RuntimeError, match="cannot accommodate"):
        zed_x11.bounded_sleep(2.0, -1.0)
    with pytest.raises(RuntimeError, match="cannot accommodate"):
        zed_x11.bounded_sleep(1.1, 0.25)


def test_owned_window_requires_exact_strong_identity_and_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selectors: list[tuple[str, str]] = []

    def search(selector: tuple[str, str], **_kwargs: object) -> tuple[str, ...]:
        selectors.append(selector)
        return ("123",)

    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(zed_x11, "_search_window_ids", search)
    monkeypatch.setattr(
        zed_x11,
        "_required_window_title",
        lambda *_args, **_kwargs: "project — README.md",
    )
    monkeypatch.setattr(zed_x11, "_required_window_pid", lambda *_args, **_kwargs: 42)
    monkeypatch.setattr(zed_x11, "_required_process_group", lambda _pid: 77)
    assert zed_x11.find_owned_window(1.0, process_group=77, project_name="project") == "123"
    assert zed_x11._PINNED_ZED_APP_ID == "dev.zed.Zed"
    assert zed_x11._PINNED_ZED_APP_ID_REGEX == _PINNED_ZED_X11_REGEX
    assert selectors == [
        ("--class", _PINNED_ZED_X11_REGEX),
        ("--classname", _PINNED_ZED_X11_REGEX),
    ]


def test_window_search_uses_the_exact_selector_and_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []

    def run(*args: str, deadline: float) -> subprocess.CompletedProcess[str]:
        calls.append((args, deadline))
        return _completed(stdout="123\n")

    monkeypatch.setattr(zed_x11, "_run_xdotool", run)
    assert zed_x11._search_window_ids(
        ("--class", _PINNED_ZED_X11_REGEX),
        deadline=7.0,
    ) == ("123",)
    assert calls == [
        (("search", "--onlyvisible", "--class", _PINNED_ZED_X11_REGEX), 7.0),
    ]


@pytest.mark.parametrize(
    ("class_windows", "instance_windows", "message"),
    [
        (("123",), (), "selectors disagreed"),
        (("123", "456"), ("123", "456"), "multiple strong window candidates"),
    ],
)
def test_owned_window_rejects_selector_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
    class_windows: tuple[str, ...],
    instance_windows: tuple[str, ...],
    message: str,
) -> None:
    outputs = iter((class_windows, instance_windows))
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        zed_x11,
        "_search_window_ids",
        lambda *_args, **_kwargs: next(outputs),
    )
    with pytest.raises(RuntimeError, match=message):
        zed_x11.find_owned_window(1.0, process_group=77, project_name="project")


def test_owned_window_rejects_wrong_title_or_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        zed_x11,
        "_search_window_ids",
        lambda *_args, **_kwargs: ("123",),
    )
    monkeypatch.setattr(
        zed_x11,
        "_required_window_title",
        lambda *_args, **_kwargs: "attacker",
    )
    with pytest.raises(RuntimeError, match="title did not match"):
        zed_x11.find_owned_window(1.0, process_group=77, project_name="project")

    monkeypatch.setattr(
        zed_x11,
        "_required_window_title",
        lambda *_args, **_kwargs: "project",
    )
    monkeypatch.setattr(zed_x11, "_required_window_pid", lambda *_args, **_kwargs: 42)
    monkeypatch.setattr(zed_x11, "_required_process_group", lambda _pid: 78)
    with pytest.raises(RuntimeError, match="not owned"):
        zed_x11.find_owned_window(1.0, process_group=77, project_name="project")


def test_owned_window_ignores_title_only_impostors_and_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    clock = iter((0.0, 0.0, 1.0))

    def search(selector: tuple[str, str], **_kwargs: object) -> tuple[str, ...]:
        calls.append(selector)
        return ()

    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(zed_x11, "_search_window_ids", search)
    monkeypatch.setattr(zed_x11, "bounded_sleep", lambda *_args: None)
    with pytest.raises(RuntimeError, match="owned visible window"):
        zed_x11.find_owned_window(0.5, process_group=77, project_name="project")
    assert calls == [
        ("--class", _PINNED_ZED_X11_REGEX),
        ("--classname", _PINNED_ZED_X11_REGEX),
        ("--class", _PINNED_ZED_X11_REGEX),
        ("--classname", _PINNED_ZED_X11_REGEX),
    ]


@pytest.mark.parametrize(
    ("process_group", "project_name"),
    [(1, "project"), (77, "")],
)
def test_owned_window_rejects_invalid_identity_inputs(
    process_group: int,
    project_name: str,
) -> None:
    with pytest.raises(ValueError, match="valid process group and project name"):
        zed_x11.find_owned_window(
            1.0,
            process_group=process_group,
            project_name=project_name,
        )
