# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual descriptor escape after writer subtree termination
"""Empty writer membership cannot certify custody of an escaped target descriptor."""

from __future__ import annotations

import os
import select
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from fixtures.protected_write_recovery_evidence_linux import _collect
from fixtures.protected_write_recovery_snapshot_linux import RecoveryFixture, _create, _snapshot
from fixtures.protected_write_recovery_snapshot_linux import recovery_fixture as recovery_fixture
from synapse_channel.core.protected_write_execution_journal import record_protected_execution_start
from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step
from synapse_channel.core.protected_write_recovery_evidence import (
    read_protected_recovery_evidence,
)


@pytest.fixture
def delegated_subtree() -> Iterator[Path]:
    """Own a transient, resource-bounded unprivileged service and its subgroups."""
    unit = "synapse-protected-recovery-test-" + uuid.uuid4().hex + ".service"
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


def _kill_owned_writer_group(descriptor: int) -> None:
    """Use the real kernel control of this fixture's retained owned group."""
    control = os.open("cgroup.kill", os.O_WRONLY | os.O_CLOEXEC, dir_fd=descriptor)
    try:
        assert os.write(control, b"1") == 1
    finally:
        os.close(control)


_SENDER = """
import array, os, socket, sys, time
channel = socket.socket(fileno=int(sys.argv[1]))
input()
if sys.argv[3] == "filtered":
    from synapse_channel.core.protected_write_descriptor_filter import (
        install_protected_writer_descriptor_filter,
    )
    install_protected_writer_descriptor_filter(expected_abi="linux-x86_64")
descriptor = os.open(sys.argv[2], os.O_RDWR)
try:
    try:
        assert channel.sendmsg([b"target"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                            array.array("i", [descriptor]))]) == 6
    except PermissionError:
        print("denied", flush=True)
        sys.exit(0)
    print("sent", flush=True)
    time.sleep(40)
finally:
    os.close(descriptor)
    channel.close()
"""

_RECEIVER = """
import array, os, socket, sys
channel = socket.socket(fileno=int(sys.argv[1]))
descriptors = array.array("i")
message, ancillary, flags, _ = channel.recvmsg(16, socket.CMSG_SPACE(descriptors.itemsize),
                                             socket.MSG_CMSG_CLOEXEC)
if not message and not ancillary:
    print("no descriptor", flush=True)
    sys.exit(0)
assert message == b"target" and flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) == 0
assert len(ancillary) == 1
level, kind, payload = ancillary[0]
assert (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS)
descriptors.frombytes(payload)
assert len(descriptors) == 1
descriptor = descriptors[0]
try:
    details = os.fstat(descriptor)
    print(f"held {details.st_dev} {details.st_ino}", flush=True)
    input()
    assert os.pwrite(descriptor, b"escaped foreign contents", 0) == 24
    os.ftruncate(descriptor, 24)
    os.fsync(descriptor)
    print("written", flush=True)
finally:
    os.close(descriptor)
    channel.close()
"""


def _line(process: subprocess.Popen[str]) -> str:
    """Read one actual child handshake with a bounded readiness wait."""
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], 5)
    assert ready, "child handshake deadline expired"
    line = process.stdout.readline()
    assert isinstance(line, str)
    return line.strip()


def _signal(process: subprocess.Popen[str]) -> None:
    """Release one bounded child phase after its real prerequisite is verified."""
    assert process.stdin is not None
    process.stdin.write("continue\n")
    process.stdin.flush()


@pytest.mark.parametrize("filtered", [False, True])
def test_descriptor_escape_boundary_before_recursive_stop(
    recovery_fixture: RecoveryFixture, delegated_subtree: Path, tmp_path: Path, filtered: bool
) -> None:
    """Compare blocked transfer with actual escaped custody surviving recursive stop.

    This deliberately exercises the current unisolated same-UID profile. It is
    negative evidence requiring independent descriptor custody; it does not
    certify the separate-UID privileged profile or admit recovery.
    """
    prepared, begun, service, evidence, _root = recovery_fixture
    execution = record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    record_protected_execution_step(
        prepared,
        execution,
        execution.operation,
        step_index=0,
        phase="started",
        service_journal=service,
        max_journal_operations=64,
    )
    _create(recovery_fixture)
    reference = _collect(recovery_fixture)
    frozen = read_protected_recovery_evidence(
        reference,
        prepared=prepared,
        begun=begun,
        evidence_store=evidence,
        max_bytes=4096,
        max_total_evidence_bytes=16384,
    )
    assert _snapshot(recovery_fixture).position == "after_frontier"
    sender_socket, receiver_socket = socket.socketpair()
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    children: list[subprocess.Popen[str]] = []
    try:
        sender = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _SENDER,
                str(sender_socket.fileno()),
                str(tmp_path / "records/note.md"),
                "filtered" if filtered else "unfiltered",
            ],
            pass_fds=(sender_socket.fileno(),),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        children.append(sender)
        receiver = subprocess.Popen(
            [sys.executable, "-c", _RECEIVER, str(receiver_socket.fileno())],
            pass_fds=(receiver_socket.fileno(),),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        children.append(receiver)
        sender_socket.close()
        receiver_socket.close()
        (delegated_subtree / "cgroup.procs").write_text(str(sender.pid))
        members = (delegated_subtree / "cgroup.procs").read_text().split()
        assert str(sender.pid) in members and str(receiver.pid) not in members
        _signal(sender)
        if filtered:
            assert _line(sender) == "denied"
            assert sender.wait(timeout=5) == 0
            assert _line(receiver) == "no descriptor"
            assert receiver.wait(timeout=5) == 0
            before = os.fstat(descriptor)
            _kill_owned_writer_group(descriptor)
            after = os.fstat(descriptor)
            assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
            assert "populated 0" in (delegated_subtree / "cgroup.events").read_text().splitlines()
            assert _snapshot(recovery_fixture).position == "after_frontier"
            assert _collect(recovery_fixture) == reference
            return
        assert _line(sender) == "sent"
        identity = (tmp_path / "records/note.md").stat()
        assert _line(receiver) == f"held {identity.st_dev} {identity.st_ino}"
        before = os.fstat(descriptor)
        _kill_owned_writer_group(descriptor)
        assert sender.wait(timeout=5) == -9
        after = os.fstat(descriptor)
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        assert "populated 0" in (delegated_subtree / "cgroup.events").read_text().splitlines()
        assert receiver.poll() is None
        assert _snapshot(recovery_fixture).position == "after_frontier"
        before_events, before_operations = service.read_all(), service.read_operations()
        _signal(receiver)
        assert _line(receiver) == "written"
        assert receiver.wait(timeout=5) == 0
        assert (tmp_path / "records/note.md").read_bytes() == b"escaped foreign contents"
        with pytest.raises(ValueError, match="declared effect"):
            _snapshot(recovery_fixture)
        with pytest.raises(ValueError, match="declared effect"):
            _collect(recovery_fixture)
        retained = read_protected_recovery_evidence(
            reference,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=16384,
        )
        assert retained == frozen
        assert service.read_all() == before_events
        assert service.read_operations() == before_operations
    finally:
        for child in children:
            child.kill()
            child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                assert stream is not None
                stream.close()
        os.close(descriptor)
        sender_socket.close()
        receiver_socket.close()
