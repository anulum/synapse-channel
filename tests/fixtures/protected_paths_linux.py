# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — native Linux retained-path qualification
"""Real kernel resolution, procfs boundaries and independent file mutators.

Collected explicitly on every Linux CI interpreter, including accumulated
coverage on Python 3.12. No native case is replaced by a mock or platform skip.
"""

from __future__ import annotations

import errno
import os
import select
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synapse_channel.core.protected_write_descriptors import hold_protected_path
from synapse_channel.core.protected_write_inspection import (
    ProtectedFileInspection,
    inspect_protected_file,
)
from synapse_channel.core.protected_write_openat2 import verify_protected_openat2_path

_MUTATOR = """import ctypes, os, pathlib, select, sys, time
target = pathlib.Path(sys.argv[1])
size = int(sys.argv[2])
mode = sys.argv[3]
fd = os.open(target, os.O_RDWR)
try:
    if mode in {'grow', 'parent'}:
        libc = ctypes.CDLL(None, use_errno=True)
        notify = libc.inotify_init1(os.O_CLOEXEC)
        assert notify >= 0
        assert libc.inotify_add_watch(notify, os.fsencode(target), 1) >= 0
        print('ready', flush=True)
        try:
            assert select.select([notify], [], [], 5)[0]
            os.read(notify, 4096)
            if mode == 'grow':
                os.ftruncate(fd, size + 8192)
            else:
                target.parent.rename(target.parent.with_name('detached'))
                target.parent.mkdir(mode=0o700)
                target.write_bytes(b'substituted')
        finally:
            os.close(notify)
    else:
        assert mode in {'same-size', 'content', 'metadata'}
        print('ready', flush=True)
        end = time.monotonic() + 8
        index = 0
        while time.monotonic() < end:
            if mode == 'metadata':
                os.utime(fd)
            else:
                os.pwrite(fd, b'y' if index % 2 else b'z', 0)
                index += 1
finally:
    os.close(fd)
"""


@contextmanager
def _mutator(target: Path, mode: str) -> Iterator[subprocess.Popen[str]]:
    """Own one finite independent child that mutates actual filesystem objects."""
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            _MUTATOR,
            str(target),
            str(target.stat().st_size),
            mode,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 5)[0], "native mutator did not become ready"
        assert child.stdout.readline().strip() == "ready"
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
        _output, error = child.communicate(timeout=5)
        assert child.returncode in (0, -signal.SIGTERM) and not error


@pytest.mark.parametrize("change", ["grow", "same-size"])
def test_real_concurrent_file_change_is_refused(tmp_path: Path, change: str) -> None:
    """A separate live process changes real bytes during bounded inspection."""
    target = tmp_path / "record"
    size = (64 if change == "grow" else 8) * 1024 * 1024
    target.write_bytes(b"x" * size)
    with _mutator(target, change) as child:
        with pytest.raises(ValueError, match="grew|changed|exceeds"):
            _inspect(tmp_path, budget=size + 4096)
        assert child.poll() is None or child.returncode == 0


@pytest.mark.parametrize("change", ["content", "grow", "parent", "metadata"])
def test_capture_refuses_native_mutation_after_descriptor_retention(
    tmp_path: Path, change: str
) -> None:
    """Native content, topology and metadata races cannot publish captured bytes."""
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    target = parent / "record"
    target.write_bytes(b"x" * (64 * 1024 * 1024))
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        info = os.fstat(root)
        expected = inspect_protected_file(
            root,
            "parent/record",
            root_identity=(info.st_dev, info.st_ino),
            max_bytes=target.stat().st_size,
        )
        with (
            hold_protected_path(
                root,
                expected,
                owner_uid=os.getuid(),
                max_bytes=expected.size_bytes,
                require_openat2=True,
            ) as held,
            _mutator(target, change) as child,
        ):
            with pytest.raises(ValueError, match="grew|changed|mismatch"):
                held.read_bytes()
            assert child.poll() is None or child.returncode == 0
    finally:
        os.close(root)


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_kernel_resolution_refuses_an_existing_leaf_under_an_absent_expectation(
    tmp_path: Path, kind: str
) -> None:
    """A missing-descriptor expectation cannot authenticate an actual existing leaf."""
    target = tmp_path / "present"
    if kind == "file":
        target.write_bytes(b"present")
    elif kind == "directory":
        target.mkdir(mode=0o700)
    else:
        (tmp_path / "real").write_bytes(b"present")
        target.symlink_to("real")
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    before = len(os.listdir("/proc/self/fd"))
    try:
        for _ in range(3):
            if kind == "symlink":
                with pytest.raises(OSError) as refusal:
                    verify_protected_openat2_path(root, "present", root, None)
                assert refusal.value.errno == errno.ELOOP
            else:
                with pytest.raises(ValueError, match="absent leaf appeared"):
                    verify_protected_openat2_path(root, "present", root, None)
            assert len(os.listdir("/proc/self/fd")) == before
        assert os.fstat(root).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(root)


def test_kernel_resolution_refuses_a_disappeared_retained_leaf(tmp_path: Path) -> None:
    """A still-open inode cannot stand in for a leaf removed from its enrolled parent."""
    target = tmp_path / "record"
    target.write_bytes(b"retained")
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    leaf = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    try:
        target.unlink()
        with pytest.raises(FileNotFoundError):
            verify_protected_openat2_path(root, "record", root, leaf)
        assert os.pread(leaf, 64, 0) == b"retained"
    finally:
        os.close(leaf)
        os.close(root)


def test_oversized_descriptor_integers_cannot_wrap_to_real_open_files(tmp_path: Path) -> None:
    """The Python boundary rejects C-int aliases of actual borrowed descriptors."""
    target = tmp_path / "record"
    target.write_bytes(b"known")
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    leaf = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    before = len(os.listdir("/proc/self/fd"))
    try:
        for root_arg, parent_arg, leaf_arg in (
            (root + 2**32, root, leaf),
            (root, root + 2**32, leaf),
            (root, root, leaf + 2**32),
        ):
            with pytest.raises(ValueError, match="invalid protected openat2"):
                verify_protected_openat2_path(root_arg, "record", parent_arg, leaf_arg)
            assert len(os.listdir("/proc/self/fd")) == before
        assert os.pread(leaf, 64, 0) == b"known"
    finally:
        os.close(leaf)
        os.close(root)


def _inspect(root: Path, relative: str = "record", budget: int = 64) -> ProtectedFileInspection:
    """Inspect a real borrowed directory without retaining an external path handle."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        return inspect_protected_file(
            descriptor, relative, root_identity=(info.st_dev, info.st_ino), max_bytes=budget
        )
    finally:
        os.close(descriptor)


def test_live_openat2_accepts_exact_retained_parent_and_leaf(tmp_path: Path) -> None:
    """The real kernel opens the exact enrolled parent and existing object."""
    parent = tmp_path / "records"
    parent.mkdir()
    target = parent / "note"
    target.write_bytes(b"before")
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    target_fd = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    try:
        verify_protected_openat2_path(root_fd, "records/note", parent_fd, target_fd)
        verify_protected_openat2_path(root_fd, "records/absent", parent_fd, None)
        with pytest.raises(ValueError, match="parent identity changed"):
            verify_protected_openat2_path(root_fd, "records/note", root_fd, target_fd)
        with pytest.raises(ValueError, match="object identity changed"):
            verify_protected_openat2_path(root_fd, "records/note", parent_fd, parent_fd)
    finally:
        os.close(target_fd)
        os.close(parent_fd)
        os.close(root_fd)


def test_live_openat2_refuses_symlink_escape_and_bad_path(tmp_path: Path) -> None:
    """A linked ancestor cannot substitute for the enrolled parent."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "note").write_bytes(b"before")
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    parent_fd = os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for path in ("", "/real/note", "../real/note", "real//note", "real/./note"):
            with pytest.raises(ValueError, match="invalid protected openat2"):
                verify_protected_openat2_path(root_fd, path, parent_fd, None)
        with pytest.raises(OSError) as linked:
            verify_protected_openat2_path(root_fd, "link/note", parent_fd, None)
        assert linked.value.errno in {errno.ELOOP, errno.EXDEV}
        with pytest.raises(OSError) as missing:
            verify_protected_openat2_path(root_fd, "missing/note", parent_fd, None)
        assert missing.value.errno == errno.ENOENT
    finally:
        os.close(parent_fd)
        os.close(root_fd)


def test_live_openat2_refuses_mount_crossing() -> None:
    """The host's proc mount is outside a retained root mount."""
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(OSError) as crossing:
            verify_protected_openat2_path(root_fd, "proc/self", root_fd, None)
        assert crossing.value.errno == errno.EXDEV
    finally:
        os.close(root_fd)


def test_directory_device_mismatch_refused() -> None:
    """Reject the actual procfs directory across the borrowed root device boundary."""
    root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    identity = os.fstat(root)
    try:
        assert os.stat("/proc").st_dev != identity.st_dev
        with pytest.raises(ValueError, match="directory crosses"):
            inspect_protected_file(
                root,
                "proc",
                root_identity=(identity.st_dev, identity.st_ino),
                max_bytes=64,
                allow_directory=True,
            )
        assert os.fstat(root) == identity
    finally:
        os.close(root)


def test_changed_device_metadata_is_refused() -> None:
    """The actual procfs mount fails the portable device-boundary check."""
    root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    info = os.fstat(root)
    try:
        with pytest.raises(ValueError, match="device boundary"):
            inspect_protected_file(
                root, "proc/version", root_identity=(info.st_dev, info.st_ino), max_bytes=64
            )
    finally:
        os.close(root)


@pytest.mark.parametrize("kind", ["file", "directory", "absent", "nested_absent"])
def test_kernel_restricted_inspection_matches_real_descriptor_states(
    tmp_path: Path, kind: str
) -> None:
    """Linux inspection accepts exact file, directory and absent-leaf states."""
    folder = tmp_path / "nested"
    folder.mkdir(mode=0o700)
    target = tmp_path / "record"
    if kind == "file":
        target.write_bytes(b"known bytes")
        target.chmod(0o600)
    elif kind == "directory":
        target.mkdir(mode=0o700)
    relative = "nested/missing" if kind == "nested_absent" else "record"
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    info = os.fstat(root)
    try:
        expected = inspect_protected_file(
            root,
            relative,
            root_identity=(info.st_dev, info.st_ino),
            max_bytes=64,
            allow_directory=True,
        )
        actual = inspect_protected_file(
            root,
            relative,
            root_identity=(info.st_dev, info.st_ino),
            max_bytes=64,
            allow_directory=True,
            require_openat2=True,
        )
        assert actual == expected
        assert (
            actual.file_identity is None if "absent" in kind else actual.file_identity is not None
        )
    finally:
        os.close(root)


@pytest.mark.parametrize("relative", ["proc/version", "proc"])
def test_kernel_restricted_inspection_refuses_live_mount_crossing(relative: str) -> None:
    """Real Linux procfs mount crossings refuse before file or directory inspection."""
    import errno

    root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    info = os.fstat(root)
    before = len(os.listdir("/proc/self/fd"))
    try:
        for _ in range(3):
            with pytest.raises(OSError) as failure:
                inspect_protected_file(
                    root,
                    relative,
                    root_identity=(info.st_dev, info.st_ino),
                    max_bytes=64,
                    allow_directory=True,
                    require_openat2=True,
                )
            assert failure.value.errno == errno.EXDEV
            assert len(os.listdir("/proc/self/fd")) == before
    finally:
        os.close(root)
