# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual host guard for privileged mount restrictions
"""Exercise the public refusal without changing any host mount attributes."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.core.protected_write_mount_attributes import (
    apply_protected_writer_mount_restrictions,
    inspect_protected_writer_mount_targets,
    require_protected_writer_mount_abi,
)


@pytest.fixture(autouse=True)
def native_mount_profile() -> None:
    """Require the declared Linux ABI and unprivileged caller without skipping."""
    assert sys.platform == "linux"
    assert platform.machine() in {"x86_64", "aarch64"}
    assert ctypes.sizeof(ctypes.c_void_p) == 8
    assert os.geteuid() != 0


@pytest.mark.parametrize(
    ("observed_platform", "machine", "pointer_bytes", "accepted"),
    [
        ("linux", "x86_64", 8, True),
        ("linux", "aarch64", 8, True),
        ("win32", "x86_64", 8, False),
        ("linux", "ppc64le", 8, False),
        ("linux", "x86_64", 4, False),
    ],
)
def test_mount_abi_observation_contract(
    observed_platform: str, machine: str, pointer_bytes: int, accepted: bool
) -> None:
    """Validate value tuples without claiming native foreign-platform execution."""
    if accepted:
        require_protected_writer_mount_abi(observed_platform, machine, pointer_bytes)
    else:
        with pytest.raises(OSError) as error:
            require_protected_writer_mount_abi(observed_platform, machine, pointer_bytes)
        assert str(error.value) == "writer mount restrictions require supported native Linux"


def test_mount_restrictions_refuse_actual_unqualified_host_before_mutation() -> None:
    """Unsupported, non-root or actual host namespaces cannot reach mount mutation."""
    host = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    try:
        namespace = os.fstat(host)
        mounts = Path("/proc/self/mountinfo").read_bytes()
        # The privilege/namespace guard must run before even malformed target
        # declarations can cause a mount_setattr call or a legacy fallback.
        with pytest.raises(PermissionError, match="trusted root-stage child"):
            apply_protected_writer_mount_restrictions(
                host_mount_namespace=(namespace.st_dev, namespace.st_ino),
                writable_mount_descriptors={},
                writer_uid=os.geteuid() + 1,
                writer_gid=os.getegid(),
                shared_uid=os.geteuid(),
            )
        assert Path("/proc/self/mountinfo").read_bytes() == mounts
        current = os.fstat(host)
        assert (current.st_dev, current.st_ino) == (namespace.st_dev, namespace.st_ino)
    finally:
        os.close(host)


def test_target_inspection_refuses_real_subdirectory_before_mount_change(tmp_path: Path) -> None:
    """A directory FD cannot authorize attributes for its containing filesystem."""
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        before = Path("/proc/self/mountinfo").read_bytes()
        with pytest.raises(ValueError, match="exact mountpoint"):
            inspect_protected_writer_mount_targets(
                {str(tmp_path): descriptor}, owner_uid=os.geteuid(), owner_gid=os.getegid()
            )
        assert Path("/proc/self/mountinfo").read_bytes() == before
        assert os.fstat(descriptor).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    "case",
    [
        "inspect",
        "wrong-mode",
        "wrong-owner",
        "unsafe-ancestor",
        "bad-owner",
        "empty-targets",
        "invalid-target",
        "bad-host-pin",
        "current-namespace",
        "threaded",
        "root-writer",
        "shared-writer",
        "bad-gid",
        "float-writer",
        "wrong-writer-owner",
    ],
)
def test_actual_private_mount_observation_and_root_stage_refusals(
    tmp_path: Path, case: str
) -> None:
    """Use actual namespace metadata; invalid declared inputs grant no host authority."""
    assert os.geteuid() != 0
    host_root = Path("/").stat()
    source = Path(__file__).resolve().parents[2]
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    host_descriptor = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    script = """import json,os,sys,threading
from pathlib import Path
source,site,case,host_device,host_inode,coverage_dir=sys.argv[1:]
sys.path[:0]=[source+'/src',source,site]
recorder=None
if coverage_dir:
 import coverage
 recorder=coverage.Coverage(
  data_file=str(Path(coverage_dir)/'.coverage'),data_suffix=True,branch=True,
  source_pkgs=['synapse_channel.core.protected_write_mount_attributes'],config_file=False)
 recorder.start()
from synapse_channel.core.protected_write_mount_attributes import (
 apply_protected_writer_mount_restrictions,inspect_protected_writer_mount_targets)
assert os.getresuid()==(0,0,0)
host=(int(host_device),int(host_inode))
actual=Path('/proc/self/ns/mnt').stat()
assert (actual.st_dev,actual.st_ino)!=host
descriptor=os.open('/data',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
released=threading.Event();started=threading.Event();thread=None
expected=''
try:
 if case in {
  'inspect','wrong-mode','wrong-owner','unsafe-ancestor',
  'bad-owner','empty-targets','invalid-target'
 }:
  targets={'/data':descriptor};uid=0
  if case=='wrong-mode':os.chmod('/data',0o755)
  if case=='wrong-owner':uid=1
  if case=='unsafe-ancestor':os.chmod('/',0o777)
  if case=='bad-owner':uid=True
  if case=='empty-targets':targets={}
  if case=='invalid-target':targets={'../data':descriptor}
  if case in {'wrong-mode','wrong-owner'}:expected='private ownership differs'
  elif case=='unsafe-ancestor':expected='ancestors must remain root owned'
  elif case=='bad-owner':expected='observation credentials'
  elif case=='empty-targets':expected='writable mount count'
  elif case=='invalid-target':expected='writable mount enrollment'
  def exercise():return inspect_protected_writer_mount_targets(targets,owner_uid=uid,owner_gid=0)
 else:
  pin=host;uid=1;gid=1
  if case=='bad-host-pin':pin=(host[0],0);expected='host namespace identity'
  if case=='current-namespace':
   pin=(actual.st_dev,actual.st_ino);expected='retained host namespace'
  if case=='threaded':
   def wait():started.set();released.wait(10)
   thread=threading.Thread(target=wait);thread.start()
   assert started.wait(2);expected='single-threaded root stage'
  if case=='root-writer':uid=0
  if case=='shared-writer':uid=1000
  if case=='bad-gid':gid=0
  if case=='float-writer':uid=1.5
  if case in {'root-writer','shared-writer','bad-gid','float-writer'}:
   expected='dedicated writer mount credentials'
  if case=='wrong-writer-owner':expected='private ownership differs'
  def exercise():return apply_protected_writer_mount_restrictions(
   host_mount_namespace=pin,writable_mount_descriptors={'/data':descriptor},
   writer_uid=uid,writer_gid=gid,shared_uid=1000)
 before=Path('/proc/self/mountinfo').read_bytes()
 if case=='inspect':
  result=exercise();root=result.roots[0]
  assert root.name=='/data'and root.device==os.fstat(descriptor).st_dev
  assert root.inode==os.fstat(descriptor).st_ino
 else:
  try:exercise()
  except ValueError as error:assert expected in str(error),(expected,str(error))
  else:raise AssertionError('invalid mount declaration accepted')
 assert Path('/proc/self/mountinfo').read_bytes()==before
 print(json.dumps({'case':case,'native_private_observation':True,'host_qualification':False}))
finally:
 released.set()
 if thread is not None:thread.join(2);assert not thread.is_alive()
 os.close(descriptor)
 if recorder is not None:recorder.stop();recorder.save()
"""
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    command = [
        "/usr/bin/bwrap",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-net",
        "--new-session",
        "--tmpfs",
        "/",
    ]
    for directory in sorted(
        {"/usr", "/media", "/home", "/lib", "/lib64", "/sys", str(Path(sys.base_prefix))}
    ):
        command.extend(("--ro-bind", directory, directory))
    command.extend(("--proc", "/proc", "--bind", str(data), "/data"))
    if coverage_dir:
        assert Path(coverage_dir).is_absolute() and Path(coverage_dir).is_dir()
        command.extend(("--bind", coverage_dir, coverage_dir))
    try:
        host = os.fstat(host_descriptor)
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
                case,
                str(host.st_dev),
                str(host.st_ino),
                coverage_dir,
            )
        )
        child = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        assert child.returncode == 0, child.stderr
        assert json.loads(child.stdout.splitlines()[-1]) == {
            "case": case,
            "native_private_observation": True,
            "host_qualification": False,
        }
        current = os.fstat(host_descriptor)
        assert (current.st_dev, current.st_ino) == (host.st_dev, host.st_ino)
        current_root = Path("/").stat()
        assert (current_root.st_dev, current_root.st_ino, current_root.st_mode) == (
            host_root.st_dev,
            host_root.st_ino,
            host_root.st_mode,
        )
    finally:
        os.close(host_descriptor)
