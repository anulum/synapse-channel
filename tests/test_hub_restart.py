# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the one-job hub restart on real user units
"""The hub restart on the real systemd user manager, with units the test owns.

Every test starts its own hub as a transient user unit on a free port, with its
own dependents, and removes them afterwards. When that hub's own error says the
bind lost the port, the start takes a fresh port and tries again, and it stops
on any other failure. No installed unit is read for state, started, stopped or
restarted: every call names the test's hub unit.
The test hub keeps its event store and its identity pins in the test's own
directory; ``synapse hub`` would otherwise default the pin file to the one the
installed hub uses.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import pytest

from hub_e2e_helpers import free_port
from synapse_channel.cli import main as cli_main
from synapse_channel.cli_service import PROBE_NAME, hub_answers
from synapse_channel.hub_restart import (
    HubRestartReport,
    default_lock_path,
    plan_hub_restart,
    render_plan,
    restart_hub,
)
from synapse_channel.service_dependencies import ServiceQueryError, unit_states
from synapse_channel.waker_commands import run_waker_command

pytestmark = [
    pytest.mark.real_hub,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="the hub restart needs Linux with systemd"
    ),
]

_RUNNER = partial(run_waker_command, timeout=30.0)
_BIND_ATTEMPTS = 5


def _lost_bind(detail: str) -> bool:
    """Return whether the hub's own error says this bind lost its port."""
    return os.strerror(errno.EADDRINUSE).casefold() in detail.casefold()


def _replacement_port(detail: str, attempt: int) -> int | None:
    """Return a fresh port only when this attempt lost its bind and another remains."""
    if attempt >= _BIND_ATTEMPTS or not _lost_bind(detail):
        return None
    return free_port()


def _user_manager_available() -> bool:
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return False
    completed = subprocess.run(
        ["systemctl", "--user", "show", "--property=Version", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _require_user_manager() -> None:
    """Skip when this host has no systemd user manager for the test units."""
    if not _user_manager_available():
        pytest.skip("no systemd user manager answers here")


def _unit_has_stopped(unit: str) -> bool:
    """Return whether the test's transient unit has already exited."""
    completed = subprocess.run(
        ["systemctl", "--user", "show", unit, "--property=ActiveState", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() in {"failed", "inactive"}


def _tcp_accepts(port: int) -> bool:
    """Return whether a loopback client can connect to ``port``."""
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


@contextlib.contextmanager
def _held_listener(port: int) -> Iterator[None]:
    """Hold an IPv4 listener on ``port``, as another process on this host can."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen()
        yield


_CLOSING_HUB_SCRIPT = (
    "import asyncio, json, sys\n"
    "from websockets.asyncio.server import serve\n"
    "async def handler(connection):\n"
    "    await connection.send(json.dumps({'type': 'welcome', 'hub_id': 'closing'}))\n"
    "    await connection.close(4013, 'identity pin mismatch')\n"
    "async def main():\n"
    "    async with serve(handler, '127.0.0.1', int(sys.argv[1])):\n"
    "        print('listening', flush=True)\n"
    "        await asyncio.Future()\n"
    "asyncio.run(main())\n"
)


def _finish_script(server: subprocess.Popen[str]) -> str:
    """Stop a script, close its pipes, and return the error it wrote."""
    if server.poll() is None:
        server.terminate()
    try:
        _, detail = server.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        _, detail = server.communicate(timeout=10)
    return detail or ""


def _stop_script(server: subprocess.Popen[str]) -> None:
    """Stop a script and close the pipe that carries its own error."""
    _finish_script(server)


def _child_error(server: subprocess.Popen[str]) -> str:
    """Return the error a script wrote, stopping it first when it is still running."""
    return _finish_script(server)


def _script_is_listening(server: subprocess.Popen[str], timeout: float) -> bool:
    """Return whether the script reported that its own bind is listening."""
    stream = server.stdout
    assert stream is not None
    readable, _, _ = select.select([stream], [], [], timeout)
    if not readable:
        return False
    return stream.readline().startswith("listening") and server.poll() is None


def _closing_server(script: Path) -> tuple[subprocess.Popen[str], int]:
    """Start the welcome-then-close script, moving on only after its bind loses the port."""
    port = free_port()
    for attempt in range(1, _BIND_ATTEMPTS + 1):
        server = subprocess.Popen(
            [sys.executable, str(script), str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if _script_is_listening(server, 20.0):
            return server, port
        detail = _child_error(server)
        replacement = _replacement_port(detail, attempt)
        if replacement is None:
            break
        port = replacement
    raise AssertionError("the test hub did not answer")


@dataclass
class OwnedFleet:
    """A hub unit and dependents that exist only for one test."""

    tag: str
    port: int
    workdir: Path
    units: list[str] = field(default_factory=list)

    @property
    def hub_unit(self) -> str:
        return f"synapse-test-{self.tag}-hub.service"

    @property
    def uri(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def answers(self) -> bool:
        return hub_answers(uri=self.uri, token=None, ready_timeout=2.0)

    def _run(self, name: str, properties: list[str], command: list[str]) -> str:
        unit = f"synapse-test-{self.tag}-{name}.service"
        completed = subprocess.run(
            [
                "systemd-run",
                "--user",
                "--quiet",
                f"--unit={unit}",
                f"--working-directory={self.workdir}",
                *(f"--property={item}" for item in properties),
                *command,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        self.units.append(unit)
        return unit

    def _hub_error_path(self) -> Path:
        return self.workdir / "hub-process-error.txt"

    def _release_unit(self, unit: str) -> None:
        """Stop a transient unit so the same name can start again."""
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, check=False)
        subprocess.run(
            ["systemctl", "--user", "reset-failed", unit], capture_output=True, check=False
        )
        self.units = [item for item in self.units if item != unit]

    def _discard_hub_store(self) -> None:
        """Remove a store left by a hub that exited before it listened."""
        for path in self.workdir.glob("hub.db*"):
            if path.is_file():
                path.unlink()

    def _hub_process_error(self) -> str:
        """Return the hub process's own stderr from the attempt that just ended."""
        return self._hub_error_path().read_text(encoding="utf-8", errors="replace")

    def _wait_until_ready(self, unit: str, ready: Callable[[], bool], timeout: float) -> bool:
        """Return whether ``ready`` became true before the unit exited or the wait ended."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready() or _unit_has_stopped(unit):
                break
            time.sleep(0.2)
        return ready()

    def _start_hub_process(self, *, ready: Callable[[], bool], timeout: float) -> str:
        """Start the hub, taking a fresh port only when its own error says the bind lost it."""
        for attempt in range(1, _BIND_ATTEMPTS + 1):
            self._hub_error_path().write_bytes(b"")
            unit = self._run(
                "hub",
                [f"StandardError=truncate:{self._hub_error_path()}"],
                [
                    sys.executable,
                    "-m",
                    "synapse_channel.cli",
                    "hub",
                    f"--port={self.port}",
                    f"--db={self.workdir / 'hub.db'}",
                    # Without this the hub would use the installed hub's pin file.
                    f"--identity-pins={self.workdir / 'identity-pins.json'}",
                ],
            )
            if self._wait_until_ready(unit, ready, timeout):
                return unit
            detail = self._hub_process_error()
            self._release_unit(unit)
            replacement = _replacement_port(detail, attempt)
            if replacement is None:
                break
            self._discard_hub_store()
            self.port = replacement
        raise AssertionError("the test hub did not answer")

    def start_hub(self) -> str:
        """Start the test hub and wait until its probe answers."""
        return self._start_hub_process(ready=self.answers, timeout=30.0)

    def sleeper(self, name: str, relations: list[str]) -> str:
        """Start a dependent that only sleeps, related to the hub as given."""
        properties = [f"{relation}={self.hub_unit}" for relation in relations]
        return self._run(name, properties, ["/usr/bin/sleep", "infinity"])

    def listener(self, name: str, *, restart: bool) -> str:
        """Start a real presence listener, which exits when the hub drops."""
        properties = [f"After={self.hub_unit}"]
        if restart:
            properties += ["Restart=always", "RestartSec=6"]
        unit = self._run(
            name,
            properties,
            [
                sys.executable,
                "-m",
                "synapse_channel.cli",
                "listen",
                f"--name=test-{self.tag}-{name}",
                f"--uri={self.uri}",
            ],
        )
        time.sleep(1.5)
        assert self.pid(unit) > 0
        return unit

    def pid(self, unit: str) -> int:
        return unit_states([unit], runner=_RUNNER)[unit].main_pid

    def lock(self) -> Path:
        return self.workdir / "restart.lock"

    def restart(self, **overrides: object) -> HubRestartReport:
        arguments: dict[str, object] = {
            "authorized_hub_pid": self.pid(self.hub_unit),
            "runner": _RUNNER,
            "hub_answers": self.answers,
            "lock_path": self.lock(),
            "hub_unit": self.hub_unit,
            "answer_timeout": 30.0,
            "settle_timeout": 15.0,
            "poll_interval": 0.2,
        }
        arguments.update(overrides)
        return restart_hub(**arguments)  # type: ignore[arg-type]


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[OwnedFleet]:
    _require_user_manager()
    owned = OwnedFleet(tag=uuid.uuid4().hex[:10], port=free_port(), workdir=tmp_path)
    try:
        yield owned
    finally:
        for unit in reversed(owned.units):
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, check=False)
            subprocess.run(
                ["systemctl", "--user", "reset-failed", unit], capture_output=True, check=False
            )


def _kinds(report: HubRestartReport) -> dict[str, str]:
    return {change.unit: change.kind for change in report.changes}


def test_plan_names_each_dependent_with_its_relation_and_changes_nothing(
    fleet: OwnedFleet,
) -> None:
    hub = fleet.start_hub()
    ordered = fleet.sleeper("ordered", ["After"])
    wanting = fleet.sleeper("wanting", ["Wants", "After"])
    requiring = fleet.sleeper("requiring", ["Requires", "After"])
    unrelated = fleet._run("unrelated", [], ["/usr/bin/sleep", "infinity"])
    pids = {unit: fleet.pid(unit) for unit in (hub, ordered, wanting, requiring)}

    plan = plan_hub_restart(runner=_RUNNER, hub_unit=fleet.hub_unit)

    relations = {item.unit: item.relations for item in plan.dependents}
    assert relations == {
        ordered: ("Before",),
        wanting: ("WantedBy", "Before"),
        requiring: ("RequiredBy", "Before"),
    }
    assert unrelated not in relations
    assert [item.unit for item in plan.restarted_with_hub] == [requiring]
    assert plan.hub.main_pid == pids[hub]
    assert all(plan.states[unit].active for unit in (ordered, wanting, requiring))
    text = "\n".join(render_plan(plan))
    assert "dependent service units: 3" in text
    assert "systemd would stop or restart with the hub: 1:" in text
    assert f"  {requiring} [RequiredBy, Before] active/running pid {pids[requiring]}" in text
    assert "left alone by systemd: 2 (3 of all dependents running now)" in text
    assert {unit: fleet.pid(unit) for unit in pids} == pids


def test_one_job_restarts_the_hub_and_leaves_order_only_dependents_running(
    fleet: OwnedFleet,
) -> None:
    hub = fleet.start_hub()
    ordered = fleet.sleeper("ordered", ["After"])
    wanting = fleet.sleeper("wanting", ["Wants", "After"])
    before = {unit: fleet.pid(unit) for unit in (hub, ordered, wanting)}

    report = fleet.restart()

    assert report.ok and report.restarted
    assert fleet.pid(hub) not in (0, before[hub])
    assert fleet.pid(ordered) == before[ordered]
    assert fleet.pid(wanting) == before[wanting]
    assert _kinds(report) == {ordered: "unchanged", wanting: "unchanged"}
    assert f"ok: systemctl --user restart -- {hub} (one job)" in report.lines
    assert "hub answers" in report.lines
    assert "dependents: 2 unchanged" in report.lines
    assert fleet.answers()


def test_a_wrong_process_id_is_refused_and_nothing_is_restarted(fleet: OwnedFleet) -> None:
    hub = fleet.start_hub()
    pid = fleet.pid(hub)

    report = fleet.restart(authorized_hub_pid=pid + 1)

    assert not report.ok and not report.restarted
    assert report.lines[-1] == (
        f"refused: the hub runs as pid {pid}, the authorisation names pid {pid + 1}"
    )
    assert fleet.pid(hub) == pid


def test_a_requiring_dependent_refuses_the_restart_unless_allowed(fleet: OwnedFleet) -> None:
    hub = fleet.start_hub()
    requiring = fleet.sleeper("requiring", ["Requires", "After"])
    before = {unit: fleet.pid(unit) for unit in (hub, requiring)}

    refused = fleet.restart()

    assert not refused.ok and not refused.restarted
    assert refused.lines[-1].startswith("refused: systemd would stop or restart the units")
    assert {unit: fleet.pid(unit) for unit in before} == before

    allowed = fleet.restart(allow_dependent_restart=True)

    # One restart job restarts the requiring unit with the hub. A stop followed
    # by a start would leave it stopped: nothing starts it again.
    assert allowed.ok and allowed.restarted
    assert _kinds(allowed) == {requiring: "restarted"}
    assert fleet.pid(requiring) not in (0, before[requiring])
    assert any(line.startswith(f"  restarted: {requiring} — ") for line in allowed.lines)


def test_a_listener_that_restarts_itself_is_reported_restarted(fleet: OwnedFleet) -> None:
    fleet.start_hub()
    presence = fleet.listener("presence", restart=True)
    before = fleet.pid(presence)

    started = time.monotonic()
    report = fleet.restart()
    waited = time.monotonic() - started

    # The listener comes back six seconds after it exits, later than the hub
    # answers. A report made without waiting for it would call it stopped.
    assert report.ok and report.restarted
    assert _kinds(report) == {presence: "restarted"}
    assert fleet.pid(presence) not in (0, before)
    assert waited >= 5.0


def test_a_listener_that_stays_down_fails_the_report_with_recovery_commands(
    fleet: OwnedFleet,
) -> None:
    fleet.start_hub()
    presence = fleet.listener("presence", restart=False)

    report = fleet.restart(settle_timeout=3.0)

    assert report.restarted and not report.ok
    assert _kinds(report) == {presence: "stopped"}
    assert "dependents: 1 stopped" in report.lines
    assert report.lines[-3:] == (
        "to bring them back, after checking why they stopped:",
        f"  systemctl --user reset-failed -- {presence}",
        f"  systemctl --user start -- {presence}",
    )
    assert fleet.answers()


def test_a_hub_that_does_not_answer_fails_the_report(fleet: OwnedFleet) -> None:
    hub = fleet.start_hub()
    before = fleet.pid(hub)

    report = fleet.restart(hub_answers=lambda: False, answer_timeout=1.0)

    assert report.restarted and not report.ok
    assert "hub did not answer within 1 s" in report.lines
    assert fleet.pid(hub) not in (0, before)


def test_a_held_lock_refuses_the_restart(fleet: OwnedFleet) -> None:
    hub = fleet.start_hub()
    pid = fleet.pid(hub)
    import fcntl

    descriptor = os.open(fleet.lock(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = fleet.restart()
    finally:
        os.close(descriptor)

    assert not report.ok and not report.restarted
    assert report.lines[-1] == f"refused: another restart or redeploy holds {fleet.lock()}"
    assert fleet.pid(hub) == pid

    assert fleet.restart().ok


def test_a_stopped_hub_is_refused(fleet: OwnedFleet) -> None:
    hub = fleet.start_hub()
    pid = fleet.pid(hub)
    subprocess.run(["systemctl", "--user", "stop", hub], capture_output=True, check=True)

    report = fleet.restart(authorized_hub_pid=pid)

    assert not report.ok and not report.restarted
    assert report.lines[-1] == ("refused: the hub unit is not running; there is nothing to restart")
    assert fleet.pid(hub) == 0


def test_an_unknown_hub_unit_has_no_dependents_and_is_refused(fleet: OwnedFleet) -> None:
    report = fleet.restart(authorized_hub_pid=1)

    assert not report.ok and not report.restarted
    assert report.lines[1] == "dependent service units: 0"


def test_the_probe_reports_an_absent_hub_and_a_refusing_hub_without_raising(
    fleet: OwnedFleet,
) -> None:
    assert not fleet.answers()

    # A pin for the probe's name under another key makes the hub refuse it.
    (fleet.workdir / "identity-pins.json").write_text(
        json.dumps(
            {
                "pins": {
                    PROBE_NAME: {
                        "key_id": "machine-0000000000000000",
                        "pinned_at": 1.0,
                        "public_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def port_accepts() -> bool:
        return _tcp_accepts(fleet.port)

    fleet._start_hub_process(ready=port_accepts, timeout=30.0)

    assert not fleet.answers()
    assert fleet.pid(fleet.hub_unit) > 0


def test_the_probe_reports_a_hub_that_welcomes_and_then_closes(tmp_path: Path) -> None:
    # A hub can accept a client, welcome it and close at once, for example on
    # an identity pin mismatch. The question then fails while it is sent.
    script = tmp_path / "closing_hub.py"
    script.write_text(_CLOSING_HUB_SCRIPT, encoding="utf-8")
    server, port = _closing_server(script)
    try:
        outcomes = {
            hub_answers(uri=f"ws://127.0.0.1:{port}", token=None, ready_timeout=3.0)
            for _ in range(5)
        }
    finally:
        _stop_script(server)

    assert outcomes == {False}


class FaultyRunner:
    """Send every command to the real service manager, except one planted fault."""

    def __init__(self, fault: Callable[[list[str], int], object]) -> None:
        self.fault = fault
        self.calls = 0

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        planted = self.fault(args, self.calls)
        if isinstance(planted, BaseException):
            raise planted
        if isinstance(planted, subprocess.CompletedProcess):
            return planted
        return _RUNNER(args, capture_output=capture_output, text=text, check=check)


def test_a_hub_replaced_before_the_lock_is_held_is_refused(fleet: OwnedFleet) -> None:
    hub = fleet.start_hub()
    pid = fleet.pid(hub)

    def replace_hub_before_the_recheck(args: list[str], call: int) -> object:
        # Calls 1 and 2 are the plan; call 3 is the recheck under the lock.
        if call == 3:
            subprocess.run(["systemctl", "--user", "restart", hub], check=True)
        return None

    runner = FaultyRunner(replace_hub_before_the_recheck)
    report = fleet.restart(runner=runner, authorized_hub_pid=pid)

    replaced = fleet.pid(hub)
    assert replaced not in (0, pid)
    assert not report.ok and not report.restarted
    assert report.lines[-1] == (
        f"refused: the hub changed to pid {replaced} before the lock was held"
    )
    assert runner.calls == 3


@pytest.mark.parametrize(
    ("planted", "last_line"),
    [
        (
            subprocess.TimeoutExpired("systemctl", 30.0),
            "uncertain: systemctl --user restart -- {hub} — Command 'systemctl' timed out "
            "after 30.0 seconds; the job may still run",
        ),
        (
            PermissionError("denied"),
            "uncertain: systemctl --user restart -- {hub} — denied; the job may still run",
        ),
        (
            subprocess.CompletedProcess([], 1, "", "Job for unit failed.\n"),
            "failed: systemctl --user restart -- {hub} — Job for unit failed.",
        ),
        (
            subprocess.CompletedProcess([], 1, "", ""),
            "failed: systemctl --user restart -- {hub}",
        ),
    ],
)
def test_a_restart_job_that_is_not_confirmed_is_reported_and_not_verified(
    fleet: OwnedFleet, planted: object, last_line: str
) -> None:
    hub = fleet.start_hub()
    pid = fleet.pid(hub)

    def fail_the_restart(args: list[str], call: int) -> object:
        return planted if "restart" in args else None

    report = fleet.restart(runner=FaultyRunner(fail_the_restart))

    assert not report.ok and not report.restarted
    assert report.lines[-1] == last_line.format(hub=hub)
    assert report.changes == ()
    assert fleet.pid(hub) == pid


def test_a_service_manager_that_stops_answering_after_the_job_says_the_job_was_sent(
    fleet: OwnedFleet,
) -> None:
    hub = fleet.start_hub()
    pid = fleet.pid(hub)
    sent: list[int] = []

    def silent_after_the_job(args: list[str], call: int) -> object:
        if "restart" in args:
            sent.append(call)
            return None
        if sent:
            return subprocess.CompletedProcess(args, 1, "", "Failed to connect to bus\n")
        return None

    with pytest.raises(ServiceQueryError, match="^the restart job was sent, then: failed: "):
        fleet.restart(runner=FaultyRunner(silent_after_the_job))

    assert fleet.pid(hub) not in (0, pid)


def test_the_lock_path_needs_a_runtime_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")
    assert default_lock_path() == Path("/run/user/4242/synapse-channel-redeploy.lock")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    with pytest.raises(ServiceQueryError, match="XDG_RUNTIME_DIR is required"):
        default_lock_path()


def test_command_prints_the_plan_and_restarts_only_with_the_process_id(
    fleet: OwnedFleet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = fleet.start_hub()
    ordered = fleet.sleeper("ordered", ["After"])
    pid = fleet.pid(hub)
    base = [
        "service",
        "hub-restart",
        f"--hub-unit={hub}",
        f"--uri={fleet.uri}",
        f"--lock-file={fleet.lock()}",
    ]

    assert cli_main(base) == 0
    printed = capsys.readouterr().out
    assert "dependent service units: 1" in printed
    assert printed.splitlines()[-1] == (
        "no restart requested; to restart, pass the hub pid shown above: "
        f"--authorized-hub-pid {pid}"
    )
    assert fleet.pid(hub) == pid

    assert cli_main([*base, f"--authorized-hub-pid={pid + 1}"]) == 2
    assert "refused: the hub runs as pid" in capsys.readouterr().out
    assert fleet.pid(hub) == pid

    ordered_pid = fleet.pid(ordered)
    assert cli_main([*base, f"--authorized-hub-pid={pid}", "--settle-timeout=10"]) == 0
    printed = capsys.readouterr().out
    assert "(one job)" in printed and "dependents: 1 unchanged" in printed
    assert fleet.pid(hub) not in (0, pid)
    assert fleet.pid(ordered) == ordered_pid
    assert fleet.lock().exists()


def test_command_returns_one_when_a_dependent_stays_down(
    fleet: OwnedFleet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = fleet.start_hub()
    fleet.listener("presence", restart=False)

    code = cli_main(
        [
            "service",
            "hub-restart",
            f"--hub-unit={hub}",
            f"--uri={fleet.uri}",
            f"--authorized-hub-pid={fleet.pid(hub)}",
            f"--lock-file={fleet.lock()}",
            "--settle-timeout=3",
        ]
    )

    assert code == 1
    assert "dependents: 1 stopped" in capsys.readouterr().out


def test_command_returns_two_when_the_service_manager_does_not_answer(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # An empty runtime directory holds no user bus, so every query fails.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    for extra in ([], ["--authorized-hub-pid=4242"]):
        code = cli_main(["service", "hub-restart", "--hub-unit=synapse-test-none.service", *extra])

        assert code == 2
        assert capsys.readouterr().out.startswith(
            "synapse service hub-restart: failed: systemctl --user show"
        )


def test_a_missing_service_manager_tool_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    def absent(name: str) -> str | None:
        return None

    monkeypatch.setattr(shutil, "which", absent)
    assert _user_manager_available() is False


def test_the_suite_skips_without_a_user_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable() -> bool:
        return False

    monkeypatch.setattr(sys.modules[__name__], "_user_manager_available", unavailable)
    with pytest.raises(pytest.skip.Exception):
        _require_user_manager()


def test_a_silent_script_has_not_reported_that_it_is_listening() -> None:
    server = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert _script_is_listening(server, 0.05) is False
    finally:
        _stop_script(server)


def test_a_script_that_does_not_stop_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    server = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    calls = {"n": 0}

    def expire_once(self: subprocess.Popen[str], timeout: float | None = None) -> tuple[str, str]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        return "", ""

    monkeypatch.setattr(subprocess.Popen, "communicate", expire_once)
    try:
        assert _finish_script(server) == ""
    finally:
        if server.stdout is not None:
            server.stdout.close()
        if server.stderr is not None:
            server.stderr.close()
        if server.poll() is None:
            server.kill()
            server.wait(timeout=5)
    assert calls["n"] == 2


def test_a_store_left_by_a_failed_bind_is_removed(tmp_path: Path) -> None:
    owned = OwnedFleet(tag="store", port=9, workdir=tmp_path)
    (tmp_path / "hub.db").write_text("left behind", encoding="utf-8")
    (tmp_path / "hub.db-dir").mkdir()
    owned._discard_hub_store()
    assert not (tmp_path / "hub.db").exists()
    assert (tmp_path / "hub.db-dir").is_dir()


def test_a_hub_wait_that_ends_immediately_is_not_ready(tmp_path: Path) -> None:
    def not_ready() -> bool:
        return False

    owned = OwnedFleet(tag="wait", port=9, workdir=tmp_path)
    assert owned._wait_until_ready("unused", not_ready, 0.0) is False


def test_a_lost_bind_takes_a_fresh_port_only_while_an_attempt_remains() -> None:
    detail = f"OSError: [Errno {errno.EADDRINUSE}] {os.strerror(errno.EADDRINUSE)}"
    assert _replacement_port(detail, 1) is not None
    assert _replacement_port(detail, _BIND_ATTEMPTS) is None
    assert _replacement_port("refused: the database is locked", 1) is None


def test_a_hub_start_does_not_retry_when_the_error_is_not_a_lost_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = OwnedFleet(tag="once", port=9, workdir=tmp_path)
    attempts: list[int] = []

    def fake_run(name: str, properties: list[str], command: list[str]) -> str:
        attempts.append(owned.port)
        return "synapse-test-once-hub.service"

    def not_ready(unit: str, ready: Callable[[], bool], timeout: float) -> bool:
        return False

    def locked_error() -> str:
        return "refused: the database is locked"

    def release(unit: str) -> None:
        return None

    monkeypatch.setattr(owned, "_run", fake_run)
    monkeypatch.setattr(owned, "_wait_until_ready", not_ready)
    monkeypatch.setattr(owned, "_hub_process_error", locked_error)
    monkeypatch.setattr(owned, "_release_unit", release)

    with pytest.raises(AssertionError, match="the test hub did not answer"):
        owned.start_hub()

    assert attempts == [9]


def test_a_closing_hub_does_not_retry_when_it_fails_for_another_reason(tmp_path: Path) -> None:
    script = tmp_path / "closing_hub.py"
    script.write_text(
        "import sys\nsys.stderr.write('refused: the database is locked\\n')\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="the test hub did not answer"):
        _closing_server(script)


def test_the_fleet_moves_on_when_its_port_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listener on the fleet's first port makes both hub starts take another port."""
    _require_user_manager()
    held = free_port()
    owned = OwnedFleet(tag=uuid.uuid4().hex[:10], port=held, workdir=tmp_path)
    script = tmp_path / "closing_hub.py"
    script.write_text(_CLOSING_HUB_SCRIPT, encoding="utf-8")
    offered_held = False
    real_free_port = free_port

    def held_once() -> int:
        nonlocal offered_held
        if not offered_held:
            offered_held = True
            return held
        return real_free_port()

    server: subprocess.Popen[str] | None = None
    try:
        with _held_listener(held):
            unit = owned.start_hub()
            assert owned.port != held
            assert owned.answers()
            assert owned.pid(unit) > 0
            monkeypatch.setattr(sys.modules[__name__], "free_port", held_once)
            server, moved = _closing_server(script)
            assert moved != held
            assert server.poll() is None
    finally:
        if server is not None:
            _stop_script(server)
        for unit_name in reversed(owned.units):
            subprocess.run(
                ["systemctl", "--user", "stop", unit_name], capture_output=True, check=False
            )
            subprocess.run(
                ["systemctl", "--user", "reset-failed", unit_name],
                capture_output=True,
                check=False,
            )
