# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual private-namespace payload storage and readonly exposure
"""Exercise public storage APIs with real Samsung files and native private mounts."""

from __future__ import annotations

import errno
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.protected_runtime_image import samsung_backing_or_refusal

_SCRIPT = """import ctypes,errno,hashlib,json,os,resource,signal,sys
from pathlib import Path
source,site,directory,case,coverage_dir,head=sys.argv[1:]
sys.path[:0]=[source+'/src',source,site]
recorder=None
if coverage_dir:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(coverage_dir)/'.coverage'),data_suffix=True,branch=True,source=['tools.protected_runtime_probe.storage'],config_file=False)
 recorder.start()
from tools.protected_runtime_probe.capsule import CapsuleManifest,FilePin,capture_capsule
from tools.protected_runtime_probe.storage import (
 bind_readonly,create_backing,measure_backing_tree,open_directory,unbind,write_payload)
root=Path(directory)
run_id='run-0123456789abcdef'
view_parent=Path('/opt/synapse-channel/protected-runtime-qualification')
view=view_parent/run_id
backing=None
payload=None
bound=False
try:
 if case=='invalid-run':
  try: create_backing(root,'../invalid')
  except ValueError: pass
  else: raise AssertionError('invalid run accepted')
  assert not list(root.iterdir())
 elif case=='relative-directory':
  try: open_directory(Path('relative'),trusted=False,create=False)
  except ValueError: pass
  else: raise AssertionError('relative directory accepted')
 elif case=='symlink-parent':
  actual=root/'actual';actual.mkdir()
  alias=root/'alias';alias.symlink_to(actual,target_is_directory=True)
  try: create_backing(alias,run_id)
  except OSError as error: assert error.errno in {errno.ELOOP,errno.ENOTDIR}
  else: raise AssertionError('linked backing parent accepted')
  assert not list(actual.iterdir())
 elif case=='dotdot-parent':
  try: create_backing(root/'..'/'escape',run_id)
  except ValueError as error: assert str(error)=='invalid prerequisite backing ancestry'
  else: raise AssertionError('parent traversal accepted')
  assert not list(root.iterdir())
 elif case=='unbound-target':
  parent=open_directory(view_parent,trusted=True,create=True);os.close(parent)
  view.mkdir()
  identity=view.stat()
  assert not view.is_mount()
  try: unbind(view,expected_identity=(identity.st_dev,identity.st_ino))
  except OSError as error:
   assert error.errno==errno.EINVAL
   assert error.strerror=='root prerequisite exposure could not unmount'
  else: raise AssertionError('unmounted target accepted as a removable exposure')
  observed=view.stat()
  assert (observed.st_dev,observed.st_ino)==(identity.st_dev,identity.st_ino)
  assert not list(view.iterdir())and not list(root.iterdir())
 elif case=='unmounted-storage':
  approved=Path('/media/anulum/GOTM')
  assert approved.is_dir()and not os.path.ismount(approved)
  lines=Path('/proc/self/mountinfo').read_text().splitlines()
  rows=[line.split(' - ',1)[0].split()for line in lines]
  assert all(row[4]!=str(approved)for row in rows)
  try: create_backing(root,run_id)
  except ValueError as error:
   assert str(error)=='approved Samsung storage mount is unavailable'
  else: raise AssertionError('ordinary directory accepted as approved storage mount')
  assert not list(root.iterdir())
  print(json.dumps({'approved_storage_mount_present':False,'uid_map':Path('/proc/self/uid_map').read_text(),'host_installation_qualified':False}))
 elif case=='foreign-device':
  foreign=root/'foreign-mount'
  assert foreign.stat().st_dev!=root.stat().st_dev
  try: create_backing(foreign,run_id)
  except ValueError as error:
   assert str(error)=='prerequisite backing escaped the approved storage device'
  else: raise AssertionError('nested device accepted')
  assert not list(foreign.iterdir())
  descriptor=open_directory(root,trusted=False,create=False)
  identity=os.fstat(descriptor)
  try:
   try: measure_backing_tree(descriptor)
   except ValueError as error:
    assert str(error)=='retained backing exceeds its filesystem or entry bound'
   else: raise AssertionError('nested device included in retained storage measurement')
   observed=os.fstat(descriptor)
   assert (observed.st_dev,observed.st_ino)==(identity.st_dev,identity.st_ino)
   assert not list(foreign.iterdir())
  finally: os.close(descriptor)
 elif case=='untrusted-directory':
  unsafe=Path('/opt/unsafe');unsafe.mkdir(parents=True);unsafe.chmod(0o777)
  try: open_directory(unsafe,trusted=True,create=False)
  except ValueError as error:
   assert str(error)=='bootstrap executable exposure requires trusted root ancestry'
  else: raise AssertionError('writable executable ancestry accepted')
  descriptor=open_directory(unsafe,trusted=False,create=False)
  assert os.fstat(descriptor).st_ino==unsafe.stat().st_ino
  os.close(descriptor)
 else:
  stage=root/'stage';stage.mkdir()
  content={
   'app/synapse_channel/__init__.py':Path(source+'/src/synapse_channel/__init__.py').read_bytes(),
   'probe/protected_runtime_probe/storage.py':Path(source+'/tools/protected_runtime_probe/storage.py').read_bytes(),
   'runtime/python/bin/python3.12':Path('/usr/bin/python3.12').read_bytes(),
   'runtime/native/lib/ld-linux-x86-64.so.2':Path('/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2').read_bytes(),
  }
  for name,data in content.items():
   target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
  pins=tuple(FilePin(name,hashlib.sha256(data).hexdigest(),len(data))
   for name,data in sorted(content.items()))
  manifest=CapsuleManifest(head,directory,pins)
  raw=manifest.canonical_bytes();(stage/'manifest.json').write_bytes(raw)
  capsule=capture_capsule(stage,hashlib.sha256(raw).hexdigest())
  backing=create_backing(root,run_id)
  assert os.fstat(backing).st_mode&0o777==0o700
  if case=='reused-run':
   try: create_backing(root,run_id)
   except FileExistsError: pass
   else: raise AssertionError('run identifier reused')
  if case=='write-limit':
   previous=resource.getrlimit(resource.RLIMIT_FSIZE)
   signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
   resource.setrlimit(resource.RLIMIT_FSIZE,(32,previous[1]))
   try:
    try: write_payload(backing,capsule)
    except OSError as error: assert error.errno==errno.EFBIG
    else: raise AssertionError('bounded write unexpectedly completed')
   finally: resource.setrlimit(resource.RLIMIT_FSIZE,previous)
   assert (root/run_id/'payload/app/synapse_channel/__init__.py').stat().st_size==32
  else:
   payload=write_payload(backing,capsule)
   for name,data in capsule.content.items():
    copied=root/run_id/'payload'/name
    assert copied.read_bytes()==data
    assert copied.stat().st_ino!=(stage/name).stat().st_ino
    executables={'runtime/python/bin/python3.12','runtime/native/lib/ld-linux-x86-64.so.2'}
    expected=0o555 if name in executables else 0o444
    assert copied.stat().st_mode&0o777==expected
   if case=='reused-payload':
    try: write_payload(backing,capsule)
    except FileExistsError: pass
    else: raise AssertionError('payload reused')
   parent=open_directory(view_parent,trusted=True,create=True);os.close(parent)
   view.mkdir()
   if case=='unsafe-source': os.fchmod(payload,0o777)
   if case=='unsafe-target': view.chmod(0o777)
   target=view.parent/'unapproved' if case=='foreign-target' else view
   placeholder=view.stat()
   if case in {'remount-denied','remount-cleanup-held'}:
    # Native x86_64 constants: linux/seccomp.h, linux/filter.h, asm/unistd_64.h.
    class Instruction(ctypes.Structure):
     _fields_=[('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint32)]
    class Program(ctypes.Structure):
     _fields_=[('length',ctypes.c_ushort),('instructions',ctypes.POINTER(Instruction))]
    rules=[
     Instruction(0x20,0,0,4),Instruction(0x15,1,0,0xC000003E),
     Instruction(0x06,0,0,0x80000000),Instruction(0x20,0,0,0),
     Instruction(0x45,0,1,0x40000000),Instruction(0x06,0,0,0x80000000)]
    if case=='remount-cleanup-held':
     rules.extend((Instruction(0x15,0,1,166),Instruction(0x06,0,0,0x50000|errno.EPERM)))
    rules.extend((
     Instruction(0x15,0,3,165),Instruction(0x20,0,0,40),
     Instruction(0x45,0,1,32),Instruction(0x06,0,0,0x50000|errno.EPERM),
     Instruction(0x06,0,0,0x7FFF0000)))
    instructions=(Instruction*len(rules))(*rules)
    program=Program(len(instructions),instructions)
    libc=ctypes.CDLL(None,use_errno=True)
    libc.prctl.argtypes=[ctypes.c_ulong]*5;libc.prctl.restype=ctypes.c_int
    libc.syscall.restype=ctypes.c_long
    assert libc.prctl(38,1,0,0,0)==0,ctypes.get_errno()
    assert libc.syscall(317,1,0,ctypes.byref(program))==0,ctypes.get_errno()
    status=Path('/proc/self/status').read_text()
    assert int(status.split('NoNewPrivs:')[1].splitlines()[0])==1
    assert int(status.split('Seccomp:')[1].splitlines()[0])==2
   if case in {
    'unsafe-source','unsafe-target','foreign-target','mount-denied',
    'remount-denied','remount-cleanup-held'}:
    try: bind_readonly(payload,target)
    except (OSError,ValueError) as error:
     if case=='mount-denied':
      assert isinstance(error,OSError)and error.errno in {errno.EPERM,errno.EACCES}
     elif case=='remount-denied':
      assert isinstance(error,OSError)and error.errno==errno.EPERM
      assert error.strerror=='root prerequisite readonly bind failed'
     elif case=='remount-cleanup-held':
      assert isinstance(error,OSError)and error.errno==errno.EPERM
      assert error.strerror=='root prerequisite exposure could not unmount'
      original=error.__context__
      assert isinstance(original,OSError)and original.errno==errno.EPERM
      assert original.strerror=='root prerequisite readonly bind failed'
    else: raise AssertionError('invalid exposure accepted')
    if case=='remount-cleanup-held':
     assert view.is_mount()
     lines=Path('/proc/self/mountinfo').read_text().splitlines()
     rows=[line.split(' - ',1)[0].split()for line in lines]
     mounted=next(row for row in rows if row[4]==str(view))
     assert 'rw'in mounted[5].split(',')and'ro'not in mounted[5].split(',')
     assert capture_capsule(view,hashlib.sha256(raw).hexdigest()).content==capsule.content
    else: assert not view.is_mount()
    if case=='remount-denied':
     observed=view.stat()
     assert (observed.st_dev,observed.st_ino)==(placeholder.st_dev,placeholder.st_ino)
     assert not list(view.iterdir())
     observed_capsule=capture_capsule(root/run_id/'payload',hashlib.sha256(raw).hexdigest())
     assert observed_capsule.content==capsule.content
   else:
    bind_readonly(payload,view);bound=True
    lines=Path('/proc/self/mountinfo').read_text().splitlines()
    rows=[line.split(' - ',1)[0].split()for line in lines]
    mounted=next(row for row in rows if row[4]==str(view))
    assert {'ro','nosuid','nodev'}<=set(mounted[5].split(','))
    assert 'noexec' not in mounted[5].split(',')
    observed=capture_capsule(view,hashlib.sha256(raw).hexdigest())
    assert observed.content==capsule.content
    try: (view/'app/synapse_channel/__init__.py').write_bytes(b'change')
    except OSError as error: assert error.errno==errno.EROFS
    else: raise AssertionError('readonly view writable')
    identity=os.fstat(payload)
    if case=='wrong-identity':
     try: unbind(view,expected_identity=(identity.st_dev,identity.st_ino+1))
     except ValueError: pass
     else: raise AssertionError('substituted identity accepted')
     assert view.is_mount()
    unbind(view,expected_identity=(identity.st_dev,identity.st_ino));bound=False
    assert not view.is_mount()and not list(view.iterdir())
 print(json.dumps({'case':case,'storage_contract_passed':True,'host_installation_qualified':False}))
finally:
 if bound and payload is not None:
  metadata=os.fstat(payload);unbind(view,expected_identity=(metadata.st_dev,metadata.st_ino))
 if payload is not None: os.close(payload)
 if backing is not None: os.close(backing)
 if recorder is not None: recorder.stop();recorder.save()
"""


@pytest.mark.parametrize(
    "case",
    [
        "normal",
        "invalid-run",
        "relative-directory",
        "symlink-parent",
        "dotdot-parent",
        "unmounted-storage",
        "unbound-target",
        "foreign-device",
        "untrusted-directory",
        "reused-run",
        "write-limit",
        "reused-payload",
        "unsafe-source",
        "unsafe-target",
        "foreign-target",
        "mount-denied",
        "remount-denied",
        "remount-cleanup-held",
        "wrong-identity",
    ],
)
def test_actual_payload_storage_and_readonly_view(tmp_path: Path, case: str) -> None:
    """Copy real source bytes and refuse actual reuse, aliases, modes and mount denial."""
    if not samsung_backing_or_refusal(tmp_path):
        return
    from tools.protected_runtime_probe.native_closure import require_native_runtime_abi

    observed = (
        sys.platform,
        platform.machine(),
        sys.version_info[:2],
        sys.maxsize.bit_length() + 1,
    )
    if observed != ("linux", "x86_64", (3, 12), 64):
        with pytest.raises(OSError, match="native Linux"):
            require_native_runtime_abi(*observed)
        return
    assert os.geteuid() != 0
    source = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(
        ["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=10
    ).strip()
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    parent_policy = {
        line.partition(":")[0]: line.partition(":")[2].strip()
        for line in Path("/proc/self/status").read_text().splitlines()
        if line.startswith(("NoNewPrivs:", "Seccomp:", "Seccomp_filters:"))
    }
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
    visible_roots: list[Path] = []
    for directory in ("/usr", "/media", "/home", "/lib", "/lib64"):
        root = source if directory == "/media" and case == "unmounted-storage" else Path(directory)
        command.extend(("--ro-bind", str(root), str(root)))
        visible_roots.append(root)
    for prefix in (
        Path(sys.base_prefix).resolve(strict=True),
        Path(sys.prefix).resolve(strict=True),
    ):
        if not any(prefix.is_relative_to(root) for root in visible_roots):
            command.extend(("--ro-bind", str(prefix), str(prefix)))
            visible_roots.append(prefix)
    command.extend(("--proc", "/proc", "--bind", str(tmp_path), str(tmp_path)))
    if case == "foreign-device":
        foreign = tmp_path / "foreign-mount"
        foreign.mkdir()
        command.extend(("--tmpfs", str(foreign)))
    if coverage_dir:
        assert Path(coverage_dir).is_absolute() and Path(coverage_dir).is_dir()
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(("--cap-drop", "ALL"))
    if case != "mount-denied":
        command.extend(("--cap-add", "CAP_SYS_ADMIN"))
    command.extend(
        (
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            _SCRIPT,
            str(source),
            str(Path(pytest.__file__).parents[1]),
            str(tmp_path),
            case,
            coverage_dir,
            head,
        )
    )
    process = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert process.returncode == 0, process.stderr
    assert {
        line.partition(":")[0]: line.partition(":")[2].strip()
        for line in Path("/proc/self/status").read_text().splitlines()
        if line.startswith(("NoNewPrivs:", "Seccomp:", "Seccomp_filters:"))
    } == parent_policy
    assert json.loads(process.stdout.splitlines()[-1]) == {
        "case": case,
        "storage_contract_passed": True,
        "host_installation_qualified": False,
    }


@pytest.mark.parametrize(
    "case",
    [
        "backing-file",
        "report-directory",
        "report-name",
        "relative-target",
        "different-target",
        "unbound-report",
    ],
)
def test_retention_requires_matching_real_descriptors(tmp_path: Path, case: str) -> None:
    """Retained reports must name their actual backing, file and readonly mount."""
    from tools.protected_runtime_probe import require_native_linux
    from tools.protected_runtime_probe.storage import describe_retained_attempt

    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            require_native_linux(sys.platform, operation="retained report observation")
        return
    backing = tmp_path / "backing"
    backing.mkdir()
    report = backing / "report.json"
    report.write_text("{}\n")
    evidence_path = report
    target = report
    if case == "report-name":
        evidence_path = backing / "other.json"
        evidence_path.write_text("{}\n")
    elif case == "report-directory":
        evidence_path = backing
    elif case == "relative-target":
        target = Path("report.json")
    elif case == "different-target":
        target = tmp_path / "different.json"
        target.write_text("{}\n")
    root = os.open(report if case == "backing-file" else backing, os.O_RDONLY)
    evidence = os.open(evidence_path, os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="retention|retained"):
            describe_retained_attempt(root, evidence, target)
        assert os.fstat(root).st_ino > 0 and os.fstat(evidence).st_ino > 0
    finally:
        os.close(evidence)
        os.close(root)


@pytest.mark.parametrize(
    "case", ["native", "missing", "nondecimal", "zero", "negative", "duplicate"]
)
def test_mount_id_text_contract_with_observed_fdinfo(tmp_path: Path, case: str) -> None:
    """Parse real fdinfo and reject explicit text variants, without kernel spoofing."""
    from tools.protected_runtime_probe import require_native_linux
    from tools.protected_runtime_probe.storage import parse_descriptor_mount_id

    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            require_native_linux(sys.platform, operation="descriptor mount observation")
        return
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        observed = Path(f"/proc/self/fdinfo/{descriptor}").read_text()
        lines = observed.splitlines()
        mount_lines = [line for line in lines if line.startswith("mnt_id:")]
        assert len(mount_lines) == 1
        expected = int(mount_lines[0].split(":", 1)[1])
        assert expected > 0
        if case == "native":
            assert parse_descriptor_mount_id(observed) == expected
        else:
            variants = {
                "missing": [],
                "nondecimal": ["mnt_id:\tinvalid"],
                "zero": ["mnt_id:\t0"],
                "negative": ["mnt_id:\t-1"],
                "duplicate": [mount_lines[0], mount_lines[0]],
            }
            text = "\n".join(
                [line for line in lines if not line.startswith("mnt_id:")] + variants[case]
            )
            with pytest.raises(ValueError, match="no exact kernel mount identity"):
                parse_descriptor_mount_id(text)
        assert os.fstat(descriptor).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("kind", ["files", "directories"])
def test_retained_tree_accepts_exact_entry_bound_then_refuses_overflow(
    tmp_path: Path, kind: str
) -> None:
    """Measure actual entries at the limit and remove every generated entry on exit."""
    from tools.protected_runtime_probe.capsule import require_posix_descriptors
    from tools.protected_runtime_probe.storage import measure_backing_tree

    if os.name != "posix":
        with pytest.raises(OSError, match="POSIX"):
            require_posix_descriptors(os.name)
        return

    tree = tmp_path / "entry-bound"
    tree.mkdir()
    descriptor = os.open(tree, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index in range(19_999):
            child = tree / str(index)
            if kind == "files":
                child.touch()
            else:
                child.mkdir()
        measured = measure_backing_tree(descriptor)
        assert measured["files"] == (19_999 if kind == "files" else 0)
        assert measured["directories"] == (1 if kind == "files" else 20_000)
        overflow = tree / "overflow"
        if kind == "files":
            overflow.touch()
        else:
            overflow.mkdir()
        with pytest.raises(ValueError, match="filesystem or entry bound"):
            measure_backing_tree(descriptor)
        assert os.fstat(descriptor).st_ino == tree.stat().st_ino
    finally:
        os.close(descriptor)
        for child in tree.iterdir():
            if kind == "files":
                child.unlink()
            else:
                child.rmdir()
        tree.rmdir()
    assert not tree.exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_streamed_backing_measurement_refuses_actual_special_entries(
    tmp_path: Path, kind: str
) -> None:
    """Never follow an alias or open a producer-owned FIFO during measurement."""
    from tools.protected_runtime_probe.storage import measure_backing_tree

    if sys.platform != "linux":
        return
    target = tmp_path / "entry"
    if kind == "symlink":
        target.symlink_to(tmp_path, target_is_directory=True)
    else:
        os.mkfifo(target)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="nonregular"):
            measure_backing_tree(directory)
        assert os.fstat(directory).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(directory)


def test_real_backing_ancestry_keeps_the_borrowed_descriptor(tmp_path: Path) -> None:
    """Observe the actual parent chain and both trusted and writable mode states."""
    from tools.protected_runtime_probe.storage import describe_backing_ancestry

    if sys.platform != "linux":
        return
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for mode in (0o700, 0o777):
            os.fchmod(directory, mode)
            ancestors = describe_backing_ancestry(directory)
            first = ancestors[0]
            assert first["path"] == str(tmp_path) and first["mode"] == mode
            assert first["uid"] == os.getuid() and first["gid"] == os.getgid()
            assert first["root_controlled"] is False
            assert ancestors[-1]["path"] == "/"
            assert os.fstat(directory).st_ino == first["inode"]
    finally:
        os.fchmod(directory, 0o700)
        os.close(directory)


def test_real_backing_ancestry_refuses_excessive_depth(tmp_path: Path) -> None:
    """Bound real parent traversal while leaving the caller's descriptor usable."""
    from tools.protected_runtime_probe.storage import describe_backing_ancestry

    if sys.platform != "linux":
        return
    parents: list[Path] = []
    current = tmp_path
    try:
        for _ in range(257):
            current = current / "d"
            current.mkdir()
            parents.append(current)
        directory = os.open(current, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(ValueError, match="ancestry exceeds its depth bound"):
                describe_backing_ancestry(directory)
            assert os.fstat(directory).st_ino == current.stat().st_ino
        finally:
            os.close(directory)
    finally:
        for parent in reversed(parents):
            parent.rmdir()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("raw", ["null", "0", "1", '"true"', "[]", "{}"])
def test_bind_refuses_nonboolean_execution_mode_before_effects(tmp_path: Path, raw: str) -> None:
    """Reject malformed execution modes before privilege checks or mount effects."""
    from tools.protected_runtime_probe.storage import bind_readonly

    if os.name != "posix":
        with pytest.raises(ValueError, match="invalid prerequisite bind execution mode"):
            bind_readonly(-1, tmp_path / "unopened-target", executable=json.loads(raw))
        return

    source = tmp_path / "source"
    source.write_bytes(b"owned payload")
    target = tmp_path / "target"
    target.mkdir()
    descriptor = os.open(source, os.O_RDONLY)
    identity = (os.geteuid(), os.getegid())
    try:
        with pytest.raises(ValueError, match="invalid prerequisite bind execution mode"):
            bind_readonly(descriptor, target, executable=json.loads(raw))
        assert os.fstat(descriptor).st_ino == source.stat().st_ino
        assert source.read_bytes() == b"owned payload" and not list(target.iterdir())
        assert (os.geteuid(), os.getegid()) == identity
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    "case", ["empty", "value", "exact-bound", "oversized", "fifo", "symlink", "missing"]
)
def test_public_marker_read_preserves_custody_and_enforces_actual_file_bounds(
    tmp_path: Path, case: str
) -> None:
    """Read real owned files through the public API without claiming role quiescence."""
    from tools.protected_runtime_probe.storage import read_effect_marker

    if os.name != "posix":
        with pytest.raises(OSError, match="requires native POSIX descriptors"):
            read_effect_marker(-1)
        return

    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    marker = directory / "marker"
    untouched = tmp_path / "untouched"
    untouched.write_bytes(b"retain these independent bytes")
    raw = b"actual ordinary marker"
    if case == "empty":
        raw = b""
    elif case in {"exact-bound", "oversized"}:
        raw = b"x" * (4096 if case == "exact-bound" else 4097)
    if case == "fifo":
        os.mkfifo(marker, 0o600)
    elif case == "symlink":
        marker.symlink_to(untouched)
    elif case != "missing":
        marker.write_bytes(raw)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    identity = os.fstat(descriptor)
    inventory = os.listdir(descriptor)
    try:
        if case == "oversized":
            with pytest.raises(ValueError, match="prerequisite marker exceeds its byte bound"):
                read_effect_marker(descriptor)
        elif case == "fifo":
            with pytest.raises(ValueError, match="prerequisite marker is not a regular file"):
                read_effect_marker(descriptor)
        elif case in {"symlink", "missing"}:
            with pytest.raises(OSError) as failure:
                read_effect_marker(descriptor)
            assert failure.value.errno == (errno.ELOOP if case == "symlink" else errno.ENOENT)
        else:
            assert read_effect_marker(descriptor) == raw
            assert marker.read_bytes() == raw
        observed = os.fstat(descriptor)
        assert (observed.st_dev, observed.st_ino) == (identity.st_dev, identity.st_ino)
        assert os.listdir(descriptor) == inventory
        assert untouched.read_bytes() == b"retain these independent bytes"
    finally:
        os.close(descriptor)
