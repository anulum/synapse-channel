# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual writer ancillary syscall restriction
"""Exercise irreversible kernel restrictions only inside bounded disposable processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.core.protected_write_descriptor_filter import (
    install_protected_writer_descriptor_filter,
)

_INSTALL = """
import ctypes, errno, os, socket, sys
from synapse_channel.core.protected_write_descriptor_filter import (
    install_protected_writer_descriptor_filter,
)
install_protected_writer_descriptor_filter(expected_abi="linux-x86_64")
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
"""


def _run(code: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the actual public filter and native calls in a bounded child."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import resource; resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n" + code,
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )


@pytest.mark.parametrize("number", [46, 47, 299, 307, 425, 426, 427, 438])
def test_native_ancillary_and_uring_calls_are_denied(number: int) -> None:
    """Real native entrypoints refuse before processing supplied invalid arguments."""
    result = _run(
        _INSTALL
        + """
ctypes.set_errno(0)
assert libc.syscall(int(sys.argv[1]), -1, 0, 0, 0, 0, 0) == -1
assert ctypes.get_errno() == errno.EPERM
print("denied")
""",
        str(number),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "denied\n"


def test_scm_rights_transfer_refused_while_plain_protocol_and_file_io_work(tmp_path: Path) -> None:
    """Kernel refuses an actual file-descriptor transfer without disabling data I/O."""
    result = _run(
        _INSTALL
        + """
import array
left, right = socket.socketpair()
descriptor = os.open(sys.argv[1], os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
try:
    assert os.write(descriptor, b"retained bytes") == 14
    os.fsync(descriptor)
    assert os.pread(descriptor, 14, 0) == b"retained bytes"
    left.sendall(b"request")
    assert right.recv(7) == b"request"
    try:
        left.sendmsg([b"target"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                   array.array("i", [descriptor]))])
    except PermissionError as error:
        assert error.errno == errno.EPERM
    else:
        raise RuntimeError("target descriptor escaped")
    right.settimeout(0.05)
    try:
        right.recv(1)
    except TimeoutError:
        pass
    else:
        raise RuntimeError("refused transfer still delivered data")
    print("retained")
finally:
    os.close(descriptor)
    left.close()
    right.close()
""",
        str(tmp_path / "target"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "retained\n"
    assert (tmp_path / "target").read_bytes() == b"retained bytes"


def test_filter_synchronizes_existing_threads_and_is_inherited_by_children() -> None:
    """A pre-existing thread and actual fork child cannot bypass synchronized rules."""
    result = _run(
        """
import errno, os, socket, threading
from synapse_channel.core.protected_write_descriptor_filter import (
    install_protected_writer_descriptor_filter,
)
ready, proceed = threading.Event(), threading.Event()
outcomes = []
def worker():
    left, right = socket.socketpair()
    ready.set()
    assert proceed.wait(5)
    try:
        left.sendmsg([b"x"])
    except PermissionError as error:
        outcomes.append(error.errno)
    finally:
        left.close()
        right.close()
thread = threading.Thread(target=worker)
thread.start()
assert ready.wait(5)
install_protected_writer_descriptor_filter(expected_abi="linux-x86_64")
proceed.set()
thread.join(5)
assert not thread.is_alive() and outcomes == [errno.EPERM]
child = os.fork()
if child == 0:
    left, right = socket.socketpair()
    try:
        left.sendmsg([b"x"])
    except PermissionError as error:
        os._exit(0 if error.errno == errno.EPERM else 2)
    os._exit(3)
assert os.waitpid(child, 0)[1] == 0
print("inherited")
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "inherited\n"


@pytest.mark.parametrize("abi", ["linux-aarch64", "darwin-x86_64", "", "Linux-x86_64"])
def test_wrong_enrollment_refuses_before_process_mutation(abi: str) -> None:
    """Unsupported enrollment refuses in the parent without changing its filter."""
    before = Path("/proc/self/status").read_text()
    with pytest.raises(ValueError, match="enrolled native"):
        install_protected_writer_descriptor_filter(expected_abi=abi)
    assert (
        Path("/proc/self/status").read_text().split("NoNewPrivs:")[1].splitlines()[0]
        == (before.split("NoNewPrivs:")[1].splitlines()[0])
    )


@pytest.mark.parametrize("number", [157, 317])
def test_existing_kernel_restriction_refuses_filter_installation(number: int) -> None:
    """A real earlier kernel filter denying prctl/seccomp cannot yield success."""
    result = _run(
        """
import ctypes, errno, sys
from synapse_channel.core.protected_write_descriptor_filter import (
    install_protected_writer_descriptor_filter,
)
class Instruction(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]
class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("code", ctypes.POINTER(Instruction))]
instructions = (Instruction * 4)(Instruction(0x20, 0, 0, 0),
    Instruction(0x15, 0, 1, int(sys.argv[1])),
    Instruction(0x06, 0, 0, 0x50000 | errno.EPERM), Instruction(0x06, 0, 0, 0x7fff0000))
program = Program(4, instructions)
libc = ctypes.CDLL(None, use_errno=True)
libc.prctl.argtypes = [ctypes.c_ulong] * 5
assert libc.prctl(38, 1, 0, 0, 0) == 0
assert libc.syscall(317, 1, 0, ctypes.byref(program)) == 0
try:
    install_protected_writer_descriptor_filter(expected_abi="linux-x86_64")
except OSError as error:
    assert error.errno == errno.EPERM
    print("refused")
else:
    raise RuntimeError("installation failure accepted")
""",
        str(number),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "refused\n"


def test_x32_encoding_kills_disposable_process() -> None:
    """An actual syscall with the x32 bit cannot bypass native descriptor rules."""
    result = _run(_INSTALL + "libc.syscall(0x40000000 | 46, -1, 0, 0)\n")
    assert result.returncode == -signal.SIGSYS


def test_incompatible_existing_thread_refuses_synchronization() -> None:
    """A real divergent thread filter returns EBUSY instead of usable protection."""
    result = _run(
        """
import ctypes, errno, pathlib, socket, threading
from synapse_channel.core.protected_write_descriptor_filter import (
    install_protected_writer_descriptor_filter,
)
ready, finish = threading.Event(), threading.Event()
errors = []
def divergent_thread():
    try:
        class Instruction(ctypes.Structure):
            _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                        ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]
        class Program(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ushort), ("code", ctypes.POINTER(Instruction))]
        instructions = (Instruction * 1)(Instruction(0x06, 0, 0, 0x7fff0000))
        program = Program(1, instructions)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_ulong] * 5
        assert libc.prctl(38, 1, 0, 0, 0) == 0
        assert libc.syscall(317, 1, 0, ctypes.byref(program)) == 0
        ready.set()
        assert finish.wait(5)
    except BaseException as error:
        errors.append(str(error))
        ready.set()
thread = threading.Thread(target=divergent_thread)
thread.start()
try:
    assert ready.wait(5) and not errors
    try:
        install_protected_writer_descriptor_filter(expected_abi="linux-x86_64")
    except OSError as error:
        assert error.errno == errno.EBUSY
        assert "synchronization failed" in str(error)
    else:
        raise RuntimeError("divergent thread accepted")
    status = pathlib.Path("/proc/self/status").read_text()
    assert status.split("NoNewPrivs:")[1].splitlines()[0].strip() == "1"
    left, right = socket.socketpair()
    try:
        assert left.sendmsg([b"unprotected"]) == 11
        assert right.recv(11) == b"unprotected"
    finally:
        left.close()
        right.close()
    print("launch refused")
finally:
    finish.set()
    thread.join(5)
    assert not thread.is_alive() and not errors
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "launch refused\n"


def test_compatibility_audit_architecture_kills_disposable_process() -> None:
    """A real int80 compatibility syscall cannot enter the native allow path."""
    result = _run(
        _INSTALL
        + """
import mmap
code = mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
code.write(bytes.fromhex("b814000000cd80c3"))
address = ctypes.addressof(ctypes.c_char.from_buffer(code))
ctypes.CFUNCTYPE(ctypes.c_int)(address)()
"""
    )
    assert result.returncode == -signal.SIGSYS
