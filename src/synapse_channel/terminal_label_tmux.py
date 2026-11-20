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
import subprocess  # nosec B404 # fixed argument vectors only, never a shell
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from synapse_channel.terminal_label import (
    MAX_STATUS_LINES,
    NOT_DECLARED,
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

TITLE_OWNER_OPTION = "@synapse_label_title_owner"
"""Who set the tab title: the seat, ``synapse``, or ``operator``."""

TITLE_WRITTEN_OPTION = "@synapse_label_title_written"
"""The exact title this command last wrote."""

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


def title_writes(
    *,
    keep: bool,
    owner: str | None,
    current: str | None,
    written: str | None,
    label: str,
    seat: str,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Return the title to report and the title options a call should write.

    A refresh that finds a hand-set title writes only the operator marker.
    Any other call writes the label and records the title it wrote.
    """
    if hand_owns_title(keep=keep, owner=owner, current=current, written=written):
        shown = current or label
        if owner == "operator":
            return shown, ()
        return shown, ((TITLE_OWNER_OPTION, "operator"),)
    setter = seat if seat != NOT_DECLARED else "synapse"
    return label, (
        ("set-titles-string", label),
        (TITLE_OWNER_OPTION, setter),
        (TITLE_WRITTEN_OPTION, label),
    )


def hand_owns_title(
    *, keep: bool, owner: str | None, current: str | None, written: str | None
) -> bool:
    """Return whether a refresh must leave the tab title as it stands.

    The title is hand-set when it differs from the title this command last
    wrote, or when the owner marker already says ``operator``. A session
    labelled by an older release has no written title and is not treated as
    hand-set. An explicit request always rewrites.
    """
    if not keep:
        return False
    if owner == "operator":
        return True
    if not written:
        return False
    return bool(current) and current != written


def label_hook_log(environ: Mapping[str, str]) -> Path:
    """Return the file that keeps errors from a label refresh hook."""
    state = environ.get("XDG_STATE_HOME", "").strip()
    if not state:
        home = environ.get("HOME", "").strip() or str(Path.home())
        state = str(Path(home) / ".local" / "state")
    return Path(state) / "synapse" / "label-hook.log"


_LOG_LIMIT = 256 * 1024
"""Rotate the label hook log once it passes this many bytes."""


def refresh_script(refresh_command: Sequence[str], log: Path) -> str:
    """Return the silenced shell script a label hook runs.

    The whole group discards the shell's own errors, including a redirect
    that cannot create the log. The command's errors are appended to the log.
    A log over 256 KiB is moved to ``label-hook.log.1`` first. The script
    exits 0: tmux opens view mode when a hook command exits non-zero, even
    when that command printed nothing.
    """
    command = shlex.join(refresh_command)
    directory = shlex.quote(str(log.parent))
    target = shlex.quote(str(log))
    previous = shlex.quote(str(log.with_name(f"{log.name}.1")))
    rotate = (
        f'[ ! -f {target} ] || [ "$(wc -c < {target})" -le {_LOG_LIMIT} ]'
        f" || mv -f {target} {previous}"
    )
    group = f"{{ mkdir -p {directory}; {rotate}; {command} >/dev/null 2>>{target}; }}"
    return f"{group} >/dev/null 2>&1 || true"


def _write_hook_script(log: Path, body: str) -> Path:
    """Store the refresh script where ``sh`` can run it.

    When the log directory cannot be created, the script is kept in a
    temporary file so the hook text itself stays a short ``sh`` command.
    """
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        path = log.with_name("label-refresh.sh")
        path.write_text(body, encoding="utf-8")
    except OSError:
        descriptor, name = tempfile.mkstemp(prefix="synapse-label-", suffix=".sh")
        os.write(descriptor, body.encode())
        os.close(descriptor)
        path = Path(name)
    path.chmod(0o700)
    return path


def refresh_hook(refresh_command: Sequence[str], log: Path) -> str:
    """Return a background tmux hook that prints nothing.

    The script runs under ``sh``. A failed redirect stays inside the silenced
    group, including when tmux's own shell is not a POSIX shell.
    """
    script = _write_hook_script(log, refresh_script(refresh_command, log) + "\n")
    return "run-shell -b " + shlex.quote(inert("sh " + shlex.quote(str(script))))


def _resolved_hook_log(hook_log: Path | None, environ: Mapping[str, str] | None) -> Path:
    """Return the caller's log, or the log for the environment."""
    if hook_log is not None:
        return hook_log
    return label_hook_log(os.environ if environ is None else environ)


def apply(
    tmux_bin: str,
    session: str,
    details: TerminalDetails,
    *,
    runner: CommandRunner,
    refresh_command: Sequence[str],
    environ: Mapping[str, str] | None = None,
    keep_hand_title: bool = False,
    hook_log: Path | None = None,
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
    keep_hand_title : bool, optional
        When true, a title this command did not write is left in place and
        ``@synapse_label_title_owner`` records ``operator``.
    hook_log : path or None, optional
        File that receives the refresh hook's errors. The default is the
        label hook log under the state directory.

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
    shown, title_options = title_writes(
        keep=keep_hand_title,
        owner=tmux.option(TITLE_OWNER_OPTION),
        current=tmux.option("set-titles-string"),
        written=tmux.option(TITLE_WRITTEN_OPTION),
        label=label,
        seat=details.seat,
    )
    wanted = [
        (DETAILS_OPTION, details.to_json()),
        ("set-titles", "on"),
        *title_options,
        ("status", "on" if len(lines) == 1 else str(len(lines))),
        *((f"status-format[{index}]", _STYLE + line) for index, line in enumerate(lines)),
    ]
    for name, value in wanted:
        if not tmux.set(name, value):
            return LabelResult(False, f"tmux refused {name}", shown, tuple(lines))
    for index in range(len(lines), MAX_STATUS_LINES):
        tmux.unset(f"status-format[{index}]")
    # tmux shows what a ``run-shell -b`` command prints in view mode over the
    # session's active pane. Success text is discarded. Errors go to the log.
    hook = refresh_hook(refresh_command, _resolved_hook_log(hook_log, environ))
    for name in HOOKS:
        if not tmux.ok("set-hook", "-t", tmux.target, name, hook):
            return LabelResult(False, f"tmux refused the {name} hook", shown, tuple(lines))
    return LabelResult(True, f"label and {len(lines)} status line(s) set", shown, tuple(lines))


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
    tmux.unset(TITLE_OWNER_OPTION)
    tmux.unset(TITLE_WRITTEN_OPTION)
    tmux.unset(SAVED_OPTION)
    return LabelResult(True, "the session's earlier label and status were restored")
