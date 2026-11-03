# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real ordinary sealed recording analysis
"""Exercise public API/CLI with actual process datasets and byte/source disagreements."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import websockets
from tools.protected_runtime_probe.capsule import capture_capsule
from tools.protected_runtime_probe.instrumentation_analysis import analyze_recordings
from tools.protected_runtime_probe.instrumentation_analysis_inputs import (
    canonical_analysis_metadata,
    require_ordinary_analysis,
)
from tools.protected_runtime_probe.roles import ROOT_COMMAND_ENV

from fixtures.protected_runtime_analysis import (
    materialize_analysis_inputs,
    materialize_unsupported_collector_inputs,
    run_unsupported_collector_pair,
)
from fixtures.protected_runtime_image import samsung_backing_or_refusal
from fixtures.protected_runtime_measurement import (
    measure_paired_analysis_worker,
    run_memory_measured_analysis_cli,
    run_worker_with_capture_change,
)

_SOURCE = Path(__file__).resolve().parents[1]
Fixture = tuple[Path, str, Path, Path, str]


@pytest.fixture(scope="module")
def analysis_inputs(tmp_path_factory: pytest.TempPathFactory) -> Fixture | None:
    """Produce six real finite recordings, with no installed-host qualification claim."""
    directory = tmp_path_factory.mktemp("ordinary-analysis-inputs")
    if not samsung_backing_or_refusal(directory):
        return None
    if (sys.platform, platform.machine(), sys.version_info[:2]) != ("linux", "x86_64", (3, 12)):
        from tools.protected_runtime_probe.native_closure import require_native_runtime_abi

        with pytest.raises(OSError, match="native Linux"):
            require_native_runtime_abi(
                sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1
            )
        return None
    supplied = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL")
    pin = json.loads(
        (_SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json").read_bytes()
    )["coverage"]
    artifact = Path(supplied) if supplied else directory / pin["filename"]
    if not supplied:
        with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
            artifact.write_bytes(response.read(8 * 1024 * 1024 + 1))
    return materialize_analysis_inputs(
        _SOURCE, Path(websockets.__file__).parent, directory, artifact
    )


def _input_copies(directory: Path, fixture: Fixture) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Retain real readonly byte copies for a single deliberate input disagreement."""
    datasets = directory / "datasets"
    datasets.mkdir(mode=0o700)
    for original in fixture[2].iterdir():
        path = datasets / original.name
        path.write_bytes(original.read_bytes())
        path.chmod(0o444)
    return (
        datasets,
        json.loads(fixture[3].read_bytes()),
        json.loads((datasets / "manifest.json").read_bytes()),
    )


def _write_parent(
    directory: Path, report: dict[str, Any], manifest: dict[str, Any]
) -> tuple[Path, str]:
    """Bind deliberately changed diagnostic inputs with ordinary parent metadata hashes."""
    raw = canonical_analysis_metadata(manifest)
    path = directory / "datasets/manifest.json"
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o444)
    report["instrumentation"]["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    report["instrumentation"]["datasets"] = manifest["datasets"]
    report["instrumentation"]["source_entries_sha256"] = manifest["source_entries_sha256"]
    encoded = canonical_analysis_metadata(report)
    target = directory / "report.json"
    target.write_bytes(encoded)
    return target, hashlib.sha256(encoded).hexdigest()


def test_actual_ordinary_api_combines_six_contexts_and_preserves_every_sealed_input(
    analysis_inputs: Fixture | None, tmp_path: Path
) -> None:
    """Public analysis combines real source arcs while retaining their exact associations."""
    if analysis_inputs is None:
        return
    view, digest, datasets, report, report_sha = analysis_inputs
    require_ordinary_analysis()
    before = {path.name: path.read_bytes() for path in datasets.iterdir()}
    result = analyze_recordings(report, report_sha, view, datasets, _SOURCE, tmp_path / "analysis")
    assert result == json.loads((tmp_path / "analysis/analysis.json").read_bytes())
    assert result["capsule_manifest_sha256"] == digest
    assert result["report_sha256"] == report_sha
    assert result["producer_component_checks_passed"] is False
    assert result["analysis_purpose"] == "diagnostic recording analysis; no runtime acceptance"
    assert result["contexts"] == [
        "authority",
        "client",
        "controller",
        "namespace-wrapper",
        "operator",
        "writer",
    ]
    arc_count = result["arc_count"]
    assert isinstance(arc_count, int) and arc_count > 0
    expected = str(_SOURCE / "tools/protected_runtime_probe/capsule.py")
    measured_files = result["executed_files"]
    assert isinstance(measured_files, list) and expected in measured_files
    assert str(_SOURCE / "src/synapse_channel/core/protected_write_mounts.py") in measured_files
    assert result["source_files_without_arcs"]
    registered = result["registered_files"]
    assert isinstance(registered, list) and len(registered) > len(measured_files)
    assert (
        result["whole_runtime_accepted"] is False and result["class_consumption_accepted"] is False
    )
    assert {path.name: path.read_bytes() for path in datasets.iterdir()} == before
    raw = (tmp_path / "analysis/combined.sqlite").read_bytes()
    combined = result["combined"]
    assert isinstance(combined, dict)
    assert hashlib.sha256(raw).hexdigest() == combined["sha256"]
    with pytest.raises(FileExistsError):
        analyze_recordings(report, report_sha, view, datasets, _SOURCE, tmp_path / "analysis")


@pytest.mark.parametrize("case", ["group-writable", "other-writable", "symlink", "file"])
def test_analysis_refuses_actual_unsafe_output_parent(
    analysis_inputs: Fixture | None, tmp_path: Path, case: str
) -> None:
    """Reject unsafe output custody after verifying genuine sealed recording inputs."""
    if analysis_inputs is None:
        return
    view, _digest, datasets, report, report_sha = analysis_inputs
    before = {path.name: path.read_bytes() for path in datasets.iterdir()}
    parent = tmp_path / "parent"
    if case == "file":
        parent.write_bytes(b"not a directory")
        expected: type[OSError] = NotADirectoryError
    else:
        parent.mkdir(mode=0o700)
        expected = PermissionError if case.endswith("writable") else OSError
    output = parent / "analysis"
    if case.endswith("writable"):
        parent.chmod(0o770 if case == "group-writable" else 0o702)
    elif case == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        output = alias / "analysis"
    try:
        with pytest.raises(expected) as failure:
            analyze_recordings(report, report_sha, view, datasets, _SOURCE, output)
        if case.endswith("writable"):
            assert (
                str(failure.value) == "analysis output parent is not owned stable Samsung storage"
            )
        if case != "file":
            assert not list(parent.iterdir())
        else:
            assert parent.read_bytes() == b"not a directory"
        assert {path.name: path.read_bytes() for path in datasets.iterdir()} == before
    finally:
        if case != "file":
            parent.chmod(0o700)


def test_actual_ordinary_cli_reaches_the_isolated_public_data_combiner(
    analysis_inputs: Fixture | None, tmp_path: Path
) -> None:
    """The real CLI returns the exact final private analysis artifact without root execution."""
    if analysis_inputs is None:
        return
    view, _digest, datasets, report, report_sha = analysis_inputs
    arguments = [
        sys.executable,
        "-B",
        "-m",
        "tools.protected_runtime_probe.instrumentation_analysis",
        "--report",
        str(report),
        "--report-sha256",
        report_sha,
        "--capsule",
        str(view),
        "--datasets",
        str(datasets),
        "--source",
        str(_SOURCE),
        "--output",
        str(tmp_path / "cli"),
    ]
    result = subprocess.run(
        arguments, cwd=_SOURCE, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value == json.loads((tmp_path / "cli/analysis.json").read_bytes())
    assert value["source_entries_sha256"]
    assert set(value["input_dataset_sha256"]) == {
        "controller",
        "namespace-wrapper",
        "authority",
        "client",
        "operator",
        "writer",
    }


def _metadata_change(case: str, report: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Introduce a specific contradiction in actual parent fixture metadata."""
    if case == "incomplete":
        report["instrumentation"]["collection_complete"] = False
    elif case == "component-grade-type":
        report["component_checks_passed"] = "false"
    elif case == "producer-exit":
        report["children"][0]["returncode"] = -9
    elif case == "principal":
        manifest["datasets"][2]["producer_uid"] = 1
    elif case == "duplicate-pid":
        manifest["datasets"][2]["host_pid"] = manifest["datasets"][0]["host_pid"]
    elif case == "boolean":
        manifest["datasets"][0]["host_pid"] = True
    elif case == "source-pin":
        manifest["source_entries_sha256"] = "f" * 64
    elif case == "source-view":
        manifest["code_view"] += "/different"
    elif case == "manifest-shape":
        manifest["unexpected"] = True
    elif case == "manifest-run":
        manifest["run_id"] = "run-fedcba9876543210"
    elif case == "relative-view":
        manifest["code_view"] = "relative/view"
    elif case == "configuration-binding":
        manifest["configuration_sha256"] = "0" * 64
    elif case == "dataset-count":
        manifest["datasets"].pop()
    elif case == "binding-shape":
        manifest["datasets"][0]["unexpected"] = True
    elif case == "binding-role":
        manifest["datasets"][0]["role"] = "writer"
    elif case == "role-bindings":
        del report["roles"]["synapse-writer"]
    elif case == "sealed-owner":
        manifest["datasets"][0]["sealed_uid"] = 1
    elif case == "zero-pid":
        manifest["datasets"][0]["host_pid"] = 0


def _dataset_change(case: str, datasets: Path, manifest: dict[str, Any], fixture: Fixture) -> None:
    """Change real file topology or actual recorded bytes without manufacturing source hits."""
    path = datasets / "controller.sqlite"
    if case == "extra":
        (datasets / "coverage.sqlite-journal").write_bytes(b"unexpected sidecar")
    elif case == "missing":
        path.unlink()
    elif case == "data-mode":
        path.chmod(0o600)
    elif case == "symlink":
        path.unlink()
        path.symlink_to(fixture[2] / "controller.sqlite")
    elif case in {"data-bytes", "database", "context"}:
        raw = (
            (fixture[2] / "client.sqlite").read_bytes() if case == "context" else b"not a database"
        )
        path.chmod(0o600)
        path.write_bytes(raw)
        path.chmod(0o444)
        if case != "data-bytes":
            manifest["datasets"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
            manifest["datasets"][0]["size_bytes"] = len(raw)


def _changed_source(directory: Path, fixture: Fixture, case: str) -> Path:
    """Make ordinary Python-only fixture copies, then change actual bytes or topology."""
    root = directory / "changed-source"
    capsule = capture_capsule(fixture[0], fixture[1])
    for name, raw in capsule.content.items():
        if name.endswith(".py") and name.startswith(
            ("app/synapse_channel/", "probe/protected_runtime_probe/")
        ):
            prefix = "src/" if name.startswith("app/") else "tools/"
            path = root / (prefix + name.split("/", 1)[1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    target = root / "src/synapse_channel/__init__.py"
    if case == "source-change":
        target.write_bytes(target.read_bytes() + b"\n")
    elif case == "source-same-size-change":
        original = target.read_bytes()
        changed = bytes([original[0] ^ 1]) + original[1:]
        assert len(changed) == len(original)
        assert hashlib.sha256(changed).digest() != hashlib.sha256(original).digest()
        target.write_bytes(changed)
    elif case == "source-extra":
        (target.parent / "unexpected.py").write_bytes(b"extra = True\n")
    elif case == "source-link":
        target.unlink()
        target.symlink_to(fixture[0] / "app/synapse_channel/__init__.py")
    return root


@pytest.mark.parametrize(
    "case",
    [
        "report-pin",
        "incomplete",
        "producer-exit",
        "component-grade-type",
        "manifest-pin",
        "duplicate-report-key",
        "extra",
        "missing",
        "data-bytes",
        "data-mode",
        "symlink",
        "principal",
        "duplicate-pid",
        "boolean",
        "source-pin",
        "source-view",
        "database",
        "context",
        "source-change",
        "source-same-size-change",
        "source-extra",
        "source-link",
        "report-constant",
        "report-array",
        "digest-shape",
        "relative-report",
        "manifest-shape",
        "manifest-run",
        "relative-view",
        "configuration-binding",
        "dataset-count",
        "binding-shape",
        "binding-role",
        "role-bindings",
        "sealed-owner",
        "zero-pid",
        "relative-output",
    ],
)
def test_actual_ordinary_analysis_refuses_changed_associations_bytes_contexts_or_sources(
    analysis_inputs: Fixture | None, tmp_path: Path, case: str
) -> None:
    """Real API refusals preserve input copies and never publish successful analysis metadata."""
    if analysis_inputs is None:
        return
    datasets, report, manifest = _input_copies(tmp_path, analysis_inputs)
    _metadata_change(case, report, manifest)
    _dataset_change(case, datasets, manifest, analysis_inputs)
    report_path, report_sha = _write_parent(tmp_path, report, manifest)
    if case == "report-pin":
        report_sha = "0" * 64
    elif case == "manifest-pin":
        report["instrumentation"]["manifest_sha256"] = "0" * 64
        report_path.write_bytes(canonical_analysis_metadata(report))
        report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    elif case == "duplicate-report-key":
        raw = report_path.read_bytes()[:-1] + b', "schema":"repeated"}'
        report_path.write_bytes(raw)
        report_sha = hashlib.sha256(raw).hexdigest()
    if case in {"report-constant", "report-array"}:
        report_path.write_bytes(b'{"bad": NaN}' if case == "report-constant" else b"[]")
        report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    elif case == "digest-shape":
        report_sha = "not-a-sha256"
    elif case == "relative-report":
        report_path = Path("relative-report.json")
    source = (
        _changed_source(tmp_path, analysis_inputs, case)
        if case in {"source-change", "source-same-size-change", "source-extra", "source-link"}
        else _SOURCE
    )
    output = Path("relative-output") if case == "relative-output" else tmp_path / "refused"
    with pytest.raises((ValueError, OSError)) as failure:
        analyze_recordings(report_path, report_sha, analysis_inputs[0], datasets, source, output)
    expected = {
        "role-bindings": "recording report lacks parent role bindings",
        "sealed-owner": "recording dataset process or sealed metadata differs",
        "zero-pid": "recording dataset process or sealed metadata differs",
        "source-same-size-change": "current analysis source bytes differ from the capsule",
    }
    if case in expected:
        assert str(failure.value) == expected[case]
    assert not (tmp_path / "refused/analysis.json").exists()
    assert (tmp_path / "refused").exists() is (case in {"source-view", "database", "context"})


@pytest.fixture(scope="module")
def combined_analysis(
    analysis_inputs: Fixture | None, tmp_path_factory: pytest.TempPathFactory
) -> Path | None:
    """Materialize real public-API working copies once for standalone worker entry tests."""
    if analysis_inputs is None:
        return None
    directory = tmp_path_factory.mktemp("analysis-worker-baseline") / "analysis"
    analyze_recordings(
        analysis_inputs[3],
        analysis_inputs[4],
        analysis_inputs[0],
        analysis_inputs[2],
        _SOURCE,
        directory,
    )
    return directory


def _worker_collector_metadata(configuration: dict[str, Any], case: str) -> None:
    """Make explicit untrusted inventory disagreements without executing changed code."""
    if case == "collector-row-shape":
        configuration["collector"][0]["unexpected"] = True
    elif case == "collector-row-path":
        configuration["collector"][0]["path"] = "../escape"
    elif case == "collector-row-count":
        configuration["collector"] = []
    elif case == "collector-row-duplicate":
        configuration["collector"].append(dict(configuration["collector"][0]))


def _worker_recording_metadata(directory: Path, configuration: dict[str, Any], case: str) -> None:
    """Use genuine records or zero-execution metadata for actual refusal cases."""
    if case not in {"unbound-source", "dataset-context", "dataset-no-arcs", "dataset-tracer"}:
        return
    from coverage import CoverageData

    path = directory / "inputs/controller.sqlite"
    data = CoverageData(basename=str(path))
    data.read()
    filename = sorted(data.measured_files())[0]
    if case == "dataset-tracer":
        filename = next(name for name in sorted(data.measured_files()) if data.arcs(name))
        original_arcs = data.arcs(filename)
        data.add_file_tracers({filename: "unadmitted-child-tracer"})
        assert data.arcs(filename) == original_arcs
        data.write()
    data.close(force=True)
    if case == "unbound-source":
        prefix = configuration["code_view"] + "/"
        assert filename.startswith(prefix)
        del configuration["source_paths"][filename[len(prefix) :]]
        return
    if case == "dataset-context":
        path.write_bytes((directory / "inputs/client.sqlite").read_bytes())
    elif case == "dataset-no-arcs":
        path.unlink()
        empty = CoverageData(basename=str(path))
        empty.set_context("controller")
        empty.add_arcs({filename: []})
        empty.touch_files([filename])
        assert empty.has_arcs() and not empty.arcs(filename)
        empty.write()
        empty.close(force=True)
        path.chmod(0o600)
    raw = path.read_bytes()
    configuration["datasets"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    configuration["datasets"][0]["size_bytes"] = len(raw)


def _worker_inputs(directory: Path, baseline: Path, case: str) -> tuple[Path, str]:
    """Copy genuine successful inputs, then introduce one actual private-file disagreement."""
    for name in ("collector", "inputs"):
        shutil.copytree(baseline / name, directory / name)
    configuration = json.loads((baseline / "analysis-inputs.json").read_bytes())
    if case == "collector-bytes":
        path = directory / "collector/coverage/control.py"
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b"\n")
    elif case == "collector-extra":
        (directory / "collector/unexpected").write_bytes(b"unlisted")
    elif case == "collector-link":
        (directory / "collector/unexpected").symlink_to(directory / "collector/coverage/control.py")
    elif case == "collector-hardlink":
        os.link(directory / "collector/coverage/control.py", directory / "alias")
    elif case == "dataset-bytes":
        (directory / "inputs/controller.sqlite").write_bytes(b"changed after parent capture")
    elif case == "roles":
        configuration["datasets"][0]["role"] = "client"
    elif case == "existing-combined":
        (directory / "combined.sqlite").write_bytes((baseline / "combined.sqlite").read_bytes())
    elif case == "configuration-shape":
        configuration["unexpected"] = True
    _worker_collector_metadata(configuration, case)
    _worker_recording_metadata(directory, configuration, case)
    raw = canonical_analysis_metadata(configuration)
    target = directory / "analysis-inputs.json"
    target.write_bytes(raw)
    target.chmod(0o644 if case == "private-mode" else 0o600)
    return target, "0" * 64 if case == "configuration-pin" else hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "case",
    [
        "success",
        "nonisolated",
        "site-enabled",
        "bytecode-enabled",
        "configuration-pin",
        "configuration-shape",
        "private-mode",
        "collector-bytes",
        "collector-extra",
        "collector-link",
        "collector-hardlink",
        "dataset-bytes",
        "roles",
        "existing-combined",
        "file-limit",
        "collector-row-shape",
        "collector-row-path",
        "collector-row-count",
        "collector-row-duplicate",
        "unbound-source",
        "dataset-context",
        "dataset-no-arcs",
        "dataset-tracer",
        "capture-change",
    ],
)
def test_actual_standalone_worker_enforces_isolation_private_inputs_and_finite_writes(
    combined_analysis: Path | None, tmp_path: Path, case: str
) -> None:
    """The real ordinary worker accepts exact bytes and refuses actual OS/data disagreements."""
    if combined_analysis is None:
        return
    configuration, digest = _worker_inputs(tmp_path, combined_analysis, case)
    flags = ["-I", "-B", "-S"]
    removed = {"nonisolated": "-I", "site-enabled": "-S", "bytecode-enabled": "-B"}
    if case in removed:
        flags.remove(removed[case])

    def finite_file_limit() -> None:
        """Lower only this owned ordinary child to a real one-page file budget."""
        import resource

        resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))

    worker = _SOURCE / "tools/protected_runtime_probe/instrumentation_analysis_worker.py"
    arguments = [
        "/usr/bin/python3.12",
        *flags,
        str(worker),
        "--configuration",
        str(configuration),
        "--sha256",
        digest,
    ]
    result = (
        run_worker_with_capture_change(arguments)
        if case == "capture-change"
        else subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=90,
            env=ROOT_COMMAND_ENV,
            preexec_fn=finite_file_limit if case == "file-limit" else None,
            check=False,
        )
    )
    if case == "success":
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value["arc_count"] > 0 and len(value["contexts"]) == 6
        assert (tmp_path / "combined.sqlite").stat().st_mode & 0o777 == 0o600
    else:
        assert result.returncode != 0 and not result.stdout
        if case in removed:
            assert "isolated no-site no-bytecode" in result.stderr
        elif case == "file-limit":
            assert (tmp_path / "combined.sqlite").stat().st_size <= 4096
        elif case == "configuration-pin":
            assert "configuration digest differs" in result.stderr
        expected = {
            "collector-row-shape": "analysis collector pin shape differs",
            "collector-row-path": "analysis collector member path differs",
            "collector-row-count": "analysis collector inventory differs",
            "collector-row-duplicate": "analysis collector members must be unique",
            "unbound-source": "unknown or unbound physical source alias",
            "dataset-context": "exact role context or actual branch data",
            "dataset-no-arcs": "contains no actual source arcs",
            "dataset-tracer": "unadmitted file tracer",
            "capture-change": "analysis working copy changed during capture",
        }
        if case in expected:
            assert expected[case] in result.stderr
    assert not (tmp_path / "analysis.json").exists()

    paired = tmp_path / "measured"
    paired.mkdir(mode=0o700)
    paired_configuration, paired_digest = _worker_inputs(paired, combined_analysis, case)
    measure_paired_analysis_worker(
        [*arguments[:-3], str(paired_configuration), "--sha256", paired_digest],
        paired / "paired-measurement.json",
        result,
        finite_file_limit if case == "file-limit" else None,
        capture_change=case == "capture-change",
    )


def test_actual_worker_refuses_mapped_root_before_opening_configuration(
    analysis_inputs: Fixture | None, tmp_path: Path
) -> None:
    """Real namespace UID0 refuses before input parsing in both command profiles."""
    if analysis_inputs is None:
        return
    worker = _SOURCE / "tools/protected_runtime_probe/instrumentation_analysis_worker.py"
    absent = tmp_path / "unopened.json"
    arguments = [
        "/usr/bin/bwrap",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-net",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--cap-drop",
        "ALL",
        "/usr/bin/python3.12",
        "-I",
        "-B",
        "-S",
        str(worker),
        "--configuration",
        str(absent),
        "--sha256",
        "0" * 64,
    ]
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=30, env=ROOT_COMMAND_ENV, check=False
    )
    assert result.returncode == 1 and not result.stdout
    assert (
        "PermissionError: recording analysis worker requires an ordinary nonroot user"
        in result.stderr
    )
    assert not absent.exists()
    pin = json.loads(
        (_SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json").read_bytes()
    )["coverage"]
    artifact = analysis_inputs[0] / "runtime/artifacts" / pin["filename"]
    measure_paired_analysis_worker(
        arguments, tmp_path / "paired-root-measurement.json", result, None, artifact
    )
    assert not absent.exists()


def test_actual_worker_refuses_genuine_unsupported_collector_version(
    combined_analysis: Path | None, tmp_path: Path
) -> None:
    """Both real command profiles reject an authentic incompatible release."""
    if combined_analysis is None:
        return
    configuration, digest, artifact, published_sha = materialize_unsupported_collector_inputs(
        combined_analysis, tmp_path
    )
    worker = _SOURCE / "tools/protected_runtime_probe/instrumentation_analysis_worker.py"
    arguments = [
        "/usr/bin/python3.12",
        "-I",
        "-B",
        "-S",
        str(worker),
        "--configuration",
        str(configuration),
        "--sha256",
        digest,
    ]
    run_unsupported_collector_pair(
        arguments, artifact, published_sha, tmp_path / "unsupported-version-pair.json"
    )
    assert not (tmp_path / "combined.sqlite").exists()
    assert not (tmp_path / "analysis.json").exists()


@pytest.mark.parametrize("measured", [False, True])
def test_actual_ordinary_cli_partial_write_fails_without_publishing_analysis(
    analysis_inputs: Fixture | None, tmp_path: Path, measured: bool
) -> None:
    """A real finite file limit refuses collector materialization and preserves failed output."""
    if analysis_inputs is None:
        return

    def finite_file_limit() -> None:
        """Lower only the owned ordinary CLI child to a real one-page write budget."""
        import resource

        resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))

    view, _digest, datasets, report, report_sha = analysis_inputs
    arguments = [
        sys.executable,
        "-B",
        "-m",
        "tools.protected_runtime_probe.instrumentation_analysis",
        "--report",
        str(report),
        "--report-sha256",
        report_sha,
        "--capsule",
        str(view),
        "--datasets",
        str(datasets),
        "--source",
        str(_SOURCE),
        "--output",
        str(tmp_path / "failed"),
    ]
    if measured:
        result = run_memory_measured_analysis_cli(
            arguments, _SOURCE, tmp_path / "memory-measurement.json", finite_file_limit
        )
    else:
        result = subprocess.run(
            arguments,
            cwd=_SOURCE,
            capture_output=True,
            text=True,
            timeout=90,
            env=ROOT_COMMAND_ENV,
            preexec_fn=finite_file_limit,
            check=False,
        )
    assert result.returncode != 0 and not result.stdout
    assert "write was incomplete" in result.stderr or "File too large" in result.stderr
    assert (tmp_path / "failed").is_dir()
    assert not (tmp_path / "failed/analysis.json").exists()


def test_standalone_worker_checks_the_observed_host_before_posix_adapters() -> None:
    """Actual isolated entry refuses unsupported hosts before importing native adapters."""
    worker = _SOURCE / "tools/protected_runtime_probe/instrumentation_analysis_worker.py"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-S", str(worker)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0 and not result.stdout
    if (sys.platform, platform.machine(), sys.version_info[:2]) != ("linux", "x86_64", (3, 12)):
        assert "native Linux x86_64 CPython3.12" in result.stderr
        assert "ModuleNotFoundError" not in result.stderr
    else:
        assert "--configuration" in result.stderr and "--sha256" in result.stderr


def test_actual_mapped_root_analysis_refuses_before_opening_input_paths(tmp_path: Path) -> None:
    """Real namespace-root credentials cannot open analysis inputs or create its output."""
    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            require_ordinary_analysis()
        return
    assert os.geteuid() != 0
    absent = tmp_path / "absent-input"
    code = """import json,os,sys
from pathlib import Path
source,site,coverage_dir,absent_text=sys.argv[1:]
sys.path[:0]=[source,source+'/src',site]
recorder=None
if coverage_dir:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(coverage_dir)/'.coverage'),data_suffix=True,branch=True,source=['tools.protected_runtime_probe.instrumentation_analysis_inputs'],config_file=False)
 recorder.start()
from tools.protected_runtime_probe.instrumentation_analysis import analyze_recordings
absent=Path(absent_text)
try:
 assert os.getresuid()==(0,0,0)and os.getresgid()==(0,0,0)
 assert not absent.exists()
 try:
  analyze_recordings(absent/'report.json','0'*64,absent/'capsule',absent/'datasets',Path(source),absent/'analysis')
 except PermissionError as error:
  assert str(error)=='recording analysis requires an ordinary nonroot user'
 else: raise AssertionError('namespace-root credentials admitted to ordinary analysis')
 assert not absent.exists()
 print(json.dumps({'ordinary_analysis_refused':True,'uid_map':Path('/proc/self/uid_map').read_text(),'native_qualified':False}))
finally:
 if recorder is not None: recorder.stop();recorder.save()
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
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(
        (
            "--proc",
            "/proc",
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            code,
            str(_SOURCE),
            str(Path(pytest.__file__).parents[1]),
            coverage_dir,
            str(absent),
        )
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["ordinary_analysis_refused"] is True
    assert observed["native_qualified"] is False
    assert [int(x) for x in observed["uid_map"].split()] == [0, os.geteuid(), 1]
    assert not absent.exists()
