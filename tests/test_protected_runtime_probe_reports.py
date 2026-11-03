# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real retained report workflow in private namespaces
"""Exercise public controller failure publication through actual kernel mounts."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest
from tools.protected_runtime_probe.prepare import prepare_packet

from fixtures.protected_runtime_image import samsung_backing_or_refusal


def _native_profile_or_refusal(tmp_path: Path) -> bool:
    """Exercise the genuine unsupported-profile refusal outside the captured ABI."""
    if (
        sys.platform == "linux"
        and platform.machine() == "x86_64"
        and sys.version_info[:2] == (3, 12)
    ):
        return True
    source = Path(__file__).resolve().parents[1]
    with pytest.raises(OSError, match="native Linux"):
        prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
    return False


@pytest.mark.parametrize(
    ("anomalous", "write_failure", "retirement_failure"),
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
)
def test_failed_native_preflight_retains_report_and_records_code_view_retirement(
    tmp_path: Path, anomalous: bool, write_failure: bool, retirement_failure: bool
) -> None:
    """Exercise the real public controller's failure publication in a private user namespace.

    System files have unmapped owners, so the native preflight must refuse.
    This is a real namespace failure path, not successful host qualification.
    Removing a still-mounted code view must retain a failed retirement result.
    """
    if not samsung_backing_or_refusal(tmp_path):
        return
    if not _native_profile_or_refusal(tmp_path):
        return
    assert os.geteuid() != 0
    source = Path(__file__).resolve().parents[1]
    backing = tmp_path / "backing"
    backing.mkdir(mode=0o700)
    view_source = tmp_path / "view"
    (view_source / "app").mkdir(parents=True)
    (view_source / "app/fixture.py").write_bytes(b"pass\n")
    if anomalous:
        os.mkfifo(backing / "unexpected-object")
    head = subprocess.check_output(
        ["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=10
    ).strip()
    script = """import errno,hashlib,json,os,resource,signal,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[1],sys.argv[2]]
recorder=None
if sys.argv[6]:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(sys.argv[6])/'.coverage'),data_suffix=True,branch=True,source=['tools.protected_runtime_probe'],config_file=False)
 recorder.start()
from tools.protected_runtime_probe.capsule import CapsuleManifest,CapturedCapsule,FilePin
from tools.protected_runtime_probe.controller import run
from tools.protected_runtime_probe.reports import publish_probe_result
from tools.protected_runtime_probe.storage import measure_backing_tree,unbind
view=Path('/opt/synapse-channel/protected-runtime-qualification/run-0123456789abcdef')
raw=(view/'app/fixture.py').read_bytes()
manifest=CapsuleManifest(sys.argv[3],sys.argv[4],(FilePin('app/fixture.py',hashlib.sha256(raw).hexdigest(),len(raw)),))
capsule=CapturedCapsule(manifest,hashlib.sha256(manifest.canonical_bytes()).hexdigest(),{'app/fixture.py':raw})
def retire():
 if sys.argv[7]=='retirement-failure': view.rmdir()
 info=view.stat()
 unbind(view,expected_identity=(info.st_dev,info.st_ino))
 view.rmdir()
 assert not os.path.lexists(view)
 return {'path':str(view),'removed':True,'source_device':info.st_dev,'source_inode':info.st_ino}
backing=os.open('/backing',os.O_RDONLY|os.O_DIRECTORY)
try:
 try: publish_probe_result(backing,'../invalid',{})
 except ValueError: pass
 else: raise AssertionError('invalid report identifier accepted')
 assert not Path('/backing/report.json').exists()
 write_failure=sys.argv[5]=='write-failure'
 limits=resource.getrlimit(resource.RLIMIT_FSIZE)
 failure=None
 if write_failure:
  signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
  resource.setrlimit(resource.RLIMIT_FSIZE,(100,limits[1]))
 try:
  outcome=run(capsule,backing,view,'run-0123456789abcdef',retire)
 except OSError as error:
  if not write_failure: raise
  failure=str(error)
 finally:
  if write_failure: resource.setrlimit(resource.RLIMIT_FSIZE,limits)
 report=Path('/opt/synapse-channel/protected-runtime-qualification-evidence/run-0123456789abcdef.json')
 if write_failure:
  assert failure is not None and not os.path.lexists(report) and not os.path.lexists(view)
  assert 0<(Path('/backing/report.json').stat().st_size)<=100
  print(json.dumps({'publication_error':failure,'evidence_removed':True,'code_view_removed':True}))
 else:
  value=json.loads(report.read_bytes())
  assert outcome==1 and value['component_checks_passed'] is False
  assert value['phase']=='native-preflight' and value['quiescent'] is False
  if sys.argv[7]=='retirement-failure':
   assert value['code_view_cleanup']['removed'] is False and os.path.lexists(view)
   assert f'[Errno {errno.EBUSY}]' in value['code_view_cleanup']['error']
   assert value['cleanup_error']==value['code_view_cleanup']['error']
  else:
   assert value['code_view_cleanup']['removed'] is True and not os.path.lexists(view)
  assert report.stat().st_mode&0o777==0o444
  bind=value['retention']['evidence_bind']
  lines=Path('/proc/self/mountinfo').read_text().splitlines()
  rows=[line.split(' - ',1)[0].split()for line in lines]
  row=next(row for row in rows if row[0]==str(bind['mount_id']))
  assert {'ro','nosuid','nodev','noexec'}<=set(row[5].split(','))
  ancestry=value['retention']['backing']['ancestry']
  assert ancestry[0]['path']=='/backing' and ancestry[-1]['path']=='/'
  assert ancestry[0]['device']==os.fstat(backing).st_dev
  assert all(type(item['root_controlled'])is bool for item in ancestry)
  if value['retention']['size']['status']=='complete':
   assert value['retention']['size']['size_bytes']==measure_backing_tree(backing)['size_bytes']
  else:
   assert value['retention']['size']['directory_size_bytes']==os.fstat(backing).st_size
  print(json.dumps({'outcome':outcome,'result':value}))
finally:
 os.close(backing)
 if recorder is not None:
  recorder.stop();recorder.save()
"""
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
    for directory in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command.extend(("--ro-bind", directory, directory))
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    if coverage_dir:
        assert Path(coverage_dir).is_absolute() and Path(coverage_dir).is_dir()
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(
        (
            "--proc",
            "/proc",
            "--bind",
            str(backing),
            "/backing",
            "--ro-bind",
            str(view_source),
            "/opt/synapse-channel/protected-runtime-qualification/run-0123456789abcdef",
            "--dir",
            "/opt/synapse-channel/protected-runtime-qualification-evidence",
            "--cap-add",
            "CAP_SYS_ADMIN",
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            script,
            str(source),
            str(Path(pytest.__file__).parents[1]),
            head,
            str(tmp_path),
            "write-failure" if write_failure else "normal",
            coverage_dir,
            "retirement-failure" if retirement_failure else "normal",
        )
    )
    process = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert process.returncode == 0, process.stderr
    if write_failure:
        value = json.loads(process.stdout.splitlines()[-1])
        assert value["evidence_removed"] and value["code_view_removed"]
        failure = json.loads(process.stderr.splitlines()[-1])
        assert failure["schema"] == "synapse-prerequisite-retention-failure.v1"
        assert failure["evidence_bind_remaining"] is False
        assert failure["code_view_cleanup"]["removed"] is True
        assert failure["retention_reason"] == "publication failure; retained for owner recovery"
        assert failure["report_size_bytes"] == (backing / "report.json").stat().st_size
        assert failure["whole_runtime_accepted"] is False
        return
    result = json.loads(process.stdout.splitlines()[-1])["result"]
    assert result["code_view_cleanup"]["removed"] is (not retirement_failure)
    assert result["retention"]["size"]["status"] == ("unavailable" if anomalous else "complete")
    assert result["whole_runtime_accepted"] is False
    assert json.loads((backing / "report.json").read_text()) == result


def test_unprivileged_report_publication_refuses_before_creating_files(tmp_path: Path) -> None:
    """The public publisher does not mutate an ordinary caller's backing directory."""
    from tools.protected_runtime_probe.reports import publish_probe_result

    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            publish_probe_result(-1, "run-0123456789abcdef", {})
        return
    assert os.geteuid() != 0
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    result: dict[str, object] = {}
    try:
        with pytest.raises(PermissionError, match="native root context"):
            publish_probe_result(directory, "run-0123456789abcdef", result)
        assert result == {} and not tuple(tmp_path.iterdir())
    finally:
        os.close(directory)


_SIZED_REPORT = """import errno,json,os,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[1],sys.argv[2]]
recorder=None
if sys.argv[4]:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(sys.argv[4])/'.coverage'),data_suffix=True,branch=True,source=['tools.protected_runtime_probe'],config_file=False)
 recorder.start()
from tools.protected_runtime_probe.reports import publish_probe_result
from tools.protected_runtime_probe.storage import measure_backing_tree
root=os.open('/backing',os.O_RDONLY|os.O_DIRECTORY)
padding=int(sys.argv[3])
try:
 report={'padding':'x'*max(padding,0),'whole_runtime_accepted':False,'component_checks_passed':False}
 if padding==-1:
  report['instrumentation']={'collection_complete':False,
   'retention_hold':{'state':'unknown; owner inspection required'}}
 try:
  path=publish_probe_result(root,'run-0123456789abcdef',report)
 except OSError as error:
  if padding!=-1: raise
  assert error.errno in {errno.EPERM,errno.EACCES}
  evidence=Path('/opt/synapse-channel/protected-runtime-qualification-evidence')
  assert not (evidence/'run-0123456789abcdef.json').exists()
  assert Path('/backing/report.json').stat().st_size==0
  print(json.dumps({'mount_denied':True,'report_placeholder_removed':True}))
 else:
  assert padding>=0
  value=json.loads(path.read_bytes())
  measured=measure_backing_tree(root)
  assert value['retention']['size']['size_bytes']==measured['size_bytes']
  assert value['padding']=='x'*padding
  assert value['whole_runtime_accepted'] is False
  assert path.stat().st_mode&0o777==0o444
  try: path.write_bytes(b'not permitted')
  except OSError as error: assert error.errno==errno.EROFS
  else: raise AssertionError('published evidence remained writable')
  print(json.dumps({'size_bytes':measured['size_bytes'],'padding_bytes':padding,'readonly':True}))
finally:
 os.close(root)
 if recorder is not None:
  recorder.stop();recorder.save()
"""


@pytest.mark.parametrize("padding", [0, 5000, 95000, -1])
def test_actual_report_size_and_missing_mount_capability(tmp_path: Path, padding: int) -> None:
    """Real file-size accounting spans decimal widths; real mount denial retires its placeholder."""
    if not _native_profile_or_refusal(tmp_path):
        return
    source = Path(__file__).resolve().parents[1]
    backing = tmp_path / "backing"
    backing.mkdir()

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
    for directory in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command.extend(("--ro-bind", directory, directory))
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    if coverage_dir:
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(
        (
            "--proc",
            "/proc",
            "--bind",
            str(backing),
            "/backing",
            "--dir",
            "/opt/synapse-channel/protected-runtime-qualification-evidence",
            "--cap-drop",
            "ALL",
        )
    )
    if padding >= 0:
        command.extend(("--cap-add", "CAP_SYS_ADMIN"))
    command.extend(
        (
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            _SIZED_REPORT,
            str(source),
            str(Path(pytest.__file__).parents[1]),
            str(padding),
            coverage_dir,
        )
    )
    process = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.splitlines()[-1])
    if padding == -1:
        assert result == {"mount_denied": True, "report_placeholder_removed": True}
        failure = json.loads(process.stderr.splitlines()[-1])
        assert failure["evidence_bind"] is None and failure["evidence_bind_remaining"] is False
        assert failure["whole_runtime_accepted"] is False
        assert failure["instrumentation"] == {
            "collection_complete": False,
            "retention_hold": {"state": "unknown; owner inspection required"},
        }
    else:
        assert result["readonly"] is True and result["size_bytes"] > padding


@pytest.mark.parametrize("case", ["extra-field", "wrong-type", "non-finite"])
def test_invalid_child_observation_still_publishes_a_failed_report(
    tmp_path: Path, case: str
) -> None:
    """Refuse an actual child's bad event and publish its failure in a private namespace."""
    if not _native_profile_or_refusal(tmp_path):
        return
    source = Path(__file__).resolve().parents[1]
    backing = tmp_path / "backing"
    backing.mkdir(mode=0o700)
    event: dict[str, object] = {
        "event": "writer-probe-complete",
        "marker_sha256": "a" * 64,
        "marker_bytes": 36,
        "base_write_errno": 30,
        "scm_rights_errno": 1,
    }
    if case == "extra-field":
        event["extra"] = "unaccepted"
    else:
        event["marker_bytes"] = "36" if case == "wrong-type" else float("nan")
    raw = json.dumps(event).encode("ascii") + b"\n"
    script = """import asyncio,json,os,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[1],sys.argv[2]]
from tools.protected_runtime_probe.controller import read_probe_event
from tools.protected_runtime_probe.reports import publish_probe_result
async def observe():
 report={'component_checks_passed':False,'whole_runtime_accepted':False,'quiescent':False}
 child=await asyncio.create_subprocess_exec(
  sys.executable,'-I','-B','-S','-c',
  'import sys;sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))',
  sys.argv[3],stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
 try:
  try:
   report['writer']=await read_probe_event(child,'writer-probe-complete')
  except ValueError as error:
   report['error']=str(error)
  else:
   raise AssertionError('invalid child event accepted')
 finally:
  await child.wait()
 assert child.returncode==0
 assert 'writer'not in report
 root=os.open('/backing',os.O_RDONLY|os.O_DIRECTORY)
 try:
  target=publish_probe_result(root,'run-0123456789abcdef',report)
 finally:
  os.close(root)
 actual=json.loads(target.read_bytes())
 assert actual['component_checks_passed']is False and 'writer'not in actual
 print(json.dumps({'published':True,'result':actual}))
asyncio.run(observe())
"""
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
    for directory in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command.extend(("--ro-bind", directory, directory))
    command.extend(
        (
            "--proc",
            "/proc",
            "--bind",
            str(backing),
            "/backing",
            "--dir",
            "/opt/synapse-channel/protected-runtime-qualification-evidence",
            "--cap-add",
            "CAP_SYS_ADMIN",
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            script,
            str(source),
            str(Path(pytest.__file__).parents[1]),
            raw.hex(),
        )
    )
    process = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert process.returncode == 0, process.stderr
    actual = json.loads(process.stdout.splitlines()[-1])
    assert actual["published"] and actual["result"]["component_checks_passed"] is False
    assert "writer" not in actual["result"]
    assert json.loads((backing / "report.json").read_text()) == actual["result"]
