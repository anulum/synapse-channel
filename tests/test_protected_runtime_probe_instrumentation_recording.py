# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual memory recording and exclusive dataset saving
"""Exercise admitted original collector code, real memory data and actual file descriptors."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import websockets
from tools.protected_runtime_probe.instrumentation_recording import prepare_recording

from fixtures.protected_runtime_image import (
    materialize_instrumentation_view,
    samsung_backing_or_refusal,
)

_SOURCE = Path(__file__).resolve().parents[1]
_LOCK = _SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json"
_CHILD = r"""import sys, json, os, resource, hashlib, shutil
from pathlib import Path

view = Path(sys.argv[1])
digest = sys.argv[2]
work = Path(sys.argv[3])
case = sys.argv[4]
role = sys.argv[5]
sys.path[:0] = [str(view / "probe"), str(view / "runtime/python/lib/python3.12/dist-packages")]
if case == "foreign-cache":
    foreign = work / "foreign-library"
    foreign.mkdir()
    shutil.copytree(
        view / "runtime/python/lib/python3.12/dist-packages/coverage", foreign / "coverage"
    )
    sys.path.insert(0, str(foreign))
import coverage

assert coverage.__version__ == "7.17.0"
outer = coverage.Coverage(
    data_file=None,
    branch=True,
    config_file=False,
    plugins=[],
    source_pkgs=["protected_runtime_probe.instrumentation_recording"],
)
outer.set_option("run:core", "ctrace")
outer.start()
from protected_runtime_probe.instrumentation_recording import (
    prepare_recording,
    CoverageRecording,
    snapshot_memory_coverage,
    MAX_RECORDING_BYTES,
    RECORDING_FILENAME,
)
from protected_runtime_probe.capsule import capture_capsule, require_posix_descriptors

parent = work / "private"
parent.mkdir()
leaf = parent / "leaf"
leaf.mkdir()
fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
result = {"case": case, "role": role}


def memory_data():
    mini = coverage.Coverage(
        data_file=None,
        branch=True,
        config_file=False,
        plugins=[],
        source_pkgs=["protected_runtime_probe.capsule"],
    )
    mini.set_option("run:core", "ctrace")
    with mini.collect():
        require_posix_descriptors("posix")
    return mini.get_data()


try:
    if case.startswith("snapshot-"):
        data = memory_data()
        try:
            if case == "snapshot-valid":
                raw = snapshot_memory_coverage(data)
                result["bytes"] = len(raw)
            else:
                selected = data
                maximum = MAX_RECORDING_BYTES
                if case == "snapshot-disk":
                    disk = work / "not-a-memory-dataset"
                    disk.write_bytes(b"unchanged input")
                    selected = coverage.CoverageData(basename=str(disk))
                elif case == "snapshot-empty":
                    selected = coverage.CoverageData(no_disk=True)
                elif case == "snapshot-zero-bound":
                    maximum = 0
                elif case == "snapshot-bool-bound":
                    maximum = True
                elif case == "snapshot-large-bound":
                    maximum = MAX_RECORDING_BYTES + 1
                elif case == "snapshot-small-bound":
                    maximum = 1024
                try:
                    snapshot_memory_coverage(selected, maximum_bytes=maximum)
                except ValueError as error:
                    result["refused"] = str(error)
                else:
                    raise AssertionError("invalid snapshot was admitted")
                if case == "snapshot-disk":
                    assert disk.read_bytes() == b"unchanged input"
                if selected is not data:
                    selected.close(force=True)
        finally:
            data.close(force=True)
    else:
        selected_view = view
        selected_digest = digest
        selected_role = role
        selected_fd = fd
        uid, gid = os.geteuid(), os.getegid()
        failures = {
            "nonisolated",
            "bad-role",
            "bad-fd",
            "bad-uid",
            "bad-gid",
            "bad-mode",
            "bad-owner",
            "nonempty",
            "not-directory",
            "relative-view",
            "changed-digest",
            "foreign-cache",
            "version-metadata",
            "zero-budget",
        }
        if case == "bad-role":
            selected_role = "unregistered"
        elif case == "bad-fd":
            selected_fd = -1
        elif case == "bad-uid":
            uid = True
        elif case == "bad-gid":
            gid = -1
        elif case == "bad-mode":
            leaf.chmod(0o755)
        elif case == "bad-owner":
            uid += 1
        elif case == "nonempty":
            (leaf / "existing").write_bytes(b"unchanged input")
        elif case == "not-directory":
            regular = work / "regular"
            regular.write_bytes(b"ordinary input")
            selected_fd = os.open(regular, os.O_RDONLY)
        elif case == "relative-view":
            selected_view = Path("relative")
        elif case == "changed-digest":
            selected_digest = "f" * 64
        elif case == "version-metadata":
            # This perturbs observed metadata, not the original artifact or its real version.
            coverage.__version__ = "observed metadata changed"
        elif case == "zero-budget":
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        elif case == "lower-budget":
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        try:
            recording = prepare_recording(
                selected_view,
                selected_digest,
                role=selected_role,
                output_descriptor=selected_fd,
                uid=uid,
                gid=gid,
            )
        except (ValueError, PermissionError, OSError) as error:
            if case not in failures:
                raise
            result["refused"] = str(error)
        else:
            if case in failures:
                raise AssertionError("invalid recording was admitted")
            os.close(fd)
            fd = -1
            with recording.collector.collect():
                measured = capture_capsule(view, digest)
                assert measured.manifest_sha256 == digest
            assert not list(leaf.iterdir())
            if case == "existing-output":
                (leaf / RECORDING_FILENAME).write_bytes(b"do not overwrite")
            elif case == "changed-directory":
                leaf.chmod(0o755)
            elif case == "renamed-directory":
                retained = parent / "retained"
                leaf.rename(retained)
                leaf.mkdir()
                (leaf / "canary").write_bytes(b"untouched replacement")
                leaf = retained
            elif case == "short-write":
                resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))
            if case in {"wrong-save-uid", "wrong-save-gid"}:
                declared_uid = os.geteuid() + (case == "wrong-save-uid")
                declared_gid = os.getegid() + (case == "wrong-save-gid")
                borrowed = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                info = os.fstat(borrowed)
                inconsistent = CoverageRecording(
                    recording.collector,
                    borrowed,
                    (info.st_dev, info.st_ino),
                    role,
                    declared_uid,
                    declared_gid,
                    digest,
                    MAX_RECORDING_BYTES,
                )
                try:
                    inconsistent.save()
                except PermissionError as error:
                    result["save_principal_refused"] = str(error)
                else:
                    raise AssertionError("wrong final principal was admitted")
                assert not list(leaf.iterdir())
            try:
                saved = recording.save()
            except OSError as error:
                if case not in {"existing-output", "changed-directory", "short-write"}:
                    raise
                result["refused"] = str(error)
                if case == "existing-output":
                    assert (leaf / RECORDING_FILENAME).read_bytes() == b"do not overwrite"
                elif case == "short-write":
                    assert "write was incomplete" in str(error)
                    assert (leaf / RECORDING_FILENAME).stat().st_size == 4096
            else:
                if case in {"existing-output", "changed-directory", "short-write"}:
                    raise AssertionError("unsafe save was admitted")
                result["saved"] = saved
                assert saved["uid"] == os.geteuid() and saved["gid"] == os.getegid()
                assert saved["size_bytes"] == (leaf / RECORDING_FILENAME).stat().st_size
                assert (
                    saved["dataset_sha256"]
                    == hashlib.sha256((leaf / RECORDING_FILENAME).read_bytes()).hexdigest()
                )
                disk = coverage.CoverageData(basename=str(leaf / RECORDING_FILENAME))
                disk.read()
                measured_file = str(view / "probe/protected_runtime_probe/capsule.py")
                assert disk.arcs(measured_file)
                assert role in disk.measured_contexts()
                disk.close(force=True)
                assert (leaf / RECORDING_FILENAME).stat().st_mode & 0o777 == 0o600
                if case == "renamed-directory":
                    assert (parent / "leaf/canary").read_bytes() == b"untouched replacement"
            try:
                recording.save()
            except ValueError:
                result["repeat_refused"] = True
            else:
                raise AssertionError("terminal recording saved twice")
        finally:
            coverage.__version__ = "7.17.0"
            if case == "not-directory":
                os.close(selected_fd)
finally:
    if fd >= 0:
        os.close(fd)
    outer.stop()
    observed = outer.get_data()
    source = str(view / "probe/protected_runtime_probe/instrumentation_recording.py")
    result["measurement"] = {
        "path": source,
        "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
        "arcs": observed.arcs(source) or [],
    }
    observed.close(force=True)
print(json.dumps(result))
"""


def _native() -> bool:
    """Exercise the real public unsupported ABI refusal without a platform patch."""
    if (sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1) == (
        "linux",
        "x86_64",
        (3, 12),
        64,
    ):
        return True
    with pytest.raises(OSError, match="native Linux x86_64 CPython3.12"):
        prepare_recording(
            Path("unopened"), "0" * 64, role="controller", output_descriptor=-1, uid=0, gid=0
        )
    return False


@pytest.fixture(scope="module")
def admitted_view(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str] | None:
    """Build actual source and installed runtime bytes without a root/loader invocation."""
    directory = tmp_path_factory.mktemp("recording-view")
    if not _native() or not samsung_backing_or_refusal(directory):
        return None
    supplied = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL")
    pin = json.loads(_LOCK.read_bytes())["coverage"]
    artifact = Path(supplied) if supplied else directory / pin["filename"]
    if not supplied:
        with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
            artifact.write_bytes(response.read(8 * 1024 * 1024 + 1))
    return materialize_instrumentation_view(
        _SOURCE, Path(websockets.__file__).parent, directory, artifact
    )


def _case(
    admitted_view: tuple[Path, str], tmp_path: Path, case: str, role: str = "controller"
) -> dict[str, Any]:
    """Run the actual collector in a fresh isolated process, then verify source-bound metrics."""
    view, digest = admitted_view
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("COVERAGE_")
    }
    environment.update(PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [
            sys.executable,
            *([] if case == "nonisolated" else ["-I"]),
            "-B",
            "-S",
            "-c",
            _CHILD,
            str(view),
            digest,
            str(tmp_path),
            case,
            role,
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed: dict[str, Any] = json.loads(result.stdout)
    current = _SOURCE / "tools/protected_runtime_probe/instrumentation_recording.py"
    assert observed["measurement"]["sha256"] == hashlib.sha256(current.read_bytes()).hexdigest()
    target = os.environ.get("SYNAPSE_RECORDING_MEASUREMENT_DIR")
    if target:
        path = Path(target) / (case + "-" + role + ".json")
        path.write_text(json.dumps(observed, indent=2) + "\n")
    return observed


def test_actual_nonisolated_caller_is_refused_before_directory_access() -> None:
    """The real ordinary pytest interpreter cannot start the finite isolated profile."""
    if not _native():
        return
    if sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode:
        return
    with pytest.raises(PermissionError, match="isolated"):
        prepare_recording(
            Path("unopened"), "0" * 64, role="controller", output_descriptor=-1, uid=0, gid=0
        )


@pytest.mark.parametrize(
    "role", ["controller", "namespace-wrapper", "authority", "client", "operator", "writer"]
)
def test_actual_original_collector_records_and_saves_each_profile_label(
    admitted_view: tuple[Path, str] | None, tmp_path: Path, role: str
) -> None:
    """Each fresh ordinary process saves real branch/context data; no UID drop is claimed."""
    if admitted_view is None:
        return
    result = _case(admitted_view, tmp_path, "valid", role)
    assert result["saved"]["role"] == role
    assert result["repeat_refused"]


@pytest.mark.parametrize(
    "case",
    [
        "bad-role",
        "bad-fd",
        "bad-uid",
        "bad-gid",
        "bad-mode",
        "bad-owner",
        "nonempty",
        "not-directory",
        "relative-view",
        "changed-digest",
        "foreign-cache",
        "version-metadata",
        "zero-budget",
    ],
)
def test_actual_profile_factory_refuses_invalid_enrollment_or_runtime_state(
    admitted_view: tuple[Path, str] | None, tmp_path: Path, case: str
) -> None:
    """Actual inputs/cached metadata are rejected without an output dataset."""
    if admitted_view is None:
        return
    assert _case(admitted_view, tmp_path, case)["refused"]


def test_actual_nonisolated_subprocess_cannot_prepare_a_recording(
    admitted_view: tuple[Path, str] | None, tmp_path: Path
) -> None:
    """The real missing isolated flag is refused in a separately measured process."""
    if admitted_view is None:
        return
    assert "isolated" in _case(admitted_view, tmp_path, "nonisolated")["refused"]


@pytest.mark.parametrize("case", ["existing-output", "changed-directory"])
def test_actual_save_refuses_preexisting_data_or_changed_directory(
    admitted_view: tuple[Path, str] | None, tmp_path: Path, case: str
) -> None:
    """A terminal save never overwrites another file or accepts changed custody."""
    if admitted_view is None:
        return
    assert _case(admitted_view, tmp_path, case)["refused"]


def test_actual_file_limit_partial_write_is_terminal_failure(
    admitted_view: tuple[Path, str] | None, tmp_path: Path
) -> None:
    """A real lower kernel file limit truncates the write without reporting success."""
    if admitted_view is None:
        return
    result = _case(admitted_view, tmp_path, "short-write")
    assert "write was incomplete" in result["refused"]
    assert "saved" not in result
    assert result["repeat_refused"]


@pytest.mark.parametrize("case", ["wrong-save-uid", "wrong-save-gid"])
def test_actual_process_refuses_inconsistent_declared_final_principal(
    admitted_view: tuple[Path, str] | None, tmp_path: Path, case: str
) -> None:
    """Public save observes real current credentials against deliberately inconsistent inputs."""
    if admitted_view is None:
        return
    assert _case(admitted_view, tmp_path, case)["save_principal_refused"]


@pytest.mark.parametrize("case", ["renamed-directory", "lower-budget"])
def test_actual_retained_output_and_inherited_file_budget(
    admitted_view: tuple[Path, str] | None, tmp_path: Path, case: str
) -> None:
    """Retained directory identity survives renaming and an inherited limit is never raised."""
    if admitted_view is None:
        return
    result = _case(admitted_view, tmp_path, case)
    assert result["saved"]["file_limit_bytes"] == (
        1024 * 1024 if case == "lower-budget" else 16 * 1024 * 1024
    )


@pytest.mark.parametrize(
    "case",
    [
        "snapshot-valid",
        "snapshot-disk",
        "snapshot-empty",
        "snapshot-zero-bound",
        "snapshot-bool-bound",
        "snapshot-large-bound",
        "snapshot-small-bound",
    ],
)
def test_public_memory_snapshot_uses_actual_collected_data_and_storage_contract(
    admitted_view: tuple[Path, str] | None, tmp_path: Path, case: str
) -> None:
    """Real in-memory arc data is serialized; disk/empty/over-budget data is refused."""
    if admitted_view is None:
        return
    result = _case(admitted_view, tmp_path, case)
    assert result.get("bytes", 0) > 0 if case == "snapshot-valid" else result["refused"]
