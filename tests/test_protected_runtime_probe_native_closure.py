# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual native import, installed package and wheel verification
"""Exercise current native dependencies without executing proposed captured binaries."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest
import websockets
from tools.protected_runtime_probe.native_closure import (
    NativeRuntimeSources,
    classify_native_import_paths,
    observe_native_import_closure,
    require_native_runtime_abi,
    verify_native_runtime_content,
)
from tools.protected_runtime_probe.native_provenance import (
    load_native_wheel,
    verify_dpkg_native_file,
    verify_wheel_native_file,
)

from _platform_caps import requires_packaged_python312

_SOURCE = Path(__file__).resolve().parents[1]
_DEPENDENCY = Path(websockets.__file__).parent
_LOCK = _SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json"


def _supported_or_refused() -> bool:
    """Exercise the real platform refusal where this Linux profile is unavailable."""
    if (
        sys.platform == "linux"
        and platform.machine() == "x86_64"
        and sys.version_info[:2] == (3, 12)
    ):
        return True
    with pytest.raises(OSError, match="Linux x86_64 CPython3.12"):
        observe_native_import_closure(_SOURCE, _DEPENDENCY)
    return False


@pytest.fixture(scope="module")
def native_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Acquire the actual public wheel once and require its existing source lock."""
    directory = tmp_path_factory.mktemp("native-dependency-artifact")
    if not _supported_or_refused():
        return directory / "unsupported-profile.whl"
    pin = json.loads(_LOCK.read_bytes())["websockets"]
    filename = pin["filename"]
    assert isinstance(filename, str)
    path = directory / filename
    with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
        raw = response.read(8 * 1024 * 1024)
    assert len(raw) == pin["size_bytes"]
    assert hashlib.sha256(raw).hexdigest() == pin["sha256"]
    path.write_bytes(raw)
    load_native_wheel(_LOCK, path)
    return path


@requires_packaged_python312
def test_actual_probe_closure_has_current_package_and_wheel_provenance(native_wheel: Path) -> None:
    """Current native objects match installed dpkg/wheel records without unused libraries."""
    if not _supported_or_refused():
        return
    observed = observe_native_import_closure(_SOURCE, _DEPENDENCY)
    content = {item.payload: item.installed_source.read_bytes() for item in observed}
    result = verify_native_runtime_content(content, _SOURCE, _DEPENDENCY, native_wheel)
    assert result["deferred_imports"] == ["fcntl"]
    assert result["account_resolution"] == {
        "owned_interpreters": "root-controlled-local-files",
        "external_processes": ["/usr/sbin/useradd", "/usr/bin/setfacl"],
        "external_nss": "outside-interpreter-import-observation",
    }
    objects = result["native_objects"]
    assert isinstance(objects, list) and len(objects) == len(observed)
    assert {row["payload"] for row in objects} == set(content)
    assert any(
        row["manager"] == "wheel" and row["wheel_record_verified"] is True for row in objects
    )
    assert all(row["dpkg_md5_verified"] is True for row in objects if row["manager"] == "dpkg")
    assert not any("Tk" in name or "X11" in name or "BLT" in name for name in content)
    assert any("lib-dynload/" in name for name in content)


@pytest.mark.parametrize("change", ["changed", "extra", "missing"])
@requires_packaged_python312
def test_runtime_refuses_native_bytes_or_names_outside_actual_closure(
    native_wheel: Path, change: str
) -> None:
    """Real loaded-library bytes cannot be changed, duplicated under a new name or omitted."""
    if not _supported_or_refused():
        return
    observed = observe_native_import_closure(_SOURCE, _DEPENDENCY)
    content = {item.payload: item.installed_source.read_bytes() for item in observed}
    name = next(name for name in content if name.startswith("runtime/native/lib/"))
    if change == "changed":
        content[name] = bytes([content[name][0] ^ 1]) + content[name][1:]
    elif change == "extra":
        content["runtime/native/lib/unneeded-copy.so"] = content[name]
    else:
        del content[name]
    with pytest.raises(ValueError, match="installed source|observed import closure"):
        verify_native_runtime_content(content, _SOURCE, _DEPENDENCY, native_wheel)


@requires_packaged_python312
def test_native_wheel_artifact_and_installed_module_refuse_substitution(
    native_wheel: Path, tmp_path: Path
) -> None:
    """Pinned upstream wheel bytes and its actual installed extension are independently checked."""
    if not _supported_or_refused():
        return
    artifact = load_native_wheel(_LOCK, native_wheel)
    native = next(
        item
        for item in observe_native_import_closure(_SOURCE, _DEPENDENCY)
        if item.manager == "wheel"
    )
    raw = native.installed_source.read_bytes()
    member = native.payload.removeprefix("runtime/python/lib/python3.12/dist-packages/")
    with pytest.raises(ValueError, match="installed source"):
        verify_wheel_native_file(native.installed_source, member, raw + b"changed", artifact)
    changed = tmp_path / native_wheel.name
    changed.write_bytes(artifact.content[:-1] + bytes([artifact.content[-1] ^ 1]))
    with pytest.raises(ValueError, match="artifact pin"):
        load_native_wheel(_LOCK, changed)


def test_unmanaged_actual_file_cannot_be_labelled_as_a_system_package(tmp_path: Path) -> None:
    """An ordinary user-owned file cannot acquire system-library provenance."""
    if not _supported_or_refused():
        return
    path = tmp_path / "unmanaged-native.so"
    path.write_bytes(b"ordinary unprivileged file")
    with pytest.raises(ValueError, match="protected system ownership"):
        verify_dpkg_native_file(path, path.read_bytes())


@requires_packaged_python312
def test_observation_refuses_a_source_without_the_actual_probe_entrypoints(tmp_path: Path) -> None:
    """An actual isolated import failure cannot become an accepted empty native closure."""
    if not _supported_or_refused():
        return
    with pytest.raises(ValueError, match="could not be observed"):
        observe_native_import_closure(tmp_path, _DEPENDENCY)


@pytest.mark.parametrize(
    ("system", "machine", "version", "bits"),
    [
        ("win32", "x86_64", (3, 12), 64),
        ("linux", "aarch64", (3, 12), 64),
        ("linux", "x86_64", (3, 11), 64),
        ("linux", "x86_64", (3, 13), 64),
        ("linux", "x86_64", (3, 12), 32),
    ],
)
def test_native_runtime_abi_refuses_unsupported_observations(
    system: str, machine: str, version: tuple[int, int], bits: int
) -> None:
    """Public ABI decisions refuse each independently unsupported observed dimension."""
    with pytest.raises(OSError, match="native Linux x86_64 CPython3.12"):
        require_native_runtime_abi(system, machine, version, bits)


def test_native_runtime_abi_accepts_the_declared_64_bit_profile() -> None:
    """The same domain decision used by both real adapters accepts the supported ABI."""
    require_native_runtime_abi("linux", "x86_64", (3, 12), 64)


@pytest.mark.parametrize("paths", [None, {}, [], ["/missing"] * 257])
def test_native_mapping_classification_refuses_unbounded_or_invalid_lists(paths: object) -> None:
    """Decoded observation data must be a nonempty bounded list."""
    with pytest.raises(ValueError, match="malformed or unbounded"):
        classify_native_import_paths(paths, _DEPENDENCY)


@pytest.mark.parametrize("name", [None, 5, "relative.so", "/lost.so (deleted)"])
def test_native_mapping_classification_refuses_unavailable_names(name: object) -> None:
    """Unusable mapping names are rejected before filesystem interpretation."""
    with pytest.raises(ValueError, match="path is unavailable"):
        classify_native_import_paths([name], _DEPENDENCY)


def test_native_mapping_ignores_actual_non_elf_files(tmp_path: Path) -> None:
    """Ordinary mapped data does not become a native executable payload."""
    source = tmp_path / "mapped-data"
    source.write_bytes(b"non executable data")
    if sys.platform == "win32":
        with pytest.raises(ValueError, match="native import path is unavailable"):
            classify_native_import_paths([str(source)], _DEPENDENCY)
        return
    assert classify_native_import_paths([str(source)], _DEPENDENCY) == ()


@requires_packaged_python312
def test_native_mapping_refuses_an_actual_elf_outside_declared_roots(tmp_path: Path) -> None:
    """A copied real interpreter outside all declared roots is not silently admitted."""
    if not _supported_or_refused():
        return
    source = tmp_path / "unlisted-python"
    shutil.copyfile("/usr/bin/python3.12", source)
    with pytest.raises(ValueError, match="outside its declared package sources"):
        classify_native_import_paths([str(source)], _DEPENDENCY)


@pytest.mark.parametrize("case", ["bad-elf", "no-soname", "collision"])
@requires_packaged_python312
def test_native_mapping_reads_real_loader_metadata_and_refuses_ambiguity(
    tmp_path: Path, case: str
) -> None:
    """Actual readelf and actual copied files expose malformed or duplicate loader names."""
    if not _supported_or_refused():
        return
    stdlib, libraries = tmp_path / "stdlib", tmp_path / "libraries"
    stdlib.mkdir()
    libraries.mkdir()
    first = libraries / "first.so"
    if case == "bad-elf":
        first.write_bytes(b"\x7fELFtruncated")
        message = "loader name is unavailable"
    elif case == "no-soname":
        shutil.copyfile("/usr/bin/python3.12", first)
        message = "loader name is ambiguous"
    else:
        shutil.copyfile("/usr/lib/x86_64-linux-gnu/libc.so.6", first)
        message = "colliding payload names"
    paths = [str(first)]
    if case == "collision":
        second = libraries / "second.so"
        shutil.copyfile(first, second)
        paths.append(str(second))
    roots = NativeRuntimeSources(Path("/usr/bin/python3.12"), stdlib, libraries)
    with pytest.raises(ValueError, match=message):
        classify_native_import_paths(paths, _DEPENDENCY, sources=roots)


@requires_packaged_python312
def test_native_mapping_labels_real_copies_using_explicit_source_roots(tmp_path: Path) -> None:
    """Declared ordinary source trees retain their real paths and deterministic placement."""
    if not _supported_or_refused():
        return
    stdlib, libraries, dependency = (
        tmp_path / "stdlib",
        tmp_path / "libraries",
        tmp_path / "websockets",
    )
    for root in (stdlib, libraries, dependency):
        root.mkdir()
    interpreter = tmp_path / "python3.12"
    shutil.copyfile("/usr/bin/python3.12", interpreter)
    original = observe_native_import_closure(_SOURCE, _DEPENDENCY)
    paths = [interpreter]
    for manager, prefix, destination in (
        ("dpkg", "runtime/python/lib/python3.12/lib-dynload/", stdlib / "actual-extension.so"),
        ("dpkg", "runtime/native/lib/libc.so.6", libraries / "actual-libc.so"),
        ("wheel", "runtime/python/lib/python3.12/dist-packages/", dependency / "actual-wheel.so"),
    ):
        item = next(x for x in original if x.manager == manager and x.payload.startswith(prefix))
        shutil.copyfile(item.installed_source, destination)
        paths.append(destination)
    roots = NativeRuntimeSources(interpreter, stdlib, libraries)
    result = classify_native_import_paths([str(p) for p in paths], dependency, sources=roots)
    assert {x.installed_source for x in result} == set(paths)
    assert {x.payload for x in result} == {
        "runtime/python/bin/python3.12",
        "runtime/python/lib/python3.12/actual-extension.so",
        "runtime/native/lib/libc.so.6",
        "runtime/python/lib/python3.12/dist-packages/websockets/actual-wheel.so",
    }
    assert sum(x.manager == "wheel" for x in result) == 1


@pytest.mark.parametrize("member", ["other/module.so", "websockets/../module.so"])
def test_wheel_native_refuses_member_paths_outside_the_declared_dependency(
    native_wheel: Path, member: str
) -> None:
    """A real pinned wheel grants no member access outside its dependency namespace."""
    if not _supported_or_refused():
        return
    artifact = load_native_wheel(_LOCK, native_wheel)
    with pytest.raises(ValueError, match="outside the declared dependency"):
        verify_wheel_native_file(Path("/not-opened"), member, b"", artifact)


@requires_packaged_python312
def test_wheel_native_refuses_an_actual_different_member(native_wheel: Path) -> None:
    """Actual installed native bytes cannot be relabelled as another real wheel member."""
    if not _supported_or_refused():
        return
    artifact = load_native_wheel(_LOCK, native_wheel)
    native = next(
        item
        for item in observe_native_import_closure(_SOURCE, _DEPENDENCY)
        if item.manager == "wheel"
    )
    with pytest.raises(ValueError, match="differs from its wheel RECORD"):
        verify_wheel_native_file(
            native.installed_source,
            "websockets/__init__.py",
            native.installed_source.read_bytes(),
            artifact,
        )


@pytest.mark.parametrize("change", ["hash", "size", "duplicate"])
@requires_packaged_python312
def test_wheel_native_checks_the_actual_record_even_when_member_bytes_match(
    native_wheel: Path, tmp_path: Path, change: str
) -> None:
    """A separately byte-pinned real ZIP still fails with inconsistent native RECORD data."""
    if not _supported_or_refused():
        return
    native = next(
        item
        for item in observe_native_import_closure(_SOURCE, _DEPENDENCY)
        if item.manager == "wheel"
    )
    member = native.payload.removeprefix("runtime/python/lib/python3.12/dist-packages/")
    record_name = "websockets-17.4.dist-info/RECORD"
    with zipfile.ZipFile(native_wheel) as original:
        contents = {name: original.read(name) for name in original.namelist()}
    rows = list(csv.reader(io.StringIO(contents[record_name].decode())))
    row = next(row for row in rows if row[0] == member)
    if change == "hash":
        row[1] = "sha256=" + "A" * 43
    elif change == "size":
        row[2] = str(len(native.installed_source.read_bytes()) + 1)
    else:
        rows.append(row.copy())
    text = io.StringIO()
    csv.writer(text).writerows(rows)
    contents[record_name] = text.getvalue().encode()
    changed = tmp_path / native_wheel.name
    with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    pin = json.loads(_LOCK.read_bytes())
    raw = changed.read_bytes()
    pin["websockets"].update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    lock = tmp_path / "changed-input-lock.json"
    lock.write_text(json.dumps(pin))
    artifact = load_native_wheel(lock, changed)
    assert contents[member] == native.installed_source.read_bytes()
    with pytest.raises(ValueError, match="differs from its wheel RECORD"):
        verify_wheel_native_file(native.installed_source, member, contents[member], artifact)


@pytest.fixture(scope="module")
def instrumentation_library(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Materialize the entire original collector distribution as ordinary input."""
    import os

    from tools.protected_runtime_probe.instrumentation_artifacts import (
        capture_instrumentation_wheel,
    )

    directory = tmp_path_factory.mktemp("collector-native-input")
    if not _supported_or_refused():
        return directory / "coverage", directory / "unsupported-profile.whl"
    supplied = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL")
    if supplied:
        artifact = Path(supplied)
    else:
        pin = json.loads(_LOCK.read_bytes())["coverage"]
        artifact = directory / pin["filename"]
        with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
            artifact.write_bytes(response.read(8 * 1024 * 1024 + 1))
    wheel = capture_instrumentation_wheel(_LOCK, artifact)
    library = directory / "library"
    library.mkdir()
    for name, raw in wheel.members.items():
        path = library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return library / "coverage", artifact


@requires_packaged_python312
def test_actual_instrumented_native_closure_binds_original_tracer_and_database_dependencies(
    native_wheel: Path, instrumentation_library: tuple[Path, Path]
) -> None:
    """Actual C tracing and memory serialization map only verified native objects."""
    from tools.protected_runtime_probe.instrumentation_artifacts import (
        capture_instrumentation_wheel,
        instrumentation_runtime_content,
    )

    if not _supported_or_refused():
        return
    package, artifact = instrumentation_library
    observed = observe_native_import_closure(
        _SOURCE,
        _DEPENDENCY,
        instrumentation_root=package,
        instrumentation_wheel=artifact,
    )
    content = {item.payload: item.installed_source.read_bytes() for item in observed}
    collector = capture_instrumentation_wheel(_LOCK, artifact)
    content.update(instrumentation_runtime_content(collector))
    result = verify_native_runtime_content(
        content,
        _SOURCE,
        _DEPENDENCY,
        native_wheel,
        instrumentation_root=package,
        instrumentation_wheel=artifact,
    )
    assert result["instrumentation"] == "coverage7.17.0-ctrace-memory-snapshot"
    entry_modules = result["entry_modules"]
    assert isinstance(entry_modules, list)
    assert "tools.protected_runtime_probe.instrumentation" in entry_modules
    assert "tools.protected_runtime_probe.instrumentation_recording" in entry_modules
    records = result["native_objects"]
    assert isinstance(records, list)
    collector_records = [row for row in records if row["manager"] == "instrumentation-wheel"]
    assert len(collector_records) == 1
    assert collector_records[0]["distribution"] == "coverage"
    assert collector_records[0]["wheel_record_verified"] is True
    assert collector_records[0]["wheel_sha256"] == collector.artifact.sha256
    assert any("_sqlite3." in name for name in content)
    assert any("libsqlite3.so" in name for name in content)
    resource_objects = [
        item
        for item in observed
        if item.payload.startswith("runtime/python/lib/python3.12/lib-dynload/resource.")
    ]
    assert len(resource_objects) == 1
    resource_object = resource_objects[0]
    resource_source = resource_object.installed_source.resolve(strict=True)
    assert resource_source.parent == Path("/usr/lib/python3.12/lib-dynload")
    assert content[resource_object.payload] == resource_source.read_bytes()
    resource_records = [row for row in records if row["payload"] == resource_object.payload]
    assert len(resource_records) == 1
    assert resource_records[0]["manager"] == "dpkg"
    assert resource_records[0]["dpkg_md5_verified"] is True
    production = observe_native_import_closure(_SOURCE, _DEPENDENCY)
    assert all(item.manager != "instrumentation-wheel" for item in production)
    assert not any("/coverage/" in item.payload for item in production)


@pytest.mark.parametrize("missing", ["tree", "artifact"])
def test_instrumented_observation_refuses_incomplete_input_tuple(
    instrumentation_library: tuple[Path, Path], missing: str
) -> None:
    """A half-selected observer cannot import an ambient collector."""
    if not _supported_or_refused():
        return
    package, artifact = instrumentation_library
    with pytest.raises(ValueError, match="both complete tree and original wheel"):
        observe_native_import_closure(
            _SOURCE,
            _DEPENDENCY,
            instrumentation_root=None if missing == "tree" else package,
            instrumentation_wheel=None if missing == "artifact" else artifact,
        )


def test_changed_collector_tree_refuses_before_observed_import(
    tmp_path: Path, instrumentation_library: tuple[Path, Path]
) -> None:
    """The actual observer rejects changed preparation input before collector execution."""
    if not _supported_or_refused():
        return
    package, artifact = instrumentation_library
    library = tmp_path / "changed-library"
    shutil.copytree(package.parent, library)
    (library / "coverage/control.py").write_bytes(b"changed ordinary preparation input")
    with pytest.raises(ValueError, match="source tree differs"):
        observe_native_import_closure(
            _SOURCE,
            _DEPENDENCY,
            instrumentation_root=library / "coverage",
            instrumentation_wheel=artifact,
        )


def test_instrumented_observation_refuses_distribution_metadata_as_package(
    instrumentation_library: tuple[Path, Path],
) -> None:
    """A valid complete tree cannot relabel its metadata as the executable package."""
    if not _supported_or_refused():
        return
    package, artifact = instrumentation_library
    with pytest.raises(ValueError, match="fixed coverage package"):
        observe_native_import_closure(
            _SOURCE,
            _DEPENDENCY,
            instrumentation_root=package.parent / "coverage-7.17.0.dist-info",
            instrumentation_wheel=artifact,
        )


@pytest.mark.parametrize("change", ["licence-missing", "resource-changed"])
@requires_packaged_python312
def test_native_verifier_refuses_incomplete_or_changed_collector_distribution(
    native_wheel: Path, instrumentation_library: tuple[Path, Path], change: str
) -> None:
    """Matching native ELF objects cannot substitute for complete distribution admission."""
    from tools.protected_runtime_probe.instrumentation_artifacts import (
        capture_instrumentation_wheel,
        instrumentation_runtime_content,
    )

    if not _supported_or_refused():
        return
    package, artifact = instrumentation_library
    observed = observe_native_import_closure(
        _SOURCE,
        _DEPENDENCY,
        instrumentation_root=package,
        instrumentation_wheel=artifact,
    )
    content = {item.payload: item.installed_source.read_bytes() for item in observed}
    content.update(instrumentation_runtime_content(capture_instrumentation_wheel(_LOCK, artifact)))
    if change == "licence-missing":
        del content[
            "runtime/python/lib/python3.12/dist-packages/coverage-7.17.0.dist-info/licenses/LICENSE.txt"
        ]
    else:
        content["runtime/python/lib/python3.12/dist-packages/coverage/control.py"] = (
            b"changed resource"
        )
    with pytest.raises(ValueError, match="complete original wheel"):
        verify_native_runtime_content(
            content,
            _SOURCE,
            _DEPENDENCY,
            native_wheel,
            instrumentation_root=package,
            instrumentation_wheel=artifact,
        )
