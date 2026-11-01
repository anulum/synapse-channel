# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live Linux writer boundary evidence
"""Capture real kernel facts and refuse this shared-UID agent as a writer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from fixtures.protected_write_subtree_linux import delegated_subtree as delegated_subtree
from synapse_channel.core.protected_write_kernel_boundary import (
    ProtectedWriterKernelPolicy,
    capture_protected_writer_kernel_evidence,
    verify_protected_writer_kernel_boundary,
)


def test_live_kernel_capture_refuses_shared_uid_writer(tmp_path: Path) -> None:
    """Actual process and root descriptors cannot masquerade as the pilot UID."""
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        evidence = capture_protected_writer_kernel_evidence({"memory": descriptor})
        assert evidence.uids[:3] == os.getresuid()
        assert evidence.gids[:3] == os.getresgid()
        assert evidence.groups == tuple(os.getgroups())
        assert len(evidence.mountinfo_sha256) == 64
        assert evidence.roots[0].mount_id > 0
        root = tmp_path.stat()
        assert (evidence.roots[0].device, evidence.roots[0].inode) == (
            root.st_dev,
            root.st_ino,
        )
        host = evidence.mount_namespace
        with pytest.raises(ValueError, match="invalid dedicated writer kernel policy"):
            ProtectedWriterKernelPolicy(
                writer_uid=os.geteuid(),
                writer_gid=os.getegid(),
                shared_uid=os.geteuid(),
                host_mount_namespace=host,
                writer_mount_namespace=(host[0], host[1] + 1),
                mountinfo_sha256=evidence.mountinfo_sha256,
                roots=evidence.roots,
                writable_mount_targets=frozenset(),
                writer_subtree_identity=(
                    evidence.process_subtree.device,
                    evidence.process_subtree.inode,
                ),
            )
        policy = ProtectedWriterKernelPolicy(
            writer_uid=os.geteuid() + 1,
            writer_gid=os.getegid() + 1,
            shared_uid=os.geteuid(),
            host_mount_namespace=host,
            writer_mount_namespace=(host[0], host[1] + 1),
            mountinfo_sha256=evidence.mountinfo_sha256,
            roots=evidence.roots,
            writable_mount_targets=frozenset(),
            writer_subtree_identity=(
                evidence.process_subtree.device,
                evidence.process_subtree.inode,
            ),
        )
        with pytest.raises(ValueError, match="kernel boundary differs"):
            verify_protected_writer_kernel_boundary(evidence, policy)
        assert capture_protected_writer_kernel_evidence({"memory": descriptor}) == evidence
    finally:
        os.close(descriptor)


def test_kernel_capture_requires_real_open_directory(tmp_path: Path) -> None:
    """Missing and non-directory root descriptors refuse before enrollment."""
    with pytest.raises(ValueError, match="requires enrolled root descriptors"):
        capture_protected_writer_kernel_evidence({})
    file = tmp_path / "leaf"
    file.write_bytes(b"not a directory")
    descriptor = os.open(file, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(ValueError, match="not a directory"):
            capture_protected_writer_kernel_evidence({"memory": descriptor})
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("oversized-status", "exceeds read budget"),
        ("missing-status", "incomplete writer process status"),
        ("duplicate-status", "duplicate writer process status"),
        ("short-uids", "invalid writer principal status"),
        ("nonnumeric-uids", "invalid writer principal status"),
        ("missing-mount-id", "mount ID is unavailable"),
        ("zero-mount-id", "mount ID is unavailable"),
        ("duplicate-mount-id", "mount ID is unavailable"),
        ("inconsistent-uids", "principal evidence changed"),
        ("inconsistent-groups", "supplementary groups changed"),
    ],
)
def test_capture_refuses_inconsistent_private_proc_mounts(
    tmp_path: Path, case: str, expected: str
) -> None:
    """Read controlled inconsistent files through the public capture API.

    Only a disposable private mount namespace receives the bind. These are
    malformed-file counterexamples, not observations of changed host principals.
    """
    script = """import ctypes,json,os,sys
from pathlib import Path
source,site,backing,case,expected,coverage_dir=sys.argv[1:]
sys.path[:0]=[source+'/src',site]
descriptor=os.open(backing,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
target='/proc/self/status'
raw=Path(target).read_bytes()
if case=='oversized-status':raw=b'x'*65537
elif case=='missing-status':raw=b'Uid: 0 0 0 0'+bytes([10])
elif case=='duplicate-status':raw+=b'Uid: 0 0 0 0'+bytes([10])
elif case in {'short-uids','nonnumeric-uids','inconsistent-uids','inconsistent-groups'}:
 fields={'short-uids':('Uid',b'0 0 0'),'nonnumeric-uids':('Uid',b'0 0 x 0'),
  'inconsistent-uids':('Uid',b'1 1 1 1'),'inconsistent-groups':('Groups',b'999999')}
 name,value=fields[case]
 raw=bytes([10]).join(
  name.encode()+b': '+value if line.startswith(name.encode()+b':') else line
  for line in raw.splitlines())+bytes([10])
else:
 target='/proc/self/fdinfo/'+str(descriptor)
 raw={'missing-mount-id':b'pos: 0'+bytes([10]),
  'zero-mount-id':b'mnt_id: 0'+bytes([10]),
  'duplicate-mount-id':b'mnt_id: 1'+bytes([10])+b'mnt_id: 2'+bytes([10])}[case]
replacement=Path(backing)/'controlled-proc-bytes'
if 'mount-id' in case:
 replacement.mkdir();(replacement/str(descriptor)).write_bytes(raw)
 target='/proc/self/fdinfo'
else:replacement.write_bytes(raw)
libc=ctypes.CDLL(None,use_errno=True)
if libc.mount(
 ctypes.c_char_p(os.fsencode(replacement)),ctypes.c_char_p(os.fsencode(target)),
 ctypes.c_void_p(),ctypes.c_ulong(4096),ctypes.c_void_p()):
 raise OSError(ctypes.get_errno(),'private proc bind refused')
recorder=None
if coverage_dir:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(coverage_dir)/'.coverage'),
  data_suffix=True,branch=True,source_pkgs=['synapse_channel.core.protected_write_kernel_boundary'],
  config_file=False)
 recorder.start()
from synapse_channel.core.protected_write_kernel_boundary import (
 capture_protected_writer_kernel_evidence)
try:
 try:capture_protected_writer_kernel_evidence({'data':descriptor})
 except ValueError as error:assert expected in str(error),(case,str(error))
 else:raise AssertionError('inconsistent private proc bytes accepted')
 print(json.dumps({'case':case,'public_capture_refused':True,'host_principal_changed':False}))
finally:
 os.close(descriptor)
 if recorder is not None:recorder.stop();recorder.save()
"""
    source = Path(__file__).resolve().parents[2]
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    command = [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-net",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        str(tmp_path),
        str(tmp_path),
        "--cap-add",
        "CAP_SYS_ADMIN",
    ]
    if coverage_dir:
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(
        (
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            script,
            str(source),
            str(Path(pytest.__file__).parents[1]),
            str(tmp_path),
            case,
            expected,
            coverage_dir,
        )
    )
    namespace = Path("/proc/self/ns/mnt").stat()
    result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "case": case,
        "public_capture_refused": True,
        "host_principal_changed": False,
    }
    current = Path("/proc/self/ns/mnt").stat()
    assert (current.st_dev, current.st_ino) == (namespace.st_dev, namespace.st_ino)


def test_kernel_capture_refuses_changed_root_descriptor(tmp_path: Path) -> None:
    """Actual concurrent dup2 replacement refuses an inconsistent root identity."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    original = os.open(first, os.O_RDONLY | os.O_DIRECTORY)
    alternate = os.open(second, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = os.dup(original)
    stop, started = threading.Event(), threading.Event()

    def replace_descriptor() -> None:
        """Alternate two actual directory descriptors until capture refuses."""
        started.set()
        while not stop.is_set():
            os.dup2(alternate, descriptor)
            os.dup2(original, descriptor)

    changer = threading.Thread(target=replace_descriptor)
    changer.start()
    try:
        assert started.wait(timeout=5)
        refused = False
        deadline = time.monotonic() + 5
        while not refused:
            assert time.monotonic() < deadline, "descriptor race was not observed before deadline"
            try:
                capture_protected_writer_kernel_evidence({"memory": descriptor})
            except ValueError as error:
                assert str(error) == "writer root descriptor changed during capture"
                refused = True
        assert refused
    finally:
        stop.set()
        changer.join(timeout=5)
        assert not changer.is_alive()
        os.close(descriptor)
        os.close(alternate)
        os.close(original)


def test_kernel_capture_refuses_other_retained_subtree(
    tmp_path: Path,
    delegated_subtree: Path,
) -> None:
    """Full kernel capture cannot substitute an empty group's membership."""
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    subtree = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError, match="belongs to another process subtree"):
            capture_protected_writer_kernel_evidence({"memory": root}, subtree_descriptor=subtree)
    finally:
        os.close(subtree)
        os.close(root)


@pytest.mark.parametrize(
    "case",
    [
        "missing-host",
        "missing-writer",
        "mutable-host",
        "boolean-writer-device",
        "negative-host-inode",
        "zero-writer-inode",
        "identical-namespace",
    ],
)
def test_kernel_policy_refuses_malformed_namespace_enrollment(tmp_path: Path, case: str) -> None:
    """Invalid operator declarations cannot omit or coerce the namespace binding.

    Other metadata comes from actual current kernel capture. The proposed distinct
    principal is a declaration only; no account or accepted writer is created.
    """
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        evidence = capture_protected_writer_kernel_evidence({"memory": descriptor})
        host: object = evidence.mount_namespace
        writer: object = evidence.mount_namespace
        if case == "missing-host":
            host = None
        elif case == "missing-writer":
            writer = None
        elif case == "mutable-host":
            host = list(evidence.mount_namespace)
        elif case == "boolean-writer-device":
            writer = (True, evidence.mount_namespace[1])
        elif case == "negative-host-inode":
            host = (evidence.mount_namespace[0], -1)
        elif case == "zero-writer-inode":
            writer = (evidence.mount_namespace[0], 0)
        with pytest.raises(ValueError, match="invalid dedicated writer kernel policy"):
            ProtectedWriterKernelPolicy(
                writer_uid=os.geteuid() + 1,
                writer_gid=os.getegid() + 1,
                shared_uid=os.geteuid(),
                host_mount_namespace=cast(tuple[int, int], host),
                writer_mount_namespace=cast(tuple[int, int], writer),
                mountinfo_sha256=evidence.mountinfo_sha256,
                roots=evidence.roots,
                writable_mount_targets=frozenset(),
                writer_subtree_identity=(
                    evidence.process_subtree.device,
                    evidence.process_subtree.inode,
                ),
            )
    finally:
        os.close(descriptor)
