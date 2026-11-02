# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real cross-filesystem descriptor capture races
"""Reject genuine descriptor replacement across different kernel filesystems."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fixtures.capable_bwrap import capable_bwrap
from fixtures.protected_write_subtree_linux import ManagedProcess
from fixtures.protected_write_subtree_linux import delegated_subtree as delegated_subtree
from fixtures.protected_write_subtree_linux import managed_process as managed_process
from synapse_channel.core.protected_write_kernel_boundary import (
    capture_protected_writer_kernel_evidence,
)
from synapse_channel.core.protected_write_subtree import inspect_protected_writer_subtree

pytestmark = pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")


def test_mount_capture_refuses_cross_filesystem_descriptor_race(tmp_path: Path) -> None:
    """Real dup2 across mount devices cannot produce a coherent root identity."""
    original = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    alternate = os.open("/proc", os.O_RDONLY | os.O_DIRECTORY)
    descriptor = os.dup(original)
    assert os.fstat(original).st_dev != os.fstat(alternate).st_dev
    stop = threading.Event()

    def replace_descriptor() -> None:
        """Alternate actual retained descriptors until the observer stops."""
        while not stop.is_set():
            os.dup2(alternate, descriptor)
            os.dup2(original, descriptor)

    changer = threading.Thread(target=replace_descriptor)
    changer.start()
    try:
        refused = False
        deadline = time.monotonic() + 10
        while not refused:
            assert time.monotonic() < deadline, "mount descriptor race exceeded its deadline"
            try:
                capture_protected_writer_kernel_evidence({"memory": descriptor})
            except ValueError as error:
                assert str(error) in {
                    "writer root mount is outside current namespace",
                    "writer root descriptor changed during capture",
                }
                if str(error) == "writer root mount is outside current namespace":
                    refused = True
        assert refused
    finally:
        stop.set()
        changer.join(timeout=5)
        assert not changer.is_alive()
        os.close(descriptor)
        os.close(alternate)
        os.close(original)


def test_events_capture_refuses_real_nonregular_descriptor_race(
    delegated_subtree: Path, tmp_path: Path
) -> None:
    """An actual caller-FD replacement cannot make a directory an events file."""
    (tmp_path / "cgroup.events").mkdir()
    original = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY)
    alternate = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = os.dup(original)
    stop = threading.Event()

    def replace_descriptor() -> None:
        """Alternate actual retained descriptors until the observer stops."""
        while not stop.is_set():
            os.dup2(alternate, descriptor)
            os.dup2(original, descriptor)

    changer = threading.Thread(target=replace_descriptor)
    changer.start()
    try:
        refused = False
        deadline = time.monotonic() + 10
        while not refused:
            assert time.monotonic() < deadline, "events descriptor race exceeded its deadline"
            try:
                inspect_protected_writer_subtree(descriptor)
            except ValueError as error:
                assert str(error) in {
                    "writer subtree events are not a kernel file",
                    "writer subtree is not on the kernel cgroup filesystem",
                    "cgroup controls are outside the kernel filesystem",
                    "writer subtree changed during observation",
                }
                if str(error) == "writer subtree events are not a kernel file":
                    refused = True
        assert refused
    finally:
        stop.set()
        changer.join(timeout=5)
        assert not changer.is_alive()
        os.close(descriptor)
        os.close(alternate)
        os.close(original)


def test_full_kernel_capture_refuses_actual_subtree_migration(
    delegated_subtree: Path, tmp_path: Path, managed_process: ManagedProcess
) -> None:
    """A disposable process changing real membership cannot retain a stale subtree."""
    first, second = delegated_subtree / "first", delegated_subtree / "second"
    first.mkdir()
    second.mkdir()
    code = """
import os, sys, time
from synapse_channel.core.protected_write_kernel_boundary import (
    capture_protected_writer_kernel_evidence,
)
root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
print("ready", flush=True)
input()
try:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            capture_protected_writer_kernel_evidence({"memory": root})
        except ValueError as error:
            if str(error) == "writer process subtree changed during kernel capture":
                print("SUBTREE_REFUSED", flush=True)
                assert input() == "finish"
                break
            if str(error) not in {
                "writer cgroup membership changed during capture",
                "writer subtree changed during observation",
            }:
                raise
    else:
        raise RuntimeError("full capture migration was not observed")
finally:
    os.close(root)
"""
    observer = managed_process(
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        )
    )
    stop = threading.Event()

    def migrate() -> None:
        """Move the live owned observer between its two delegated cgroups."""
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
        assert observer.stdout.readline() == "SUBTREE_REFUSED\n"
    finally:
        stop.set()
        mover.join(timeout=5)
        assert not mover.is_alive()
    observer.stdin.write("finish\n")
    observer.stdin.flush()
    assert observer.wait(timeout=5) == 0


def test_kernel_capture_refuses_actual_private_mount_table_changes(tmp_path: Path) -> None:
    """Race genuine mounts in a disposable namespace against the public capture."""
    source = Path(__file__).resolve().parents[2]
    (tmp_path / "source").mkdir()
    (tmp_path / "target").mkdir()
    script = """import ctypes,json,os,sys,threading,time
from pathlib import Path
source,site,backing,coverage_dir=sys.argv[1:]
sys.path[:0]=[source+'/src',site]
recorder=None
if coverage_dir:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(coverage_dir)/'.coverage'),
  data_suffix=True,branch=True,
  source_pkgs=['synapse_channel.core.protected_write_kernel_boundary'],config_file=False)
 recorder.start()
from synapse_channel.core.protected_write_kernel_boundary import (
 capture_protected_writer_kernel_evidence)
libc=ctypes.CDLL(None,use_errno=True)
origin=os.fsencode(Path(backing)/'source');target=os.fsencode(Path(backing)/'target')
stop=threading.Event();errors=[];changes=[]
def change():
 while not stop.is_set():
  if libc.mount(ctypes.c_char_p(origin),ctypes.c_char_p(target),
   ctypes.c_void_p(),ctypes.c_ulong(4096),ctypes.c_void_p()):
   errors.append(('mount',ctypes.get_errno()));return
  if libc.umount2(ctypes.c_char_p(target),ctypes.c_int(2)):
   errors.append(('unmount',ctypes.get_errno()));return
  changes.append(1)
descriptor=os.open(backing,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
thread=threading.Thread(target=change)
try:
 capture_protected_writer_kernel_evidence({'data':descriptor})
 thread.start()
 deadline=time.monotonic()+10
 while time.monotonic()<deadline:
  assert not errors,errors
  try:capture_protected_writer_kernel_evidence({'data':descriptor})
  except ValueError as error:
   assert str(error)=='writer mount namespace changed during capture',str(error)
   break
 else:raise AssertionError('actual mount-table change was not refused')
finally:
 stop.set();thread.join(3);assert not thread.is_alive()
 os.close(descriptor)
 if recorder is not None:recorder.stop();recorder.save()
assert not errors and changes,errors
print(json.dumps({'actual_mount_changes':len(changes),'public_capture_refused':True}))
"""
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
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
            coverage_dir,
        )
    )
    namespace = Path("/proc/self/ns/mnt").stat()
    result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["public_capture_refused"] and observed["actual_mount_changes"] > 0
    current = Path("/proc/self/ns/mnt").stat()
    assert (current.st_dev, current.st_ino) == (namespace.st_dev, namespace.st_ino)
