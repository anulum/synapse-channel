# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real private-namespace recording dataset retention
"""Exercise data-only readonly binds inside an ordinary single-UID private namespace."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest
from tools.protected_runtime_probe.instrumentation_retention import publish_recording_outputs

from fixtures.capable_bwrap import capable_bwrap

_CHILD = """import sys,os,json
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/src']
from tools.protected_runtime_probe.instrumentation import RECORDING_ROLES
from tools.protected_runtime_probe.instrumentation_recording import MAX_RECORDING_BYTES
from tools.protected_runtime_probe.instrumentation_retention import publish_recording_outputs
from tools.protected_runtime_probe.storage import unbind
directory=Path(sys.argv[2]);case=sys.argv[3];directory.mkdir(mode=0o700)
for role in RECORDING_ROLES:
 path=directory/(role+'.sqlite');path.write_bytes(b'opaque retained measurement bytes')
 path.chmod(0o444)
manifest=directory/'manifest.json';manifest.write_text('{}');manifest.chmod(0o444)
directory.chmod(0o555)
if case=='extra':
 (directory/'unexpected').write_bytes(b'extra')
elif case=='missing':
 (directory/'writer.sqlite').unlink()
elif case=='directory-mode':
 directory.chmod(0o755)
elif case=='file-mode':
 manifest.chmod(0o644)
elif case=='hardlink':
 os.link(manifest,directory.parent/'manifest-alias')
elif case=='symlink':
 manifest.unlink();manifest.symlink_to(directory/'client.sqlite')
elif case=='oversized':
 target=directory/'client.sqlite';target.chmod(0o600)
 with target.open('r+b')as stream:stream.truncate(MAX_RECORDING_BYTES+1)
 target.chmod(0o444)
fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
run='invalid' if case=='invalid-id' else 'run-0123456789abcdef'
target=Path('/opt/synapse-channel/protected-runtime-qualification-evidence')/run
fault={}
if case=='changed-readonly-bind':
 import subprocess
 def remount_after_bind(frame,event,argument):
  if event!='return' or frame.f_code.co_name!='bind_readonly':return
  if (Path(frame.f_code.co_filename)!=
   Path(sys.argv[1])/'tools/protected_runtime_probe/storage.py'):return
  sys.setprofile(None)
  rows=[line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
  original=[row for row in rows if row[4]==str(target)]
  assert len(original)==1 and 'ro' in original[0][5].split(',')
  subprocess.run(['/usr/bin/mount','-o','remount,bind,rw,nosuid,nodev,noexec',str(target)],
   check=True,capture_output=True,text=True,timeout=5)
  rows=[line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
  changed=[row for row in rows if row[4]==str(target)]
  assert len(changed)==1 and changed[0][0]==original[0][0]
  assert 'rw' in changed[0][5].split(',')
  fault.update({'mount_id':int(original[0][0]),'target':str(target),
   'pid':os.getpid(),'actual_readonly_to_writable_remount':True})
 sys.setprofile(remount_after_bind)
try:
 if case!='normal':
  before={path.name:path.read_bytes() for path in directory.iterdir()}
  expected=PermissionError if case=='mount-denied' else ValueError
  try:publish_recording_outputs(fd,run)
  except expected as error:
   if case=='invalid-id':assert str(error)=='invalid recording exposure run identifier',error
   if case=='mount-denied':assert error.errno==1,error
   if case=='changed-readonly-bind':assert 'exact data-only readonly bind' in str(error),error
  else:raise AssertionError('unsealed set was exposed')
  assert not target.exists()
  assert {path.name:path.read_bytes() for path in directory.iterdir()}==before
  if case=='changed-readonly-bind':
   assert fault['actual_readonly_to_writable_remount']is True
   assert all(int(row.split()[0])!=fault['mount_id']
    for row in Path('/proc/self/mountinfo').read_text().splitlines())
  print(json.dumps({'case':case,'refused':True,'host_root_qualified':False,'fault':fault}))
 else:
  result=publish_recording_outputs(fd,run);binding=result['dataset_bind']
  assert binding['target']==str(target)
  assert all(binding[flag]is True for flag in ('readonly','nosuid','nodev','noexec'))
  assert binding['mount_id']!=binding['source_mount_id']
  assert target.stat().st_ino==os.fstat(fd).st_ino
  for role in RECORDING_ROLES:
   assert (target/(role+'.sqlite')).read_bytes()==b'opaque retained measurement bytes'
  try:(target/'new').write_bytes(b'forbidden')
  except OSError as error:assert error.errno==30
  else:raise AssertionError('retained view was writable')
  identity=os.fstat(fd)
  unbind(target,expected_identity=(identity.st_dev,identity.st_ino));target.rmdir()
  assert not target.exists()
  print(json.dumps({'case':case,'binding':binding,'host_root_qualified':False}))
finally:sys.setprofile(None);os.close(fd)
"""

_MEASURED_CHILD = r"""import hashlib,json,sys
from pathlib import Path
source,output,package,driver=map(Path,sys.argv[4:8]);original=sys.argv[8]
sys.path.insert(0,str(package.parent));import coverage
sys.argv=sys.argv[:4]
collector=coverage.Coverage(data_file=str(output/'.coverage'),data_suffix=True,
 branch=True,include=[str(source/'tools/protected_runtime_probe/*.py')],
 config_file=False,context='measured-diagnostic/readonly-recording-retention')
collector.start()
try:exec(compile(original,'<readonly-retention-driver>','exec'))
finally:
 collector.stop();collector.save()
 data=collector.get_data();data_path=Path(data.data_filename())
 sources=[{'path':name,'sha256':hashlib.sha256(Path(name).read_bytes()).hexdigest()}
  for name in sorted(data.measured_files())]
 with (output/('source-binding-'+data_path.name+'.json')).open('x') as stream:
  json.dump({'data_filename':data_path.name,'sources':sources,
   'driver_path':str(driver),'driver_sha256':hashlib.sha256(driver.read_bytes()).hexdigest(),
   'profile':'measured-diagnostic','host_root_qualified':False},stream)
  stream.write('\n')
"""


def test_actual_ordinary_caller_cannot_publish_a_recording_exposure() -> None:
    """The public root gate refuses before opening any caller-supplied descriptor."""
    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            publish_recording_outputs(-1, "run-0123456789abcdef")
        return
    assert os.geteuid() != 0
    with pytest.raises(PermissionError, match="actual root controller"):
        publish_recording_outputs(-1, "run-0123456789abcdef")


@pytest.mark.parametrize(
    "case",
    [
        "normal",
        "extra",
        "missing",
        "directory-mode",
        "file-mode",
        "hardlink",
        "symlink",
        "oversized",
        "invalid-id",
        "mount-denied",
        "changed-readonly-bind",
    ],
)
def test_actual_dataset_bind_requires_a_complete_readonly_sealed_set(
    tmp_path: Path, case: str
) -> None:
    """Private namespace root proves mount mechanics only, with no host role or root claim."""
    if not _native_profile_or_refusal():
        return
    result = _execute_retention(tmp_path, case)
    _require_retention_result(result, case)


def _native_profile_or_refusal() -> bool:
    """Exercise the genuine ABI refusal when this namespace fixture cannot run."""
    if (sys.platform, platform.machine(), sys.version_info[:2]) == ("linux", "x86_64", (3, 12)):
        return True
    from tools.protected_runtime_probe.native_closure import require_native_runtime_abi

    with pytest.raises(OSError, match="native Linux"):
        require_native_runtime_abi(
            sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1
        )
    return False


def _execute_retention(
    tmp_path: Path, case: str, measurement_directory: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the original real namespace operation with optional separate source-bound tracing."""
    source = Path(__file__).resolve().parents[1]
    supplied = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR")
    if measurement_directory is None and supplied:
        measurement_directory = Path(supplied) / tmp_path.name
        measurement_directory.mkdir(mode=0o700)
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
        "--tmpfs",
        "/",
    ]
    for directory in (
        "/usr",
        "/media",
        "/home",
        "/lib",
        "/lib64",
        str(Path(sys.base_prefix).resolve()),
    ):
        command += ["--ro-bind", directory, directory]
    command += [
        "--proc",
        "/proc",
        "--bind",
        str(tmp_path),
        str(tmp_path),
        "--cap-add",
        "ALL",
    ]
    if case == "mount-denied":
        command += ["--cap-drop", "CAP_SYS_ADMIN"]
    interpreter = [sys.executable, "-I", "-B", "-S", "-c"]
    original_arguments = [str(source), str(tmp_path / "sealed"), case]
    if measurement_directory is None:
        command += [*interpreter, _CHILD, *original_arguments]
    else:
        import coverage

        command += ["--bind", str(measurement_directory), str(measurement_directory)]
        command += [
            *interpreter,
            _MEASURED_CHILD,
            *original_arguments,
            str(source),
            str(measurement_directory),
            str(Path(coverage.__file__).parent),
            str(Path(__file__).resolve()),
            _CHILD,
        ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=20,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )


def _require_retention_result(result: subprocess.CompletedProcess[str], case: str) -> None:
    """Check the actual publication/refusal and its original namespace-only attribution."""
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["case"] == case
    assert observed["host_root_qualified"] is False
    if case == "normal":
        assert observed["binding"]["noexec"] is True
    else:
        assert observed["refused"] is True
        if case == "changed-readonly-bind":
            assert observed["fault"]["actual_readonly_to_writable_remount"] is True
            assert observed["fault"]["mount_id"] > 0
            assert observed["fault"]["pid"] > 0


@pytest.mark.parametrize("case", ["normal", "symlink", "mount-denied", "changed-readonly-bind"])
def test_measured_namespace_retention_binds_real_publication_and_refusal(
    tmp_path: Path, case: str
) -> None:
    """Real mount success and an actual unsafe leaf produce honest source-bound observations."""
    if not _native_profile_or_refusal():
        return
    import coverage

    recordings = tmp_path / "recordings"
    recordings.mkdir(mode=0o700)
    result = _execute_retention(tmp_path, case, recordings)
    _require_retention_result(result, case)
    bindings = list(recordings.glob("source-binding-*.json"))
    assert len(bindings) == 1
    binding = json.loads(bindings[0].read_bytes())
    assert binding["profile"] == "measured-diagnostic"
    assert binding["host_root_qualified"] is False
    driver = Path(binding["driver_path"])
    assert driver == Path(__file__).resolve()
    assert binding["driver_sha256"] == hashlib.sha256(driver.read_bytes()).hexdigest()
    data = coverage.CoverageData(basename=str(recordings / binding["data_filename"]))
    data.read()
    try:
        expected = driver.parents[1] / "tools/protected_runtime_probe/instrumentation_retention.py"
        assert data.arcs(str(expected))
        assert data.measured_contexts() == {"measured-diagnostic/readonly-recording-retention"}
        assert set(data.measured_files()) == {item["path"] for item in binding["sources"]}
        assert all(
            hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
            for item in binding["sources"]
        )
        assert set(recordings.iterdir()) == {bindings[0], recordings / binding["data_filename"]}
    finally:
        data.close(force=True)
