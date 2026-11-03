# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual recording output custody and raw sealing contracts
"""Exercise public descriptor sealing on ordinary owned files, without root custody claims."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from tools.protected_runtime_probe.instrumentation_outputs import (
    RecordingOutput,
    create_recording_output,
    seal_recording_output,
)
from tools.protected_runtime_probe.instrumentation_recording import (
    MAX_RECORDING_BYTES,
    RECORDING_FILENAME,
)
from tools.protected_runtime_probe.native_closure import require_native_runtime_abi

_FSYNC_REFUSAL = """import ctypes,errno,json,os,sys
from pathlib import Path
source,site,measure,leaf_text,target_text=sys.argv[1:]
sys.path[:0]=[source,source+'/src',site]
collector=None
if measure:
 import coverage
 collector=coverage.Coverage(data_file=None,branch=True,
  source=['tools.protected_runtime_probe.instrumentation_outputs'],config_file=False)
 collector.start();collector.switch_context('measured-diagnostic/actual-fsync-refusal')
from tools.protected_runtime_probe.instrumentation_outputs import (
 RecordingOutput,seal_recording_output)
if not measure:assert 'coverage' not in sys.modules
leaf,target=int(leaf_text),int(target_text);info=os.fstat(leaf)
output=RecordingOutput('controller',leaf,os.geteuid(),os.getegid(),(info.st_dev,info.st_ino))
original=os.open('coverage.sqlite',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=leaf)
os.fsync(original)
class Instruction(ctypes.Structure):
 _fields_=[('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint32)]
class Program(ctypes.Structure):
 _fields_=[('length',ctypes.c_ushort),('instructions',ctypes.POINTER(Instruction))]
# Native constants: linux/seccomp.h, linux/filter.h, asm/unistd_64.h.
rules=[Instruction(0x20,0,0,4),Instruction(0x15,1,0,0xC000003E),
 Instruction(0x06,0,0,0x80000000),Instruction(0x20,0,0,0),
 Instruction(0x45,0,1,0x40000000),Instruction(0x06,0,0,0x80000000),
 Instruction(0x15,0,1,74),Instruction(0x06,0,0,0x50000|errno.EIO),
 Instruction(0x06,0,0,0x7fff0000)]
instructions=(Instruction*len(rules))(*rules);program=Program(len(rules),instructions)
libc=ctypes.CDLL(None,use_errno=True)
libc.prctl.argtypes=[ctypes.c_ulong]*5;libc.prctl.restype=ctypes.c_int
libc.syscall.restype=ctypes.c_long
assert libc.prctl(38,1,0,0,0)==0,ctypes.get_errno()
assert libc.syscall(317,1,0,ctypes.byref(program))==0,ctypes.get_errno()
try:
 try:os.fsync(original)
 except OSError as error:assert error.errno==errno.EIO,error
 else:raise AssertionError('native fsync filter did not refuse')
 before=set(os.listdir('/proc/self/fd'));borrowed=[os.fstat(fd)for fd in(leaf,target)]
 try:seal_recording_output(output,target,host_pid=os.getpid())
 except OSError as error:assert error.errno==errno.EIO,error
 else:raise AssertionError('fsync refusal returned a successful seal')
 assert set(os.listdir('/proc/self/fd'))==before
 for fd,pin in zip((leaf,target),borrowed,strict=True):
  observed=os.fstat(fd)
  assert(observed.st_dev,observed.st_ino)==(pin.st_dev,pin.st_ino)
 assert os.fstat(leaf).st_mode&0o777==0o700
finally:os.close(original)
data_hex=None
if collector is not None:
 collector.stop();data_hex=collector.get_data().dumps().hex()
print(json.dumps({'fsync_errno':errno.EIO,'descriptor_set_unchanged':True,
 'native_qualification':False,'coverage_hex':data_hex}))
"""


def _supported() -> bool:
    if os.name == "posix":
        return True
    with pytest.raises(OSError, match="POSIX"):
        create_recording_output(-1, "controller", 0, 0)
    return False


@contextmanager
def _outputs(directory: Path, role: str = "controller") -> Iterator[tuple[RecordingOutput, int]]:
    """Keep actual parent, leaf and destination FDs alive through each public operation."""
    incoming = directory / "incoming"
    destination = directory / "sealed"
    incoming.mkdir(mode=0o700)
    destination.mkdir(mode=0o700)
    parent = os.open(incoming, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    target = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    output = create_recording_output(parent, role, os.geteuid(), os.getegid())
    try:
        yield output, target
    finally:
        os.fchmod(output.descriptor, 0o700)
        os.close(output.descriptor)
        os.close(target)
        os.close(parent)


def _dataset(directory: Path, raw: bytes = b"opaque original recorder bytes\n") -> Path:
    """Create a private input file without requiring a database format."""
    path = directory / "incoming/controller" / RECORDING_FILENAME
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("body", ["opaque", "sqlite"])
def test_public_sealer_copies_exact_raw_bytes_without_parsing_their_format(
    tmp_path: Path, body: str
) -> None:
    """Opaque and real SQLite bytes are sealed unchanged; format validation belongs downstream."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        path = _dataset(tmp_path)
        if body == "sqlite":
            path.unlink()
            with closing(sqlite3.connect(path)) as database, database:
                database.execute("create table observation (value text)")
                database.execute("insert into observation values (?)", ("actual ordinary data",))
            path.chmod(0o600)
        original = path.read_bytes()
        pin = seal_recording_output(output, target, host_pid=os.getpid())
        copied = tmp_path / "sealed/controller.sqlite"
        assert copied.read_bytes() == original
        assert pin["sha256"] == hashlib.sha256(original).hexdigest()
        assert pin["size_bytes"] == len(original)
        assert pin["host_pid"] == os.getpid()
        assert pin["producer_uid"] == pin["sealed_uid"] == os.geteuid()
        assert pin["producer_gid"] == pin["sealed_gid"] == os.getegid()
        assert pin["source_inode"] == path.stat().st_ino
        assert pin["sealed_inode"] == copied.stat().st_ino
        assert pin["source_inode"] != pin["sealed_inode"]
        assert path.stat().st_mode & 0o777 == 0o400
        assert copied.stat().st_mode & 0o777 == 0o444
        assert (tmp_path / "incoming/controller").stat().st_mode & 0o777 == 0o500


@pytest.mark.parametrize(
    "change", ["empty", "oversized", "mode", "hardlink", "symlink", "fifo", "directory", "sidecar"]
)
def test_actual_input_topology_and_metadata_refusals_leave_no_sealed_dataset(
    tmp_path: Path, change: str
) -> None:
    """Actual unsafe files/links/sidecars fail before a destination dataset is created."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        path = _dataset(tmp_path)
        if change == "empty":
            path.write_bytes(b"")
        elif change == "oversized":
            with path.open("r+b") as stream:
                stream.truncate(MAX_RECORDING_BYTES + 1)
        elif change == "mode":
            path.chmod(0o644)
        elif change == "hardlink":
            os.link(path, tmp_path / "outside-link")
        elif change in {"symlink", "fifo", "directory"}:
            path.unlink()
            if change == "symlink":
                ordinary = tmp_path / "ordinary"
                ordinary.write_bytes(b"untouched ordinary input")
                path.symlink_to(ordinary)
            elif change == "fifo":
                os.mkfifo(path, 0o600)
            else:
                path.mkdir(mode=0o700)
        else:
            path.with_name(RECORDING_FILENAME + "-journal").write_bytes(b"unexpected")
        with pytest.raises((ValueError, OSError)):
            seal_recording_output(output, target, host_pid=os.getpid())
        assert not list((tmp_path / "sealed").iterdir())
        if change == "symlink":
            assert (tmp_path / "ordinary").read_bytes() == b"untouched ordinary input"


@pytest.mark.parametrize("change", ["uid", "gid", "identity", "leaf-mode", "destination-mode"])
def test_actual_directory_custody_is_checked_before_any_copy(tmp_path: Path, change: str) -> None:
    """Declared principals and retained inode pins must match real private directories."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        _dataset(tmp_path)
        if change == "uid":
            output = replace(output, uid=os.geteuid() + 1)
        elif change == "gid":
            output = replace(output, gid=os.getegid() + 1)
        elif change == "identity":
            observed = os.fstat(target)
            output = replace(output, identity=(observed.st_dev, observed.st_ino))
        elif change == "leaf-mode":
            os.fchmod(output.descriptor, 0o755)
        else:
            os.fchmod(target, 0o755)
        with pytest.raises((ValueError, PermissionError)):
            seal_recording_output(output, target, host_pid=os.getpid())
        assert not list((tmp_path / "sealed").iterdir())


def test_existing_sealed_name_is_never_overwritten(tmp_path: Path) -> None:
    """A reused destination preserves its original bytes and leaves source ownership unchanged."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        source = _dataset(tmp_path)
        existing = tmp_path / "sealed/controller.sqlite"
        existing.write_bytes(b"retain this exact previous result")
        with pytest.raises(FileExistsError):
            seal_recording_output(output, target, host_pid=os.getpid())
        assert existing.read_bytes() == b"retain this exact previous result"
        assert source.stat().st_mode & 0o777 == 0o600


def test_retained_leaf_survives_actual_path_replacement(tmp_path: Path) -> None:
    """Sealing follows the kept directory FD and never a replacement pathname."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        source = _dataset(tmp_path)
        expected = source.read_bytes()
        path = source.parent
        path.rename(path.with_name("retained"))
        path.mkdir()
        (path / RECORDING_FILENAME).write_bytes(b"replacement must remain unchanged")
        seal_recording_output(output, target, host_pid=os.getpid())
        assert (tmp_path / "sealed/controller.sqlite").read_bytes() == expected
        assert (path / RECORDING_FILENAME).read_bytes() == b"replacement must remain unchanged"


def test_factory_refuses_reuse_and_invalid_names_before_new_leaf_creation(tmp_path: Path) -> None:
    """Only fresh fixed names are created under the actual private caller-owned parent."""
    if not _supported():
        return
    with _outputs(tmp_path) as (_output, _target):
        parent = os.open(tmp_path / "incoming", os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(FileExistsError):
                create_recording_output(parent, "controller", os.geteuid(), os.getegid())
            with pytest.raises(ValueError):
                create_recording_output(parent, "../outside", os.geteuid(), os.getegid())
        finally:
            os.close(parent)
        assert {path.name for path in (tmp_path / "incoming").iterdir()} == {"controller"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("descriptor", -1),
        ("descriptor", True),
        ("descriptor", False),
        ("uid", -1),
        ("uid", 2**32 - 1),
        ("uid", True),
        ("uid", False),
        ("gid", -1),
        ("gid", 2**32 - 1),
        ("gid", True),
        ("gid", False),
    ],
)
def test_malformed_enrollment_creates_no_leaf_and_preserves_borrowed_descriptors(
    tmp_path: Path, field: str, value: int
) -> None:
    """Reject invalid descriptors and exact numeric principals before creating an output."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        parent = os.open(tmp_path / "incoming", os.O_RDONLY | os.O_DIRECTORY)
        try:
            identity = os.fstat(parent)
            descriptor = value if field == "descriptor" else parent
            uid = value if field == "uid" else os.geteuid()
            gid = value if field == "gid" else os.getegid()
            reason = "descriptor" if field == "descriptor" else "owner"
            with pytest.raises(ValueError, match="invalid recording output " + reason):
                create_recording_output(descriptor, "writer", uid, gid)
            assert os.fstat(parent) == identity
            assert os.fstat(output.descriptor).st_ino == output.identity[1]
            assert os.fstat(target).st_mode & 0o777 == 0o700
            assert os.listdir(parent) == ["controller"]
            assert not os.listdir(output.descriptor) and not os.listdir(target)
        finally:
            os.close(parent)


@pytest.mark.parametrize(
    ("device", "inode"),
    [
        (-1, None),
        (True, None),
        (False, None),
        (None, -1),
        (None, 0),
        (None, True),
        (None, False),
        (None, None),
    ],
)
def test_recording_identity_requires_exact_nonnegative_device_and_positive_inode(
    tmp_path: Path, device: int | None, inode: int | None
) -> None:
    """Validate declared identity pins without consuming the real borrowed descriptor."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        identity = (
            output.identity[0] if device is None else device,
            output.identity[1] if inode is None else inode,
        )
        if device is None and inode is None:
            assert (
                RecordingOutput(output.role, output.descriptor, output.uid, output.gid, identity)
                == output
            )
        else:
            with pytest.raises(ValueError, match="invalid recording output directory identity"):
                RecordingOutput(output.role, output.descriptor, output.uid, output.gid, identity)
        assert os.fstat(output.descriptor).st_ino == output.identity[1]
        assert not os.listdir(output.descriptor) and not os.listdir(target)


@pytest.mark.parametrize("pid", [0, -1, True, False])
def test_invalid_producer_pid_leaves_original_private_and_destination_empty(
    tmp_path: Path, pid: int
) -> None:
    """Refuse absent or boolean producer identity before any sealing operation."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        source = _dataset(tmp_path)
        original = source.read_bytes()
        with pytest.raises(ValueError, match="independently observed host PID"):
            seal_recording_output(output, target, host_pid=pid)
        assert source.read_bytes() == original
        assert source.stat().st_mode & 0o777 == 0o600
        assert os.fstat(output.descriptor).st_mode & 0o777 == 0o700
        assert not os.listdir(target)


def test_actual_owner_change_failure_closes_only_the_new_descriptor(tmp_path: Path) -> None:
    """A real refused chown retains its private partial leaf without leaking an owned FD."""
    if not _supported():
        return
    assert os.geteuid() != 0
    with _outputs(tmp_path) as (_output, _target):
        parent = os.open(tmp_path / "incoming", os.O_RDONLY | os.O_DIRECTORY)
        code = """import errno,json,os,sys
from pathlib import Path
source,site,coverage_dir,parent_text=sys.argv[1:]
sys.path[:0]=[source,source+'/src',site]
recorder=None
if coverage_dir:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(coverage_dir)/'.coverage'),data_suffix=True,branch=True,source=['tools.protected_runtime_probe.instrumentation_outputs'],config_file=False)
 recorder.start()
from tools.protected_runtime_probe.instrumentation_outputs import create_recording_output
parent=int(parent_text)
try:
 assert os.geteuid()!=0
 fd_directory=Path('/proc/self/fd')if Path('/proc/self/fd').is_dir()else Path('/dev/fd')
 before=set(os.listdir(fd_directory));identity=os.fstat(parent)
 try: create_recording_output(parent,'writer',0,os.getegid())
 except PermissionError as error: assert error.errno in {errno.EPERM,errno.EACCES}
 else: raise AssertionError('ordinary caller changed the leaf owner to root')
 assert set(os.listdir(fd_directory))==before
 observed=os.fstat(parent)
 assert (observed.st_dev,observed.st_ino)==(identity.st_dev,identity.st_ino)
 leaf=os.stat('writer',dir_fd=parent,follow_symlinks=False)
 assert leaf.st_uid==os.geteuid()and leaf.st_mode&0o777==0o700
 print(json.dumps({'ownership_refused':True,'descriptor_set_unchanged':True,'native_qualification':False}))
finally:
 if recorder is not None: recorder.stop();recorder.save()
"""
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-S",
                    "-c",
                    code,
                    str(Path(__file__).resolve().parents[1]),
                    str(Path(pytest.__file__).parents[1]),
                    os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", ""),
                    str(parent),
                ],
                pass_fds=(parent,),
                capture_output=True,
                text=True,
                timeout=10,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {
                "ownership_refused": True,
                "descriptor_set_unchanged": True,
                "native_qualification": False,
            }
            assert os.fstat(parent).st_ino == (tmp_path / "incoming").stat().st_ino
            assert not list((tmp_path / "incoming/writer").iterdir())
        finally:
            os.close(parent)


def test_actual_partial_copy_is_retained_as_failure_without_sealing_the_original(
    tmp_path: Path,
) -> None:
    """A real4KiB file limit interrupts copying and leaves an explicitly incomplete private file."""
    if not _supported():
        return
    with _outputs(tmp_path) as (output, target):
        original = _dataset(tmp_path, b"actual bounded raw byte copy\n" * 4096)
        code = """import sys,os,resource
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/src']
from tools.protected_runtime_probe.instrumentation_outputs import (
 RecordingOutput,seal_recording_output)
leaf=int(sys.argv[2]);target=int(sys.argv[3]);info=os.fstat(leaf)
output=RecordingOutput('controller',leaf,os.geteuid(),os.getegid(),(info.st_dev,info.st_ino))
resource.setrlimit(resource.RLIMIT_FSIZE,(4096,4096))
try: seal_recording_output(output,target,host_pid=os.getpid())
except OSError as error:
 assert 'write was incomplete' in str(error),str(error)
else: raise AssertionError('partial copy was graded successful')
"""
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-S",
                "-c",
                code,
                str(Path(__file__).resolve().parents[1]),
                str(output.descriptor),
                str(target),
            ],
            pass_fds=(output.descriptor, target),
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        partial = tmp_path / "sealed/controller.sqlite"
        assert partial.stat().st_size == 4096
        assert partial.stat().st_mode & 0o777 == 0o600
        assert original.stat().st_mode & 0o777 == 0o600
        assert os.fstat(output.descriptor).st_mode & 0o777 == 0o700


@pytest.mark.parametrize("profile", ["cold", "measured"])
def test_actual_fsync_refusal_retains_complete_private_copy_without_sealing_source(
    tmp_path: Path,
    profile: str,
) -> None:
    """A child-only kernel fsync refusal must fail even after every raw byte was copied."""
    if not _supported():
        return
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.maxsize <= 2**32:
        with pytest.raises(OSError, match="native Linux"):
            require_native_runtime_abi(
                sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1
            )
        return

    def policy() -> list[str]:
        """Observe the parent's actual irreversible-filter state around its disposable child."""
        return [
            line
            for line in Path("/proc/self/status").read_text().splitlines()
            if line.startswith(("NoNewPrivs:", "Seccomp:", "Seccomp_filters:"))
        ]

    before = policy()
    with _outputs(tmp_path) as (output, target):
        original = _dataset(tmp_path, b"complete bytes still require durable sealing\n" * 2048)
        expected = original.read_bytes()
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-S",
                "-c",
                _FSYNC_REFUSAL,
                str(Path(__file__).resolve().parents[1]),
                str(Path(pytest.__file__).parents[1]),
                os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "") if profile == "measured" else "",
                str(output.descriptor),
                str(target),
            ],
            pass_fds=(output.descriptor, target),
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        data_hex = observed.pop("coverage_hex")
        assert observed == {
            "fsync_errno": 5,
            "descriptor_set_unchanged": True,
            "native_qualification": False,
        }
        copied = tmp_path / "sealed/controller.sqlite"
        assert copied.read_bytes() == expected == original.read_bytes()
        assert copied.stat().st_mode & 0o777 == original.stat().st_mode & 0o777 == 0o600
        assert copied.stat().st_ino != original.stat().st_ino
        assert os.fstat(output.descriptor).st_mode & 0o777 == 0o700
        assert os.listdir(target) == ["controller.sqlite"]
        if data_hex is not None:
            import coverage

            # A private disk copy avoids depending on SQLite shared-memory URI attachment.
            data = coverage.CoverageData(basename=str(tmp_path / "child-observations.coverage"))
            try:
                data.loads(bytes.fromhex(data_hex))
                source = Path(__file__).resolve().parents[1]
                module = str(source / "tools/protected_runtime_probe/instrumentation_outputs.py")
                assert set(data.measured_files()) == {module}
                assert data.measured_contexts() == {"measured-diagnostic/actual-fsync-refusal"}
                active = coverage.Coverage.current()
                assert active is not None, (
                    "measured child observations require an active parent collector"
                )
                active.get_data().update(data)
            finally:
                data.close(force=True)
        (tmp_path / "actual-fsync-refusal.json").write_text(json.dumps(observed) + "\n")
    assert policy() == before
