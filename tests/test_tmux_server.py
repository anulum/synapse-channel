# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for keeping the tmux server out of other units
"""The tmux server's control group, on real tmux and the real user manager.

Every test uses a tmux server of its own on a private socket: ``tmux_bin`` is a
wrapper that runs ``tmux -L synapse-test-<id>``. The user's tmux server is
never asked, moved or stopped. The "bridge" that starts tmux is a transient
service unit the test owns.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from synapse_channel.cli import main as cli_main
from synapse_channel.tmux_server import (
    MOVED_SCOPE_PREFIX,
    NEW_SERVER_UNIT_PREFIX,
    SERVER_DESCRIPTION,
    TmuxServerPlacement,
    move_to_own_scope,
    own_server_command,
    placement,
    server_pid,
    start_own_server,
    unit_of_pid,
    user_manager_available,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="control groups and systemd are Linux-only"
)


def _real_surface_available() -> bool:
    if any(shutil.which(tool) is None for tool in ("tmux", "systemd-run", "systemctl", "busctl")):
        return False
    return user_manager_available(runner=subprocess.run)


@dataclass
class Rig:
    """A private tmux socket and the transient units of one test."""

    tag: str
    workdir: Path
    units: list[str] = field(default_factory=list)

    @property
    def socket_name(self) -> str:
        return f"synapse-test-{self.tag}"

    @property
    def tmux_bin(self) -> str:
        wrapper = self.workdir / "tmux-private"
        if not wrapper.exists():
            wrapper.write_text(
                f'#!/bin/sh\nexec tmux -L {self.socket_name} "$@"\n', encoding="utf-8"
            )
            wrapper.chmod(0o755)
        return str(wrapper)

    def bridge(self, name: str, code: str, *properties: str) -> str:
        """Run ``code`` as a service unit that stays active after it exits.

        ``properties`` are extra unit properties, such as a sandbox. The unit
        imports the same Synapse source as the test (``PYTHONPATH``).
        """
        unit = f"synapse-test-{self.tag}-{name}.service"
        source = ["--setenv=PYTHONPATH"] if os.environ.get("PYTHONPATH") else []
        completed = subprocess.run(
            [
                "systemd-run",
                "--user",
                "--quiet",
                f"--unit={unit}",
                "--service-type=oneshot",
                "--property=RemainAfterExit=yes",
                *(f"--property={item}" for item in properties),
                *source,
                sys.executable,
                "-c",
                code,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        self.units.append(unit)
        return unit

    def plain_start(self) -> str:
        """Code that starts a session the way a unit would without this module."""
        return (
            "import subprocess\n"
            f"subprocess.run([{self.tmux_bin!r}, 'new-session', '-d', '-s', 'work', "
            "'sleep', '600'], check=True)\n"
        )

    def scoped_start(self) -> str:
        """Code that starts the server in its own service, then a session plainly."""
        return (
            "import subprocess\n"
            "from synapse_channel.tmux_server import start_own_server\n"
            f"start_own_server({self.tmux_bin!r}, runner=subprocess.run)\n"
            f"subprocess.run([{self.tmux_bin!r}, 'new-session', '-d', '-s', 'work', "
            "'sleep', '600'], check=True)\n"
        )

    def stop(self, unit: str) -> None:
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, check=True)

    def has_session(self) -> bool:
        completed = subprocess.run(
            [self.tmux_bin, "has-session", "-t", "work"], capture_output=True, check=False
        )
        return completed.returncode == 0

    def where(self) -> TmuxServerPlacement:
        return placement(self.tmux_bin, runner=subprocess.run)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    if not _real_surface_available():
        pytest.skip("tmux, systemd-run, busctl or a systemd user manager is missing")
    owned = Rig(tag=uuid.uuid4().hex[:10], workdir=tmp_path)
    try:
        yield owned
    finally:
        subprocess.run(
            ["tmux", "-L", owned.socket_name, "kill-server"], capture_output=True, check=False
        )
        for unit in owned.units:
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, check=False)
            subprocess.run(
                ["systemctl", "--user", "reset-failed", unit], capture_output=True, check=False
            )
        socket_dir = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"  # nosec B108
        (socket_dir / owned.socket_name).unlink(missing_ok=True)


def test_a_server_started_plainly_inside_a_unit_dies_with_that_unit(rig: Rig) -> None:
    # The hazard this module exists for, shown on real systemd.
    unit = rig.bridge("bridge", rig.plain_start())

    found = rig.where()
    assert found.running and found.unit == unit and found.in_service_unit
    assert rig.has_session()

    rig.stop(unit)

    assert not rig.where().running
    assert not rig.has_session()


def test_a_server_started_with_the_prefix_survives_the_unit_that_started_it(rig: Rig) -> None:
    unit = rig.bridge("bridge", rig.scoped_start())

    found = rig.where()
    assert found.running and not found.in_service_unit
    assert found.unit is not None
    assert found.unit.startswith(NEW_SERVER_UNIT_PREFIX) and found.unit.endswith(".service")
    described = subprocess.run(
        ["systemctl", "--user", "show", found.unit, "--property=Description", "--value"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert described.stdout.strip() == SERVER_DESCRIPTION

    rig.stop(unit)

    assert rig.where() == found
    assert rig.has_session()


def test_a_session_started_by_the_product_survives_the_unit_that_started_it(rig: Rig) -> None:
    # The product path: ``start_session`` run from inside a service unit, as a
    # hand-written bridge unit runs ``synapse agent-tmux start``.
    code = (
        "from pathlib import Path\n"
        "from synapse_channel.agent_tmux import AgentTmuxConfig, start_session\n"
        "config = AgentTmuxConfig(\n"
        "    identity='TEST/seat', session='work', cwd=Path('/'),\n"
        "    agent_command=('sleep', '600'),\n"
        f"    tmux_bin={rig.tmux_bin!r},\n"
        f"    registry_dir=Path({str(rig.workdir / 'registry')!r}),\n"
        ")\n"
        "result = start_session(config)\n"
        "raise SystemExit(0 if result.started else 1)\n"
    )
    unit = rig.bridge("bridge", code)

    found = rig.where()
    assert found.running and found.unit is not None
    assert found.unit.startswith(NEW_SERVER_UNIT_PREFIX) and not found.in_service_unit
    assert rig.has_session()

    rig.stop(unit)

    assert rig.where() == found
    assert rig.has_session()


def test_the_server_starts_in_one_service_and_only_when_none_answers(rig: Rig) -> None:
    caller = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "SECRET_TOKEN": "never-copied"}
    unit = f"{NEW_SERVER_UNIT_PREFIX}{rig.tag}.service"
    assert own_server_command(rig.tmux_bin, unit=unit, environ=caller) == [
        "systemd-run",
        "--user",
        "--quiet",
        "--collect",
        "--service-type=forking",
        f"--unit={unit}",
        f"--description={SERVER_DESCRIPTION}",
        "--setenv=PATH",
        "--setenv=LANG",
        "--",
        rig.tmux_bin,
        "start-server",
        ";",
        "set-option",
        "-g",
        "exit-empty",
        "off",
    ]

    started = start_own_server(rig.tmux_bin, runner=subprocess.run, token=rig.tag)

    # systemd-run returns once the forking start is done: the empty server answers.
    assert started.started and started.unit == unit
    assert rig.where().unit == unit and not rig.where().in_service_unit
    subprocess.run([rig.tmux_bin, "new-session", "-d", "-s", "work", "sleep", "600"], check=True)
    assert rig.has_session() and rig.where().unit == unit
    again = start_own_server(rig.tmux_bin, runner=subprocess.run)
    assert (again.started, again.detail) == (False, "a tmux server already answers")


def _no_new_privs(pid: int) -> str:
    """Return the NoNewPrivs flag of one process."""
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("NoNewPrivs:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("no NoNewPrivs line")


def test_a_server_first_started_from_a_sandboxed_unit_does_not_inherit_its_sandbox(
    rig: Rig,
) -> None:
    # The reported bug: with a scope the server kept the starting unit's
    # NoNewPrivileges, and every later pane inherited it (sudo failed).
    unit = rig.bridge("sandboxed", rig.scoped_start(), "NoNewPrivileges=yes")

    found = rig.where()
    assert found.pid is not None and found.unit is not None
    assert found.unit.startswith(NEW_SERVER_UNIT_PREFIX) and not found.in_service_unit
    assert _no_new_privs(found.pid) == "0"

    rig.stop(unit)

    assert rig.has_session()


def test_a_running_server_is_moved_out_of_its_unit_without_a_restart(rig: Rig) -> None:
    unit = rig.bridge("bridge", rig.plain_start())
    before = rig.where()
    assert before.unit == unit and before.pid is not None

    result = move_to_own_scope(rig.tmux_bin, runner=subprocess.run)

    scope = f"{MOVED_SCOPE_PREFIX}{before.pid}.scope"
    assert result.moved and result.unit == scope
    assert result.detail == f"tmux server pid {before.pid} moved from {unit} to {scope}"
    assert rig.where() == TmuxServerPlacement(before.pid, scope)

    rig.stop(unit)

    assert rig.where() == TmuxServerPlacement(before.pid, scope)
    assert rig.has_session()

    again = move_to_own_scope(rig.tmux_bin, runner=subprocess.run)
    assert not again.moved and again.unit == scope
    assert again.detail == f"tmux server pid {before.pid} is in {scope}"


def test_the_command_reports_the_placement_and_moves_only_when_asked(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["service", "tmux-server", f"--tmux-bin={rig.tmux_bin}"]

    assert cli_main(base) == 0
    assert capsys.readouterr().out == "no tmux server answers\n"

    unit = rig.bridge("bridge", rig.plain_start())
    pid = rig.where().pid

    assert cli_main(base) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"tmux server pid {pid} is in {unit}",
        f"stopping {unit} would kill the tmux server and every session on it",
        "to move it into a scope of its own, without a restart: add --move",
    ]
    assert rig.where().unit == unit

    assert cli_main([*base, "--move"]) == 0
    scope = f"{MOVED_SCOPE_PREFIX}{pid}.scope"
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"tmux server pid {pid} moved from {unit} to {scope}"
    )

    assert cli_main([*base, "--move"]) == 0
    assert capsys.readouterr().out == f"tmux server pid {pid} is in {scope}\n"
    rig.stop(unit)
    assert rig.has_session()


def test_the_command_fails_when_the_move_is_refused(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    unit = rig.bridge("bridge", rig.plain_start())
    pid = rig.where().pid
    # A scope of that name already exists, so the service manager refuses.
    holder = subprocess.Popen(
        [
            "systemd-run",
            "--user",
            "--quiet",
            "--scope",
            f"--unit={MOVED_SCOPE_PREFIX}{pid}.scope",
            "sleep",
            "600",
        ]
    )
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            active = subprocess.run(
                ["systemctl", "--user", "is-active", f"{MOVED_SCOPE_PREFIX}{pid}.scope"],
                capture_output=True,
                text=True,
                check=False,
            )
            if active.stdout.strip() == "active":
                break
            time.sleep(0.1)

        assert cli_main(["service", "tmux-server", f"--tmux-bin={rig.tmux_bin}", "--move"]) == 1

        assert (
            capsys.readouterr()
            .out.splitlines()[-1]
            .startswith(f"could not create {MOVED_SCOPE_PREFIX}{pid}.scope — ")
        )
        assert rig.where().unit == unit
    finally:
        holder.terminate()
        holder.wait(timeout=10)


def test_nothing_is_moved_when_no_server_answers(rig: Rig) -> None:
    result = move_to_own_scope(rig.tmux_bin, runner=subprocess.run)

    assert not result.moved and result.unit is None
    assert result.detail == "no tmux server answers"
    assert rig.where() == TmuxServerPlacement(None, None)
    assert not rig.where().running and not rig.where().in_service_unit


def test_the_unit_of_a_process_is_the_leaf_of_its_unified_control_group(tmp_path: Path) -> None:
    own = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    leaf = next(line for line in own.splitlines() if line.startswith("0::")).rsplit("/", 1)[-1]
    assert unit_of_pid(os.getpid()) == (leaf if leaf.endswith((".service", ".scope")) else None)

    cases = {
        1: ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/a.service\n", "a.service"),
        2: ("0::/user.slice/user-1000.slice/session-3.scope/\n", "session-3.scope"),
        3: ("0::/user.slice\n", None),
        4: ("0::/\n", None),
        5: ("12:pids:/user.slice/a.service\n1:name=systemd:/user.slice/a.service\n", None),
        6: ("", None),
    }
    for pid, (text, _expected) in cases.items():
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "cgroup").write_text(text, encoding="utf-8")
    for pid, (_text, expected) in cases.items():
        assert unit_of_pid(pid, proc_root=tmp_path) == expected, pid
    assert unit_of_pid(7, proc_root=tmp_path) is None


class Replay:
    """Answer by executable name; everything not named runs for real."""

    def __init__(self, answers: dict[str, subprocess.CompletedProcess[str] | BaseException]):
        self.answers = answers
        self.asked: list[str] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        name = Path(args[0]).name
        self.asked.append(name)
        answer = self.answers.get(name)
        if isinstance(answer, BaseException):
            raise answer
        if answer is not None:
            return answer
        return subprocess.run(args, capture_output=capture_output, text=text, check=check)


def _done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.mark.parametrize(
    "answer",
    [
        _done(1, stderr="Failed to connect to bus\n"),
        _done(0, stdout="\n"),
        FileNotFoundError("systemctl"),
        subprocess.TimeoutExpired("systemctl", 5.0),
    ],
)
def test_without_a_user_manager_no_server_is_started(
    answer: subprocess.CompletedProcess[str] | BaseException, tmp_path: Path
) -> None:
    runner = Replay({"systemctl": answer, "tmux": _done(1, stderr="no server running\n")})

    assert not user_manager_available(runner=runner)
    assert start_own_server("tmux", runner=runner).detail == "no systemd user manager answers"


@pytest.mark.parametrize(
    "answer",
    [_done(0, stdout="not-a-number\n"), _done(1), FileNotFoundError("tmux")],
)
def test_a_tmux_that_gives_no_process_id_has_no_server(
    answer: subprocess.CompletedProcess[str] | BaseException,
) -> None:
    runner = Replay({"tmux": answer})

    assert server_pid("tmux", runner=runner) is None
    assert placement("tmux", runner=runner) == TmuxServerPlacement(None, None)


def test_a_move_the_service_manager_refuses_is_reported_and_not_claimed(tmp_path: Path) -> None:
    (tmp_path / "41").mkdir()
    (tmp_path / "41" / "cgroup").write_text("0::/app.slice/bridge.service\n", encoding="utf-8")
    for refusal, detail in (
        (
            _done(1, stderr="Unit tmux-server-41.scope was already loaded.\n"),
            " — Unit tmux-server-41",
        ),
        (_done(1), ""),
        (FileNotFoundError("busctl"), ""),
    ):
        runner = Replay({"tmux": _done(0, stdout="41\n"), "busctl": refusal})

        result = move_to_own_scope("tmux", runner=runner, proc_root=tmp_path)

        assert not result.moved and result.unit == "bridge.service"
        assert result.detail.startswith("could not create tmux-server-41.scope" + detail)


def test_a_move_that_did_not_change_the_control_group_is_not_claimed(tmp_path: Path) -> None:
    (tmp_path / "41").mkdir()
    (tmp_path / "41" / "cgroup").write_text("0::/app.slice/bridge.service\n", encoding="utf-8")
    runner = Replay({"tmux": _done(0, stdout="41\n"), "busctl": _done(0)})
    ticks = iter([0.0, 0.0, 1.0, 9.0])
    slept: list[float] = []

    result = move_to_own_scope(
        "tmux",
        runner=runner,
        proc_root=tmp_path,
        settle_timeout=5.0,
        clock=lambda: next(ticks),
        sleep=slept.append,
    )

    assert not result.moved and result.unit == "bridge.service"
    assert result.detail == "tmux server pid 41 is still in bridge.service"
    assert slept == [0.1, 0.1]
    assert runner.asked == ["tmux", "busctl"]


def test_a_server_outside_every_unit_is_left_where_it_is(tmp_path: Path) -> None:
    (tmp_path / "41").mkdir()
    (tmp_path / "41" / "cgroup").write_text("0::/user.slice\n", encoding="utf-8")
    runner = Replay({"tmux": _done(0, stdout="41\n")})

    result = move_to_own_scope("tmux", runner=runner, proc_root=tmp_path)

    assert not result.moved and result.unit is None
    assert result.detail == "tmux server pid 41 is in no service or scope unit"
    assert runner.asked == ["tmux"]


def test_a_server_that_vanishes_during_the_move_is_not_claimed(tmp_path: Path) -> None:
    (tmp_path / "41").mkdir()
    cgroup = tmp_path / "41" / "cgroup"
    cgroup.write_text("0::/app.slice/bridge.service\n", encoding="utf-8")

    class Vanishing(Replay):
        def __call__(self, args: Sequence[str], **kwargs: bool) -> subprocess.CompletedProcess[str]:
            if Path(args[0]).name == "busctl":
                cgroup.unlink()
            return super().__call__(args, **kwargs)

    runner = Vanishing({"tmux": _done(0, stdout="41\n"), "busctl": _done(0)})

    result = move_to_own_scope("tmux", runner=runner, proc_root=tmp_path, settle_timeout=0.0)

    assert not result.moved and result.unit is None
    assert result.detail == "tmux server pid 41 is still in an unknown unit"


@pytest.mark.parametrize(
    ("unit", "counts"),
    [
        ("my-bridge.service", True),
        (f"{NEW_SERVER_UNIT_PREFIX}abc.service", False),
        ("tmux-server-4810.scope", False),
        (None, False),
    ],
)
def test_only_another_service_unit_counts_as_holding_the_server(
    unit: str | None, counts: bool
) -> None:
    """The service the prefix starts holds only the server, so it is not a hazard."""
    assert TmuxServerPlacement(4810, unit).in_service_unit is counts


@pytest.mark.parametrize(
    ("answer", "detail"),
    [
        (
            _done(1, stderr="Job failed\n"),
            "could not start synapse-tmux-server-t.service — Job failed",
        ),
        (FileNotFoundError("systemd-run"), "could not start synapse-tmux-server-t.service"),
    ],
)
def test_a_refused_server_start_is_reported_and_not_claimed(
    answer: subprocess.CompletedProcess[str] | BaseException, detail: str
) -> None:
    """The caller then starts the server with its own command, as tmux always did."""
    runner = Replay(
        {
            "tmux": _done(1, stderr="no server running\n"),
            "systemctl": _done(0, stdout="255\n"),
            "systemd-run": answer,
        }
    )
    started = start_own_server("tmux", runner=runner, token="t")
    assert (started.started, started.unit, started.detail) == (False, None, detail)
