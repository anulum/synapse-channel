# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real retained report workflow in private namespaces
"""Exercise the complete public prerequisite loader inside an ordinary private namespace."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest
from tools.protected_runtime_probe import require_native_linux
from tools.protected_runtime_probe.bootstrap import (
    require_disposable_guest,
    require_native_bootstrap_abi,
)
from tools.protected_runtime_probe.capsule import require_posix_descriptors
from tools.protected_runtime_probe.prepare import prepare_packet

from fixtures.protected_runtime_image import samsung_backing_or_refusal

_BOOTSTRAP_WORKFLOW = """import json,os,runpy,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[1],sys.argv[3]]
recorder=None
if sys.argv[4]:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(sys.argv[4])/'.coverage'),data_suffix=True,branch=True,config_file=False)
 recorder.start()
try:
 bundle=Path(sys.argv[2])
 metadata=json.loads((bundle.parent/'packet-metadata.json').read_text())
 manifest=json.loads((bundle/'manifest.json').read_bytes())
 assert os.getresuid()==(0,0,0) and os.getresgid()==(0,0,0)
 actual_map=Path('/proc/self/uid_map').read_text()
 assert actual_map.split()!=['0','0','4294967295']
 sys.argv=[sys.argv[1]+'/tools/protected_runtime_probe/bootstrap.py','--bundle',str(bundle),'--manifest-sha256',metadata['manifest_sha256']]
 try:runpy.run_path(sys.argv[0],run_name='__main__')
 except PermissionError as error:
  assert 'initial user namespace' in str(error),str(error)
 else:raise AssertionError('unqualified namespace entered guest setup')
 assert not Path('/opt/synapse-channel').exists()
 assert not Path(manifest['backing_parent']).exists()
 print(json.dumps({'refused_before_setup':True,'guest_uuid':manifest['guest_uuid'],'uid_map':actual_map}))
finally:
 if recorder is not None:recorder.stop();recorder.save()
"""


def test_complete_loader_refuses_mapped_root_before_guest_setup(tmp_path: Path) -> None:
    """The complete public loader refuses actual mapped root before any installation."""
    if not samsung_backing_or_refusal(tmp_path):
        return
    source = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
        return
    assert os.geteuid() != 0
    packet = tmp_path / "packet"
    prepared = prepare_packet(source, packet, tmp_path / "runs")
    (tmp_path / "packet-metadata.json").write_text(json.dumps(prepared))
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
        "--unshare-pid",
        "--tmpfs",
        "/",
    ]
    for directory in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command.extend(("--ro-bind", directory, directory))
    command.extend(("--proc", "/proc", "--dev", "/dev", "--bind", str(tmp_path), str(tmp_path)))
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    if coverage_dir:
        destination = Path(coverage_dir)
        assert destination.is_absolute() and destination.is_dir()
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(
        (
            "--cap-add",
            "ALL",
            "/usr/bin/python3.12",
            "-I",
            "-B",
            "-S",
            "-c",
            _BOOTSTRAP_WORKFLOW,
            str(source),
            str(packet),
            str(Path(pytest.__file__).parents[1]),
            coverage_dir,
        )
    )
    process = subprocess.run(command, capture_output=True, text=True, timeout=40, check=False)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.splitlines()[-1])
    assert result["refused_before_setup"] is True
    assert result["guest_uuid"] == prepared["guest_uuid"]
    assert not (tmp_path / "runs").exists()


_REFUSED_BOOTSTRAP = """import json,os,runpy,stat,sys,threading
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[1],sys.argv[3]]
recorder=None
if sys.argv[4]:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(sys.argv[4])/'.coverage'),data_suffix=True,branch=True,config_file=False)
 recorder.start()
thread=None
event=threading.Event()
try:
 mode=sys.argv[5]
 expected=sys.argv[6]
 if mode=='threaded':
  thread=threading.Thread(target=event.wait);thread.start()
 metadata=json.loads((Path(sys.argv[2])/'metadata.json').read_text())
 if mode=='codec-virtual-size':
  codec=Path(sys.argv[2])/'probe/protected_runtime_probe/capsule.py'
  observed=codec.stat()
  assert stat.S_ISREG(observed.st_mode) and observed.st_size==0
  assert len(codec.read_bytes())>1
 sys.argv=[sys.argv[1]+'/tools/protected_runtime_probe/bootstrap.py','--bundle',sys.argv[2],'--manifest-sha256',metadata['digest']]
 try: runpy.run_path(sys.argv[0],run_name='__main__')
 except (ValueError,PermissionError,OSError) as error:
  assert expected in str(error),str(error)
  print(json.dumps({'mode':mode,'error':type(error).__name__,'message':str(error)}))
 else: raise AssertionError('unqualified bootstrap input was accepted')
 assert not Path('/opt/synapse-channel').exists()
finally:
 event.set()
 if thread is not None:thread.join()
 if recorder is not None:
  recorder.stop();recorder.save()
"""


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("no-isolation", "isolated no-bytecode no-site"),
        ("no-site", "isolated no-bytecode no-site"),
        ("bytecode-enabled", "isolated no-bytecode no-site"),
        ("missing-capability", "actual root setup capabilities"),
        ("threaded", "single-threaded root parent"),
        ("invalid-digest", "invalid independently approved capsule digest"),
        ("manifest-mismatch", "manifest differs from independent approval"),
        ("missing-codec", "one exact bounded codec pin"),
        ("duplicate-codec", "one exact bounded codec pin"),
        ("duplicate-manifest-key", "repeated object key"),
        ("duplicate-pin-key", "repeated object key"),
        ("codec-type", "one exact bounded codec pin"),
        ("codec-zero", "codec exceeds byte bound"),
        ("codec-oversized", "codec exceeds byte bound"),
        ("codec-directory", "not a bounded regular file"),
        ("codec-changed", "codec differs from approved captured bytes"),
        ("codec-virtual-size", "bootstrap input exceeds its byte bound"),
    ],
)
def test_public_bootstrap_refuses_actual_context_and_packet_defects(
    tmp_path: Path, mode: str, expected: str
) -> None:
    """Actual flags, capabilities, threads and packet objects refuse before any installation."""
    if not samsung_backing_or_refusal(tmp_path):
        return
    source = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
        return
    assert os.geteuid() != 0
    bundle = tmp_path / "packet"
    codec = bundle / "probe/protected_runtime_probe/capsule.py"
    codec.parent.mkdir(parents=True)
    raw = (source / "tools/protected_runtime_probe/capsule.py").read_bytes()
    codec.write_bytes(raw)
    pin: dict[str, object] = {
        "path": "probe/protected_runtime_probe/capsule.py",
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if mode == "codec-type":
        pin["size_bytes"] = str(len(raw))
    elif mode == "codec-zero":
        pin["size_bytes"] = 0
    elif mode == "codec-oversized":
        pin["size_bytes"] = 64 * 1024 * 1024 + 1
    elif mode == "codec-virtual-size":
        pin["size_bytes"] = 1
    elif mode == "codec-directory":
        codec.unlink()
        codec.mkdir()
    elif mode == "codec-changed":
        codec.write_bytes(b"x" * len(raw))
    files = [] if mode == "missing-codec" else [pin, pin] if mode == "duplicate-codec" else [pin]
    manifest = json.dumps({"files": files}, sort_keys=True, separators=(",", ":")).encode()
    if mode == "duplicate-manifest-key":
        manifest = b'{"files":[],' + manifest[1:]
    elif mode == "duplicate-pin-key":
        manifest = manifest.replace(b'{"path":', b'{"path":"unapproved", "path":', 1)
    (bundle / "manifest.json").write_bytes(manifest)
    digest = hashlib.sha256(manifest).hexdigest()
    if mode == "invalid-digest":
        digest = "not-an-approved-digest"
    elif mode == "manifest-mismatch":
        digest = "0" * 64
    (bundle / "metadata.json").write_text(json.dumps({"digest": digest}))
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
        "--unshare-pid",
        "--tmpfs",
        "/",
    ]
    for directory in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command.extend(("--ro-bind", directory, directory))
    command.extend(("--proc", "/proc"))
    if mode == "codec-virtual-size":
        command.extend(("--ro-bind", "/proc/version", str(codec)))
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    if coverage_dir:
        destination = Path(coverage_dir)
        assert destination.is_absolute() and destination.is_dir()
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(("--cap-drop", "ALL") if mode == "missing-capability" else ("--cap-add", "ALL"))
    command.append("/usr/bin/python3.12")
    for flag, omitted in (("-I", "no-isolation"), ("-B", "bytecode-enabled"), ("-S", "no-site")):
        if mode != omitted:
            command.append(flag)
    command.extend(
        (
            "-c",
            _REFUSED_BOOTSTRAP,
            str(source),
            str(bundle),
            str(Path(pytest.__file__).parents[1]),
            coverage_dir,
            mode,
            expected,
        )
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["mode"] == mode and expected in observed["message"]
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("observed_os", ["nt", "java"])
def test_observed_nonposix_backend_refuses_without_accessing_descriptors(observed_os: str) -> None:
    """Unsupported declared OS backends are domain inputs, never altered host observations."""
    with pytest.raises(OSError, match="native POSIX"):
        require_posix_descriptors(observed_os)


def test_actual_posix_backend_is_used_by_native_capture() -> None:
    """The validator accepts precisely the adapter's real current POSIX observation."""
    if os.name == "posix":
        require_posix_descriptors(os.name)
    else:
        with pytest.raises(OSError, match="native POSIX"):
            require_posix_descriptors(os.name)


@pytest.mark.parametrize("observed_platform", ["win32", "darwin"])
def test_observed_nonlinux_probe_platform_refuses(observed_platform: str) -> None:
    """The package platform domain check refuses non-Linux values without native effects."""
    with pytest.raises(OSError, match="test observation requires native Linux"):
        require_native_linux(observed_platform, operation="test observation")


@pytest.mark.parametrize(
    "observed_platform,machine,pointer_bytes,python_version",
    [
        ("win32", "x86_64", 8, (3, 12)),
        ("darwin", "x86_64", 8, (3, 12)),
        ("linux", "aarch64", 8, (3, 12)),
        ("linux", "x86_64", 4, (3, 12)),
        ("linux", "x86_64", 8, (3, 13)),
    ],
)
def test_observed_operator_abi_refuses_unsupported_domains(
    observed_platform: str, machine: str, pointer_bytes: int, python_version: tuple[int, int]
) -> None:
    """The standalone loader validates explicit domains without importing caller packages."""
    with pytest.raises(OSError, match="native Linux x86_64 CPython3.12"):
        require_native_bootstrap_abi(observed_platform, machine, pointer_bytes, python_version)


def test_actual_host_cannot_enter_a_new_manifest_enrolled_guest(tmp_path: Path) -> None:
    """Native host observations refuse a fresh capsule UUID without root or effects."""
    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            require_disposable_guest("00000000-0000-4000-8000-000000000001")
        return
    from tools.protected_runtime_probe.capsule import CapsuleManifest, FilePin

    manifest = CapsuleManifest(
        "a" * 40,
        "/media/anulum/GOTM/guest-refusal-test",
        (FilePin("app/non-executable-input", "0" * 64, 0),),
    )
    with pytest.raises(PermissionError):
        require_disposable_guest(manifest.guest_uuid)
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("identity", ["", "not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_guest_identity_requires_a_per_attempt_uuid(identity: str) -> None:
    """Malformed identity domains refuse before any native observation is accepted."""
    with pytest.raises(ValueError, match="per-attempt UUID4"):
        require_disposable_guest(identity)
