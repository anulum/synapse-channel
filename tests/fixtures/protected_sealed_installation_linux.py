# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real sealed installation descriptor lifetime tests
"""Exercise native kernel sealing and descriptor cleanup without claiming launch isolation."""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

import synapse_channel
from synapse_channel.core.protected_write_sealed_installation import (
    retain_sealed_installation_artifacts,
)
from test_protected_write_installation_set import native_pins


def test_verified_bytes_remain_sealed_through_duplicate_descriptors() -> None:
    """Actual captured root bytes refuse writes, resizing and seal replacement."""
    pins = native_pins()
    with retain_sealed_installation_artifacts(
        pins,
        expected_paths=frozenset(pin.path for pin in pins),
        private_paths=frozenset(),
        max_total_bytes=sum(pin.max_bytes for pin in pins),
    ) as descriptors:
        for path, descriptor in descriptors.items():
            content = Path(path).read_bytes()
            assert os.pread(descriptor, len(content) + 1, 0) == content
            assert fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
            assert os.fstat(descriptor).st_mode & 0o777 == 0o400
            required = (
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
            )
            assert fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & required == required
            duplicate = os.dup(descriptor)
            try:
                with pytest.raises(OSError):
                    os.pwrite(duplicate, b"x", 0)
                for size in (0, len(content) + 1):
                    with pytest.raises(OSError):
                        os.ftruncate(duplicate, size)
                with pytest.raises(OSError):
                    fcntl.fcntl(duplicate, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE)
            finally:
                os.close(duplicate)
        borrowed = tuple(descriptors.values())
    for descriptor in borrowed:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_explicit_child_handover_reads_exact_sealed_bytes() -> None:
    """A real disposable reader receives only its explicitly passed byte descriptor."""
    pins = native_pins()[:1]
    with retain_sealed_installation_artifacts(
        pins,
        expected_paths=frozenset(pin.path for pin in pins),
        private_paths=frozenset(),
        max_total_bytes=sum(pin.max_bytes for pin in pins),
    ) as descriptors:
        descriptor = descriptors[pins[0].path]
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-S",
                "-c",
                "import os,sys,hashlib; "
                "print(hashlib.sha256(os.pread(int(sys.argv[1]),4096,0)).hexdigest())",
                str(descriptor),
            ],
            pass_fds=(descriptor,),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert result.stdout.strip() == pins[0].sha256


def test_exceptional_consumer_exit_closes_owned_descriptors() -> None:
    """A consumer exception never strands its supervisor-owned descriptor set."""
    pins = native_pins()
    borrowed: tuple[int, ...] = ()
    with pytest.raises(RuntimeError, match="consumer stopped"):
        with retain_sealed_installation_artifacts(
            pins,
            expected_paths=frozenset(pin.path for pin in pins),
            private_paths=frozenset(),
            max_total_bytes=sum(pin.max_bytes for pin in pins),
        ) as descriptors:
            borrowed = tuple(descriptors.values())
            raise RuntimeError("consumer stopped")
    assert borrowed
    for descriptor in borrowed:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_real_file_size_limit_refuses_partial_memory_file() -> None:
    """A disposable process's real RLIMIT_FSIZE forces a partial write and cleanup."""
    code = """
import os, resource
from synapse_channel.core.protected_write_sealed_installation import (
    retain_sealed_installation_artifacts,
)
from synapse_channel.core.protected_write_installation_set import ProtectedInstallArtifactPin
import hashlib
path = "/etc/issue"
content = open(path, "rb").read()
pin = ProtectedInstallArtifactPin(path, hashlib.sha256(content).hexdigest(), len(content))
before = set(os.listdir("/proc/self/fd"))
previous_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
resource.setrlimit(resource.RLIMIT_FSIZE, (1, previous_limit[1]))
try:
    with retain_sealed_installation_artifacts(
        (pin,), expected_paths=frozenset({path}), private_paths=frozenset(), max_total_bytes=4096,
    ):
        raise RuntimeError("partial bytes were published")
except ValueError as error:
    assert str(error) == "sealed installation artifact write was incomplete"
else:
    raise RuntimeError("partial write was accepted")
finally:
    resource.setrlimit(resource.RLIMIT_FSIZE, previous_limit)
assert set(os.listdir("/proc/self/fd")) == before
print("PARTIAL_REFUSED")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [
                    str(Path(synapse_channel.__file__).parent.parent),
                    os.environ.get("PYTHONPATH", ""),
                ]
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "PARTIAL_REFUSED\n"


def test_exec_cannot_implicitly_inherit_installation_descriptor() -> None:
    """A real exec with close_fds disabled still loses the kernel CLOEXEC descriptor."""
    pins = native_pins()[:1]
    code = """
import errno, os, sys
try:
    os.fstat(int(sys.argv[1]))
except OSError as error:
    assert error.errno == errno.EBADF
    print("CLOSED")
else:
    raise RuntimeError("installation descriptor leaked across exec")
"""
    with retain_sealed_installation_artifacts(
        pins,
        expected_paths=frozenset(pin.path for pin in pins),
        private_paths=frozenset(),
        max_total_bytes=sum(pin.max_bytes for pin in pins),
    ) as descriptors:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-S", "-c", code, str(descriptors[pins[0].path])],
            close_fds=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "CLOSED\n"
