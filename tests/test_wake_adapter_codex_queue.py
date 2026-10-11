# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the native-queue wake adapter
"""The native-queue adapter against a program that stands in for the client.

**This is a stand-in.** The vendor's client and its daemon are not run here.
The program in the test directory is started as a real child process and
answers the way the client was observed to answer in the qualification of
2026-10-04 (queued line with exit status 0; exit status 1 with the daemon's
refusal for an unknown session), plus the cases the adapter must survive:
no answer, a foreign answer, an unknown failure. Every start appends the
arguments it received to a file; "one wake" is one line in that file.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
import time
from pathlib import Path

import pytest

from synapse_channel.agent_tmux import build_wake_prompt
from synapse_channel.wake_adapters import ACCEPTED, REFUSED, UNCERTAIN, WakeTarget
from synapse_channel.wake_adapters.codex_queue import (
    KIND,
    PROVES,
    CodexQueueAdapter,
    canonical_session,
)
from synapse_channel.wake_relay import relay

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the stand-in is a POSIX script")

SEAT = "PROJECT/codex-aaaa"
SESSION = "01a0fcac-3407-71c3-9081-0bff345ef6c2"
HOST = "this-host"
TARGET = WakeTarget(SEAT, SESSION, HOST)

_PROGRAM = """#!{python}
import json, os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "mode")).read().strip()
with open(os.path.join(here, "calls"), "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + chr(10))
thread = sys.argv[sys.argv.index("--thread") + 1]
if mode == "queue":
    print("Queued message 01a10909-405b-7471-86b2-3a44cd8bf3bc for thread " + thread)
elif mode == "refuse":
    sys.stderr.write("thread/queue/add failed: no rollout found, code -32603" + chr(10))
    sys.exit(1)
elif mode == "other-thread":
    print("Queued message 01a10909 for thread 00000000-0000-4000-8000-000000000009")
elif mode == "silent":
    pass
elif mode == "fail":
    sys.stderr.write("connection reset" + chr(10))
    sys.exit(3)
elif mode == "escape":
    # A descendant in a session of its own keeps the output open after the group is killed.
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    open(os.path.join(here, "grandchild"), "w").write(str(child.pid))
    time.sleep(120)
elif mode == "root-exits":
    # The command ends at once; a process it started keeps the output open.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(os.path.join(here, "grandchild"), "w").write(str(child.pid))
    sys.exit(0)
elif mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    open(os.path.join(here, "grandchild"), "w").write(str(child.pid))
    time.sleep(120)
"""


class Client:
    """The stand-in program, its mode and the starts it recorded."""

    def __init__(self, tmp_path: Path) -> None:
        self.directory = tmp_path / "client"
        self.directory.mkdir()
        self.program = self.directory / "codex"
        self.program.write_text(_PROGRAM.format(python=sys.executable), encoding="utf-8")
        self.program.chmod(0o700)
        self.mode("queue")

    def mode(self, name: str) -> None:
        (self.directory / "mode").write_text(name, encoding="utf-8")

    def calls(self) -> list[list[str]]:
        path = self.directory / "calls"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def client(tmp_path: Path) -> Client:
    return Client(tmp_path)


def _adapter(client: Client) -> CodexQueueAdapter:
    return CodexQueueAdapter(client.program, host=HOST)


def test_a_queued_message_is_accepted_and_the_command_is_exactly_the_documented_one(
    tmp_path: Path, client: Client
) -> None:
    adapter = _adapter(client)
    marks = tmp_path / "marks"

    assert adapter.kind == KIND == "native_queue" and adapter.available(TARGET).available
    first = relay(marks, TARGET, 900, [adapter])
    second = relay(marks, TARGET, 900, [adapter])

    assert (first.state, first.wake_path, first.proves) == (ACCEPTED, KIND, PROVES)
    assert first.attempts[0].detail == "queued message 01a10909-405b-7471-86b2-3a44cd8bf3bc"
    assert not second.sent_now
    assert client.calls() == [["queue", "--thread", SESSION, "--message", build_wake_prompt(SEAT)]]


@pytest.mark.parametrize(
    ("mode", "state", "detail"),
    [
        ("refuse", REFUSED, "the daemon refused (exit status 1)"),
        ("other-thread", UNCERTAIN, "exit status 0 without the queued line for the session"),
        ("silent", UNCERTAIN, "exit status 0 without the queued line for the session"),
        ("fail", UNCERTAIN, "exit status 3 with an unknown error text"),
    ],
)
def test_only_the_daemons_known_refusal_is_a_refusal(
    client: Client, mode: str, state: str, detail: str
) -> None:
    client.mode(mode)

    outcome = _adapter(client).wake(TARGET, 1, "fixed prompt", 20.0)

    assert (outcome.state, outcome.proves, outcome.detail) == (state, "", detail)
    assert len(client.calls()) == 1


def test_no_answer_within_the_limit_is_uncertain_and_nothing_is_left_running(
    client: Client,
) -> None:
    client.mode("hang")
    started = time.monotonic()

    outcome = _adapter(client).wake(TARGET, 1, "fixed prompt", 1.0)

    assert (outcome.state, outcome.proves) == (UNCERTAIN, "")
    assert outcome.detail == "no answer within the time limit; it may be queued"
    assert time.monotonic() - started < 10
    grandchild = int((client.directory / "grandchild").read_text(encoding="utf-8"))
    for _ in range(200):
        if not Path(f"/proc/{grandchild}").exists():
            break
        time.sleep(0.02)
    assert not Path(f"/proc/{grandchild}").exists()


def test_the_prompt_and_the_session_are_arguments_and_never_shell_text(
    tmp_path: Path, client: Client
) -> None:
    marker = tmp_path / "marker"
    hostile = (
        f"wake $(touch {marker}); `touch {marker}` && touch {marker} | 'x' \"y\"\n--thread other"
    )

    outcome = _adapter(client).wake(TARGET, 1, hostile, 20.0)

    assert outcome.state == ACCEPTED and not marker.exists()
    assert client.calls() == [["queue", "--thread", SESSION, "--message", hostile]]
    for session in (f"{SESSION}; touch {marker}", "night-shift", "", SESSION.upper(), SESSION[:-1]):
        target = WakeTarget(SEAT, session, HOST)
        refused = _adapter(client).wake(target, 1, "fixed prompt", 20.0)
        assert not _adapter(client).available(target).available
        assert (refused.state, refused.detail) == (
            REFUSED,
            "not started: the session identifier is not a UUID",
        )
    assert len(client.calls()) == 1 and not marker.exists()
    assert canonical_session(SESSION) == SESSION and canonical_session(SESSION.upper()) is None


def test_another_host_and_a_program_that_is_not_at_its_place(
    tmp_path: Path, client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter(client)
    elsewhere = adapter.available(WakeTarget(SEAT, SESSION, "other-host"))
    assert (elsewhere.available, elsewhere.detail) == (False, "the session runs on another host")
    assert CodexQueueAdapter(client.program).available(TARGET).available is False

    reason = "the client's executable is not at its fixed path"
    relative = CodexQueueAdapter(Path("codex"), host=HOST)
    absent = CodexQueueAdapter(tmp_path / "absent" / "codex", host=HOST)
    assert relative.available(TARGET).detail == reason and absent.available(TARGET).detail == reason
    # A relative path is refused even when it names the program from here.
    monkeypatch.chdir(client.directory)
    assert Path("codex").is_file() and relative.available(TARGET).detail == reason
    client.program.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert adapter.available(TARGET).detail == reason
    not_started = adapter.wake(TARGET, 1, "fixed prompt", 20.0)
    assert (not_started.state, not_started.detail) == (REFUSED, "not started: PermissionError")
    assert client.calls() == []


def test_a_descendant_that_escaped_cannot_hold_the_adapter_past_its_bounds(
    client: Client,
) -> None:
    client.mode("escape")
    adapter = CodexQueueAdapter(client.program, host=HOST, end_wait_seconds=1.0)
    started = time.monotonic()

    outcome = adapter.wake(TARGET, 1, "fixed prompt", 1.0)

    elapsed = time.monotonic() - started
    grandchild = int((client.directory / "grandchild").read_text(encoding="utf-8"))
    try:
        assert (outcome.state, outcome.proves) == (UNCERTAIN, "")
        assert outcome.detail == "no answer within the time limit; it may be queued"
        # The time limit, then the bounded wait for output, and no more.
        assert 2.0 <= elapsed < 8.0
    finally:
        os.kill(grandchild, signal.SIGKILL)


def test_what_the_command_started_is_ended_also_when_the_command_itself_has_exited(
    client: Client,
) -> None:
    client.mode("root-exits")
    adapter = CodexQueueAdapter(client.program, host=HOST, end_wait_seconds=2.0)
    started = time.monotonic()

    outcome = adapter.wake(TARGET, 1, "fixed prompt", 1.5)

    elapsed = time.monotonic() - started
    grandchild = int((client.directory / "grandchild").read_text(encoding="utf-8"))
    for _ in range(200):
        if not Path(f"/proc/{grandchild}").exists():
            break
        time.sleep(0.02)
    assert (outcome.state, outcome.detail) == (
        UNCERTAIN,
        "no answer within the time limit; it may be queued",
    )
    assert not Path(f"/proc/{grandchild}").exists()
    assert elapsed < 6.0
