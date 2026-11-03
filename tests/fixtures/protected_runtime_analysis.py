# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real ordinary analysis dataset fixtures
"""Produce truthful diagnostic process datasets without claiming installed host qualification."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .protected_runtime_image import materialize_instrumentation_view

_PRODUCER = """import os,sys
from pathlib import Path
view=Path(sys.argv[1]);sys.path[:0]=[str(view/'probe'),str(view/'app')]
from protected_runtime_probe.capsule import capture_capsule,require_posix_descriptors,FilePin
from protected_runtime_probe.instrumentation_recording import prepare_recording
from protected_runtime_probe.runtime import captured_runtime_selected
capsule=capture_capsule(view,sys.argv[2])
if sys.argv[3]=='controller':
 from synapse_channel.core.protected_write_mounts import parse_protected_writer_mounts
recording=prepare_recording(view,sys.argv[2],role=sys.argv[3],
 output_descriptor=int(sys.argv[4]),uid=0,gid=0)
try:
 with recording.collector.collect():
  require_posix_descriptors('posix')
  assert captured_runtime_selected(capsule)
  pin=capsule.manifest.files[0]
  assert FilePin(pin.path,pin.sha256,pin.size_bytes)==pin
  from synapse_channel.core.protected_write_mounts import parse_protected_writer_mounts
  assert parse_protected_writer_mounts(Path('/proc/self/mountinfo').read_bytes())
finally:recording.save()
"""

_PARENT = """import os,sys,json,hashlib,subprocess
from pathlib import Path
source=Path(sys.argv[1]);view=Path(sys.argv[2]);digest=sys.argv[3];root=Path(sys.argv[4])
sys.path[:0]=[str(source),str(source/'src'),str(view/'app')]
from tools.protected_runtime_probe.capsule import capture_capsule
from tools.protected_runtime_probe.instrumentation import CONFIGURATION,RECORDING_ROLES
from tools.protected_runtime_probe.instrumentation_outputs import (
 create_recording_output,seal_recording_output)
from tools.protected_runtime_probe.instrumentation_analysis import analyze_recordings
from tools.protected_runtime_probe.instrumentation_analysis_inputs import (
 canonical_analysis_metadata)
try:analyze_recordings(Path('/unopened'),'',Path('/unopened'),Path('/unopened'),source,Path('/unopened'))
except PermissionError as error:assert 'ordinary nonroot' in str(error)
else:raise AssertionError('namespace root parsed child datasets')
capsule=capture_capsule(view,digest);configuration=json.loads(capsule.content[CONFIGURATION])
backing=root/'recordings';backing.mkdir(mode=0o700)
sealed=root/'datasets';sealed.mkdir(mode=0o700)
incoming=os.open(backing,os.O_RDONLY|os.O_DIRECTORY)
destination=os.open(sealed,os.O_RDONLY|os.O_DIRECTORY)
datasets=[];children=[]
try:
 for role in RECORDING_ROLES:
  leaf=create_recording_output(incoming,role,0,0)
  try:
   process=subprocess.Popen(['/usr/bin/python3.12','-I','-B','-S','-c',sys.argv[5],
    '/code'if role=='writer'else str(view),digest,role,str(leaf.descriptor)],
    pass_fds=(leaf.descriptor,),
    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
   try:out,error=process.communicate(timeout=40)
   except subprocess.TimeoutExpired:
    process.kill();out,error=process.communicate();raise
   assert process.returncode==0,error.decode('utf-8','replace')
   assert not out
   datasets.append(seal_recording_output(leaf,destination,host_pid=process.pid))
   if role in {'authority','client','operator','namespace-wrapper'}:
    children.append({'returncode':process.returncode,'stderr':''})
  finally:os.close(leaf.descriptor)
 sources=configuration['sources']
 source_sha=hashlib.sha256(canonical_analysis_metadata(sources)).hexdigest()
 manifest={'schema':'synapse.runtime-recordings.v1','run_id':'run-0123456789abcdef',
  'capsule_manifest_sha256':digest,
  'configuration_sha256':hashlib.sha256(capsule.content[CONFIGURATION]).hexdigest(),
  'source_entries_sha256':source_sha,'sources':sources,'code_view':str(view),
  'writer_code_view':'/code','datasets':datasets}
 raw=canonical_analysis_metadata(manifest)
 (sealed/'manifest.json').write_bytes(raw);(sealed/'manifest.json').chmod(0o444)
 report={'schema':'synapse-prerequisite-result.v3','interpreter_profile':'captured-runtime',
  'manifest_sha256':digest,'run_id':manifest['run_id'],'quiescent':True,
  'authority_recording_graceful_exit':True,'children':children,
  'roles':{'synapse-'+role:{'uid':0,'gid':0}for role in ('authority','client','operator','writer')},
  'whole_runtime_accepted':False,'class_consumption_accepted':False,
  'component_checks_passed':False,
  'test_fixture':{'host_root_qualified':False,'role_separation_qualified':False,
   'meaning':'six real diagnostic producers; no installed role or listener qualification'},
  'instrumentation':{'collection_complete':True,
   'manifest_sha256':hashlib.sha256(raw).hexdigest(),'source_entries_sha256':source_sha,
   'datasets':datasets,'retention':{'test_fixture_only':True}}}
 encoded=canonical_analysis_metadata(report);(root/'report.json').write_bytes(encoded)
 print(json.dumps({'report_sha256':hashlib.sha256(encoded).hexdigest(),
  'datasets':len(datasets),'actual_parent_pids':[row['host_pid']for row in datasets],
  'host_root_qualified':False,'root_analysis_refused':True}))
finally:os.close(incoming);os.close(destination)
"""


def materialize_analysis_inputs(
    source: Path, dependency: Path, directory: Path, artifact: Path
) -> tuple[Path, str, Path, Path, str]:
    """Create six real raw-sealed recordings and truthful parent diagnostic metadata.

    Parameters
    ----------
    source, dependency:
        Actual candidate and installed dependency used by the existing byte fixture.
    directory:
        Ordinary-owned fresh Samsung test directory.
    artifact:
        Complete original locked collector wheel.

    Returns
    -------
    tuple[Path, str, Path, Path, str]
        Capsule view/digest, datasets, report and independent report digest.

    Notes
    -----
    Producers run as mapped namespace UID0 in an ordinary single-UID private
    namespace. Dataset labels do not qualify dedicated roles, host root or the
    installed controller. Parent PIDs, exits, raw hashes and source bytes are real.
    No parent reads a child SQLite database.
    """
    view, digest = materialize_instrumentation_view(source, dependency, directory, artifact)
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
    for root in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command += ["--ro-bind", root, root]
    command += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--ro-bind",
        str(view),
        "/code",
        "--bind",
        str(directory),
        str(directory),
        "--cap-drop",
        "ALL",
        "/usr/bin/python3.12",
        "-I",
        "-B",
        "-S",
        "-c",
        _PARENT,
        str(source),
        str(view),
        digest,
        str(directory),
        _PRODUCER,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["host_root_qualified"] is False and observed["root_analysis_refused"] is True
    assert observed["datasets"] == 6 and len(set(observed["actual_parent_pids"])) == 6
    report = directory / "report.json"
    assert hashlib.sha256(report.read_bytes()).hexdigest() == observed["report_sha256"]
    return view, digest, directory / "datasets", report, observed["report_sha256"]


_UNSUPPORTED_FILENAME = (
    "coverage-7.13.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl"
)
_UNSUPPORTED_SHA256 = "583221913fbc8f53b88c42e8dbb8fca1d0f2e597cb190ce45916662b8b9d9621"
_UNSUPPORTED_URL = (
    "https://files.pythonhosted.org/packages/5b/b6/"
    "51b5d1eb6fcbb9a1d5d6984e26cbe09018475c2922d554fd724dd0f056ee/" + _UNSUPPORTED_FILENAME
)


def materialize_unsupported_collector_inputs(
    baseline: Path, directory: Path
) -> tuple[Path, str, Path, str]:
    """Prepare genuine incompatible collector bytes as private refusal inputs.

    Parameters
    ----------
    baseline:
        Existing successful ordinary analysis working copies and configuration.
    directory:
        Fresh owned directory; the original archive is data, never an installation.

    Returns
    -------
    tuple[Path, str, Path, str]
        Configuration/hash, original archive and its exact published PyPI SHA256.

    Notes
    -----
    Fetch is hash-pinned and blocking. The complete Apache-2.0 licensed archive,
    including its licence, is retained. Only isolated worker children may import
    its code, solely to verify that the production worker rejects its version.
    """
    supplied = os.environ.get("SYNAPSE_UNSUPPORTED_INSTRUMENTATION_WHEEL")
    artifact = Path(supplied) if supplied else directory / _UNSUPPORTED_FILENAME
    if not supplied:
        with urllib.request.urlopen(_UNSUPPORTED_URL, timeout=20) as response:
            artifact.write_bytes(response.read(8 * 1024 * 1024 + 1))
        artifact.chmod(0o444)
    raw = artifact.read_bytes()
    assert len(raw) <= 8 * 1024 * 1024
    assert hashlib.sha256(raw).hexdigest() == _UNSUPPORTED_SHA256
    configuration = json.loads((baseline / "analysis-inputs.json").read_bytes())
    shutil.copytree(baseline / "inputs", directory / "inputs")
    collector = directory / "collector"
    collector.mkdir(mode=0o700)
    rows = []
    with zipfile.ZipFile(artifact) as archive:
        entries = sorted(
            (item for item in archive.infolist() if not item.is_dir()),
            key=lambda item: item.filename,
        )
        assert 0 < len(entries) <= 1024 and len({item.filename for item in entries}) == len(entries)
        for item in entries:
            name = PurePosixPath(item.filename)
            assert not name.is_absolute() and ".." not in name.parts and str(name) == item.filename
            assert 0 < item.file_size <= 8 * 1024 * 1024
            content = archive.read(item)
            target = collector / item.filename
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(content)
            target.chmod(0o400)
            rows.append(
                dict(
                    path=item.filename,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                )
            )
    assert any("licenses/LICENSE" in item.filename for item in entries)
    configuration["collector"] = rows
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode("ascii")
    path = directory / "analysis-inputs.json"
    path.write_bytes(encoded)
    path.chmod(0o600)
    return path, hashlib.sha256(encoded).hexdigest(), artifact, _UNSUPPORTED_SHA256


_UNSUPPORTED_WORKER = """import contextlib,hashlib,io,json,runpy,sys,traceback
from pathlib import Path
settings=json.loads(sys.argv[1]);worker=Path(settings['worker']);archive=Path(settings['archive'])
assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
assert hashlib.sha256(worker.read_bytes()).hexdigest()==settings['worker_sha256']
assert hashlib.sha256(archive.read_bytes()).hexdigest()==settings['archive_sha256']
sys.path.insert(0,str(archive));recorder=None
if settings['measured']:
 import coverage
 recorder=coverage.Coverage(data_file=None,timid=True,branch=True,
  config_file=False,include=[str(worker)])
 recorder.start();recorder.switch_context('measured-diagnostic/unsupported-original-collector')
sys.argv=[str(worker),'--configuration',settings['configuration'],'--sha256',settings['digest']]
product=io.StringIO();code=0;failure=None
try:
 with contextlib.redirect_stdout(product):
  try:runpy.run_path(str(worker),run_name='__main__')
  except BaseException as error:
   failure={'type':type(error).__name__,'message':str(error)}
   traceback.print_exc();code=1
finally:
 if recorder is not None:recorder.stop()
data=None if recorder is None else recorder.get_data()
print(json.dumps({'profile':'unsupported-original-collector-refusal-only',
 'original_exit':code,'original_stdout':product.getvalue(),'failure':failure,
 'actual_collector_version':sys.modules['coverage'].__version__,
 'worker_sha256':settings['worker_sha256'],'archive_sha256':settings['archive_sha256'],
 'files':[]if data is None else sorted(data.measured_files()),
 'contexts':[]if data is None else sorted(data.measured_contexts()),
 'arcs':[]if data is None else data.arcs(str(worker)),
 'runtime_accepted':False,'isolation_qualified':False,'old_version_supported':False}))
raise SystemExit(code)
"""


def run_unsupported_collector_pair(
    arguments: list[str], artifact: Path, archive_sha256: str, evidence: Path
) -> None:
    """Require real incompatible-version refusal in both isolated command profiles.

    Parameters
    ----------
    arguments:
        Production worker and private copies of the exact original old package.
    artifact, archive_sha256:
        Original downloaded wheel and its independently recorded published digest.
    evidence:
        Fresh parent-owned receipt; no old collector imports into this process.

    Notes
    -----
    Actual arcs cross versions only as source-bound JSON from public get_data.
    This proves the version refusal and executed source, never runtime support,
    isolation or first import. No cross-version serialized data is loaded.
    """
    import coverage
    from tools.protected_runtime_probe.roles import ROOT_COMMAND_ENV

    worker = Path(arguments[-5])
    assert arguments[1:-5] == ["-I", "-B", "-S"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == archive_sha256
    settings = dict(
        worker=str(worker),
        worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest(),
        archive=str(artifact),
        archive_sha256=archive_sha256,
        configuration=arguments[-3],
        digest=arguments[-1],
    )
    records = []
    for measured in (False, True):
        payload = dict(settings, measured=measured)
        result = subprocess.run(
            [arguments[0], "-I", "-B", "-S", "-c", _UNSUPPORTED_WORKER, json.dumps(payload)],
            capture_output=True,
            text=True,
            timeout=30,
            env=ROOT_COMMAND_ENV,
            check=False,
        )
        assert 0 < len(result.stdout) <= 2 * 1024 * 1024, result.stderr
        row = json.loads(result.stdout)
        assert row["original_exit"] == result.returncode == 1 and row["original_stdout"] == ""
        assert row["actual_collector_version"] == "7.13.0"
        assert row["failure"] == dict(
            type="ValueError", message="analysis collector version differs"
        )
        assert row["worker_sha256"] == settings["worker_sha256"]
        assert row["archive_sha256"] == archive_sha256
        row["original_stderr"] = result.stderr
        records.append(row)
    assert records[0]["failure"] == records[1]["failure"]
    assert (
        records[0]["original_stderr"].splitlines()[-1]
        == records[1]["original_stderr"].splitlines()[-1]
    )
    actual = records[1]
    assert actual["files"] == [str(worker)]
    assert actual["contexts"] == ["measured-diagnostic/unsupported-original-collector"]
    arcs = actual["arcs"]
    assert arcs and all(len(pair) == 2 and all(type(line) is int for line in pair) for pair in arcs)
    assert hashlib.sha256(worker.read_bytes()).hexdigest() == settings["worker_sha256"]
    data = coverage.CoverageData(no_disk=True)
    data.set_context("measured-diagnostic/unsupported-original-collector")
    data.add_arcs({str(worker): [(pair[0], pair[1]) for pair in arcs]})
    active = coverage.Coverage.current()
    if active is not None:
        active.get_data().update(data)
    evidence.write_text(json.dumps(records, indent=2) + "\n")
