# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — original collector wheel and complete RECORD admission
"""Exercise actual wheel archives without executing collector or site-hook bytes."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from tools.protected_runtime_probe.instrumentation_artifacts import (
    capture_instrumentation_wheel,
    instrumentation_runtime_content,
)

_SOURCE = Path(__file__).resolve().parents[1]
_LOCK = _SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json"
_DIST = "coverage-7.17.0.dist-info/"
_TRACER = "coverage/tracer.cpython-312-x86_64-linux-gnu.so"


def _native_files_supported() -> bool:
    """Exercise the actual public refusal where descriptor capture is unavailable."""
    if os.name == "posix":
        return True
    with pytest.raises(OSError, match="requires native POSIX descriptors"):
        capture_instrumentation_wheel(_LOCK, _LOCK.with_name("not-opened.whl"))
    return False


@pytest.fixture(scope="module")
def collector_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Use the preloaded original or acquire the exact public profile artifact."""
    if not _native_files_supported():
        return _LOCK.with_name("not-opened.whl")
    preloaded = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL")
    if preloaded:
        path = Path(preloaded)
    else:
        pin = json.loads(_LOCK.read_bytes())["coverage"]
        path = tmp_path_factory.mktemp("collector-original") / pin["filename"]
        with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        assert len(raw) == pin["size_bytes"]
        assert hashlib.sha256(raw).hexdigest() == pin["sha256"]
        path.write_bytes(raw)
    capture_instrumentation_wheel(_LOCK, path)
    return path


def _record(content: dict[str, bytes]) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream)
    for name, raw in sorted(content.items()):
        if name == _DIST + "RECORD":
            writer.writerow((name, "", ""))
        else:
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
            writer.writerow((name, "sha256=" + digest, str(len(raw))))
    return stream.getvalue().encode()


def _input_archive(directory: Path, original: Path, content: dict[str, bytes]) -> tuple[Path, Path]:
    """Repin a separate input tree to test RECORD validation, not vendor provenance."""
    wheel = directory / original.name
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in content.items():
            archive.writestr(name, raw)
    raw = wheel.read_bytes()
    lock = json.loads(_LOCK.read_bytes())
    lock["coverage"].update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    path = directory / "input-profile-lock.json"
    path.write_text(json.dumps(lock))
    return path, wheel


def test_original_collector_archive_yields_complete_detached_runtime(collector_wheel: Path) -> None:
    """Retain the original archive, licences and every RECORD-bound member."""
    if not _native_files_supported():
        return
    wheel = capture_instrumentation_wheel(_LOCK, collector_wheel)
    pin = json.loads(_LOCK.read_bytes())["coverage"]
    assert wheel.artifact.filename == pin["filename"]
    assert wheel.artifact.sha256 == pin["sha256"]
    assert wheel.artifact.content == collector_wheel.read_bytes()
    with zipfile.ZipFile(collector_wheel) as archive:
        expected = {x.filename for x in archive.infolist() if not x.is_dir()}
        assert set(wheel.members) == expected
        for name in expected:
            assert wheel.members[name] == archive.read(name)
    assert wheel.members[_TRACER].startswith(b"\x7fELF")
    assert wheel.members[_DIST + "licenses/LICENSE.txt"]
    payload = instrumentation_runtime_content(wheel)
    prefix = "runtime/python/lib/python3.12/dist-packages/"
    assert set(payload) == {prefix + name for name in expected} | {
        "runtime/artifacts/" + collector_wheel.name
    }
    assert prefix + "a1_coverage.pth" in payload
    payload[prefix + "coverage/__init__.py"] = b"changed detached output"
    assert wheel.members["coverage/__init__.py"] != b"changed detached output"


def test_real_isolated_admission_leaves_collector_and_site_hook_unloaded(
    collector_wheel: Path,
) -> None:
    """The -I -S entry verifies bytes while no coverage module runs."""
    if not _native_files_supported():
        return
    script = """import json,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/src']
from tools.protected_runtime_probe.instrumentation_artifacts import (
 capture_instrumentation_wheel,instrumentation_runtime_content,
)
before='coverage' in sys.modules
wheel=capture_instrumentation_wheel(__import__('pathlib').Path(sys.argv[2]),
 __import__('pathlib').Path(sys.argv[3]))
payload=instrumentation_runtime_content(wheel)
print(json.dumps({'before':before,'after':'coverage' in sys.modules,
 'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,'members':len(wheel.members),
 'pth_captured':any(name.endswith('/a1_coverage.pth') for name in payload)}))
"""
    child = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            script,
            str(_SOURCE),
            str(_LOCK),
            str(collector_wheel),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/nonexistent"},
    )
    result = json.loads(child.stdout)
    assert result["before"] is False and result["after"] is False
    assert result["isolated"] == result["no_site"] == 1
    assert result["pth_captured"] and result["members"] > 1
    assert child.stderr == ""


@pytest.mark.parametrize("change", ["bytes", "size", "name", "version", "abi", "missing-pin"])
def test_original_wheel_requires_its_exact_declared_profile(
    tmp_path: Path, collector_wheel: Path, change: str
) -> None:
    """Artifact and profile mismatches refuse before archive-member admission."""
    if not _native_files_supported():
        return
    lock = json.loads(_LOCK.read_bytes())
    wheel = tmp_path / collector_wheel.name
    wheel.write_bytes(collector_wheel.read_bytes())
    if change == "bytes":
        with wheel.open("ab") as stream:
            stream.write(b"changed archive input")
    elif change == "size":
        lock["coverage"]["size_bytes"] += 1
    elif change == "name":
        lock["coverage"]["filename"] = "another-artifact.whl"
    elif change == "version":
        lock["coverage"]["version"] = "0"
    elif change == "abi":
        lock["native_abi"] = "unsupported"
    else:
        del lock["coverage"]
    path = tmp_path / "input-profile-lock.json"
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="artifact pin|malformed or unsupported"):
        capture_instrumentation_wheel(path, wheel)


@pytest.mark.parametrize("change", ["hash", "size", "duplicate", "missing", "self-entry", "extra"])
def test_record_binding_is_checked_after_the_input_archive_digest(
    tmp_path: Path, collector_wheel: Path, change: str
) -> None:
    """A repinned ordinary input archive still needs its complete valid RECORD."""
    if not _native_files_supported():
        return
    captured = capture_instrumentation_wheel(_LOCK, collector_wheel)
    content = dict(captured.members)
    rows = list(csv.reader(io.StringIO(content[_DIST + "RECORD"].decode())))
    row = next(row for row in rows if row[0] == "coverage/__init__.py")
    if change == "hash":
        row[1] = "sha256=" + "A" * 43
    elif change == "size":
        row[2] = str(int(row[2]) + 1)
    elif change == "duplicate":
        rows.append(row.copy())
    elif change == "missing":
        rows.remove(row)
    elif change == "self-entry":
        next(row for row in rows if row[0] == _DIST + "RECORD")[1] = "unexpected"
    else:
        content["coverage/extra.py"] = b"unrecorded ordinary member\n"
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    content[_DIST + "RECORD"] = stream.getvalue().encode()
    lock, wheel = _input_archive(tmp_path, collector_wheel, content)
    with pytest.raises(ValueError, match="RECORD"):
        capture_instrumentation_wheel(lock, wheel)


@pytest.mark.parametrize("change", ["name", "version", "tag", "licence", "tracer", "extra-native"])
def test_complete_record_does_not_substitute_distribution_or_native_profile(
    tmp_path: Path, collector_wheel: Path, change: str
) -> None:
    """Complete internally hashed input bytes must still match the supported ABI."""
    if not _native_files_supported():
        return
    content = dict(capture_instrumentation_wheel(_LOCK, collector_wheel).members)
    if change == "name":
        content[_DIST + "METADATA"] = content[_DIST + "METADATA"].replace(
            b"Name: coverage", b"Name: different"
        )
    elif change == "version":
        content[_DIST + "METADATA"] = content[_DIST + "METADATA"].replace(
            b"Version: 7.17.0", b"Version: 0"
        )
    elif change == "tag":
        content[_DIST + "WHEEL"] = content[_DIST + "WHEEL"].replace(b"cp312", b"cp311")
    elif change == "licence":
        del content[_DIST + "licenses/LICENSE.txt"]
    elif change == "tracer":
        del content[_TRACER]
    else:
        content["coverage/extra.so"] = content[_TRACER]
    content[_DIST + "RECORD"] = _record(content)
    lock, wheel = _input_archive(tmp_path, collector_wheel, content)
    with pytest.raises(ValueError, match="metadata, licence or tracer|distribution or native ABI"):
        capture_instrumentation_wheel(lock, wheel)


@pytest.mark.parametrize("change", ["foreign", "duplicate", "symlink", "empty-directory"])
def test_real_zip_inventory_refuses_unlisted_scope_or_object_kinds(
    tmp_path: Path, collector_wheel: Path, change: str
) -> None:
    """Actual ZIP entries cannot introduce unsupported objects or duplicate names."""
    if not _native_files_supported():
        return
    content = dict(capture_instrumentation_wheel(_LOCK, collector_wheel).members)
    if change == "foreign":
        content["unrelated.txt"] = b"unlisted package member"
    elif change == "empty-directory":
        content["coverage/unlisted/"] = b""
    content[_DIST + "RECORD"] = _record(content)
    lock, wheel = _input_archive(tmp_path, collector_wheel, content)
    if change in {"duplicate", "symlink"}:
        warning = (
            pytest.warns(UserWarning, match="Duplicate name")
            if change == "duplicate"
            else nullcontext()
        )
        with warning:
            with zipfile.ZipFile(wheel, "a") as archive:
                name = "coverage/__init__.py" if change == "duplicate" else "coverage/link.py"
                entry: str | zipfile.ZipInfo = name
                if change == "symlink":
                    entry = zipfile.ZipInfo(name)
                    entry.create_system = 3
                    entry.external_attr = 0o120777 << 16
                archive.writestr(entry, b"ordinary invalid archive entry")
        value: dict[str, Any] = json.loads(lock.read_text())
        raw = wheel.read_bytes()
        value["coverage"].update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        lock.write_text(json.dumps(value))
    with pytest.raises(
        ValueError, match="member scope|member inventory|member type|empty directory"
    ):
        capture_instrumentation_wheel(lock, wheel)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("expanded", "expanded byte bound"),
        ("directory-bytes", "directory contains bytes"),
        ("member-size", "member byte bound"),
        ("ancestor-file", "file shadows a member directory"),
        ("missing-record", "RECORD is missing"),
    ],
)
def test_actual_archive_size_and_inventory_limits_refuse_before_runtime_use(
    tmp_path: Path, collector_wheel: Path, change: str, message: str
) -> None:
    """Bound real ZIP payloads and preserve an unambiguous complete member tree."""
    if not _native_files_supported():
        return
    content = dict(capture_instrumentation_wheel(_LOCK, collector_wheel).members)
    if change == "expanded":
        payload = b"x" * (7 * 1024 * 1024)
        for index in range(5):
            content[f"coverage/large-{index}.py"] = payload
    elif change == "directory-bytes":
        content["coverage/nonempty/"] = b"nonempty directory entry"
    elif change == "member-size":
        content["coverage/oversized.py"] = b"x" * (8 * 1024 * 1024 + 1)
    elif change == "ancestor-file":
        content["coverage/htmlfiles"] = b"file occupying a required directory"
    else:
        del content[_DIST + "RECORD"]
    if change != "missing-record":
        content[_DIST + "RECORD"] = _record(content)
    lock, wheel = _input_archive(tmp_path, collector_wheel, content)
    with pytest.raises(ValueError, match=message):
        capture_instrumentation_wheel(lock, wheel)


@pytest.mark.parametrize("change", ["empty", "oversized", "digest"])
def test_detached_archive_decode_rechecks_retained_artifact_pin(
    collector_wheel: Path, change: str
) -> None:
    """Captured bytes are checked again when decoded without filesystem access."""
    if not _native_files_supported():
        return
    from tools.protected_runtime_probe.instrumentation_artifacts import (
        decode_instrumentation_wheel,
    )
    from tools.protected_runtime_probe.native_provenance import NativeWheelArtifact

    original = capture_instrumentation_wheel(_LOCK, collector_wheel).artifact
    raw = original.content
    if change == "empty":
        raw = b""
    elif change == "oversized":
        raw = b"x" * (8 * 1024 * 1024 + 1)
    else:
        raw += b"changed"
    candidate = NativeWheelArtifact(original.filename, original.sha256, raw)
    with pytest.raises(ValueError, match="captured instrumentation archive"):
        decode_instrumentation_wheel(candidate)


@pytest.mark.parametrize(
    "change", ["none", "bytes", "missing", "extra", "empty-directory", "symlink", "hardlink"]
)
def test_complete_distribution_tree_is_verified_before_any_collector_import(
    tmp_path: Path, collector_wheel: Path, change: str
) -> None:
    """Real detached files retain exact bytes, topology and single-link custody."""
    if not _native_files_supported():
        return
    from tools.protected_runtime_probe.instrumentation_artifacts import verify_instrumentation_tree

    wheel = capture_instrumentation_wheel(_LOCK, collector_wheel)
    root = tmp_path / "library"
    root.mkdir()
    for name, raw in wheel.members.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    member = root / "coverage/control.py"
    if change == "none":
        verify_instrumentation_tree(root, wheel)
        return
    if change == "bytes":
        member.write_bytes(b"changed source input")
    elif change == "missing":
        member.unlink()
    elif change == "extra":
        (root / "coverage/not-in-original.py").write_bytes(b"extra")
    elif change == "empty-directory":
        (root / "not-in-original").mkdir()
    elif change == "symlink":
        member.unlink()
        member.symlink_to(collector_wheel)
    else:
        os.link(member, tmp_path / "second-link")
    with pytest.raises((ValueError, OSError)):
        verify_instrumentation_tree(root, wheel)


@pytest.mark.parametrize("change", ["none", "member", "captured", "source"])
def test_observed_tracer_matches_source_and_original_wheel(
    tmp_path: Path, collector_wheel: Path, change: str
) -> None:
    """Actual native source bytes preserve complete original RECORD provenance."""
    if not _native_files_supported():
        return
    from tools.protected_runtime_probe.instrumentation_artifacts import (
        verify_instrumentation_native_file,
    )

    wheel = capture_instrumentation_wheel(_LOCK, collector_wheel)
    member = "coverage/tracer.cpython-312-x86_64-linux-gnu.so"
    raw = wheel.members[member]
    path = tmp_path / "actual-tracer.so"
    path.write_bytes(raw)
    if change == "member":
        member = "other/tracer.so"
    elif change == "captured":
        raw += b"changed captured bytes"
    elif change == "source":
        raw += b"matching source change"
        path.write_bytes(raw)
    else:
        observation = verify_instrumentation_native_file(path, member, raw, wheel)
        assert observation["wheel_record_verified"] is True
        assert observation["distribution"] == "coverage"
        assert observation["sha256"] == hashlib.sha256(raw).hexdigest()
        return
    with pytest.raises(ValueError):
        verify_instrumentation_native_file(path, member, raw, wheel)


@pytest.mark.parametrize(
    "name",
    [
        "coverage/../foreign.py",
        "/coverage/foreign.py",
        "coverage//foreign.py",
        "coverage\\foreign.py",
        "coverage/control\nname.py",
    ],
)
def test_noncanonical_archive_paths_refuse_before_runtime_placement(
    tmp_path: Path, collector_wheel: Path, name: str
) -> None:
    """Actual ZIP input never promotes escaping or control-bearing member names."""
    if not _native_files_supported():
        return
    content = dict(capture_instrumentation_wheel(_LOCK, collector_wheel).members)
    content[name] = b"ordinary archive input, never execute"
    content[_DIST + "RECORD"] = _record(content)
    lock, wheel = _input_archive(tmp_path, collector_wheel, content)
    with pytest.raises(ValueError, match="member scope differs"):
        capture_instrumentation_wheel(lock, wheel)
