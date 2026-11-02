# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual Linux mount-table contract tests
"""Parse live kernel tables and reject malformed tables or ambient writable mounts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.capable_bwrap import capable_bwrap
from synapse_channel.core.protected_write_kernel_boundary import (
    capture_protected_writer_kernel_evidence,
)
from synapse_channel.core.protected_write_mounts import (
    parse_protected_writer_mounts,
    verify_protected_writer_mounts,
)


def test_live_kernel_mount_table_binds_each_captured_root(tmp_path: Path) -> None:
    """Capture uses parsed full kernel bytes and a namespace-visible descriptor mount."""
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        evidence = capture_protected_writer_kernel_evidence({"memory": root})
        parsed = parse_protected_writer_mounts(Path("/proc/self/mountinfo").read_bytes())
        assert evidence.mounts == parsed
        assert evidence.roots[0].mount_id in {mount.mount_id for mount in parsed}
        assert len({mount.mount_id for mount in parsed}) == len(parsed)
        assert any(mount.target == "/" for mount in parsed)
        with pytest.raises(ValueError, match="unambiguous base|base mount is writable"):
            verify_protected_writer_mounts(parsed, writable_targets=frozenset())
    finally:
        os.close(root)


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "oversize",
        "encoding",
        "separator",
        "fields",
        "identity",
        "duplicate",
        "access",
        "options",
        "propagation",
        "duplicate_propagation",
        "escape",
        "relative",
        "parent_path",
        "controls",
        "blank",
    ],
)
def test_malformed_mount_bytes_cannot_become_kernel_evidence(case: str) -> None:
    """Corrupt actual proc-format bytes at the public parser boundary, never mount proof."""
    raw = Path("/proc/self/mountinfo").read_bytes()
    line = raw.splitlines()[0]
    left, right = line.split(b" - ", 1)
    fields = left.split(b" ")
    if case == "empty":
        raw = b""
    elif case == "oversize":
        raw = b"x" * (1_048_576 + 1)
    elif case == "encoding":
        raw = b"\xff"
    elif case == "separator":
        raw = line.replace(b" - ", b" ")
    elif case == "fields":
        raw = b"1 2 - " + right
    elif case == "duplicate":
        raw = line + b"\n" + line
    elif case == "blank":
        raw = b"\n"
    else:
        if case == "identity":
            fields[0] = b"0"
        elif case == "access":
            fields[5] = b"ro,rw"
        elif case == "options":
            fields[5] += b",rw"
        elif case == "propagation":
            fields[6:] = [b"shared:0"]
        elif case == "duplicate_propagation":
            fields[6:] = [b"shared:1", b"shared:1"]
        elif case == "escape":
            fields[4] = b"/bad\\777"
        elif case == "relative":
            fields[4] = b"relative"
        elif case == "parent_path":
            fields[4] = b"/a/../b"
        else:
            fields[4] = b"/a\\011b"
        raw = b" ".join(fields) + b" - " + right
    with pytest.raises(ValueError):
        parse_protected_writer_mounts(raw)


@pytest.mark.parametrize("case", ["missing_base", "duplicate_targets", "root_enrollment"])
def test_public_mount_verifier_refuses_missing_base_and_unsafe_enrollment(case: str) -> None:
    """A subset, repeated live records or root write permission never proves isolation."""
    mounts = parse_protected_writer_mounts(Path("/proc/self/mountinfo").read_bytes())
    writable: frozenset[str] = frozenset()
    if case == "missing_base":
        mounts = tuple(mount for mount in mounts if mount.target != "/")
    elif case == "duplicate_targets":
        mounts = (*mounts, mounts[0])
    else:
        writable = frozenset({"/"})
    with pytest.raises(ValueError):
        verify_protected_writer_mounts(mounts, writable_targets=writable)


def test_namespace_inode_root_label_is_bound_only_to_nsfs() -> None:
    """Parse the actual kernel namespace label without granting mount permission."""
    label = os.readlink("/proc/self/ns/mnt").encode("ascii")
    line = Path("/proc/self/mountinfo").read_bytes().splitlines()[0]
    left, right = line.split(b" - ", 1)
    fields, tail = left.split(b" "), right.split(b" ")
    fields[3] = label
    tail[0] = b"nsfs"
    record = b" ".join(fields) + b" - " + b" ".join(tail)
    parsed = parse_protected_writer_mounts(record)
    assert parsed[0].root == label.decode("ascii")
    tail[0] = b"ext4"
    with pytest.raises(ValueError, match="noncanonical kernel mount path"):
        parse_protected_writer_mounts(b" ".join(fields) + b" - " + b" ".join(tail))


_NATIVE_MOUNT_POLICY = """import ctypes,json,os,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[2]]
recorder=None
if sys.argv[4]=='coverage':
 import coverage
 recorder=coverage.Coverage(data_file='/data/.coverage',data_suffix=True,branch=True,source=['synapse_channel.core.protected_write_mounts'],config_file=False)
 recorder.start()
from synapse_channel.core.protected_write_mounts import (
 parse_protected_writer_mounts,verify_protected_writer_mounts,
)
class Attributes(ctypes.Structure):
 _fields_=[('attr_set',ctypes.c_uint64),('attr_clr',ctypes.c_uint64),('propagation',ctypes.c_uint64),('userns_fd',ctypes.c_uint64)]
mode=sys.argv[3]
libc=ctypes.CDLL(None,use_errno=True)
root=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
private=Attributes(0,0,1<<18,0)
if libc.syscall(
 ctypes.c_long(442),ctypes.c_int(root),ctypes.c_char_p(b''),ctypes.c_uint(0x9000),
 ctypes.byref(private),ctypes.c_size_t(ctypes.sizeof(private)),
): raise OSError(ctypes.get_errno(),'private recursive propagation setup refused')
if mode!='base-writable':
 readonly=Attributes(1,0,0,0)
 if libc.syscall(
  ctypes.c_long(442),ctypes.c_int(root),ctypes.c_char_p(b''),ctypes.c_uint(0x1000),
  ctypes.byref(readonly),ctypes.c_size_t(ctypes.sizeof(readonly)),
 ):
  raise OSError(ctypes.get_errno(),'private root readonly setup refused')
os.close(root)
proc=os.open('/proc',os.O_RDONLY|os.O_DIRECTORY)
readonly=Attributes(1,0,0,0)
if libc.syscall(
 ctypes.c_long(442),ctypes.c_int(proc),ctypes.c_char_p(b''),ctypes.c_uint(0x1000),
 ctypes.byref(readonly),ctypes.c_size_t(ctypes.sizeof(readonly)),
): raise OSError(ctypes.get_errno(),'private proc readonly setup refused')
os.close(proc)
descriptor=os.open('/data',os.O_RDONLY|os.O_DIRECTORY)
try:
 flags=2|4|(0 if mode=='data-executable' else 8)
 attributes=Attributes(flags,8 if mode=='data-executable' else 0,0,0)
 result=libc.syscall(ctypes.c_long(442),ctypes.c_int(descriptor),ctypes.c_char_p(b''),ctypes.c_uint(0x1000),ctypes.byref(attributes),ctypes.c_size_t(ctypes.sizeof(attributes)))
 if result: raise OSError(ctypes.get_errno(),'private test mount attributes refused')
 if mode=='shared-data':
  if libc.mount(None,b'/data',None,ctypes.c_ulong(1<<20),None):
   raise OSError(ctypes.get_errno(),'private propagation setup refused')
 mounts=parse_protected_writer_mounts(Path('/proc/self/mountinfo').read_bytes())
 expected={
  'base-writable':'base mount is writable','extra-writable':'writable mounts differ',
  'data-executable':'data-only restrictions','shared-data':'propagation is not private',
 }
 try: verify_protected_writer_mounts(mounts,writable_targets=frozenset({'/data'}))
 except ValueError as error:
  assert mode in expected and expected[mode] in str(error),(str(error),[m.target for m in mounts])
  print(json.dumps({'mode':mode,'refused':str(error)}))
 else:
  assert mode=='accepted'
  assert frozenset(m.target for m in mounts if 'rw' in m.options)==frozenset({'/data'})
  print(json.dumps({'mode':mode,'accepted':True}))
finally:
 os.close(descriptor)
 if recorder is not None:
  recorder.stop();recorder.save()
"""


@pytest.mark.parametrize(
    "mode", ["accepted", "base-writable", "extra-writable", "data-executable", "shared-data"]
)
def test_real_private_namespace_mount_policy(tmp_path: Path, mode: str) -> None:
    """Verify actual read-only/private data-only topology and genuine kernel counterexamples."""
    source = Path(__file__).resolve().parents[2]
    backing = tmp_path / "data"
    backing.mkdir()
    command = [
        capable_bwrap(),
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-net",
        "--unshare-pid",
        "--tmpfs",
        "/",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--ro-bind",
        str(source),
        str(source),
        "--proc",
        "/proc",
        "--bind",
        str(backing),
        "/data",
        "--cap-add",
        "CAP_SYS_ADMIN",
    ]
    for prefix in sorted({"/usr", str(Path(sys.base_prefix))}):
        command.extend(("--ro-bind", prefix, prefix))
    packages = Path(pytest.__file__).parents[1]
    command.extend(("--ro-bind", str(packages), str(packages)))
    if mode == "extra-writable":
        command.extend(("--tmpfs", "/extra"))
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR")
    command.extend(
        (
            str(Path(sys.executable).resolve()),
            "-I",
            "-B",
            "-S",
            "-c",
            _NATIVE_MOUNT_POLICY,
            str(source),
            str(packages),
            mode,
            "coverage" if coverage_dir else "plain",
        )
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["mode"] == mode
    assert observed.get("accepted", False) is (mode == "accepted")
    if coverage_dir:
        destination = Path(coverage_dir)
        assert destination.is_absolute() and destination.is_dir()
        for trace in backing.glob(".coverage.*"):
            (destination / trace.name).write_bytes(trace.read_bytes())
