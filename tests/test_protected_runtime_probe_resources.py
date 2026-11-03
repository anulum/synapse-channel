# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded prerequisite control-file I/O contracts
"""Use real ordinary files; these tests do not provide kernel cgroup observations."""

from __future__ import annotations

import errno
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


def test_public_resource_enrollment_refuses_before_creating_a_host_scope() -> None:
    """The actual host reaches its platform/root refusal before touching a cgroup."""
    from tools.protected_runtime_probe.resources import create_resource_scope

    run_id = "run-" + uuid.uuid4().hex[:16]
    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            create_resource_scope(run_id)
        return
    assert os.geteuid() != 0
    target = Path("/sys/fs/cgroup") / ("synapse-qualification-" + run_id)
    assert not target.exists()
    with pytest.raises(PermissionError, match="require the root operator"):
        create_resource_scope(run_id)
    assert not target.exists()


@pytest.mark.parametrize("case", ["closed", "unknown", "declared"])
def test_resource_path_declaration_and_refusals(tmp_path: Path, case: str) -> None:
    """Check only path declarations; these borrowed ordinary FDs enroll no cgroup."""
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.resources import ResourceLeaf, ResourceScope

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        observed = os.fstat(descriptor)
        leaf = ResourceLeaf("writer", descriptor, (observed.st_dev, observed.st_ino), ())
        name = "synapse-unqualified-path-" + uuid.uuid4().hex
        scope = ResourceScope(descriptor, name, (leaf,))
        expected = Path("/sys/fs/cgroup") / name / "writer"
        assert not expected.exists()
        if case == "closed":
            scope.closed = True
            with pytest.raises(ValueError, match="unavailable prerequisite resource leaf"):
                scope.path("writer")
        elif case == "unknown":
            with pytest.raises(ValueError, match="unavailable prerequisite resource leaf"):
                scope.path("unknown")
        else:
            assert scope.path("writer") == expected
        assert not expected.exists() and not list(tmp_path.iterdir())
        assert os.fstat(descriptor).st_ino == observed.st_ino
    finally:
        os.close(descriptor)


def _native_contract_profile() -> bool:
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
        return False
    return True


@pytest.mark.parametrize(
    "case",
    [
        "normal",
        "whitespace",
        "nonnumeric",
        "boundary",
        "oversized",
        "nonascii",
        "symlink",
        "missing",
    ],
)
def test_actual_plain_file_reads_are_bounded_ascii(tmp_path: Path, case: str) -> None:
    """Read framing only; numeric policy validation belongs to the actual control verifier."""
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.resources import _read

    assert os.geteuid() != 0
    content = {
        "normal": b"32\n",
        "whitespace": b" \t32 \n",
        "nonnumeric": b"invalid\n",
        "boundary": b"x" * 4096,
        "oversized": b"x" * 4097,
        "nonascii": b"\xff",
        "symlink": b"unchanged",
        "missing": b"",
    }[case]
    path = tmp_path / "control"
    if case == "symlink":
        target = tmp_path / "target"
        target.write_bytes(content)
        path.symlink_to(target)
    elif case != "missing":
        path.write_bytes(content)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if case == "oversized":
            with pytest.raises(ValueError, match="exceeds its read bound"):
                _read(directory, "control")
        elif case == "nonascii":
            with pytest.raises(UnicodeDecodeError):
                _read(directory, "control")
        elif case == "symlink":
            with pytest.raises(OSError) as error:
                _read(directory, "control")
            assert error.value.errno == errno.ELOOP
            assert target.read_bytes() == content
        elif case == "missing":
            with pytest.raises(FileNotFoundError):
                _read(directory, "control")
        else:
            assert _read(directory, "control") == content.decode("ascii").strip()
        assert os.fstat(directory).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(directory)


def test_actual_nonroot_file_is_refused_before_control_write(tmp_path: Path) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.resources import _write

    assert os.geteuid() != 0
    path = tmp_path / "control"
    path.write_bytes(b"unchanged")
    path.chmod(0o600)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError, match="not exclusively root owned"):
            _write(directory, "control", "32")
        assert path.read_bytes() == b"unchanged"
        assert os.fstat(directory).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(directory)


@pytest.mark.parametrize(
    "case", ["normal", "unsafe-mode", "short-write", "symlink", "missing", "nonascii"]
)
def test_actual_namespace_owned_plain_file_writes(tmp_path: Path, case: str) -> None:
    """Observe complete/refused/short writes without presenting regular files as cgroups."""
    if not _native_contract_profile():
        return
    assert os.geteuid() != 0
    source = Path(__file__).resolve().parents[1]
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    script = r"""import errno,json,os,resource,signal,sys
from pathlib import Path
source,site,case,coverage_dir=sys.argv[1:]
sys.path[:0]=[source+'/src',source,site]
recorder=None
if coverage_dir:
 import coverage
 recorder=coverage.Coverage(
  data_file=str(Path(coverage_dir)/'.coverage'),data_suffix=True,branch=True,
  source_pkgs=['tools.protected_runtime_probe.resources'],config_file=False)
 recorder.start()
from tools.protected_runtime_probe.resources import _read,_write
assert os.getresuid()==(0,0,0)and os.getresgid()==(0,0,0)
os.chdir('/data');path=Path('control')
if case=='symlink':
 Path('target').write_bytes(b'unchanged');path.symlink_to('target')
elif case!='missing':
 path.write_bytes(b'unchanged'if case in{'unsafe-mode','nonascii'}else b'')
 path.chmod(0o666 if case=='unsafe-mode'else 0o600)
directory=os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 if case=='normal':
  _write(directory,'control','32');assert path.read_bytes()==b'32'
  assert _read(directory,'control')=='32'
 elif case=='unsafe-mode':
  try:_write(directory,'control','32')
  except ValueError as error:assert 'not exclusively root owned'in str(error)
  else:raise AssertionError('unsafe plain-file mode accepted')
  assert path.read_bytes()==b'unchanged'
 elif case=='short-write':
  previous=resource.getrlimit(resource.RLIMIT_FSIZE)
  signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
  resource.setrlimit(resource.RLIMIT_FSIZE,(32,previous[1]))
  try:
   try:_write(directory,'control','x'*64)
   except OSError as error:assert 'write was incomplete'in str(error)
   else:raise AssertionError('short native write accepted')
  finally:resource.setrlimit(resource.RLIMIT_FSIZE,previous)
  assert path.stat().st_size==32
 elif case=='symlink':
  try:_write(directory,'control','32')
  except OSError as error:assert error.errno==errno.ELOOP
  else:raise AssertionError('linked plain-file target accepted')
  assert Path('target').read_bytes()==b'unchanged'
 elif case=='missing':
  try:_write(directory,'control','32')
  except FileNotFoundError:pass
  else:raise AssertionError('missing plain-file target created')
  assert not path.exists()
 else:
  try:_write(directory,'control','π')
  except UnicodeEncodeError:pass
  else:raise AssertionError('non-ASCII control bytes accepted')
  assert path.read_bytes()==b'unchanged'
 print(json.dumps({'case':case,'plain_file_io':True,
  'cgroup_kernel_controls_exercised':False,'host_qualification':False}))
finally:
 os.close(directory)
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
    for directory_name in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command.extend(("--ro-bind", directory_name, directory_name))
    command.extend(("--proc", "/proc", "--bind", str(data), "/data"))
    if coverage_dir:
        assert Path(coverage_dir).is_absolute() and Path(coverage_dir).is_dir()
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
            case,
            coverage_dir,
        )
    )
    child = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout.splitlines()[-1]) == {
        "case": case,
        "plain_file_io": True,
        "cgroup_kernel_controls_exercised": False,
        "host_qualification": False,
    }


@pytest.mark.parametrize("case", ["invalid-run", "outside-leaf", "undelegated"])
def test_public_scope_refusals_preserve_real_namespace_descriptors(
    tmp_path: Path, case: str
) -> None:
    """Use actual kernel cgroup files read-only; never create a host scope."""
    from fixtures.protected_runtime_role_boundary import run_mapped_public_refusal

    if not _native_contract_profile():
        return
    kernel = None
    if case == "undelegated":
        relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
        candidate = Path("/sys/fs/cgroup") / relative.lstrip("/")
        kernel = candidate if candidate.is_dir() else Path("/sys/fs/cgroup")
        assert (kernel / "cgroup.subtree_control").is_file()
        assert not (kernel / "cgroup.subtree_control").read_text().strip()
    entry = """from tools.protected_runtime_probe.resources import (
 create_resource_scope,enter_resource_leaf,
)
if CASE=='invalid-run':
 expected='invalid prerequisite resource run identifier'
 operation=lambda:create_resource_scope('invalid')
elif CASE=='outside-leaf':
 expected='outside its exact scope'
 operation=lambda:enter_resource_leaf(Path('/not-a-cgroup'))
else:
 assert os.statvfs('/sys/fs/cgroup').f_flag & os.ST_RDONLY
 assert not Path('/sys/fs/cgroup/cgroup.subtree_control').read_text().strip()
 expected='controllers are not already enabled'
 operation=lambda:create_resource_scope('run-0123456789abcdef')
try:operation()
except ValueError as error:
 assert expected in str(error)
 print(json.dumps({'refused':True,'case':CASE},sort_keys=True))
else:raise AssertionError('invalid scope input was accepted')
""".replace("CASE", repr(case))
    before = None if kernel is None else sorted(path.name for path in kernel.iterdir())
    assert run_mapped_public_refusal(
        Path(__file__).resolve().parents[1], tmp_path, entry, cgroup_directory=kernel
    ) == {"refused": True, "case": case}
    if kernel is not None:
        assert sorted(path.name for path in kernel.iterdir()) == before
        assert not (kernel / "cgroup.subtree_control").read_text().strip()
