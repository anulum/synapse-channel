# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared real process observations
"""Test cache identity, disclosure and process lifetime using public snapshots."""

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from _platform_caps import PROC_AVAILABLE, requires_proc
from synapse_channel.host_sessions import HostSessionMonitor
from synapse_channel.host_sessions_proc import kernel_clock, observe_process

requires_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


def test_snapshot_reports_native_process_availability(tmp_path: Path) -> None:
    observation = HostSessionMonitor(
        pids=(os.getpid(),), tmux_socket=str(tmp_path / "absent")
    ).snapshot()
    if PROC_AVAILABLE:
        assert observation.process_status == "complete"
        assert len(observation.rows) == 1
        assert observation.rows[0].pid == os.getpid()
        assert observation.rows[0].started_at_status == "observed"
    else:
        assert observation.process_status == "unavailable"
        assert observation.rows == ()


@requires_proc
def test_rows_carry_kernel_start_time_distinct_from_observation_time(tmp_path: Path) -> None:
    before = time.time()
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    ) as child:
        try:
            observation = HostSessionMonitor(
                pids=(os.getpid(), child.pid), tmux_socket=str(tmp_path / "absent")
            ).snapshot()
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)
    rows = {row.pid: row for row in observation.rows}
    fresh, own = rows[child.pid], rows[os.getpid()]
    assert fresh.started_at is not None and own.started_at is not None
    assert fresh.started_at_status == own.started_at_status == "observed"
    assert before - 2.0 <= fresh.started_at <= observation.observed_at
    assert own.started_at <= fresh.started_at
    clock = kernel_clock()
    assert fresh.started_at == clock.started_at(fresh.start_ticks)
    document = json.loads(observation.to_json())
    encoded = {row["pid"]: row for row in document["rows"]}
    assert encoded[child.pid]["started_at"] == fresh.started_at
    assert encoded[child.pid]["started_at_status"] == "observed"
    assert document["observed_at"] >= encoded[child.pid]["started_at"]


@requires_proc
def test_zero_budget_withholds_every_row_as_partial(tmp_path: Path) -> None:
    observation = HostSessionMonitor(
        pids=(os.getpid(),), tmux_socket=str(tmp_path / "absent"), budget_seconds=0
    ).snapshot()
    assert observation.rows == ()
    assert observation.process_status == "partial"


@pytest.mark.parametrize("budget", [-0.001, 5.001, float("nan"), float("inf"), True, "0.25"])
def test_invalid_budget_is_refused(budget: object) -> None:
    with pytest.raises(ValueError, match="budget"):
        HostSessionMonitor(pids=(os.getpid(),), budget_seconds=cast(float, budget))


def _chain_argv(tmp_path: Path, depth: int) -> list[str]:
    """Return argv for ``depth`` nested shells whose leaf execs a sleep named ``codex``."""
    executable = shutil.which("sleep")
    assert executable is not None
    leaf_binary = tmp_path / "codex"
    shutil.copyfile(executable, leaf_binary)
    leaf_binary.chmod(0o700)
    script = tmp_path / "chain.sh"
    script.write_text(
        '#!/bin/sh\nif [ "$1" -gt 0 ]; then "$0" $(($1 - 1)) "$2"; else exec "$2" 60; fi\n'
    )
    script.chmod(0o700)
    return [str(script), str(depth), str(leaf_binary)]


def _descend_to_leaf(top: int) -> tuple[int, ...]:
    """Follow first children from ``top`` until the kernel comm reads ``codex``."""
    chain = [top]
    deadline = time.monotonic() + 10
    while observe_process(chain[-1]).command_name != "codex":
        children = [
            int(pid)
            for task in Path(f"/proc/{chain[-1]}/task").iterdir()
            for pid in (task / "children").read_text().split()
        ]
        if children:
            chain.append(children[0])
        else:
            assert time.monotonic() < deadline, "process chain did not finish starting"
            time.sleep(0.005)
    return tuple(chain)


def _terminate_chain(chain: tuple[int, ...], *, reap: Callable[[], object] | None = None) -> None:
    for pid in reversed(chain):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if reap is not None:
        reap()
    deadline = time.monotonic() + 5
    while any(Path(f"/proc/{pid}").exists() for pid in chain):
        assert time.monotonic() < deadline, "process chain was not reaped"
        time.sleep(0.01)


@requires_proc
def test_sixty_four_ancestors_without_a_pane_root_keep_the_provider_row(tmp_path: Path) -> None:
    top = subprocess.Popen(_chain_argv(tmp_path, depth=70), stdin=subprocess.DEVNULL)
    chain: tuple[int, ...] = ()
    try:
        chain = _descend_to_leaf(top.pid)
        assert len(chain) == 71
        observation = HostSessionMonitor(
            pids=chain, tmux_socket=str(tmp_path / "absent")
        ).snapshot()
        rows = {row.pid: row for row in observation.rows}
        assert set(rows) == set(chain)
        leaf = rows[chain[-1]]
        assert leaf.provider == "codex"
        assert leaf.identity is None and leaf.identity_source == "unknown"
        assert leaf.pane is None and leaf.started_at_status == "observed"
        assert observation.process_status == "complete"
    finally:
        _terminate_chain(chain or (top.pid,), reap=lambda: top.wait(timeout=5))


@requires_proc
@requires_tmux
def test_budget_exhausted_during_ancestor_validation_withholds_the_row(tmp_path: Path) -> None:
    socket = str(tmp_path / "tmux.sock")
    argv = ["tmux", "-S", socket]
    subprocess.run(
        argv
        + [
            "new-session",
            "-d",
            "-s",
            "fixture",
            "-e",
            "SYN_PROJECT=MONITOR-TEST",
            "-e",
            "SYN_IDENTITY=MONITOR-TEST/deep",
            shlex.join(_chain_argv(tmp_path, depth=40)),
        ],
        check=True,
        timeout=5,
    )
    try:
        root = int(
            subprocess.check_output(
                argv + ["display-message", "-p", "-t", "fixture", "#{pane_pid}"],
                text=True,
                timeout=5,
            )
        )
        chain = _descend_to_leaf(root)
        assert 41 <= len(chain) <= 42
        joined = HostSessionMonitor(pids=chain, tmux_socket=socket).snapshot()
        leaf = {row.pid: row for row in joined.rows}[chain[-1]]
        assert leaf.identity == "MONITOR-TEST/deep" and leaf.provider == "codex"
        assert leaf.pane is not None and joined.process_status == "complete"
        starved = HostSessionMonitor(
            pids=(chain[-1],) + chain[:-1], tmux_socket=socket, budget_seconds=0.0002
        ).snapshot()
        assert starved.process_status == "partial"
        assert all(row.pid != chain[-1] for row in starved.rows)
        _terminate_chain(chain)
    finally:
        subprocess.run(argv + ["kill-server"], check=False, timeout=5)


@requires_proc
def test_candidate_capacity_is_partial_and_bounded(
    tmp_path: Path, record_property: Callable[[str, object], None]
) -> None:
    executable = shutil.which("sleep")
    assert executable is not None
    candidate = tmp_path / "codex"
    shutil.copyfile(executable, candidate)
    candidate.chmod(0o700)
    children: list[subprocess.Popen[bytes]] = []
    try:
        for _ in range(257):
            children.append(subprocess.Popen([str(candidate), "30"], stdin=subprocess.DEVNULL))
        identities = {child.pid: observe_process(child.pid) for child in children}
        assert all(item.command_name == "codex" for item in identities.values())
        started = time.monotonic()
        observation = HostSessionMonitor(tmux_socket=str(tmp_path / "absent")).snapshot()
        elapsed = time.monotonic() - started
        own_rows = [row for row in observation.rows if row.pid in identities]
        record_property("collection_seconds_non_isolated", elapsed)
        record_property("returned_rows", len(observation.rows))
        record_property("owned_rows", len(own_rows))
        assert observation.process_status == "partial"
        assert 0 < len(own_rows) <= len(observation.rows) <= 256
        assert all(row.start_ticks == identities[row.pid].start_ticks for row in own_rows)
        assert all(row.provider == "codex" and row.identity is None for row in own_rows)
        assert all(row.cwd is None and row.context_id is None for row in observation.rows)
        assert elapsed < 3.0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            child.wait(timeout=5)
    assert all(child.poll() is not None for child in children)


@requires_proc
def test_cold_snapshot_has_bounded_contention_and_recovers(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def coordination() -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
        entered.set()
        assert release.wait(timeout=2)
        return (), ()

    monitor = HostSessionMonitor(
        pids=(os.getpid(),), tmux_socket=str(tmp_path / "absent"), coordination=coordination
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(monitor.snapshot)
        try:
            assert entered.wait(timeout=1)
            with pytest.raises(TimeoutError, match="already in progress"):
                monitor.snapshot()
        finally:
            release.set()
        observation = pending.result(timeout=2)
    assert monitor.snapshot() is observation
    assert observation.rows[0].pid == os.getpid()


@requires_proc
def test_coordination_connection_refusal_preserves_process_observation(tmp_path: Path) -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))

        def coordination() -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
            with socket.create_connection(reserved.getsockname(), timeout=0.1):
                return (), ()

        observation = HostSessionMonitor(
            pids=(os.getpid(),), tmux_socket=str(tmp_path / "absent"), coordination=coordination
        ).snapshot()
    assert observation.coordination_status == "unavailable"
    assert observation.coordination_observed_at is None
    assert observation.rows[0].presence is None
    assert observation.rows[0].pid == os.getpid()


@requires_proc
def test_shared_snapshot_and_field_consent(tmp_path: Path) -> None:
    monitor = HostSessionMonitor(pids=(os.getpid(),), tmux_socket=str(tmp_path / "absent"))
    first = monitor.snapshot()
    assert first.rows[0].pid == os.getpid()
    assert first.rows[0].cwd is None and first.rows[0].presence is None
    assert first.coordination_status == "unavailable"
    assert first.process_status == "complete"
    assert first.tmux_status == "unavailable"
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: monitor.snapshot(), range(6)))
    assert all(result is first for result in results)
    private = monitor.snapshot(paths=True)
    assert private.rows[0].cwd == os.getcwd()
    assert monitor.snapshot().rows[0].cwd is None
    assert json.loads(first.to_json())["version"] == 1
    another = HostSessionMonitor(pids=(os.getpid(),), tmux_socket=str(tmp_path / "absent"))
    assert another.snapshot().rows[0].reference == first.rows[0].reference
    assert another.snapshot().observer_instance_id != first.observer_instance_id


@pytest.mark.parametrize("pids", [(0,), (-1,), (True,), tuple(range(1, 258))])
def test_invalid_explicit_scope(pids: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        HostSessionMonitor(pids=pids)


@pytest.mark.parametrize("value", [0, 1, "yes", None])
def test_disclosure_flags_cannot_create_unbounded_cache_profiles(value: object) -> None:
    monitor = HostSessionMonitor(pids=(os.getpid(),))
    with pytest.raises(TypeError, match="booleans"):
        monitor.snapshot(paths=cast(bool, value))
    with pytest.raises(TypeError, match="booleans"):
        monitor.snapshot(context=cast(bool, value))


@requires_proc
def test_exit_does_not_reappear_after_cache_expiry(tmp_path: Path) -> None:
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) as child:
        monitor = HostSessionMonitor(pids=(child.pid,), tmux_socket=str(tmp_path / "absent"))
        alive = monitor.snapshot()
        assert alive.rows[0].pid == child.pid
        child.terminate()
        child.wait(timeout=5)
    time.sleep(1.05)
    after = monitor.snapshot()
    assert after.observation_id != alive.observation_id
    assert after.rows == () and after.process_status == "partial"


@pytest.mark.parametrize("change", ["exit", "rename"])
@requires_proc
@requires_tmux
def test_process_change_during_tmux_query_withholds_discovered_process(
    tmp_path: Path, change: str
) -> None:
    argv = ["tmux", "-S", str(tmp_path / "tmux.sock")]
    subprocess.run(
        argv + ["new-session", "-d", "-s", "fixture", "sleep 60"],
        check=True,
        timeout=5,
    )
    server = int(
        subprocess.check_output(
            argv + ["display-message", "-p", "-t", "fixture", "#{pid}"],
            text=True,
            timeout=5,
        )
    )
    try:
        program = (
            'import ctypes,sys; print("ready",flush=True); sys.stdin.readline(); '
            'assert ctypes.CDLL(None).prctl(15,b"renamed",0,0,0)==0; '
            'print("renamed",flush=True); sys.stdin.read()'
        )
        with subprocess.Popen(
            [sys.executable, "-c", program],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        ) as child:
            try:
                assert child.stdout is not None and child.stdout.readline() == "ready\n"
                monitor = HostSessionMonitor(pids=(child.pid,), tmux_socket=argv[2])
                existing_children = {
                    int(pid)
                    for task in Path("/proc/self/task").iterdir()
                    for pid in (task / "children").read_text().split()
                }
                os.kill(server, signal.SIGSTOP)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(monitor.snapshot)
                    deadline = time.monotonic() + 0.4
                    while True:
                        direct_children = {
                            int(pid)
                            for task in Path("/proc/self/task").iterdir()
                            for pid in (task / "children").read_text().split()
                        }
                        if direct_children - existing_children:
                            break
                        assert time.monotonic() < deadline, "tmux query did not start"
                        time.sleep(0.001)
                    if change == "exit":
                        child.terminate()
                        child.wait(timeout=5)
                    else:
                        assert child.stdin is not None
                        child.stdin.write("rename\n")
                        child.stdin.flush()
                        assert child.stdout.readline() == "renamed\n"
                    observation = pending.result(timeout=2)
                assert observation.rows == ()
                assert observation.process_status == "partial"
                assert observation.tmux_status == "partial"
            finally:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
    finally:
        os.kill(server, signal.SIGCONT)
        subprocess.run(argv + ["kill-server"], check=False, timeout=5)


@requires_proc
@requires_tmux
def test_exact_tmux_join_and_conflicting_assertion(tmp_path: Path) -> None:
    socket = str(tmp_path / "tmux.sock")
    argv = ["tmux", "-S", socket]
    subprocess.run(
        argv
        + [
            "new-session",
            "-d",
            "-s",
            "fixture",
            "-e",
            "SYN_PROJECT=MONITOR-TEST",
            "-e",
            "SYN_IDENTITY=MONITOR-TEST/fixture",
            "sleep 120",
        ],
        check=True,
        timeout=5,
    )
    try:
        pid = int(
            subprocess.check_output(
                argv + ["display-message", "-p", "-t", "fixture", "#{pane_pid}"],
                text=True,
                timeout=5,
            )
        )
        monitor = HostSessionMonitor(pids=(pid,), tmux_socket=socket)
        row = monitor.snapshot().rows[0]
        assert row.identity == "MONITOR-TEST/fixture"
        assert row.identity_source == "tmux-session-assertion"
        assert row.attached is False and row.pane is not None
        subprocess.run(
            argv + ["set-environment", "-t", "fixture", "SYN_PROJECT", "OTHER"],
            check=True,
            timeout=5,
        )
        conflict = HostSessionMonitor(pids=(pid,), tmux_socket=socket).snapshot().rows[0]
        assert (
            conflict.identity is None and conflict.identity_source == "conflicting-tmux-assertion"
        )
        assert conflict.reference == row.reference
        subprocess.run(
            argv + ["set-environment", "-t", "fixture", "SYN_PROJECT", "MONITOR-TEST"],
            check=True,
            timeout=5,
        )
        subprocess.run(
            argv
            + [
                "new-session",
                "-d",
                "-t",
                "fixture",
                "-s",
                "linked",
                "-e",
                "SYN_PROJECT=MONITOR-TEST",
                "-e",
                "SYN_IDENTITY=MONITOR-TEST/other",
            ],
            check=True,
            timeout=5,
        )
        ambiguous = HostSessionMonitor(pids=(pid,), tmux_socket=socket).snapshot()
        assert ambiguous.tmux_status == "partial"
        assert len(ambiguous.rows) == 1
        assert ambiguous.rows[0].identity is None
        assert ambiguous.rows[0].identity_source == "conflicting-tmux-assertion"
        assert ambiguous.rows[0].reference == row.reference
        assert ambiguous.rows[0].session is None and ambiguous.rows[0].pane is None
        subprocess.run(argv + ["kill-session", "-t", "linked"], check=True, timeout=5)
        recovered = HostSessionMonitor(pids=(pid,), tmux_socket=socket).snapshot()
        assert recovered.tmux_status == "complete"
        assert recovered.rows[0].identity == row.identity
    finally:
        subprocess.run(argv + ["kill-server"], check=False, timeout=5)


@requires_proc
@requires_tmux
def test_tmux_descendants_keep_distinct_lifetimes_for_duplicate_seat(tmp_path: Path) -> None:
    program = tmp_path / "process_tree.py"
    ready = tmp_path / "ready"
    program.write_text("""
import ctypes, os, signal, subprocess, sys, time
from pathlib import Path
if len(sys.argv) == 2:
    assert ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0) == 0
    Path(sys.argv[1]).write_text(str(os.getpid()))
    time.sleep(60)
else:
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    children = [subprocess.Popen([sys.executable, __file__, sys.argv[1] + str(i)])
                for i in range(2)]
    try:
        for child in children:
            child.wait(timeout=65)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
""")
    socket = str(tmp_path / "tmux.sock")
    argv = ["tmux", "-S", socket]
    subprocess.run(
        argv
        + [
            "new-session",
            "-d",
            "-s",
            "fixture",
            "-e",
            "SYN_PROJECT=MONITOR-TEST",
            "-e",
            "SYN_IDENTITY=MONITOR-TEST/fixture",
            shlex.join([sys.executable, str(program), str(ready), "parent"]),
        ],
        check=True,
        timeout=5,
    )
    try:
        deadline = time.monotonic() + 5
        files = [Path(str(ready) + str(i)) for i in range(2)]
        while not all(path.exists() and path.stat().st_size for path in files):
            assert time.monotonic() < deadline, "disposable children failed to start"
            time.sleep(0.01)
        children = tuple(int(path.read_text()) for path in files)
        root = int(
            subprocess.check_output(
                argv + ["display-message", "-p", "-t", "fixture", "#{pane_pid}"],
                text=True,
                timeout=5,
            )
        )
        scope = set(children)
        for pid in children:
            for _ in range(8):
                if pid == root:
                    break
                pid = observe_process(pid).parent_pid
                scope.add(pid)
            assert pid == root
        observation = HostSessionMonitor(pids=tuple(scope), tmux_socket=socket).snapshot()
        selected = [row for row in observation.rows if row.pid in children]
        assert len(selected) == 2
        assert all(row.identity == "MONITOR-TEST/fixture" for row in selected)
        assert all(row.provider == "codex" and row.duplicate_identity for row in selected)
        assert len({row.reference for row in selected}) == 2
        assert all(row.start_ticks == observe_process(row.pid).start_ticks for row in selected)
        assert all(
            not row.duplicate_identity for row in observation.rows if row.pid not in children
        )
        discovered = HostSessionMonitor(tmux_socket=socket).snapshot()
        automatic = [row for row in discovered.rows if row.pid in children]
        assert len(automatic) == 2
        assert {row.reference for row in automatic} == {row.reference for row in selected}
        assert all(row.identity == "MONITOR-TEST/fixture" for row in automatic)
        assert all(row.duplicate_identity for row in automatic)
        assert all(row.cwd is None and row.context_id is None for row in discovered.rows)
        assert all(row.pid != os.getpid() for row in discovered.rows)
        for pid in children:
            os.kill(pid, 15)
        deadline = time.monotonic() + 5
        while any(Path(f"/proc/{pid}").exists() for pid in children):
            assert time.monotonic() < deadline, "disposable children were not reaped"
            time.sleep(0.01)
    finally:
        subprocess.run(argv + ["kill-server"], check=False, timeout=5)
