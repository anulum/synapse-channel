# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for applying the label and status lines to a tmux session
"""Label and status lines on a real tmux server the test owns.

``tmux_bin`` is a wrapper that runs ``tmux -L synapse-test-<id> -f /dev/null``:
a private socket and no user configuration. The user's tmux server is never
asked or changed. Where a client is needed, tmux is attached to a
pseudo-terminal the test opens.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.cli import main as cli_main
from synapse_channel.terminal_label import NOT_DECLARED, TerminalDetails
from synapse_channel.terminal_label_tmux import (
    SAVED_OPTION,
    LabelResult,
    apply,
    enabled,
    own_session,
    restore,
    stored_details,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("tmux") is None,
    reason="needs tmux and a pseudo-terminal on Linux",
)

_GROUP = "AGENTIC-COORDINATION-ASSURANCE-AND-CONTINUITY-SYSTEMS"
_DETAILS = TerminalDetails(
    group=_GROUP,
    abbreviation="ACAACS",
    project="SYNAPSE-CHANNEL",
    focus="terminal labels",
    vendor="claude",
    model="claude-opus-5-5",
    seat="SYNAPSE-CHANNEL/claude-f636",
    session_name="anulum-ff",
    native_session="d2b65cca-a4a8-481d-b1bb-9e1348d67e0e",
    terminal_identity="user/terminal-1485372",
)
_NO_REFRESH = ["true"]


class Server:
    """A private tmux server and the clients one test attaches to it."""

    def __init__(self, workdir: Path) -> None:
        self.socket = f"synapse-test-{uuid.uuid4().hex[:10]}"
        self.workdir = workdir
        wrapper = workdir / "tmux-private"
        wrapper.write_text(
            f'#!/bin/sh\nexec tmux -L {self.socket} -f /dev/null "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
        self.tmux_bin = str(wrapper)
        self.clients: list[tuple[subprocess.Popen[bytes], int]] = []

    def tmux(self, *args: str) -> str:
        done = subprocess.run(
            [self.tmux_bin, *args], cwd=self.workdir, capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, (args, done.stderr)
        return done.stdout

    def session(self, name: str, width: int = 125) -> str:
        self.tmux("new-session", "-d", "-s", name, "-x", str(width), "-y", "40", "sleep", "600")
        return name

    def options(self, name: str) -> str:
        return self.tmux("show-options", "-t", f"={name}:")

    def option(self, name: str, option: str) -> str:
        return self.tmux("show-options", "-v", "-t", f"={name}:", option).rstrip("\n")

    def attach(self, name: str, width: int) -> int:
        """Attach a real client on a pseudo-terminal of ``width`` columns."""
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, width, 0, 0))
        environment = {k: v for k, v in os.environ.items() if k != "TMUX"}
        environment["TERM"] = "xterm-256color"
        client = subprocess.Popen(
            [self.tmux_bin, "attach-session", "-t", f"={name}:"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
            start_new_session=True,
        )
        os.close(slave)
        threading.Thread(target=self._drain, args=(master,), daemon=True).start()
        self.clients.append((client, master))
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self.tmux("list-clients", "-t", f"={name}:", "-F", "#{client_width}").strip():
                return master
            time.sleep(0.1)
        raise AssertionError("the client did not attach")

    @staticmethod
    def _drain(master: int) -> None:
        try:
            while os.read(master, 65536):
                pass
        except OSError:
            return

    def resize(self, master: int, width: int) -> None:
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, width, 0, 0))
        for client, descriptor in self.clients:
            if descriptor == master:
                os.kill(client.pid, signal.SIGWINCH)

    def close(self) -> None:
        subprocess.run([self.tmux_bin, "kill-server"], capture_output=True, check=False)
        for client, master in self.clients:
            client.wait(timeout=10)
            os.close(master)
        socket_dir = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"  # nosec B108
        (socket_dir / self.socket).unlink(missing_ok=True)


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    owned = Server(tmp_path)
    try:
        yield owned
    finally:
        owned.close()


def _apply(server: Server, name: str, details: TerminalDetails = _DETAILS) -> LabelResult:
    return apply(server.tmux_bin, name, details, runner=subprocess.run, refresh_command=_NO_REFRESH)


def test_apply_sets_only_the_named_session_and_no_global_option(server: Server) -> None:
    mine = server.session("work")
    other = server.session("work-two")
    before_other = server.options(other)
    before_global = server.tmux("show-options", "-g")

    result = _apply(server, mine)

    assert result.applied and result.label == "ACAACS · SYNAPSE-CHANNEL · claude"
    assert result.detail == "label and 3 status line(s) set"
    assert server.option(mine, "set-titles") == "on"
    assert server.option(mine, "set-titles-string") == "ACAACS · SYNAPSE-CHANNEL · claude"
    assert server.option(mine, "status") == "3"
    assert [server.option(mine, f"status-format[{i}]") for i in range(3)] == [
        "#[align=left]" + line for line in result.lines
    ]
    assert stored_details(server.tmux_bin, mine, runner=subprocess.run) == _DETAILS
    assert server.options(other) == before_other
    assert server.tmux("show-options", "-g") == before_global
    assert stored_details(server.tmux_bin, other, runner=subprocess.run) == TerminalDetails()


@pytest.mark.parametrize(("width", "lines"), [(60, 5), (125, 3), (240, 2)])
def test_the_window_width_decides_the_number_of_status_lines(
    server: Server, width: int, lines: int
) -> None:
    name = server.session("work", width)

    result = _apply(server, name)

    assert len(result.lines) == lines
    assert server.option(name, "status") == str(lines)
    assert all(len(line) <= width - 2 for line in result.lines)
    assert server.option(name, f"status-format[{lines - 1}]") != ""
    assert f"\nstatus-format[{lines}] " not in server.options(name)


def test_one_line_turns_the_status_on_and_clears_lines_left_from_before(server: Server) -> None:
    name = server.session("work", 240)
    _apply(server, name)
    assert server.option(name, "status") == "2"

    result = _apply(server, name, TerminalDetails(seat="s"))

    assert len(result.lines) == 1 and server.option(name, "status") == "on"
    assert "\nstatus-format[1] " not in server.options(name)


def test_a_name_that_is_only_a_prefix_of_a_session_reaches_no_session(server: Server) -> None:
    longer = server.session("work-two")
    before = server.options(longer)

    result = _apply(server, "work")

    assert not result.applied and result.detail == "no tmux session named 'work'"
    assert server.options(longer) == before
    assert not restore(server.tmux_bin, "work", runner=subprocess.run).applied


def test_hostile_text_is_displayed_and_never_run(server: Server) -> None:
    marker = server.workdir / "ran"
    hostile = "#(touch ran) #[fg=red]red \x1b]0;evil\x07 #{session_name}"
    name = server.session("work", 200)
    details = replace(_DETAILS, focus=hostile, seat=hostile, vendor=hostile)

    result = _apply(server, name, details)
    server.attach(name, 200)
    time.sleep(1.5)

    assert result.applied and not marker.exists()
    stored = server.option(name, "status-format[0]") + server.option(name, "status-format[1]")
    assert "##(touch ran)" in stored and "##[fg=red]red" in stored
    assert "\x1b" not in stored and "\x07" not in stored
    assert "##{session_name}" in stored
    assert server.option(name, "set-titles-string").startswith(
        "ACAACS · SYNAPSE-CHANNEL · ##(touch "
    )
    # What tmux draws: the text itself, with nothing expanded.
    drawn = "".join(
        server.tmux("display-message", "-p", "-t", f"={name}:", f"#{{E:status-format[{index}]}}")
        for index in range(len(result.lines))
    )
    assert "#(touch ran)" in drawn and "#{session_name}" in drawn
    assert not marker.exists()


def test_restore_puts_back_exactly_what_the_session_had(server: Server) -> None:
    name = server.session("work")
    server.tmux("set-option", "-t", f"={name}:", "status-format[0]", "my own line")
    server.tmux("set-option", "-t", f"={name}:", "set-titles-string", "mine")
    before = server.options(name)

    _apply(server, name)
    saved = json.loads(server.option(name, SAVED_OPTION))
    _apply(server, name, replace(_DETAILS, focus="second"))

    # A second apply keeps the first snapshot; it does not save the labelled state.
    assert json.loads(server.option(name, SAVED_OPTION)) == saved
    assert saved["status-format[0]"] == "my own line" and saved["status"] is None
    assert server.tmux("show-hooks", "-t", f"={name}:").count("run-shell") == 2

    result = restore(server.tmux_bin, name, runner=subprocess.run)

    assert result.applied
    assert server.options(name) == before
    assert server.tmux("show-hooks", "-t", f"={name}:").strip() == ""
    again = restore(server.tmux_bin, name, runner=subprocess.run)
    assert not again.applied and again.detail == "nothing was saved for this session"


def test_unreadable_saved_options_change_nothing(server: Server) -> None:
    name = server.session("work")
    for text in ("{", "[]"):
        server.tmux("set-option", "-t", f"={name}:", SAVED_OPTION, text)
        before = server.options(name)

        result = restore(server.tmux_bin, name, runner=subprocess.run)

        assert not result.applied and "unreadable" in result.detail
        assert server.options(name) == before


def test_a_resize_of_the_client_lays_the_lines_out_again(server: Server) -> None:
    name = server.session("work it's", 125)
    master = server.attach(name, 125)
    refresh = [
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "label",
        "--refresh",
        f"--session={name}",
        f"--tmux-bin={server.tmux_bin}",
    ]
    result = apply(server.tmux_bin, name, _DETAILS, runner=subprocess.run, refresh_command=refresh)
    assert result.applied and server.option(name, "status") == "3"

    server.resize(master, 60)

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and server.option(name, "status") != "5":
        time.sleep(0.2)
    assert server.option(name, "status") == "5"
    assert stored_details(server.tmux_bin, name, runner=subprocess.run) == _DETAILS


def test_the_hook_passes_format_characters_on_without_expanding_them(
    server: Server, tmp_path: Path
) -> None:
    # tmux expands formats in the argument of run-shell. A session name or a
    # path with a format in it must reach the refresh command as it is.
    name = server.session("lab", 125)
    master = server.attach(name, 125)
    record = tmp_path / "argv.json"
    hostile = "#{session_name} #(touch %s)" % (tmp_path / "ran")
    script = "import json, sys; json.dump(sys.argv[1:], open(sys.argv[1], 'w'))"
    refresh = [sys.executable, "-c", script, str(record), hostile, "it's"]
    assert apply(
        server.tmux_bin, name, _DETAILS, runner=subprocess.run, refresh_command=refresh
    ).applied

    server.resize(master, 90)

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and not record.exists():
        time.sleep(0.2)
    time.sleep(0.5)
    assert json.loads(record.read_text(encoding="utf-8")) == [str(record), hostile, "it's"]
    assert not (tmp_path / "ran").exists()


def test_the_smallest_attached_client_decides_the_width(server: Server) -> None:
    name = server.session("work", 240)
    server.attach(name, 240)
    server.attach(name, 70)

    result = _apply(server, name)

    assert all(len(line) <= 68 for line in result.lines) and len(result.lines) == 5


def test_the_opt_out_leaves_the_session_alone(server: Server) -> None:
    name = server.session("work")
    before = server.options(name)

    result = apply(
        server.tmux_bin,
        name,
        _DETAILS,
        runner=subprocess.run,
        refresh_command=_NO_REFRESH,
        environ={"SYNAPSE_TERMINAL_LABEL": " OFF "},
    )

    assert not result.applied and result.detail == "disabled by SYNAPSE_TERMINAL_LABEL=off"
    assert server.options(name) == before
    assert enabled({}) and enabled({"SYNAPSE_TERMINAL_LABEL": "on"})


def test_the_own_session_is_read_only_inside_tmux(server: Server) -> None:
    name = server.session("work")

    assert own_session(server.tmux_bin, runner=subprocess.run, environ={}) is None
    assert own_session(server.tmux_bin, runner=subprocess.run, environ={"TMUX": "x"}) == name
    assert own_session("/nonexistent/tmux", runner=subprocess.run, environ={"TMUX": "x"}) is None


def test_a_tmux_that_cannot_run_applies_nothing(tmp_path: Path) -> None:
    result = apply(
        str(tmp_path / "no-tmux"),
        "work",
        _DETAILS,
        runner=subprocess.run,
        refresh_command=_NO_REFRESH,
    )

    assert not result.applied and result.detail == "no tmux session named 'work'"
    assert stored_details(str(tmp_path / "no-tmux"), "work", runner=subprocess.run) == (
        TerminalDetails()
    )


def test_the_command_labels_shows_adds_details_and_restores(
    server: Server,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = server.session("work")
    before = server.options(name)
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {"groups": {_GROUP: {"abbreviation": "ACAACS", "projects": ["SYNAPSE-CHANNEL"]}}}
        ),
        encoding="utf-8",
    )
    base = ["label", f"--session={name}", f"--tmux-bin={server.tmux_bin}", f"--profile={profile}"]

    assert (
        cli_main([*base, "--identity=SYNAPSE-CHANNEL/claude-f636-rx", "--focus=terminal labels"])
        == 0
    )
    assert capsys.readouterr().out == "label and 3 status line(s) set\n"
    assert server.option(name, "set-titles-string") == "ACAACS · SYNAPSE-CHANNEL"

    # A later call adds a detail and keeps what was declared before; the
    # ambient identity does not replace the stored seat.
    monkeypatch.setenv("SYN_IDENTITY", "user/terminal-1")
    assert cli_main([*base, "--vendor=claude", "--model= claude-opus-5-5 "]) == 0
    capsys.readouterr()
    kept = stored_details(server.tmux_bin, name, runner=subprocess.run)
    assert (kept.focus, kept.vendor, kept.model) == ("terminal labels", "claude", "claude-opus-5-5")
    assert kept.seat == "SYNAPSE-CHANNEL/claude-f636" and kept.session_name == NOT_DECLARED
    assert server.option(name, "set-titles-string") == "ACAACS · SYNAPSE-CHANNEL · claude"

    options = server.options(name)
    assert cli_main([*base, "--show", "--native-session=abc"]) == 0
    shown = capsys.readouterr().out.splitlines()
    assert shown[0] == "tab label: ACAACS · SYNAPSE-CHANNEL · claude"
    assert shown[1] == f"group: {_GROUP} (ACAACS)" and "native session: abc" in shown
    assert server.options(name) == options

    assert cli_main([*base, "--refresh", "--identity=OTHER/seat"]) == 0
    capsys.readouterr()
    assert stored_details(server.tmux_bin, name, runner=subprocess.run) == kept

    assert cli_main([*base, "--restore"]) == 0
    assert "restored" in capsys.readouterr().out
    assert server.options(name) == before


def test_the_command_is_silent_outside_tmux_and_reports_what_it_cannot_do(
    server: Server,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("SYN_IDENTITY", "P/seat")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-profile"))

    assert cli_main(["label", f"--tmux-bin={server.tmux_bin}", "--focus=f"]) == 0
    assert capsys.readouterr().out == ""
    assert cli_main(["label", f"--tmux-bin={server.tmux_bin}", "--show"]) == 0
    assert capsys.readouterr().out.splitlines()[:2] == ["tab label: P", "group: not declared"]

    # Inside a session with no seat stored, the ambient identity is used.
    ambient = server.session("ambient")
    assert cli_main(["label", f"--session={ambient}", f"--tmux-bin={server.tmux_bin}"]) == 0
    capsys.readouterr()
    assert stored_details(server.tmux_bin, ambient, runner=subprocess.run).seat == "P/seat"

    assert cli_main(["label", "--session=absent", f"--tmux-bin={server.tmux_bin}"]) == 1
    assert capsys.readouterr().out == "no tmux session named 'absent'\n"

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert cli_main(["label", "--show", f"--profile={broken}"]) == 2
    assert capsys.readouterr().out.startswith("synapse label: ")

    name = server.session("work")
    monkeypatch.setenv("SYNAPSE_TERMINAL_LABEL", "off")
    assert cli_main(["label", f"--session={name}", f"--tmux-bin={server.tmux_bin}"]) == 0
    assert capsys.readouterr().out == "disabled by SYNAPSE_TERMINAL_LABEL=off\n"


def _refusing(server: Server, refused: str) -> str:
    """Return a tmux wrapper that fails any command with ``refused`` as an argument."""
    wrapper = server.workdir / "tmux-refusing"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'for argument in "$@"; do [ "$argument" = "{refused}" ] && exit 1; done\n'
        f'exec {server.tmux_bin} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


@pytest.mark.parametrize(
    ("refused", "detail"),
    [
        ("@synapse_label_saved", "could not save the session's earlier options"),
        ("set-titles-string", "tmux refused set-titles-string"),
        ("client-attached", "tmux refused the client-attached hook"),
    ],
)
def test_a_refused_tmux_command_is_reported_and_not_called_success(
    server: Server, refused: str, detail: str
) -> None:
    name = server.session("work")

    result = apply(
        _refusing(server, refused),
        name,
        _DETAILS,
        runner=subprocess.run,
        refresh_command=_NO_REFRESH,
    )

    assert not result.applied and result.detail == detail


def test_a_refused_restore_is_reported(server: Server) -> None:
    name = server.session("work")
    server.tmux("set-option", "-t", f"={name}:", "set-titles-string", "mine")
    _apply(server, name)

    result = restore(_refusing(server, "set-titles-string"), name, runner=subprocess.run)

    assert not result.applied and result.detail == "tmux refused to restore set-titles-string"
    assert restore(_refusing(server, "show-options"), name, runner=subprocess.run).detail == (
        "nothing was saved for this session"
    )
    assert stored_details(_refusing(server, "-v"), name, runner=subprocess.run) == (
        TerminalDetails()
    )
