# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual captured runtime packet preparation
"""Exercise the public preparer with actual installed runtime bytes and explicit pins."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import websockets
from tools.protected_runtime_probe.capsule import (
    MAX_FILE_BYTES,
    CapsuleManifest,
    FilePin,
    capture_capsule,
    decode_manifest,
)
from tools.protected_runtime_probe.native_provenance import load_native_wheel
from tools.protected_runtime_probe.prepare import capture_websockets_package, prepare_packet

from fixtures.protected_runtime_image import materialize_runtime_image, samsung_backing_or_refusal

_DEPENDENCY_LICENCE_CAPTURE = """import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tools.protected_runtime_probe.prepare import capture_websockets_package
captured=capture_websockets_package(Path(sys.argv[2]),'17.4',(Path(sys.argv[3]),))
raw=captured['licenses/websockets.txt']
print(json.dumps({'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}))
"""


def _unsafe_licence(path: Path, problem: str) -> None:
    """Create the real linked, nonregular or oversized licence used by public capture cases."""
    original = (Path(__file__).resolve().parents[1] / "LICENSE").read_bytes()
    if problem == "parent-symlink":
        target = path.parent.with_name(path.parent.name + "-actual")
        target.mkdir(mode=0o700)
        (target / path.name).write_bytes(original)
        path.parent.symlink_to(target, target_is_directory=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if problem == "symlink":
        target = path.with_name("original-licence-input")
        target.write_bytes(original)
        path.symlink_to(target)
    elif problem == "fifo":
        os.mkfifo(path, 0o600)
    elif problem == "directory":
        path.mkdir(mode=0o700)
    else:
        assert problem == "oversized"
        with path.open("xb") as stream:
            stream.truncate(MAX_FILE_BYTES + 1)


def _require_licence_refusal(result: subprocess.CompletedProcess[str], problem: str) -> None:
    """Check the exact public capture refusal without accepting a later preparation failure."""
    assert result.returncode == 1, result.stdout + result.stderr
    assert not result.stdout
    error = result.stderr.strip().splitlines()[-1]
    if problem == "symlink":
        assert error.startswith("OSError: [Errno 40]"), result.stderr
    elif problem == "parent-symlink":
        assert error.startswith("NotADirectoryError:"), result.stderr
    else:
        assert error == "ValueError: prerequisite source is not a bounded regular file"


@pytest.mark.parametrize("problem", ["symlink", "parent-symlink", "fifo", "directory", "oversized"])
def test_dependency_capture_refuses_actual_unsafe_licence_sources(
    tmp_path: Path, problem: str
) -> None:
    """Real unsafe licence inputs refuse finitely before any bytes can become a capsule member."""
    if not _native_profile_or_refused(tmp_path):
        return
    source = Path(__file__).resolve().parents[1]
    licence = tmp_path / "licences/LICENSE"
    _unsafe_licence(licence, problem)
    before = licence.lstat()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _DEPENDENCY_LICENCE_CAPTURE,
            str(source),
            str(Path(websockets.__file__).parent),
            str(licence),
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    _require_licence_refusal(result, problem)
    after = licence.lstat()
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )


@pytest.mark.parametrize("problem", ["symlink", "fifo", "directory", "oversized"])
def test_application_licence_refusal_prevents_public_cli_packet_creation(
    tmp_path: Path, problem: str
) -> None:
    """The actual preparer CLI refuses unsafe application licences before git or staging."""
    if not _native_profile_or_refused(tmp_path):
        return
    original = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    for relative in ("src/synapse_channel", "tools/protected_runtime_probe"):
        shutil.copytree(
            original / relative,
            source / relative,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    _unsafe_licence(source / "LICENSE", problem)
    packet = tmp_path / "packet"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tools.protected_runtime_probe.prepare",
            "--source",
            str(source),
            "--bundle",
            str(packet),
            "--backing-parent",
            str(tmp_path / "runs"),
        ],
        cwd=original,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    _require_licence_refusal(result, problem)
    assert not packet.exists()


def test_dependency_capture_preserves_stable_licence_cache_hardlinks(tmp_path: Path) -> None:
    """Ordinary cache hardlinks remain valid and captured bytes detach from later source changes."""
    if not _native_profile_or_refused(tmp_path):
        return
    original = (Path(__file__).resolve().parents[1] / "LICENSE").read_bytes()
    licence = tmp_path / "LICENSE"
    licence.write_bytes(original)
    alias = tmp_path / "cache-LICENSE"
    os.link(licence, alias)
    assert licence.stat().st_nlink == 2
    captured = capture_websockets_package(Path(websockets.__file__).parent, "17.4", (alias,))
    licence.write_bytes(b"changed after actual capture\n")
    assert captured["licenses/websockets.txt"] == original
    assert alias.read_bytes() != captured["licenses/websockets.txt"]


def _native_profile_or_refused(tmp_path: Path) -> bool:
    """Exercise the actual native ABI refusal where these POSIX operations are unavailable."""
    if (sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1) == (
        "linux",
        "x86_64",
        (3, 12),
        64,
    ):
        return True
    with pytest.raises(OSError, match="native Linux"):
        prepare_packet(
            Path(__file__).resolve().parents[1], tmp_path / "unsupported", tmp_path / "runs"
        )
    return False


@pytest.mark.parametrize(
    "missing", ["runtime_image", "runtime_manifest", "runtime_sha256", "dependency_wheel"]
)
def test_captured_profile_requires_every_input_before_creating_a_packet(
    tmp_path: Path, missing: str
) -> None:
    """Partial captured-profile API requests refuse without a staging directory."""
    source = Path(__file__).resolve().parents[1]
    supported = (
        sys.platform == "linux"
        and platform.machine() == "x86_64"
        and sys.version_info[:2] == (3, 12)
    )
    with pytest.raises(ValueError if supported else OSError, match="requires image|native Linux"):
        prepare_packet(
            source,
            tmp_path / "packet",
            tmp_path / "runs",
            runtime_image=None if missing == "runtime_image" else tmp_path / "image",
            runtime_manifest=None if missing == "runtime_manifest" else tmp_path / "manifest.json",
            runtime_sha256=None if missing == "runtime_sha256" else "0" * 64,
            dependency_wheel=None if missing == "dependency_wheel" else tmp_path / "dependency.whl",
        )
    assert not (tmp_path / "packet").exists()


def _dependency_wheel(source: Path, directory: Path) -> Path:
    """Acquire the actual public dependency artifact and verify the production lock."""
    lock = source / "tools/protected_runtime_probe/native-dependencies.lock.json"
    pin = json.loads(lock.read_bytes())["websockets"]
    filename = pin["filename"]
    assert isinstance(filename, str)
    wheel = directory / filename
    with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
        content = response.read(8 * 1024 * 1024)
    assert len(content) == pin["size_bytes"]
    assert hashlib.sha256(content).hexdigest() == pin["sha256"]
    wheel.write_bytes(content)
    load_native_wheel(lock, wheel)
    return wheel


def _captured_cli_output(
    source: Path, directory: Path, image: Path, manifest: Path, digest: str, wheel: Path
) -> str:
    """Run the actual ordinary CLI with all four captured-profile inputs."""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tools.protected_runtime_probe.prepare",
            "--source",
            str(source),
            "--bundle",
            str(directory / "packet"),
            "--backing-parent",
            str(directory / "runs"),
            "--runtime-image",
            str(image),
            "--runtime-manifest",
            str(manifest),
            "--runtime-sha256",
            digest,
            "--dependency-wheel",
            str(wheel),
        ],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return result.stdout


def _runtime_inputs(source: Path, directory: Path) -> tuple[Path, Path, str, Path]:
    """Capture current source/runtime metadata and the actual pinned dependency artifact."""
    head = subprocess.run(
        ["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout.strip()
    wheel = _dependency_wheel(source, directory)
    image, manifest, digest = materialize_runtime_image(
        source, Path(websockets.__file__).parent, directory, head
    )
    return image, manifest, digest, wheel


def test_captured_runtime_cli_verifies_and_embeds_actual_native_provenance(tmp_path: Path) -> None:
    """A complete installed-byte runtime reaches the real CLI and recaptures its provenance."""
    source = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
        return
    if not tmp_path.is_relative_to("/media/anulum/GOTM"):
        with pytest.raises(ValueError, match="Samsung"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
        return
    image, manifest, digest, wheel = _runtime_inputs(source, tmp_path)
    result = json.loads(_captured_cli_output(source, tmp_path, image, manifest, digest, wheel))
    captured = capture_capsule(tmp_path / "packet", result["manifest_sha256"])
    raw = captured.content["runtime/config/native-provenance.json"]
    assert hashlib.sha256(raw).hexdigest() == result["native_provenance_sha256"]
    provenance = json.loads(raw)
    natives = provenance["native_objects"]
    assert natives and {row["manager"] for row in natives} == {"dpkg", "wheel"}
    for row in natives:
        assert row["sha256"] == hashlib.sha256(captured.content[row["payload"]]).hexdigest()
        assert (
            row["dpkg_md5_verified"] if row["manager"] == "dpkg" else row["wheel_record_verified"]
        )
    assert not any("Tk" in row["payload"] or "X11" in row["payload"] for row in natives)
    assert (
        captured.content[
            "runtime/python/lib/python3.12/dist-packages/synapse_channel/core/protected_write_kernel_boundary.py"
        ]
        == (source / "src/synapse_channel/core/protected_write_kernel_boundary.py").read_bytes()
    )


@pytest.mark.parametrize("case", ["missing-module", "source-symlink", "unexpected-resource"])
def test_preparer_refuses_incomplete_or_unreviewed_source(tmp_path: Path, case: str) -> None:
    """Actual source-tree defects refuse before staging or source execution."""
    original = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(original, tmp_path / "packet", tmp_path / "runs")
        return
    source = tmp_path / "source"
    probe = source / "tools/protected_runtime_probe"
    shutil.copytree(
        original / "tools/protected_runtime_probe",
        probe,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    package = source / "src/synapse_channel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(
        (original / "src/synapse_channel/__init__.py").read_bytes()
    )
    if case == "missing-module":
        (probe / "authority_child.py").unlink()
    elif case == "source-symlink":
        (package / "linked.py").symlink_to(original / "src/synapse_channel/__init__.py")
    else:
        (package / "unreviewed.bin").write_bytes((original / "LICENSE").read_bytes())
    with pytest.raises(ValueError, match="module closure|symlink|unreviewed prerequisite"):
        prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
    assert not (tmp_path / "packet").exists()


def test_captured_profile_refuses_application_bytes_different_from_current_source(
    tmp_path: Path,
) -> None:
    """A byte-pinned runtime still refuses an application payload from a different source."""
    source = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
        return
    if not tmp_path.is_relative_to("/media/anulum/GOTM"):
        with pytest.raises(ValueError, match="Samsung"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
        return
    image, manifest_path, _digest, wheel = _runtime_inputs(source, tmp_path)
    name = "runtime/python/lib/python3.12/dist-packages/synapse_channel/__init__.py"
    path = image / name.removeprefix("runtime/")
    path.chmod(0o600)
    raw = path.read_bytes() + b"\n"
    path.write_bytes(raw)
    path.chmod(0o400)
    old = decode_manifest(manifest_path.read_bytes())
    pins = tuple(
        FilePin(pin.path, hashlib.sha256(raw).hexdigest(), len(raw)) if pin.path == name else pin
        for pin in old.files
    )
    manifest = CapsuleManifest(old.source_head, old.backing_parent, pins).canonical_bytes()
    manifest_path.write_bytes(manifest)
    with pytest.raises(ValueError, match="differs from current application/dependency"):
        prepare_packet(
            source,
            tmp_path / "packet",
            tmp_path / "runs",
            runtime_image=image,
            runtime_manifest=manifest_path,
            runtime_sha256=hashlib.sha256(manifest).hexdigest(),
            dependency_wheel=wheel,
        )
    assert not (tmp_path / "packet").exists()


@pytest.mark.parametrize("problem", ["version", "missing-licence", "ambiguous-licence"])
def test_dependency_capture_refuses_unsupported_or_ambiguous_observed_profile(
    tmp_path: Path, problem: str
) -> None:
    """Observed version and actual licence files must form the exact supported profile."""
    first = tmp_path / "LICENSE"
    first.write_bytes((Path(__file__).resolve().parents[1] / "LICENSE").read_bytes())
    second = tmp_path / "LICENSE.other"
    second.write_bytes(first.read_bytes())
    licences: tuple[Path, ...] = () if problem == "missing-licence" else (first,)
    if problem == "ambiguous-licence":
        licences = (first, second)
    with pytest.raises(ValueError, match="websockets17.4|licence provenance"):
        capture_websockets_package(
            Path(websockets.__file__).parent, "16.0" if problem == "version" else "17.4", licences
        )


def test_dependency_capture_keeps_actual_package_and_licence_bytes(tmp_path: Path) -> None:
    """The public capture used by the preparer preserves installed dependency bytes."""
    if not _native_profile_or_refused(tmp_path):
        return
    distribution = importlib.metadata.distribution("websockets")
    licences = tuple(
        Path(str(distribution.locate_file(entry)))
        for entry in distribution.files or ()
        if entry.name == "LICENSE"
    )
    package = Path(websockets.__file__).parent
    captured = capture_websockets_package(package, "17.4", licences)
    assert captured["licenses/websockets.txt"] == licences[0].read_bytes()
    assert captured["app/websockets/__init__.py"] == (package / "__init__.py").read_bytes()


def test_host_tools_packet_remains_explicitly_without_captured_native_provenance(
    tmp_path: Path,
) -> None:
    """The optional-profile path stages exact ordinary source without installed-runtime claims."""
    if not _native_profile_or_refused(tmp_path):
        return
    source = Path(__file__).resolve().parents[1]
    if not tmp_path.is_relative_to("/media/anulum/GOTM"):
        with pytest.raises(ValueError, match="Samsung"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
        return
    result = prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
    captured = capture_capsule(tmp_path / "packet", result["manifest_sha256"])
    assert "native_provenance_file" not in result
    assert not any(name.startswith("runtime/") for name in captured.content)
    assert (
        captured.content["probe/protected_runtime_probe/prepare.py"]
        == (source / "tools/protected_runtime_probe/prepare.py").read_bytes()
    )


@pytest.mark.parametrize("kind", ["relative", "outside", "parent-traversal", "other-device"])
def test_packet_preparer_refuses_actual_unsafe_storage_parents(tmp_path: Path, kind: str) -> None:
    """Storage validation refuses before creating a packet, including a real device mismatch."""
    if not _native_profile_or_refused(tmp_path):
        return
    source = Path(__file__).resolve().parents[1]
    backing = tmp_path / "runs"
    if kind in {"relative", "outside"}:
        # These paths refuse before staging or backing access, even off Samsung.
        backing = Path("/media/anulum/GOTM") / tmp_path.name / "unopened-runs"
    elif not samsung_backing_or_refusal(tmp_path):
        return
    assert not backing.exists()
    if kind == "relative":
        bundle = Path("not-absolute-packet")
    elif kind == "outside":
        bundle = Path("/not-a-samsung-location/packet")
    elif kind == "parent-traversal":
        bundle = tmp_path / ".." / "packet"
    else:
        parent = tmp_path / "other-device"
        parent.symlink_to("/proc", target_is_directory=True)
        bundle = parent / "never-created-synapse-packet"
    with pytest.raises(ValueError, match="canonical Samsung|backing device"):
        prepare_packet(source, bundle, backing)
    assert not bundle.exists() and not backing.exists()


@pytest.mark.parametrize("problem", ["unborn-head", "blocked-config"])
def test_preparer_bounds_real_git_metadata_observation(tmp_path: Path, problem: str) -> None:
    """A real unborn repository refuses; a real blocked configuration read reaches its deadline."""
    if not _native_profile_or_refused(tmp_path):
        return
    original = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(
        ["/usr/bin/git", "-c", "init.defaultBranch=main", "init", "-q", str(source)],
        check=True,
        timeout=5,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    shutil.copytree(
        original / "tools/protected_runtime_probe",
        source / "tools/protected_runtime_probe",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    package = source / "src/synapse_channel"
    package.mkdir(parents=True)
    shutil.copyfile(original / "src/synapse_channel/__init__.py", package / "__init__.py")
    shutil.copyfile(original / "LICENSE", source / "LICENSE")
    if problem == "blocked-config":
        config = source / ".git/config"
        config.unlink()
        os.mkfifo(config, 0o600)
        exception: type[Exception] = TimeoutError
        message = None
    else:
        exception, message = ValueError, "source HEAD is unavailable"
    with pytest.raises(exception, match=message):
        prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
    assert not (tmp_path / "packet").exists()


def test_public_preparer_cli_refuses_partial_collector_arguments(tmp_path: Path) -> None:
    """A partial collector request refuses before creating a packet or backing directory."""
    if not _native_profile_or_refused(tmp_path):
        return
    source = Path(__file__).resolve().parents[1]
    packet, backing = tmp_path / "never-created-packet", tmp_path / "future-runs"
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-W",
            "error::ResourceWarning",
            "-B",
            "-m",
            "tools.protected_runtime_probe.prepare",
            "--source",
            str(source),
            "--bundle",
            str(packet),
            "--backing-parent",
            str(backing),
            "--instrumentation-root",
            str(tmp_path / "absent-collector"),
        ],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1 and not result.stdout
    assert result.stderr.strip().splitlines()[-1] == (
        "ValueError: instrumentation requires both collector root and original wheel"
    )
    assert not packet.exists() and not backing.exists()
