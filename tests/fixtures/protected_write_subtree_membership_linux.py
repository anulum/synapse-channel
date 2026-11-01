# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real process subtree membership capture tests
"""Bind real process membership and refuse concurrent kernel migration."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
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
from synapse_channel.core.protected_write_subtree import (
    capture_protected_writer_subtree,
    inspect_protected_writer_subtree,
)

pytestmark = pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")


def test_capture_binds_actual_membership_to_retained_descriptor() -> None:
    """Current membership must resolve to the same supervisor-retained inode."""
    membership = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    path = Path("/sys/fs/cgroup") / membership.lstrip("/")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        expected = inspect_protected_writer_subtree(descriptor)
        assert capture_protected_writer_subtree(descriptor) == expected
        assert capture_protected_writer_subtree() == expected
    finally:
        os.close(descriptor)


def test_other_empty_subtree_cannot_replace_current_membership(delegated_subtree: Path) -> None:
    """An empty real kernel subgroup cannot certify another writer's membership."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        assert not inspect_protected_writer_subtree(descriptor).populated
        with pytest.raises(ValueError, match="belongs to another process subtree"):
            capture_protected_writer_subtree(descriptor)
    finally:
        os.close(descriptor)


def test_closed_retained_descriptor_cannot_be_reused_by_capture(tmp_path: Path) -> None:
    """Opening current membership must not recycle an invalid retained number."""
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    os.close(descriptor)
    with pytest.raises(OSError):
        capture_protected_writer_subtree(descriptor)


def test_real_process_migration_refuses_membership_capture(
    delegated_subtree: Path, managed_process: ManagedProcess
) -> None:
    """A live child moved between real subgroups cannot retain a stale capture."""
    first, second = delegated_subtree / "first", delegated_subtree / "second"
    first.mkdir()
    second.mkdir()
    code = """
import sys,time
from synapse_channel.core.protected_write_subtree import capture_protected_writer_subtree
print("ready", flush=True)
input()
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    try:
        capture_protected_writer_subtree()
    except ValueError as error:
        if str(error) == "writer cgroup membership changed during capture":
            print("MIGRATION_REFUSED", flush=True)
            assert input() == "finish"
            break
        if str(error) != "writer subtree changed during observation":
            raise
else:
    raise RuntimeError("actual process migration was not observed")
"""
    observer = managed_process(
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        )
    )
    stop = threading.Event()

    def migrate() -> None:
        """Move the owned live observer between actual kernel subtrees."""
        while not stop.is_set() and observer.poll() is None:
            (second / "cgroup.procs").write_text(str(observer.pid))
            (first / "cgroup.procs").write_text(str(observer.pid))

    mover = threading.Thread(target=migrate)
    assert observer.stdout is not None and observer.stdin is not None
    assert observer.stdout.readline() == "ready\n"
    (first / "cgroup.procs").write_text(str(observer.pid))
    mover.start()
    try:
        observer.stdin.write("start\n")
        observer.stdin.flush()
        assert observer.stdout.readline() == "MIGRATION_REFUSED\n"
    finally:
        stop.set()
        mover.join(timeout=5)
        assert not mover.is_alive()
    observer.stdin.write("finish\n")
    observer.stdin.flush()
    assert observer.wait(timeout=5) == 0


def test_actual_deep_membership_refuses_read_budget(
    delegated_subtree: Path, managed_process: ManagedProcess
) -> None:
    """A real over-budget kernel membership cannot become truncated evidence."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    code = """
from synapse_channel.core.protected_write_subtree import capture_protected_writer_subtree
print("ready", flush=True)
input()
try:
    capture_protected_writer_subtree()
except ValueError as error:
    assert str(error) == "writer cgroup membership exceeds read budget"
    print("BUDGET_REFUSED", flush=True)
else:
    raise RuntimeError("deep kernel membership was accepted")
"""
    try:
        for index in range(24):
            name = f"depth-{index:02d}-" + "x" * 180
            os.mkdir(name, dir_fd=descriptor)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        observer = managed_process(
            subprocess.Popen(
                [sys.executable, "-c", code],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
            )
        )
        assert observer.stdout is not None and observer.stdin is not None
        assert observer.stdout.readline() == "ready\n"
        control = os.open("cgroup.procs", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        try:
            assert os.write(control, str(observer.pid).encode("ascii")) > 0
        finally:
            os.close(control)
        observer.stdin.write("start\n")
        observer.stdin.flush()
        assert observer.stdout.readline() == "BUDGET_REFUSED\n"
        assert observer.wait(timeout=5) == 0
    finally:
        os.close(descriptor)
