# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — explicit public instrumentation packet preparation
"""Exercise the preparer CLI over actual original collector and installed runtime bytes."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import websockets
from tools.protected_runtime_probe.capsule import capture_capsule
from tools.protected_runtime_probe.instrumentation import CONFIGURATION, instrumentation_selected
from tools.protected_runtime_probe.instrumentation_artifacts import capture_instrumentation_wheel
from tools.protected_runtime_probe.prepare import prepare_packet

from fixtures.protected_runtime_image import materialize_runtime_image

_SOURCE = Path(__file__).resolve().parents[1]
_SUPPORTED = (sys.platform, platform.machine(), sys.version_info[:2]) == (
    "linux",
    "x86_64",
    (3, 12),
)


def _original_artifact(directory: Path, name: str) -> Path:
    """Retain a complete original archive, with its production source-lock digest."""
    pin = json.loads(
        (_SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json").read_bytes()
    )[name]
    supplied = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL") if name == "coverage" else None
    artifact = Path(supplied) if supplied else directory / pin["filename"]
    if not supplied:
        with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
            artifact.write_bytes(response.read(8 * 1024 * 1024 + 1))
    raw = artifact.read_bytes()
    assert len(raw) == pin["size_bytes"]
    assert hashlib.sha256(raw).hexdigest() == pin["sha256"]
    return artifact


@pytest.mark.parametrize("supplied", ["root", "wheel", "both-without-runtime"])
def test_public_preparer_refuses_partial_collector_selection_before_packet_creation(
    tmp_path: Path, supplied: str
) -> None:
    """Collector inputs cannot select an incomplete runtime or implicit host-tools profile."""
    expected = "requires both|requires image" if _SUPPORTED else "native Linux"
    with pytest.raises(ValueError if _SUPPORTED else OSError, match=expected):
        prepare_packet(
            _SOURCE,
            tmp_path / "never-created",
            tmp_path / "runs",
            instrumentation_root=None if supplied == "wheel" else tmp_path / "coverage",
            instrumentation_wheel=None if supplied == "root" else tmp_path / "original.whl",
        )
    assert not (tmp_path / "never-created").exists()


def _materialize_inputs(tmp_path: Path) -> tuple[Path, Path, str, Path, Path, Path]:
    """Capture real system runtime and original wheel members as ordinary input data."""
    archive = _original_artifact(tmp_path, "coverage")
    collector = capture_instrumentation_wheel(
        _SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json", archive
    )
    library = tmp_path / "library"
    for name, raw in collector.members.items():
        path = library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    dependency = _original_artifact(tmp_path, "websockets")
    head = subprocess.run(
        ["/usr/bin/git", "-C", str(_SOURCE), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout.strip()
    image, manifest, digest = materialize_runtime_image(
        _SOURCE,
        Path(websockets.__file__).parent,
        tmp_path,
        head,
        instrumentation_root=library / "coverage",
        instrumentation_wheel=archive,
    )
    return image, manifest, digest, dependency, library / "coverage", archive


def test_actual_preparer_cli_binds_complete_collector_and_literal_selector(tmp_path: Path) -> None:
    """Real ordinary preparation observes native provenance and freezes the explicit profile."""
    if not _SUPPORTED:
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(_SOURCE, tmp_path / "unsupported", tmp_path / "runs")
        return
    if not tmp_path.is_relative_to("/media/anulum/GOTM"):
        with pytest.raises(ValueError, match="Samsung"):
            prepare_packet(_SOURCE, tmp_path / "unsupported", tmp_path / "runs")
        return
    image, manifest, digest, dependency, package, archive = _materialize_inputs(tmp_path)
    arguments = [
        sys.executable,
        "-B",
        "-m",
        "tools.protected_runtime_probe.prepare",
        "--source",
        str(_SOURCE),
        "--bundle",
        str(tmp_path / "packet"),
        "--backing-parent",
        str(tmp_path / "future-runs"),
        "--runtime-image",
        str(image),
        "--runtime-manifest",
        str(manifest),
        "--runtime-sha256",
        digest,
        "--dependency-wheel",
        str(dependency),
        "--instrumentation-root",
        str(package),
        "--instrumentation-wheel",
        str(archive),
    ]
    result = subprocess.run(
        arguments, cwd=_SOURCE, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    review = json.loads(result.stdout)
    capsule = capture_capsule(tmp_path / "packet", review["manifest_sha256"])
    assert instrumentation_selected(capsule, requested=True)
    with pytest.raises(ValueError, match="production runtime refuses instrumentation"):
        instrumentation_selected(capsule, requested=False)
    command = shlex.split(review["operator_command"])
    assert command[-1] == "--instrumentation" and command.count("--instrumentation") == 1
    assert command[-2] == capsule.manifest_sha256
    provenance = json.loads(capsule.content["runtime/config/native-provenance.json"])
    assert provenance["instrumentation"] == "coverage7.17.0-ctrace-memory-snapshot"
    configuration = json.loads(capsule.content[CONFIGURATION])
    assert "collector-admission-and-startup" in configuration["unmeasured"]
    for row in configuration["collector"]:
        assert hashlib.sha256(capsule.content[row["path"]]).hexdigest() == row["sha256"]
    assert capsule.content["probe/protected_runtime_probe/native-dependencies.lock.json.license"]
    assert not (tmp_path / "future-runs").exists()
    assert not any(path.name == "coverage.sqlite" for path in tmp_path.rglob("*"))
    target = package / "control.py"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="distribution|differs|inventory"):
        prepare_packet(
            _SOURCE,
            tmp_path / "refused",
            tmp_path / "future-runs",
            runtime_image=image,
            runtime_manifest=manifest,
            runtime_sha256=digest,
            dependency_wheel=dependency,
            instrumentation_root=package,
            instrumentation_wheel=archive,
        )
    assert not (tmp_path / "refused").exists()
