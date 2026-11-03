# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — ordinary sealed-copy recording analysis
"""Combine byte-bound sealed recordings without root parsing or source substitution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from .instrumentation import CONFIGURATION, RECORDING_ROLES
from .instrumentation_analysis_inputs import (
    AnalysisInputs,
    canonical_analysis_metadata,
    capture_analysis_inputs,
    require_ordinary_analysis,
    verify_analysis_sources,
)
from .instrumentation_artifacts import InstrumentationWheel, decode_instrumentation_wheel
from .native_provenance import NativeWheelArtifact
from .roles import ROOT_COMMAND_ENV

_WORKER = """import runpy,sys
worker=sys.argv.pop(1)
runpy.run_path(worker,run_name='__main__')
"""


def _fresh_directory(output: Path) -> None:
    """Create one fresh private ordinary analysis directory on Samsung storage."""
    storage = Path("/media/anulum/GOTM")
    if not output.is_absolute() or not output.is_relative_to(storage) or ".." in output.parts:
        raise ValueError("recording analysis output must be a canonical Samsung path")
    parent = output.parent.stat()
    if (
        not storage.is_mount()
        or parent.st_dev != storage.stat().st_dev
        or (parent.st_uid, parent.st_gid) != (os.geteuid(), os.getegid())
        or parent.st_mode & 0o022
    ):
        raise PermissionError("analysis output parent is not owned stable Samsung storage")
    directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.mkdir(output.name, 0o700, dir_fd=directory)
    finally:
        os.close(directory)


def _write(path: Path, raw: bytes, mode: int = 0o600) -> None:
    """Write one exclusive fixed analysis entry and sync its exact private bytes."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb", buffering=0) as handle:
        os.fchmod(handle.fileno(), mode)
        if handle.write(raw) != len(raw):
            raise OSError("recording analysis write was incomplete")
        os.fsync(handle.fileno())


def _original_collector(inputs: AnalysisInputs) -> InstrumentationWheel:
    """Decode only the complete original archive already bound by capsule admission."""
    pin = json.loads(
        inputs.capsule.content["probe/protected_runtime_probe/native-dependencies.lock.json"]
    )["coverage"]
    raw = inputs.capsule.content["runtime/artifacts/" + pin["filename"]]
    return decode_instrumentation_wheel(NativeWheelArtifact(pin["filename"], pin["sha256"], raw))


def _prepare_worker(output: Path, inputs: AnalysisInputs) -> tuple[bytes, str]:
    """Materialize detached verified copies without importing the collector in this process."""
    wheel = _original_collector(inputs)
    collector = []
    for name, raw in sorted(wheel.members.items()):
        _write(output / "collector" / name, raw, 0o400)
        collector.append(
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        )
    datasets = []
    for role in RECORDING_ROLES:
        raw = inputs.datasets[role]
        _write(output / "inputs" / (role + ".sqlite"), raw)
        datasets.append(
            {"role": role, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        )
    configuration = canonical_analysis_metadata(
        {
            "schema": "synapse.recording-analysis-inputs.v1",
            "collector": collector,
            "datasets": datasets,
            "source_paths": dict(inputs.source_paths),
            "code_view": inputs.manifest["code_view"],
        }
    )
    if len(configuration) > 8 * 1024 * 1024:
        raise ValueError("recording analysis worker configuration is unbounded")
    return configuration, wheel.artifact.sha256


def _run_worker(output: Path, configuration: bytes, inputs: AnalysisInputs) -> dict[str, Any]:
    """Own one finite isolated ordinary process over private copies and fixed source code."""
    worker = Path(__file__).with_name("instrumentation_analysis_worker.py")
    if (
        worker.read_bytes()
        != inputs.capsule.content[
            "probe/protected_runtime_probe/instrumentation_analysis_worker.py"
        ]
    ):
        raise ValueError("ordinary analysis worker differs from the captured source")
    config_path = output / "analysis-inputs.json"
    _write(config_path, configuration)
    result = subprocess.run(
        [
            "/usr/bin/python3.12",
            "-I",
            "-B",
            "-S",
            "-c",
            _WORKER,
            str(worker),
            "--configuration",
            str(config_path),
            "--sha256",
            hashlib.sha256(configuration).hexdigest(),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=90,
        check=False,
        env=ROOT_COMMAND_ENV,
        start_new_session=True,
    )
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        detail = result.stderr[-8192:].decode("utf-8", errors="replace")
        raise ValueError("ordinary recording analysis worker failed: " + detail)
    value = json.loads(result.stdout)
    if type(value) is not dict or value.keys() != {
        "registered_files",
        "executed_files",
        "contexts",
        "arc_count",
    }:
        raise ValueError("ordinary recording analysis worker returned invalid metadata")
    if (
        value["contexts"] != sorted(RECORDING_ROLES)
        or type(value["arc_count"]) is not int
        or value["arc_count"] <= 0
    ):
        raise ValueError("ordinary recording analysis did not preserve all six contexts")
    if (
        type(value["registered_files"]) is not list
        or type(value["executed_files"]) is not list
        or not value["executed_files"]
        or not set(value["executed_files"]) <= set(value["registered_files"])
        or not set(value["registered_files"]) <= set(inputs.source_paths.values())
    ):
        raise ValueError("ordinary recording analysis returned unbound source files")
    return value


def _combined_pin(output: Path) -> dict[str, str | int]:
    """Hash the fresh bounded regular private combined dataset after its writer has exited."""
    descriptor = os.open(output / "combined.sqlite", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid())
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= 128 * 1024 * 1024
        ):
            raise ValueError("ordinary combined recording metadata differs")
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(descriptor, 65536):
            digest.update(chunk)
            total += len(chunk)
            if total > info.st_size:
                raise ValueError("ordinary combined recording grew during capture")
        after = os.fstat(descriptor)
        if total != info.st_size or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ):
            raise ValueError("ordinary combined recording changed during capture")
        return {"filename": "combined.sqlite", "sha256": digest.hexdigest(), "size_bytes": total}
    finally:
        os.close(descriptor)


def analyze_recordings(
    report: Path,
    report_sha256: str,
    capsule: Path,
    datasets: Path,
    source: Path,
    output: Path,
) -> dict[str, object]:
    """Verify and combine six sealed recordings as an ordinary user.

    Parameters
    ----------
    report, report_sha256:
        Parent report/copy and independently retained hash, with origin authenticated
        by the caller. Association alone grants no runtime acceptance.
    capsule:
        Complete exact capsule bound by that report.
    datasets:
        Six readonly sealed datasets and parent manifest, or byte-identical readonly copies.
    source:
        Current physical candidate files; changed sources refuse analysis.
    output:
        Fresh private Samsung destination for retained working copies and combined data.

    Returns
    -------
    dict[str, object]
        Result bound to report, capsule, dataset manifest, sources and original collector.
        Names all unmeasured source files without assigning manufactured hits.

    Raises
    ------
    PermissionError, ValueError, OSError, subprocess.TimeoutExpired
        On root context, changed inputs, nonfresh output or failed finite parsing.
        Partial output is retained as failed data; no analysis.json certifies success.

    Notes
    -----
    All six raw hashes and current source bytes are verified before parsing.
    Only the isolated ordinary worker reads child SQLite. Original sealed inputs
    remain untouched. No root command, operation grant or acceptance is produced.
    """
    require_ordinary_analysis()
    inputs = capture_analysis_inputs(report, report_sha256, capsule, datasets, source)
    _fresh_directory(output)
    configuration, collector_sha = _prepare_worker(output, inputs)
    observed = _run_worker(output, configuration, inputs)
    if verify_analysis_sources(inputs.capsule, source) != dict(inputs.source_paths):
        raise ValueError("current analysis source paths changed during combination")
    combined = _combined_pin(output)
    value: dict[str, object] = {
        "schema": "synapse.recording-analysis.v1",
        "run_id": inputs.manifest["run_id"],
        "report_sha256": inputs.report_sha256,
        "producer_component_checks_passed": inputs.producer_component_checks_passed,
        "analysis_purpose": "diagnostic recording analysis; no runtime acceptance",
        "capsule_manifest_sha256": inputs.capsule.manifest_sha256,
        "dataset_manifest_sha256": inputs.manifest_sha256,
        "source_entries_sha256": inputs.manifest["source_entries_sha256"],
        "collector_archive_sha256": collector_sha,
        "combined": combined,
        **observed,
        "input_dataset_sha256": {
            role: hashlib.sha256(raw).hexdigest() for role, raw in inputs.datasets.items()
        },
        "source_files_without_arcs": sorted(
            set(inputs.source_paths.values()) - set(observed["executed_files"])
        ),
        "unmeasured_boundaries": json.loads(inputs.capsule.content[CONFIGURATION])["unmeasured"],
        "whole_runtime_accepted": False,
        "class_consumption_accepted": False,
    }
    _write(output / "analysis.json", canonical_analysis_metadata(value))
    return value


def main(argv: list[str] | None = None) -> int:
    """Run ordinary sealed-copy analysis through the explicit command-line surface.

    Parameters
    ----------
    argv:
        Report/hash, exact capsule, sealed copies, current source and fresh output paths.

    Returns
    -------
    int
        Zero only after bound combined data and final analysis metadata are written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--report-sha256", required=True)
    parser.add_argument("--capsule", type=Path, required=True)
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    result = analyze_recordings(
        arguments.report,
        arguments.report_sha256,
        arguments.capsule,
        arguments.datasets,
        arguments.source,
        arguments.output,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
