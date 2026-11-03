# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — source-bound diagnostic worker measurements
"""Pair genuine command behavior with separately labelled execution observations."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

_MEMORY_CLI = """import contextlib,hashlib,io,json,resource,runpy,sys,traceback
from pathlib import Path
import coverage
settings=json.loads(sys.argv[1]);sys.argv=['analysis',*sys.argv[2:]]
collector=coverage.Coverage(data_file=None,branch=settings['branch'],
 include=settings['files'],config_file=False)
sys.path[:0]=[settings['source'],settings['source']+'/src']
assert not list(Path.cwd().iterdir())
original_stdout=io.StringIO();code=0;failure=None
collector.start();collector.switch_context('measured-diagnostic/limited-cli')
try:
 with contextlib.redirect_stdout(original_stdout):
  try:runpy.run_module('tools.protected_runtime_probe.instrumentation_analysis',run_name='__main__')
  except SystemExit as error:
   code=error.code if type(error.code)is int else 0 if error.code is None else 1
   if isinstance(error.code,str):print(error.code,file=sys.stderr)
  except BaseException as error:
   failure={'type':type(error).__name__,'message':str(error)}
   traceback.print_exc();code=1
finally:
 sys.setprofile(None);collector.stop()
data=collector.get_data()
value={'schema':'synapse.test-memory-measurement.v1','profile':'memory-measured public CLI',
 'coverage_version':coverage.__version__,'original_exit':code,
 'original_stdout':original_stdout.getvalue(),'original_failure':failure,
 'rlimit_fsize':list(resource.getrlimit(resource.RLIMIT_FSIZE)),
 'collector_files':sorted(path.name for path in Path.cwd().iterdir()),
 'data_hex':data.dumps().hex(),
 'source_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest()
  for name in sorted(data.measured_files())}}
raw=json.dumps(value,sort_keys=True)
assert len(raw)<=2*1024*1024
print(raw)
raise SystemExit(code)
"""


def run_memory_measured_analysis_cli(
    arguments: list[str], source: Path, evidence: Path, prepare_child: Callable[[], None]
) -> subprocess.CompletedProcess[str]:
    """Measure real limited CLI execution without writing child collector databases.

    Parameters
    ----------
    arguments:
        Original interpreter, flags, module and CLI arguments for the real analysis.
    source:
        Actual current checkout whose probe source hashes are independently retained.
    evidence:
        Parent-owned diagnostic transport receipt, written outside the child limit.
    prepare_child:
        Real child-only file-limit setter used by the unmodified CLI case too.

    Returns
    -------
    subprocess.CompletedProcess[str]
        Original CLI exit, product stdout and original exception stderr. A labelled
        measurement profile is retained separately; it is not cold qualification.

    Notes
    -----
    Only public Coverage/CoverageData APIs collect, serialize and merge actual
    observations. The stdout pipe is drained by communicate; no limited child
    SQLite autosave or extra undrained transport descriptor exists.
    """
    import coverage
    from tools.protected_runtime_probe.roles import ROOT_COMMAND_ENV

    assert arguments[1:4] == ["-B", "-m", "tools.protected_runtime_probe.instrumentation_analysis"]
    expected = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((source / "tools/protected_runtime_probe").glob("*.py"))
    }
    active = coverage.Coverage.current()
    scratch = evidence.parent / "memory-collector-cwd"
    scratch.mkdir(mode=0o700)
    settings = {
        "files": sorted(expected),
        "source": str(source),
        "branch": True if active is None else active.get_option("run:branch"),
    }
    measured = subprocess.run(
        [arguments[0], "-B", "-c", _MEMORY_CLI, json.dumps(settings), *arguments[4:]],
        cwd=scratch,
        capture_output=True,
        text=True,
        timeout=90,
        env=ROOT_COMMAND_ENV,
        preexec_fn=prepare_child,
        check=False,
    )
    assert 0 < len(measured.stdout) <= 2 * 1024 * 1024, measured.stderr
    value = json.loads(measured.stdout)
    assert value["schema"] == "synapse.test-memory-measurement.v1"
    assert value["coverage_version"] == coverage.__version__
    assert value["original_exit"] == measured.returncode
    assert value["rlimit_fsize"] == [4096, 4096]
    assert value["collector_files"] == [] and not list(scratch.iterdir())
    assert value["source_sha256"]
    assert all(expected.get(name) == pin for name, pin in value["source_sha256"].items())
    data = coverage.CoverageData(no_disk=True)
    data.loads(bytes.fromhex(value["data_hex"]))
    assert set(data.measured_files()) == set(value["source_sha256"])
    assert any(data.lines(name) for name in data.measured_files())
    assert data.measured_contexts() == {"measured-diagnostic/limited-cli"}
    if active is not None:
        active.get_data().update(data)
    evidence.write_text(json.dumps(value, indent=2) + "\n")
    return subprocess.CompletedProcess(
        arguments, measured.returncode, value["original_stdout"], measured.stderr
    )


_CAPTURE_CHANGE = """capture_change={}
def change_after_stat(frame,event,argument):
 "Grow only the actual retained input after its real first metadata read."
 if event!='c_return' or argument is not os.fstat:return
 if frame.f_code.co_filename!=settings['worker']or frame.f_code.co_name!='_private_bytes':return
 if frame.f_locals.get('path')!=Path(settings['configuration']):return
 sys.setprofile(None)
 descriptor=frame.f_locals['descriptor'];assert type(descriptor)is int
 before=os.fstat(descriptor)
 writer=os.open(settings['configuration'],os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  opened=os.fstat(writer)
  assert(opened.st_dev,opened.st_ino)==(before.st_dev,before.st_ino)
  assert os.write(writer,b' ')==1
  os.fsync(writer)
 finally:os.close(writer)
 after=os.fstat(descriptor)
 assert after.st_size==before.st_size+1
 capture_change.update({'pid':os.getpid(),'uid':os.geteuid(),
  'device':before.st_dev,'inode':before.st_ino,
  'before_size':before.st_size,'after_size':after.st_size,
  'configuration':settings['configuration'],'fault':'append-after-retained-fstat'})
if settings.get('capture_change')=='1':sys.setprofile(change_after_stat)
"""


_MEMORY_WORKER = (
    """import contextlib,hashlib,io,json,os,runpy,sys,traceback
from pathlib import Path
settings=json.loads(sys.argv[1]);archive=Path(settings['wheel'])
assert hashlib.sha256(archive.read_bytes()).hexdigest()==settings['wheel_sha256']
sys.path.insert(0,str(archive));import coverage
assert coverage.__file__==str(archive)+'/coverage/__init__.py'
worker=Path(settings['worker'])
assert hashlib.sha256(worker.read_bytes()).hexdigest()==settings['worker_sha256']
collector=coverage.Coverage(data_file=None,timid=True,branch=True,config_file=False,include=[str(worker)])
sys.argv=[str(worker),'--configuration',settings['configuration'],'--sha256',settings['configuration_sha256']]
product=io.StringIO();code=0;failure=None
"""
    + _CAPTURE_CHANGE
    + """
collector.start();collector.switch_context('measured-diagnostic')
try:
 with contextlib.redirect_stdout(product):
  try:runpy.run_path(str(worker),run_name='__main__')
  except SystemExit as error:code=0 if error.code is None else int(error.code)
  except BaseException as error:
   failure={'type':type(error).__name__,'message':str(error)}
   traceback.print_exc();code=1
finally:collector.stop()
data=collector.get_data()
print(json.dumps({'profile':'measured-diagnostic','first_import_qualification':False,
 'isolation_qualification':False,'flag_qualification':False,
 'original_exit':code,'original_stdout':product.getvalue(),'original_failure':failure,
 'capture_change':capture_change,
 'worker_sha256':settings['worker_sha256'],'observer_archive_sha256':settings['wheel_sha256'],
 'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,'no_bytecode':sys.flags.dont_write_bytecode,
 'coverage_version':coverage.__version__,'data_hex':data.dumps().hex()}))
raise SystemExit(code)
"""
)


def _worker_observer_settings(
    arguments: list[str], artifact: Path | None
) -> tuple[Path, Path, dict[str, str]]:
    """Bind observer provenance independently of the untrusted worker configuration."""
    worker = Path(arguments[-5])
    pin = json.loads((worker.parent / "native-dependencies.lock.json").read_bytes())["coverage"]
    configuration = Path(arguments[-3])
    if artifact is None:
        metadata = json.loads(configuration.read_bytes())
        artifact = Path(metadata["code_view"]) / "runtime/artifacts" / pin["filename"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == pin["sha256"]
    return (
        worker,
        artifact,
        dict(
            wheel=str(artifact),
            wheel_sha256=pin["sha256"],
            worker=str(worker),
            worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest(),
            configuration=str(configuration),
            configuration_sha256=arguments[-1],
        ),
    )


def measure_paired_analysis_worker(
    arguments: list[str],
    evidence: Path,
    original: subprocess.CompletedProcess[str],
    prepare_child: Callable[[], None] | None,
    artifact: Path | None = None,
    *,
    capture_change: bool = False,
) -> None:
    """Pair cold worker behavior with source-bound in-memory execution observations.

    Parameters
    ----------
    arguments:
        Worker command using a fresh equivalent actual input directory and the
        exact original flags, including deliberately omitted negative flags.
    evidence:
        Parent-owned receipt retaining both original and measured outcomes.
    original:
        Completed unmodified cold command, which remains the isolation proof.
    prepare_child:
        Same actual child-only resource setter used for the cold case, or None.
    artifact:
        Independently retained original wheel for refusal before configuration reads;
        otherwise derive its location from the real copied configuration.
    capture_change:
        Apply the same explicit retained-input growth schedule as the cold fault case.

    Notes
    -----
    Only the production worker contributes to this diagnostic's product data.
    The sha-verified original wheel supplies the pure-Python observer; it never
    executes a deliberately corrupted private collector. This measured profile
    cannot qualify first import, flags or isolation, even when its pair agrees.
    """
    import coverage
    from tools.protected_runtime_probe.roles import ROOT_COMMAND_ENV

    worker, artifact, settings = _worker_observer_settings(arguments, artifact)
    source_sha = settings["worker_sha256"]
    settings["capture_change"] = "1" if capture_change else "0"
    scratch = evidence.parent / "observer-cwd"
    scratch.mkdir(mode=0o700)
    measured = subprocess.run(
        [arguments[0], *arguments[1:-5], "-c", _MEMORY_WORKER, json.dumps(settings)],
        cwd=scratch,
        capture_output=True,
        text=True,
        timeout=90,
        env=ROOT_COMMAND_ENV,
        preexec_fn=prepare_child,
        check=False,
    )
    assert 0 < len(measured.stdout) <= 2 * 1024 * 1024, measured.stderr
    value = json.loads(measured.stdout)
    assert value["coverage_version"] == coverage.__version__
    assert value["observer_archive_sha256"] == settings["wheel_sha256"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == settings["wheel_sha256"]
    assert value["worker_sha256"] == source_sha == hashlib.sha256(worker.read_bytes()).hexdigest()
    assert value["original_exit"] == measured.returncode == original.returncode
    assert value["original_stdout"] == original.stdout
    cold_error = original.stderr.strip().splitlines()
    measured_error = measured.stderr.strip().splitlines()
    assert (cold_error[-1].partition(":")[0] if cold_error else "") == (
        measured_error[-1].partition(":")[0] if measured_error else ""
    ), (original.stderr, measured.stderr)
    assert not list(scratch.iterdir())
    if capture_change:
        _require_capture_change(value["capture_change"], Path(arguments[-3]))
    data = coverage.CoverageData(no_disk=True)
    data.loads(bytes.fromhex(value["data_hex"]))
    assert set(data.measured_files()) == {str(worker)} and data.arcs(str(worker))
    assert data.measured_contexts() == {"measured-diagnostic"}
    assert value["profile"] == "measured-diagnostic"
    assert all(
        value[key] is False
        for key in ("first_import_qualification", "isolation_qualification", "flag_qualification")
    )
    active = coverage.Coverage.current()
    if active is not None:
        assert active.get_option("run:branch") is True
        active.get_data().update(data)
    value["cold_original"] = dict(
        exit=original.returncode, stdout=original.stdout, stderr=original.stderr
    )
    value["measured_stderr"] = measured.stderr
    evidence.write_text(json.dumps(value, indent=2) + "\n")


_COLD_CAPTURE_WORKER = (
    """import hashlib,json,os,runpy,sys
from pathlib import Path
settings=json.loads(sys.argv[1]);worker=Path(settings['worker'])
assert hashlib.sha256(worker.read_bytes()).hexdigest()==settings['worker_sha256']
sys.argv=[str(worker),'--configuration',settings['configuration'],'--sha256',settings['configuration_sha256']]
"""
    + _CAPTURE_CHANGE
    + """
try:runpy.run_path(str(worker),run_name='__main__')
finally:
 sys.setprofile(None)
 with Path(settings['fault_receipt']).open('x')as target:json.dump(capture_change,target)
"""
)


def _require_capture_change(value: dict[str, int | str], configuration: Path) -> None:
    """Bind the recorded real mutation to this owned input and its actual inode."""
    import os

    assert value["fault"] == "append-after-retained-fstat"
    assert value["configuration"] == str(configuration)
    assert value["uid"] == os.geteuid() and int(value["pid"]) > 0
    actual = configuration.stat()
    assert (value["device"], value["inode"]) == (actual.st_dev, actual.st_ino)
    assert int(value["before_size"]) + 1 == value["after_size"] == actual.st_size


def run_worker_with_capture_change(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Grow the real CLI input precisely between retained fstat and read.

    Parameters
    ----------
    arguments:
        Actual isolated worker command over a fresh caller-owned private copy.

    Returns
    -------
    subprocess.CompletedProcess[str]
        Original public-worker exception exit, stdout and stderr. This explicit
        fault schedule proves refusal, not first-import or host qualification.
    """
    from tools.protected_runtime_probe.roles import ROOT_COMMAND_ENV

    _worker, _artifact, settings = _worker_observer_settings(arguments, None)
    configuration = Path(arguments[-3])
    receipt = configuration.parent / "capture-change.json"
    settings.update(capture_change="1", fault_receipt=str(receipt))
    result = subprocess.run(
        [arguments[0], *arguments[1:-5], "-c", _COLD_CAPTURE_WORKER, json.dumps(settings)],
        capture_output=True,
        text=True,
        timeout=30,
        env=ROOT_COMMAND_ENV,
        check=False,
    )
    _require_capture_change(json.loads(receipt.read_text()), configuration)
    return result
