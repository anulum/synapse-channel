# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual interrupted recorder campaign boundaries
"""Exercise controller recording without substituting namespace root for host qualification."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import websockets
from tools.protected_runtime_probe.capsule import capture_capsule
from tools.protected_runtime_probe.instrumentation_campaign import InstrumentationCampaign
from tools.protected_runtime_probe.prepare import prepare_packet

from fixtures.protected_runtime_image import (
    materialize_instrumentation_view,
    samsung_backing_or_refusal,
)

_SOURCE = Path(__file__).resolve().parents[1]
_CHILD = """import os,sys,json,stat,hashlib
from pathlib import Path
view=Path(sys.argv[1]);digest=sys.argv[2];backing=Path(sys.argv[3]);case=sys.argv[4]
sys.path[:0]=[str(view/'probe'),str(view/'app')]
from protected_runtime_probe.capsule import capture_capsule,require_posix_descriptors
from protected_runtime_probe.instrumentation_campaign import InstrumentationCampaign
capsule=capture_capsule(view,digest)
backing.mkdir(mode=0o700);fd=os.open(backing,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 if case=='invalid-id':
  try:InstrumentationCampaign(capsule,fd,view,'invalid')
  except ValueError:pass
  else:raise AssertionError('invalid run was accepted')
  assert not list(backing.iterdir())
  print(json.dumps({'refused':True,'host_root_qualified':False}))
 else:
  campaign=InstrumentationCampaign(capsule,fd,view,'run-0123456789abcdef')
  try:
   assert 'protected_runtime_probe.controller' not in sys.modules
   from protected_runtime_probe import controller
   require_posix_descriptors('posix')
   try:campaign.output('writer')
   except ValueError:pass
   else:raise AssertionError('absent writer output was enrolled')
   try:campaign.observe_process('controller',os.getpid())
   except ValueError:pass
   else:raise AssertionError('controller was enrolled twice')
   controller_fd=campaign.output('controller').descriptor
   if case not in {'close','closed-descriptor-reuse','close-save-failure'}:
    try:campaign.finish_collection(quiescent=case=='missing-processes',
     producers_succeeded=case=='missing-processes')
    except ValueError as error:assert 'all six' in str(error)
    else:raise AssertionError('interrupted campaign was accepted')
    try:campaign.enroll_roles({})
    except ValueError as error:assert 'fresh enrollment' in str(error)
    else:raise AssertionError('terminal campaign accepted enrollment')
    try:campaign.finish_collection(quiescent=True,producers_succeeded=True)
    except ValueError as error:assert 'already terminal' in str(error)
    else:raise AssertionError('terminal campaign collected twice')
   hold=campaign.failure_retention()
   assert hold['backing_relative_path']=='sealed-recordings'
  finally:
   if case=='close-save-failure':
    canary=backing/'recordings/controller/coverage.sqlite'
    with canary.open('xb') as stream:stream.write(b'preserved existing dataset')
    try:campaign.close()
    except FileExistsError:pass
    else:raise AssertionError('close overwrote an existing dataset')
   campaign.close();campaign.close()
  if case in {'closed-descriptor-reuse','close-save-failure'}:
   unrelated=backing/'unrelated';unrelated.mkdir(mode=0o700)
   borrowed=[]
   try:
    for _ in range(12):borrowed.append(os.open(unrelated,os.O_RDONLY|os.O_DIRECTORY))
    assert controller_fd in borrowed
    assert any(os.fstat(item).st_ino==hold['source_inode'] for item in borrowed) is False
    for operation in (
     lambda:campaign.output('controller'),lambda:campaign.launch('controller'),
     lambda:campaign.observe_process('controller',os.getpid()),
     lambda:campaign.enroll_roles({}),
     lambda:campaign.finish_collection(quiescent=True,producers_succeeded=True),
     campaign.failure_retention):
     try:operation()
     except ValueError:pass
     else:raise AssertionError('closed campaign retained a live resource API')
    campaign.close()
    assert all(os.fstat(item).st_ino==unrelated.stat().st_ino for item in borrowed)
    assert not list(unrelated.iterdir())
   finally:
    for item in borrowed:os.close(item)
  if case=='close-save-failure':
   assert canary.read_bytes()==b'preserved existing dataset'
   assert not list((backing/'sealed-recordings').iterdir())
   print(json.dumps({'refused':True,'host_root_qualified':False}))
   raise SystemExit(0)
  output=backing/'recordings/controller/coverage.sqlite'
  info=output.stat()
  assert (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))==(0,0,0o600)
  assert output.read_bytes().startswith(b'SQLite format 3\\x00')
  assert not list((backing/'sealed-recordings').iterdir())
  print(json.dumps({'controller_size':info.st_size,'host_root_qualified':False,
   'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'accepted':False}))
finally:os.close(fd)
"""
_READ = """import sys,json
sys.path.insert(0,sys.argv[1])
import coverage
assert coverage.__version__=='7.17.0'
data=coverage.CoverageData(basename=sys.argv[2]);data.read()
try:
 print(json.dumps({'contexts':sorted(data.measured_contexts()),
  'arcs':{name:len(data.arcs(name)or[])for name in data.measured_files()}}))
finally:data.close(force=True)
"""


@pytest.fixture(scope="module")
def campaign_view(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str] | None:
    """Build a complete real input view, with no installed host-root execution."""
    directory = tmp_path_factory.mktemp("recorder-campaign-view")
    if not samsung_backing_or_refusal(directory):
        return None
    if (sys.platform, platform.machine(), sys.version_info[:2]) != ("linux", "x86_64", (3, 12)):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(_SOURCE, directory / "unsupported", directory / "runs")
        return None
    supplied = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL")
    pin = json.loads(
        (_SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json").read_bytes()
    )["coverage"]
    artifact = Path(supplied) if supplied else directory / pin["filename"]
    if not supplied:
        with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
            artifact.write_bytes(response.read(8 * 1024 * 1024 + 1))
    return materialize_instrumentation_view(
        _SOURCE, Path(websockets.__file__).parent, directory, artifact
    )


def test_actual_ordinary_caller_cannot_start_a_recorder_campaign(
    campaign_view: tuple[Path, str] | None,
) -> None:
    """The root gate refuses a real ordinary caller before accessing a backing FD."""
    if campaign_view is None:
        return
    view, digest = campaign_view
    assert os.geteuid() != 0
    capsule = capture_capsule(view, digest)
    with pytest.raises(PermissionError, match="actual root controller"):
        InstrumentationCampaign(capsule, -1, view, "run-0123456789abcdef")


@pytest.mark.parametrize(
    "case",
    [
        "close",
        "not-quiescent",
        "missing-processes",
        "invalid-id",
        "closed-descriptor-reuse",
        "close-save-failure",
    ],
)
def test_actual_interrupted_controller_cannot_seal_or_claim_a_complete_campaign(
    tmp_path: Path, campaign_view: tuple[Path, str] | None, case: str
) -> None:
    """Private single-UID namespace execution proves own recording and terminal refusal only."""
    if campaign_view is None:
        return
    view, digest = campaign_view
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
        "--tmpfs",
        "/",
    ]
    for directory in ("/usr", "/lib", "/lib64", str(view)):
        command += ["--ro-bind", directory, directory]
    command += [
        "--proc",
        "/proc",
        "--bind",
        str(tmp_path),
        str(tmp_path),
        "--cap-add",
        "ALL",
        "--chdir",
        "/",
        "/usr/bin/python3.12",
        "-I",
        "-B",
        "-S",
        "-c",
        _CHILD,
        str(view),
        digest,
        str(tmp_path / "backing"),
        case,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["host_root_qualified"] is False
    if case in {"invalid-id", "close-save-failure"}:
        assert observed["refused"] is True
        return
    output = tmp_path / "backing/recordings/controller/coverage.sqlite"
    assert hashlib.sha256(output.read_bytes()).hexdigest() == observed["sha256"]
    result = subprocess.run(
        [
            "/usr/bin/python3.12",
            "-I",
            "-B",
            "-S",
            "-c",
            _READ,
            str(view / "runtime/python/lib/python3.12/dist-packages"),
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    measurement = json.loads(result.stdout)
    assert measurement["contexts"] == ["controller"]
    assert measurement["arcs"][str(view / "probe/protected_runtime_probe/controller.py")] > 0
    assert observed["accepted"] is False
