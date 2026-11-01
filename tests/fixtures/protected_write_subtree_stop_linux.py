# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real bounded subtree termination tests
"""Exercise kernel subtree termination and refusal through the public operation."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import pytest

from fixtures.protected_write_subtree_linux import (
    ManagedProcess,
)
from fixtures.protected_write_subtree_linux import (
    delegated_subtree as delegated_subtree,
)
from fixtures.protected_write_subtree_linux import (
    managed_process as managed_process,
)
from synapse_channel.core.protected_write_subtree_stop import (
    stop_protected_writer_subtree,
)


def test_empty_group_stop_retains_exact_identity(delegated_subtree: Path) -> None:
    """An empty owned kernel group returns its independently retained identity."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
    try:
        before = os.fstat(descriptor)
        observed = stop_protected_writer_subtree(
            descriptor,
            expected_identity=(before.st_dev, before.st_ino),
            timeout_seconds=5,
        )
        assert not observed.populated
        assert (observed.device, observed.inode) == (before.st_dev, before.st_ino)
    finally:
        os.close(descriptor)


def test_stop_kills_leader_and_nested_descendant(
    delegated_subtree: Path, managed_process: ManagedProcess
) -> None:
    """Kernel recursive termination reaches a grandchild in a nested group."""
    nested = delegated_subtree / "descendant"
    nested.mkdir()
    with ExitStack() as cleanup:
        descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
        cleanup.callback(os.close, descriptor)
        worker = managed_process(
            subprocess.Popen(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import os,time; os.read(0,1); p=os.fork(); "
                    "print(p,flush=True) if p else time.sleep(40); time.sleep(40)",
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
        descendant_fd = os.pidfd_open(int(worker.stdout.readline()))
        cleanup.callback(os.close, descendant_fd)
        before = os.fstat(descriptor)
        observed = stop_protected_writer_subtree(
            descriptor,
            expected_identity=(before.st_dev, before.st_ino),
            timeout_seconds=5,
        )
        assert not observed.populated
        assert worker.wait(timeout=5) == -9
        poller = select.poll()
        poller.register(descendant_fd, select.POLLIN)
        assert poller.poll(5000)
        assert not (nested / "cgroup.procs").read_text().strip()


def test_wrong_identity_refuses_before_signalling(
    delegated_subtree: Path, managed_process: ManagedProcess
) -> None:
    """An enrollment mismatch leaves the actual owned process alive."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
    worker = managed_process(subprocess.Popen(["/usr/bin/sleep", "40"], text=True))
    try:
        (delegated_subtree / "cgroup.procs").write_text(str(worker.pid))
        before = os.fstat(descriptor)
        with pytest.raises(ValueError, match="differs from enrollment"):
            stop_protected_writer_subtree(
                descriptor,
                expected_identity=(before.st_dev, before.st_ino + 1),
                timeout_seconds=5,
            )
        assert worker.poll() is None
        assert str(worker.pid) in (delegated_subtree / "cgroup.procs").read_text().split()
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("budget", [True, 0, -1, float("nan"), float("inf"), 31])
def test_invalid_deadline_refuses_before_descriptor_use(budget: float) -> None:
    """Invalid budgets refuse even when there is no usable kernel descriptor."""
    with pytest.raises(ValueError, match="stop deadline"):
        stop_protected_writer_subtree(-1, expected_identity=(1, 1), timeout_seconds=budget)


@pytest.mark.parametrize("identity", [(), (1,), (0, 1), (1, True), (1, 2, 3)])
def test_invalid_identity_refuses_before_descriptor_use(
    identity: tuple[int, int],
) -> None:
    """Malformed enrolled identities never reach kernel mutation."""
    with pytest.raises(ValueError, match="stop identity"):
        stop_protected_writer_subtree(-1, expected_identity=identity, timeout_seconds=5)


def test_expired_real_deadline_returns_no_empty_receipt(delegated_subtree: Path) -> None:
    """Verification consumes a tiny real budget and must return uncertainty."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
    try:
        before = os.fstat(descriptor)
        with pytest.raises(TimeoutError, match="deadline expired"):
            stop_protected_writer_subtree(
                descriptor,
                expected_identity=(before.st_dev, before.st_ino),
                timeout_seconds=1e-9,
            )
    finally:
        os.close(descriptor)


def test_threaded_kernel_group_refuses_stop(delegated_subtree: Path) -> None:
    """Actual threaded groups refuse before opening their unavailable kill file."""
    (delegated_subtree / "cgroup.type").write_text("threaded")
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
    try:
        before = os.fstat(descriptor)
        with pytest.raises(ValueError, match="ordinary domain"):
            stop_protected_writer_subtree(
                descriptor,
                expected_identity=(before.st_dev, before.st_ino),
                timeout_seconds=5,
            )
    finally:
        os.close(descriptor)


def test_filesystem_lookalike_is_never_mutated(tmp_path: Path) -> None:
    """A regular filesystem control-file copy cannot receive a kill write."""
    control = tmp_path / "cgroup.kill"
    control.write_bytes(b"untouched")
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        before = os.fstat(descriptor)
        with pytest.raises(ValueError, match="kernel cgroup filesystem"):
            stop_protected_writer_subtree(
                descriptor,
                expected_identity=(before.st_dev, before.st_ino),
                timeout_seconds=5,
            )
        assert control.read_bytes() == b"untouched"
    finally:
        os.close(descriptor)


def test_closed_descriptor_cannot_alias_newly_opened_controls(
    delegated_subtree: Path,
) -> None:
    """A closed retained handle refuses before another acquisition can reuse it."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
    before = os.fstat(descriptor)
    os.close(descriptor)
    with pytest.raises(OSError):
        stop_protected_writer_subtree(
            descriptor,
            expected_identity=(before.st_dev, before.st_ino),
            timeout_seconds=5,
        )


class KernelEventsReadBarrier:
    """Schedule real kernel transitions between paired reads, with traceable audit hooks."""

    __cantrace__ = True

    def __init__(self) -> None:
        """Keep one process-lifetime hook, inactive between each explicit test barrier."""
        self.action: Callable[[], None] | None = None
        self.reads = 0

    def __call__(self, event: str, arguments: tuple[object, ...]) -> None:
        """Observe real open events without replacing the syscall or its result."""
        action = self.action
        if action is not None and event == "open" and arguments[0] == "cgroup.events":
            self.reads += 1
            if self.reads % 2 == 0:
                action()


# CPython audit hooks live until process exit; one hook is inactive between tests.
_EVENTS_READ_BARRIER = KernelEventsReadBarrier()
sys.addaudithook(_EVENTS_READ_BARRIER)


def test_stop_waits_for_population_transition_between_real_samples(
    delegated_subtree: Path, managed_process: ManagedProcess
) -> None:
    """An owned process exiting between kernel snapshots cannot abort bounded stop."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
    worker = managed_process(subprocess.Popen(["/usr/bin/sleep", "40"], text=True))
    try:
        (delegated_subtree / "cgroup.procs").write_text(str(worker.pid))
        before = os.fstat(descriptor)
        transitions: list[tuple[bytes, bytes]] = []

        def finish_between_samples() -> None:
            """Schedule a real exit after the first kernel sample without replacing it."""
            if not transitions:
                first = (delegated_subtree / "cgroup.events").read_bytes()
                assert b"populated 1\n" in first
                worker.kill()
                assert worker.wait(timeout=5) == -9
                second = (delegated_subtree / "cgroup.events").read_bytes()
                assert b"populated 0\n" in second
                assert os.fstat(descriptor) == before
                transitions.append((first, second))

        barrier = _EVENTS_READ_BARRIER
        assert barrier.action is None
        barrier.reads = 0
        barrier.action = finish_between_samples
        try:
            observed = stop_protected_writer_subtree(
                descriptor,
                expected_identity=(before.st_dev, before.st_ino),
                timeout_seconds=5,
            )
        finally:
            barrier.action = None
        assert len(transitions) == 1
        assert not observed.populated
        assert not (delegated_subtree / "cgroup.procs").read_text().strip()
        assert worker.returncode == -9
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("change", ["metadata", "verification_timeout", "event_timeout"])
def test_stop_never_retries_metadata_or_outlives_its_verification_budget(
    delegated_subtree: Path, managed_process: ManagedProcess, change: str
) -> None:
    """Real metadata changes refuse, and event instability cannot extend a deadline."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
    worker = managed_process(subprocess.Popen(["/usr/bin/sleep", "40"], text=True))
    original_mode = delegated_subtree.stat().st_mode & 0o777
    try:
        (delegated_subtree / "cgroup.procs").write_text(str(worker.pid))
        before = os.fstat(descriptor)
        calls: list[int] = []

        def change_between_samples() -> None:
            """Apply real kernel mutations or elapsed time at the observation barrier."""
            calls.append(1)
            if change == "metadata":
                delegated_subtree.chmod(original_mode ^ 0o010)
            elif change == "verification_timeout":
                time.sleep(0.3)
            else:
                freeze = str(len(calls) % 2)
                events_path = delegated_subtree / "cgroup.events"
                events = events_path.read_text()
                assert f"frozen {freeze}\n" not in events
                (delegated_subtree / "cgroup.freeze").write_text(freeze)
                limit = time.monotonic() + 1
                while True:
                    assert time.monotonic() < limit, "real freeze did not settle"
                    time.sleep(0.001)
                    if f"frozen {freeze}\n" in events:
                        break
                    events = events_path.read_text()

        barrier = _EVENTS_READ_BARRIER
        assert barrier.action is None
        barrier.reads = 0
        barrier.action = change_between_samples
        try:
            if change == "metadata":
                with pytest.raises(ValueError, match="changed during observation"):
                    stop_protected_writer_subtree(
                        descriptor,
                        expected_identity=(before.st_dev, before.st_ino),
                        timeout_seconds=5,
                    )
            else:
                with pytest.raises(TimeoutError, match="deadline expired"):
                    stop_protected_writer_subtree(
                        descriptor,
                        expected_identity=(before.st_dev, before.st_ino),
                        timeout_seconds=0.25,
                    )
        finally:
            barrier.action = None
        assert calls
        if change != "event_timeout":
            assert len(calls) == 1
        assert worker.poll() is None
        assert str(worker.pid) in (delegated_subtree / "cgroup.procs").read_text().split()
    finally:
        (delegated_subtree / "cgroup.freeze").write_text("0")
        delegated_subtree.chmod(original_mode)
        os.close(descriptor)
