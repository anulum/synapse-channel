# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real writer process subtree observation tests
"""Exercise recursive kernel subtree observations through retained descriptors."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from synapse_channel.core.protected_write_subtree import (
    inspect_protected_writer_subtree,
)
from synapse_channel.core.protected_write_subtree_stop import stop_protected_writer_subtree

ManagedProcess = Callable[[subprocess.Popen[str]], subprocess.Popen[str]]


@pytest.fixture
def managed_process() -> Iterator[ManagedProcess]:
    """Retain adopted process handles, reap boundedly and close every pipe."""
    with ExitStack() as cleanup:

        def adopt(process: subprocess.Popen[str]) -> subprocess.Popen[str]:
            """Register bounded process termination, reaping and pipe closure."""
            cleanup.enter_context(process)
            cleanup.callback(process.wait, timeout=5)
            cleanup.callback(process.kill)
            return process

        yield adopt


def test_current_live_subtree_is_populated_and_retained() -> None:
    """The actual running process keeps the recursive populated bit set."""
    membership = Path("/proc/self/cgroup").read_text().strip()
    assert membership.startswith("0::/")
    directory = Path("/sys/fs/cgroup") / membership.split("::", 1)[1].lstrip("/")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        observation = inspect_protected_writer_subtree(descriptor)
        retained = os.fstat(descriptor)
        assert observation.populated
        assert (observation.device, observation.inode, observation.owner_uid) == (
            retained.st_dev,
            retained.st_ino,
            retained.st_uid,
        )
        assert observation.frozen is False
        assert directory.exists()
    finally:
        os.close(descriptor)


def test_filesystem_lookalike_cannot_supply_subtree_evidence(tmp_path: Path) -> None:
    """A writable copy of cgroup.events does not become kernel evidence."""
    (tmp_path / "cgroup.events").write_text("populated 0\nfrozen 1\n")
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="kernel cgroup filesystem"):
            inspect_protected_writer_subtree(descriptor)
    finally:
        os.close(descriptor)


def test_file_descriptor_cannot_replace_subtree_directory(tmp_path: Path) -> None:
    """A regular file descriptor never supplies kernel subtree identity."""
    path = tmp_path / "events"
    path.write_text("populated 0\nfrozen 1\n")
    with path.open("rb") as stream:
        with pytest.raises(ValueError, match="not a directory"):
            inspect_protected_writer_subtree(stream.fileno())


@pytest.fixture
def delegated_subtree() -> Iterator[Path]:
    """Own a transient, resource-bounded unprivileged service and its subgroups."""
    unit = "synapse-protected-writer-test-" + uuid.uuid4().hex + ".service"
    try:
        subprocess.run(
            [
                "systemd-run",
                "--user",
                "--unit=" + unit,
                "--property=Delegate=yes",
                "--property=RuntimeMaxSec=60",
                "--property=TimeoutStopSec=10",
                "--property=NoNewPrivileges=yes",
                "--property=MemoryMax=268435456",
                "--property=MemorySwapMax=0",
                "--property=TasksMax=32",
                "--property=CPUQuota=50%",
                "--property=LimitCORE=0",
                "/usr/bin/sleep",
                "60",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=ControlGroup", "--value"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        relative = result.stdout.strip()
        assert relative.startswith("/") and unit in relative
        directory = Path("/sys/fs/cgroup") / relative.lstrip("/") / "owned-test-subtree"
        directory.mkdir()
        service = directory.parent
        assert (service / "memory.max").read_text().strip() == "268435456"
        assert (service / "memory.swap.max").read_text().strip() == "0"
        assert (service / "pids.max").read_text().strip() == "32"
        quota, period = map(int, (service / "cpu.max").read_text().split())
        assert quota * 2 == period
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=MainPID", "--value"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = Path(f"/proc/{int(result.stdout.strip())}/status").read_text()
        assert "NoNewPrivs:\t1\n" in status
        yield directory
    finally:
        subprocess.run(
            ["systemctl", "--user", "stop", unit],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )


def test_empty_subtree_freeze_is_a_kernel_observation(delegated_subtree: Path) -> None:
    """Observe both kernel bits without treating an empty user scope as custody."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        observation = inspect_protected_writer_subtree(descriptor)
        assert not observation.populated and not observation.frozen
        (delegated_subtree / "cgroup.freeze").write_text("1")
        frozen = inspect_protected_writer_subtree(descriptor)
        assert not frozen.populated and frozen.frozen
        assert frozen.inode == observation.inode
        (delegated_subtree / "cgroup.freeze").write_text("0")
        assert not inspect_protected_writer_subtree(descriptor).frozen
    finally:
        os.close(descriptor)


def test_descendant_survival_keeps_recursive_population_set(
    delegated_subtree: Path, managed_process: ManagedProcess
) -> None:
    """Leader exit cannot establish quiescence while its grandchild still runs."""
    nested = delegated_subtree / "descendant"
    nested.mkdir()
    with ExitStack() as cleanup:
        descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        cleanup.callback(os.close, descriptor)
        worker = managed_process(
            subprocess.Popen(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import os,time; os.read(0,1); p=os.fork(); "
                    "print(p,flush=True) if p else time.sleep(40); os._exit(0)",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
        )
        assert worker.stdin is not None and worker.stdout is not None
        (nested / "cgroup.procs").write_text(str(worker.pid))
        worker.stdin.write("x")
        worker.stdin.flush()
        child = int(worker.stdout.readline())
        child_fd = os.pidfd_open(child)
        cleanup.callback(os.close, child_fd)
        assert worker.wait(timeout=5) == 0
        assert child in {int(value) for value in (nested / "cgroup.procs").read_text().split()}
        assert not (delegated_subtree / "cgroup.procs").read_text().strip()
        observation = inspect_protected_writer_subtree(descriptor)
        assert observation.populated
        signal.pidfd_send_signal(child_fd, signal.SIGKILL)
        stopped = stop_protected_writer_subtree(
            descriptor,
            expected_identity=(observation.device, observation.inode),
            timeout_seconds=5,
        )
        assert not stopped.populated
        poller = select.poll()
        poller.register(child_fd, select.POLLIN)
        assert poller.poll(5000)
        assert not inspect_protected_writer_subtree(descriptor).populated


def test_removed_subtree_descriptor_is_not_empty_evidence(delegated_subtree: Path) -> None:
    """A deleted cgroup cannot masquerade as a retained empty writer subtree."""
    gone = delegated_subtree / "removed"
    gone.mkdir()
    descriptor = os.open(gone, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        assert not inspect_protected_writer_subtree(descriptor).populated
        gone.rmdir()
        with pytest.raises(OSError):
            inspect_protected_writer_subtree(descriptor)
    finally:
        os.close(descriptor)


def test_real_freeze_race_refuses_mixed_samples(
    delegated_subtree: Path, managed_process: ManagedProcess
) -> None:
    """Concurrent kernel state changes must not produce a stable sampled receipt."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    toggler = managed_process(
        subprocess.Popen(
            [
                "/usr/bin/python3",
                "-c",
                "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                "print('ready',flush=True); "
                "[(p.write_text('1'),p.write_text('0')) for _ in range(10000)]",
                str(delegated_subtree / "cgroup.freeze"),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
    )
    try:
        assert toggler.stdout is not None and toggler.stdout.readline() == "ready\n"
        changed = False
        deadline = time.monotonic() + 10
        while not changed:
            assert time.monotonic() < deadline, "freeze race was not observed before deadline"
            assert toggler.poll() is None, "freeze toggler exited before its race was observed"
            try:
                inspect_protected_writer_subtree(descriptor)
            except ValueError as error:
                assert str(error) == "writer subtree changed during observation"
                changed = True
        assert changed
        assert toggler.wait(timeout=10) == 0
        assert not inspect_protected_writer_subtree(descriptor).frozen
    finally:
        os.close(descriptor)
