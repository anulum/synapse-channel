# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — apply a seat's tab label and status lines to one tmux session
"""Apply the tab label and the status lines to exactly one tmux session.

Only session options of the named session are written, never a global option
and never another session. The session is addressed as ``=<name>:``, tmux's
exact-match form: a plain name is matched as a prefix and could reach a
different session.

Applying replaces the session's status content, so the values that were set
before are saved once in the session option ``@synapse_label_saved`` and
:func:`restore` puts them back. The details are kept in
``@synapse_label_details``, so a later call without arguments, such as the one
the resize hook makes, can lay the same details out again for the new width.

``SYNAPSE_TERMINAL_LABEL=off`` in the environment turns the feature off for
people who label their tabs themselves.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from synapse_channel.terminal_label import (
    MAX_STATUS_LINES,
    TerminalDetails,
    inert,
    status_lines,
    tab_label,
)

OPT_OUT_VARIABLE = "SYNAPSE_TERMINAL_LABEL"
"""Environment variable; the value ``off`` disables the label and the status line."""

DETAILS_OPTION = "@synapse_label_details"
"""Session option holding the details as JSON."""

SAVED_OPTION = "@synapse_label_saved"
"""Session option holding the session's earlier option values as JSON."""

HOOKS = ("client-resized", "client-attached")
"""Session hooks that lay the status lines out again."""

DEFAULT_WIDTH = 80
"""Width used when the session has no client and reports no window width."""

MARGIN = 2
"""Columns left free at the right edge of a status line."""

_STYLE = "#[align=left]"
"""The only tmux format syntax this module emits by itself."""

_MANAGED = (
    "set-titles",
    "set-titles-string",
    "status",
    *(f"status-format[{index}]" for index in range(MAX_STATUS_LINES)),
)


class CommandRunner(Protocol):
    """Callable compatible with :func:`subprocess.run` for the calls made here."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return the completed process."""


@dataclass(frozen=True, slots=True)
class LabelResult:
    """What one operation did."""

    applied: bool
    detail: str
    label: str = ""
    lines: tuple[str, ...] = ()


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the operator has left the feature on."""
    env = os.environ if environ is None else environ
    return env.get(OPT_OUT_VARIABLE, "").strip().lower() != "off"


class _Tmux:
    """The tmux commands of one exact session."""

    def __init__(self, tmux_bin: str, session: str, runner: CommandRunner) -> None:
        self.tmux_bin = tmux_bin
        self.target = f"={session}:"
        self.runner = runner

    def run(self, *args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return self.runner([self.tmux_bin, *args], capture_output=True, text=True, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def ok(self, *args: str) -> bool:
        completed = self.run(*args)
        return completed is not None and completed.returncode == 0

    def exists(self) -> bool:
        return self.ok("has-session", "-t", self.target)

    def set_names(self) -> frozenset[str]:
        """Return the names of the options set on this session itself."""
        listing = self.run("show-options", "-t", self.target)
        if listing is None or listing.returncode != 0:
            return frozenset()
        return frozenset(line.split(None, 1)[0] for line in listing.stdout.splitlines() if line)

    def option(self, name: str) -> str | None:
        """Return a value set on this session, or None when it is not set here.

        tmux prints an empty line both for an empty value and for an array
        element that is not set, so the listing of set names decides.
        """
        if name not in self.set_names():
            return None
        completed = self.run("show-options", "-v", "-t", self.target, name)
        if completed is None or completed.returncode != 0:
            return None
        return completed.stdout.removesuffix("\n")

    def set(self, name: str, value: str) -> bool:
        return self.ok("set-option", "-t", self.target, name, value)

    def unset(self, name: str) -> bool:
        return self.ok("set-option", "-u", "-t", self.target, name)

    def width(self) -> int:
        """Return the width of the smallest attached client, else of the window."""
        clients = self.run("list-clients", "-t", self.target, "-F", "#{client_width}")
        widths = [
            int(value)
            for value in (clients.stdout.split() if clients is not None else [])
            if value.isdigit()
        ]
        if widths:
            return min(widths)
        window = self.run("display-message", "-p", "-t", self.target, "#{window_width}")
        value = window.stdout.strip() if window is not None else ""
        return int(value) if value.isdigit() else DEFAULT_WIDTH


def own_session(tmux_bin: str, *, runner: CommandRunner, environ: Mapping[str, str]) -> str | None:
    """Return the name of the tmux session this process runs in, or None outside tmux."""
    if not environ.get("TMUX"):
        return None
    try:
        completed = runner(
            [tmux_bin, "display-message", "-p", "#{session_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    name = completed.stdout.strip() if completed.returncode == 0 else ""
    return name or None


def stored_details(tmux_bin: str, session: str, *, runner: CommandRunner) -> TerminalDetails:
    """Return the details kept on the session, or defaults when there are none."""
    value = _Tmux(tmux_bin, session, runner).option(DETAILS_OPTION)
    return TerminalDetails.from_json(value) if value else TerminalDetails()


def apply(
    tmux_bin: str,
    session: str,
    details: TerminalDetails,
    *,
    runner: CommandRunner,
    refresh_command: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> LabelResult:
    """Set the tab label and the status lines of one exact session.

    Parameters
    ----------
    tmux_bin, session : str
        tmux executable and the exact session name.
    details : TerminalDetails
        What to show.
    runner : CommandRunner
        Command boundary for every tmux call.
    refresh_command : sequence of str
        Command the resize and attach hooks run to lay the lines out again,
        for example ``["synapse", "label", "--refresh", "--session", name]``.
    environ : mapping or None
        Environment read for the opt-out; the process environment by default.

    Returns
    -------
    LabelResult
        ``applied`` is false, with the reason, when the feature is off, the
        session does not exist, or tmux refused an option.
    """
    if not enabled(environ):
        return LabelResult(False, f"disabled by {OPT_OUT_VARIABLE}=off")
    tmux = _Tmux(tmux_bin, session, runner)
    if not tmux.exists():
        return LabelResult(False, f"no tmux session named {session!r}")
    if tmux.option(SAVED_OPTION) is None:
        saved = {name: tmux.option(name) for name in _MANAGED}
        if not tmux.set(SAVED_OPTION, json.dumps(saved, sort_keys=True)):
            return LabelResult(False, "could not save the session's earlier options")
    label = tab_label(details)
    lines = status_lines(details, tmux.width() - MARGIN)
    wanted = [
        (DETAILS_OPTION, details.to_json()),
        ("set-titles", "on"),
        ("set-titles-string", label),
        ("status", "on" if len(lines) == 1 else str(len(lines))),
        *((f"status-format[{index}]", _STYLE + line) for index, line in enumerate(lines)),
    ]
    for name, value in wanted:
        if not tmux.set(name, value):
            return LabelResult(False, f"tmux refused {name}", label, tuple(lines))
    for index in range(len(lines), MAX_STATUS_LINES):
        tmux.unset(f"status-format[{index}]")
    hook = "run-shell -b " + shlex.quote(inert(shlex.join(refresh_command)))
    for name in HOOKS:
        if not tmux.ok("set-hook", "-t", tmux.target, name, hook):
            return LabelResult(False, f"tmux refused the {name} hook", label, tuple(lines))
    return LabelResult(True, f"label and {len(lines)} status line(s) set", label, tuple(lines))


def restore(tmux_bin: str, session: str, *, runner: CommandRunner) -> LabelResult:
    """Put back the option values the session had before the first apply."""
    tmux = _Tmux(tmux_bin, session, runner)
    if not tmux.exists():
        return LabelResult(False, f"no tmux session named {session!r}")
    text = tmux.option(SAVED_OPTION)
    if text is None:
        return LabelResult(False, "nothing was saved for this session")
    try:
        saved = json.loads(text)
    except ValueError:
        saved = None
    if not isinstance(saved, dict):
        return LabelResult(False, "the saved options are unreadable; nothing was changed")
    for name in _MANAGED:
        value = saved.get(name)
        done = tmux.set(name, value) if isinstance(value, str) else tmux.unset(name)
        if not done:
            return LabelResult(False, f"tmux refused to restore {name}")
    if not any(isinstance(saved.get(name), str) for name in _MANAGED if "[" in name):
        # Unsetting the last element leaves an empty array on the session.
        tmux.unset("status-format")
    for name in HOOKS:
        tmux.ok("set-hook", "-u", "-t", tmux.target, name)
    tmux.unset(DETAILS_OPTION)
    tmux.unset(SAVED_OPTION)
    return LabelResult(True, "the session's earlier label and status were restored")
