# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — explicit instrumentation profile admission
"""Exercise complete original wheel bytes and exact manifest/profile agreement."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, cast

import pytest
from tools.protected_runtime_probe.capsule import CapsuleManifest, CapturedCapsule, FilePin
from tools.protected_runtime_probe.instrumentation import (
    CONFIGURATION,
    build_instrumentation_content,
    instrumentation_file_limit_failure,
    instrumentation_selected,
    require_production_payload,
)
from tools.protected_runtime_probe.instrumentation_artifacts import (
    InstrumentationWheel,
    capture_instrumentation_wheel,
    decode_instrumentation_wheel,
)
from tools.protected_runtime_probe.native_provenance import NativeWheelArtifact

_SOURCE = Path(__file__).resolve().parents[1]
_LOCK = _SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json"
_SITE = "runtime/python/lib/python3.12/dist-packages/"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _capsule(content: dict[str, bytes]) -> CapturedCapsule:
    """Build actual byte pins; inert interpreter fixtures make no execution claim."""
    manifest = CapsuleManifest(
        "b" * 40,
        "/media/anulum/GOTM/profile-admission-test",
        tuple(
            FilePin(name, hashlib.sha256(raw).hexdigest(), len(raw))
            for name, raw in sorted(content.items())
        ),
    )
    return CapturedCapsule(
        manifest, hashlib.sha256(manifest.canonical_bytes()).hexdigest(), content
    )


def _original_wheel_bytes(path: Path) -> InstrumentationWheel:
    """Admit actual bytes portably; only POSIX claims filesystem descriptor capture."""
    if os.name == "posix":
        return capture_instrumentation_wheel(_LOCK, path)
    pin = json.loads(_LOCK.read_bytes())["coverage"]
    raw = path.read_bytes()
    assert len(raw) == pin["size_bytes"]
    return decode_instrumentation_wheel(NativeWheelArtifact(path.name, pin["sha256"], raw))


@pytest.fixture(scope="module")
def instrumented_content(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    """Use the complete original pinned wheel, never an installed ambient collector."""
    supplied = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL")
    lock = json.loads(_LOCK.read_bytes())
    if supplied:
        wheel = Path(supplied)
    else:
        wheel = tmp_path_factory.mktemp("original-wheel") / lock["coverage"]["filename"]
        with urllib.request.urlopen(lock["coverage"]["source_url"], timeout=15) as stream:
            wheel.write_bytes(stream.read(8 * 1024 * 1024 + 1))
    content = {
        "probe/protected_runtime_probe/native-dependencies.lock.json": _LOCK.read_bytes(),
        "probe/protected_runtime_probe/controller.py": (
            _SOURCE / "tools/protected_runtime_probe/controller.py"
        ).read_bytes(),
        "app/synapse_channel/core/protected_write_kernel_boundary.py": (
            _SOURCE / "src/synapse_channel/core/protected_write_kernel_boundary.py"
        ).read_bytes(),
    }
    for name, raw in tuple(content.items()):
        if name.startswith("app/"):
            content[_SITE + name[4:]] = raw
    for name in (
        "runtime/native/lib/ld-linux-x86-64.so.2",
        "runtime/python/bin/python3.12",
        "runtime/python/lib/python3.12/encodings/__init__.py",
        _SITE + "websockets/__init__.py",
        "runtime/config/openssl.cnf",
        "runtime/config/UTC",
        "runtime/trust/ca-certificates.crt",
    ):
        content[name] = b"inert inventory fixture; never execute"
    return build_instrumentation_content(content, _original_wheel_bytes(wheel))


def _profile(content: dict[str, bytes], record: dict[str, Any]) -> dict[str, bytes]:
    return {**content, CONFIGURATION: _canonical(record)}


def test_complete_profile_requires_operator_selection(
    instrumented_content: dict[str, bytes],
) -> None:
    """An explicit selector agrees with the complete captured configuration."""
    capsule = _capsule(instrumented_content)
    assert instrumentation_selected(capsule, requested=True)
    with pytest.raises(ValueError, match="production runtime"):
        instrumentation_selected(capsule, requested=False)
    plain = {
        k: v
        for k, v in instrumented_content.items()
        if not (
            k == CONFIGURATION
            or k.startswith("runtime/artifacts/coverage-")
            or k.startswith(_SITE + "coverage")
            or k == _SITE + "a1_coverage.pth"
        )
    }
    assert not instrumentation_selected(_capsule(plain), requested=False)
    with pytest.raises(ValueError, match="explicitly captured"):
        instrumentation_selected(_capsule(plain), requested=True)
    source_only = {k: v for k, v in plain.items() if not k.startswith("runtime/")}
    with pytest.raises(ValueError, match="explicitly captured"):
        instrumentation_selected(_capsule(source_only), requested=True)
    with pytest.raises(ValueError, match="selector"):
        instrumentation_selected(capsule, requested=cast(bool, 1))


@pytest.mark.parametrize(
    "name",
    [
        CONFIGURATION,
        "runtime/artifacts/coverage-example.whl",
        _SITE + "coverage/isolated.py",
        _SITE + "coverage-7.17.0.dist-info/METADATA",
        _SITE + "a1_coverage.pth",
    ],
)
def test_any_partial_collector_is_refused_by_production(name: str) -> None:
    """Each actual payload category independently forbids production admission."""
    with pytest.raises(ValueError, match="production runtime"):
        require_production_payload({name: b"partial payload"})


@pytest.mark.parametrize("change", ["missing", "alias", "already-present"])
def test_preparation_refuses_partial_or_conflicting_sources(
    instrumented_content: dict[str, bytes], tmp_path: Path, change: str
) -> None:
    """Preparation does not silently repair changed application/runtime aliases."""
    artifact = json.loads(_LOCK.read_bytes())["coverage"]
    wheel_path = tmp_path / artifact["filename"]
    wheel_path.write_bytes(instrumented_content["runtime/artifacts/" + artifact["filename"]])
    wheel = _original_wheel_bytes(wheel_path)
    content = {
        k: v
        for k, v in instrumented_content.items()
        if not (
            k == CONFIGURATION
            or k.startswith("runtime/artifacts/coverage-")
            or k.startswith(_SITE + "coverage")
            or k == _SITE + "a1_coverage.pth"
        )
    }
    if change == "missing":
        del content["probe/protected_runtime_probe/controller.py"]
    elif change == "alias":
        content[_SITE + "synapse_channel/core/protected_write_kernel_boundary.py"] = b"changed"
    else:
        content[_SITE + "coverage/injected.py"] = b"already present"
    with pytest.raises(ValueError):
        build_instrumentation_content(content, wheel)


@pytest.mark.parametrize(
    "change",
    [
        "empty",
        "oversized",
        "duplicate-key",
        "non-object",
        "extra",
        "pretty",
        "schema",
        "version",
        "recorders",
        "start",
        "stop",
        "unmeasured",
        "root",
        "sources",
    ],
)
def test_configuration_cannot_expand_collection_authority(
    instrumented_content: dict[str, bytes], change: str
) -> None:
    """Actual pinned bytes refuse malformed or differently scoped collection."""
    content = dict(instrumented_content)
    record = json.loads(content[CONFIGURATION])
    if change == "empty":
        content[CONFIGURATION] = b""
    elif change == "oversized":
        content[CONFIGURATION] = b" " * (2 * 1024 * 1024 + 1)
    elif change == "duplicate-key":
        content[CONFIGURATION] = b'{"schema":1,"schema":2}'
    elif change == "non-object":
        content[CONFIGURATION] = b"[]"
    elif change == "extra":
        record["unknown"] = True
        content = _profile(content, record)
    elif change == "pretty":
        content[CONFIGURATION] = json.dumps(record, indent=2).encode()
    else:
        field = {
            "schema": "schema",
            "version": "collector_version",
            "recorders": "recorders",
            "start": "start_after",
            "stop": "stop_before",
            "unmeasured": "unmeasured",
            "root": "writer_extra_root",
            "sources": "sources",
        }[change]
        record[field] = "changed"
        content = _profile(content, record)
    with pytest.raises(ValueError):
        instrumentation_selected(_capsule(content), requested=True)


@pytest.mark.parametrize(
    "change",
    [
        "not-list",
        "too-many",
        "pin-shape",
        "pin-scope",
        "config-pin",
        "bytes",
        "length",
        "missing",
        "order",
        "duplicate",
        "extra",
        "locked-version",
        "locked-sha",
        "locked-size",
        "archive-absent",
        "tracer-absent",
        "entry-absent",
    ],
)
def test_collector_inventory_and_original_archive_remain_bound(
    instrumented_content: dict[str, bytes], change: str
) -> None:
    """No altered or omitted collector payload inherits a complete-profile claim."""
    content = dict(instrumented_content)
    record = json.loads(content[CONFIGURATION])
    pins = record["collector"]
    if change == "not-list":
        record["collector"] = "not a list"
    elif change == "too-many":
        record["collector"] = pins * 1024
    elif change == "pin-shape":
        pins[0]["unexpected"] = True
    elif change in {"pin-scope", "config-pin"}:
        pins[0]["path"] = "app/foreign.py" if change == "pin-scope" else CONFIGURATION
    elif change in {"bytes", "length", "missing"}:
        name = pins[0]["path"]
        if change == "missing":
            del content[name]
        elif change == "bytes":
            content[name] = b"x" * len(content[name])
        else:
            content[name] += b"extra"
    elif change == "order":
        pins.reverse()
    elif change == "duplicate":
        pins.append(pins[0])
    elif change == "extra":
        content[_SITE + "coverage/extra.py"] = b"extra"
    elif change.startswith("locked-"):
        lock = json.loads(content["probe/protected_runtime_probe/native-dependencies.lock.json"])
        field = {"locked-version": "version", "locked-sha": "sha256", "locked-size": "size_bytes"}[
            change
        ]
        lock["coverage"][field] = 0 if change == "locked-size" else "different"
        content["probe/protected_runtime_probe/native-dependencies.lock.json"] = _canonical(lock)
    else:
        if change == "archive-absent":
            name = next(k for k in content if k.startswith("runtime/artifacts/coverage-"))
        else:
            name = _SITE + (
                "coverage/__init__.py"
                if change == "entry-absent"
                else "coverage/tracer.cpython-312-x86_64-linux-gnu.so"
            )
        del content[name]
        record["collector"] = [pin for pin in pins if pin["path"] != name]
    with pytest.raises(ValueError):
        instrumentation_selected(_capsule(_profile(content, record)), requested=True)


def test_profile_admission_does_not_import_or_start_collector(
    tmp_path: Path, instrumented_content: dict[str, bytes]
) -> None:
    """An isolated real process selects the profile without importing wheel code."""
    from tools.protected_runtime_probe.capsule import capture_capsule

    capsule = _capsule(instrumented_content)
    packet = tmp_path / "packet"
    packet.mkdir()
    for name, raw in capsule.content.items():
        path = packet / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (packet / "manifest.json").write_bytes(capsule.manifest.canonical_bytes())
    if os.name != "posix":
        with pytest.raises(OSError, match="requires native POSIX descriptors"):
            capture_capsule(packet, capsule.manifest_sha256)
        return
    assert capture_capsule(packet, capsule.manifest_sha256).content == capsule.content
    code = """import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from tools.protected_runtime_probe.capsule import capture_capsule
from tools.protected_runtime_probe.instrumentation import instrumentation_selected
assert 'coverage' not in sys.modules
capsule=capture_capsule(Path(sys.argv[2]),sys.argv[3])
assert instrumentation_selected(capsule,requested=True)
assert 'coverage' not in sys.modules
assert sys.flags.no_site==sys.flags.isolated==1
print('PROFILE_ADMITTED_WITHOUT_COLLECTOR_STARTUP')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            code,
            str(_SOURCE),
            str(packet),
            capsule.manifest_sha256,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PROFILE_ADMITTED_WITHOUT_COLLECTOR_STARTUP"


@pytest.mark.parametrize(
    "name",
    [
        CONFIGURATION,
        "runtime/artifacts/coverage-partial.whl",
        _SITE + "coverage/partial.py",
        _SITE + "coverage-7.17.0.dist-info/METADATA",
        _SITE + "a1_coverage.pth",
    ],
)
def test_public_preparer_refuses_partial_collector_before_creating_packet(
    tmp_path: Path, instrumented_content: dict[str, bytes], name: str
) -> None:
    """Real image capture cannot smuggle instrumentation into default preparation."""
    from tools.protected_runtime_probe.prepare import prepare_packet

    runtime = {
        key: raw
        for key, raw in instrumented_content.items()
        if key.startswith("runtime/")
        and not (
            key == CONFIGURATION
            or key.startswith("runtime/artifacts/coverage-")
            or key.startswith(_SITE + "coverage")
            or key == _SITE + "a1_coverage.pth"
        )
    }
    runtime[name] = b"partial instrumentation input"
    capsule = _capsule(runtime)
    image = tmp_path / "image"
    image.mkdir()
    for key, raw in runtime.items():
        path = image / key.removeprefix("runtime/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = tmp_path / "runtime.json"
    manifest.write_bytes(capsule.manifest.canonical_bytes())
    packet = tmp_path / "packet"
    if (
        sys.platform != "linux"
        or platform.machine() != "x86_64"
        or sys.version_info[:2] != (3, 12)
        or sys.maxsize.bit_length() + 1 != 64
    ):
        with pytest.raises(OSError, match="native Linux x86_64 CPython3.12"):
            prepare_packet(_SOURCE, packet, tmp_path / "future-backing")
        assert not packet.exists()
        return
    with pytest.raises(ValueError, match="production runtime refuses instrumentation"):
        prepare_packet(
            _SOURCE,
            packet,
            tmp_path / "future-backing",
            runtime_image=image,
            runtime_manifest=manifest,
            runtime_sha256=capsule.manifest_sha256,
            dependency_wheel=tmp_path / "never-opened.whl",
        )
    assert not packet.exists()


@pytest.mark.parametrize(
    "suffix",
    [
        "a1_coverage.pth",
        "coverage-7.17.0.dist-info/RECORD",
        "coverage-7.17.0.dist-info/licenses/LICENSE.txt",
        "coverage/control.py",
    ],
)
def test_configuration_cannot_omit_a_coherent_original_wheel_member(
    instrumented_content: dict[str, bytes], suffix: str
) -> None:
    """Even mutually matching configuration/data omissions fail original-wheel comparison."""
    content = dict(instrumented_content)
    name = _SITE + suffix
    del content[name]
    record = json.loads(content[CONFIGURATION])
    record["collector"] = [pin for pin in record["collector"] if pin["path"] != name]
    with pytest.raises(ValueError, match="complete original wheel"):
        instrumentation_selected(_capsule(_profile(content, record)), requested=True)


@pytest.mark.parametrize("case", ["ordinary", "efbig", "sigxfsz"])
def test_real_finite_file_failures_are_instrumentation_errors(tmp_path: Path, case: str) -> None:
    """Actual child file-limit failures cannot become a successful or failed product result."""
    if sys.platform != "linux":
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "tools.protected_runtime_probe.prepare",
                "--source",
                str(_SOURCE),
                "--bundle",
                str(tmp_path / "unsupported"),
                "--backing-parent",
                str(tmp_path / "runs"),
            ],
            cwd=_SOURCE,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode != 0 and "native Linux" in result.stderr
        assert not (tmp_path / "unsupported").exists()
        return
    script = """import os,resource,signal,sys
from tools.protected_runtime_probe.instrumentation import emit_instrumentation_file_limit_error
case,path=sys.argv[1:]
if case!='ordinary':resource.setrlimit(resource.RLIMIT_FSIZE,(4096,4096))
if case=='sigxfsz':signal.signal(signal.SIGXFSZ,signal.SIG_DFL)
try:
 with open(path,'wb')as target:target.write(b'x'*8192)
except OSError as error:
 emit_instrumentation_file_limit_error(error)
 raise
"""
    target = tmp_path / "actual-write"
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, case, str(target)],
        cwd=_SOURCE,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )
    grade = instrumentation_file_limit_failure(returncode=result.returncode, stderr=result.stderr)
    assert not result.stdout
    if case == "ordinary":
        assert result.returncode == 0 and target.stat().st_size == 8192
        assert grade is None
    else:
        assert result.returncode != 0 and target.stat().st_size == 4096
        assert grade is not None and grade["grade"] == "ERROR"
        assert grade["origin"] == "instrumentation-artifact"
        assert grade["product_result"] is False
        if case == "efbig":
            assert grade["errno"] == errno.EFBIG and grade["signal"] is None
        else:
            assert grade["signal"] == signal.SIGXFSZ and grade["errno"] is None
