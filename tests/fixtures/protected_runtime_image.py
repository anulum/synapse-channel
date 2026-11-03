# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual installed runtime image fixture
"""Materialize actual installed runtime bytes without executing captured binaries."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tools.protected_runtime_probe.capsule import CapsuleManifest, FilePin
from tools.protected_runtime_probe.native_closure import observe_native_import_closure

# Includes interpreter startup, source capture and bounded Git metadata observation.
_PUBLIC_REFUSAL_TIMEOUT_SECONDS = 20


def _tree_bytes(root: Path, prefix: str, *, only_python: bool) -> dict[str, bytes]:
    """Read actual package resources or pure stdlib, excluding bytecode and nested sites."""
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or "dist-packages" in relative.parts:
            continue
        if path.is_symlink() or any(
            (root / Path(*relative.parts[:index])).is_symlink()
            for index in range(1, len(relative.parts))
        ):
            continue
        if not path.is_file() or (only_python and path.suffix != ".py"):
            continue
        result[prefix + relative.as_posix()] = path.read_bytes()
    return result


def materialize_runtime_image(
    source: Path,
    dependency: Path,
    directory: Path,
    source_head: str,
    *,
    instrumentation_root: Path | None = None,
    instrumentation_wheel: Path | None = None,
) -> tuple[Path, Path, str]:
    """Build a real readonly-data image and canonical inventory for the public preparer.

    No interpreter, loader or native module from the resulting image is executed.
    All native objects come from the current observed import closure; system
    package verification and wheel provenance remain the preparer's responsibility.
    """
    prefix = "runtime/python/lib/python3.12/"
    content = _tree_bytes(Path("/usr/lib/python3.12"), prefix, only_python=True)
    for root, package in (
        (source / "src/synapse_channel", "synapse_channel"),
        (dependency, "websockets"),
    ):
        content.update(
            _tree_bytes(root, prefix + "dist-packages/" + package + "/", only_python=False)
        )
    for item in observe_native_import_closure(
        source,
        dependency,
        instrumentation_root=instrumentation_root,
        instrumentation_wheel=instrumentation_wheel,
    ):
        if item.manager != "instrumentation-wheel":
            content[item.payload] = item.installed_source.read_bytes()
    for name, installed in {
        "config/openssl.cnf": "/etc/ssl/openssl.cnf",
        "config/UTC": "/usr/share/zoneinfo/Etc/UTC",
        "trust/ca-certificates.crt": "/etc/ssl/certs/ca-certificates.crt",
    }.items():
        content["runtime/" + name] = Path(installed).read_bytes()
    image = directory / "image"
    image.mkdir(mode=0o700)
    for name, raw in content.items():
        path = image / name.removeprefix("runtime/")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("xb") as stream:
            stream.write(raw)
        path.chmod(0o400)
    manifest = CapsuleManifest(
        source_head,
        str(directory / "runs"),
        tuple(
            FilePin(name, hashlib.sha256(raw).hexdigest(), len(raw))
            for name, raw in sorted(content.items())
        ),
    )
    raw = manifest.canonical_bytes()
    manifest_path = directory / "runtime-manifest.json"
    manifest_path.write_bytes(raw)
    return image, manifest_path, hashlib.sha256(raw).hexdigest()


def materialize_instrumentation_view(
    source: Path, dependency: Path, directory: Path, artifact: Path
) -> tuple[Path, str]:
    """Capture real probe/runtime bytes plus the complete locked collector as input.

    The resulting view is ordinary owned test data, not installed root custody.
    No captured interpreter or loader is executed by this fixture.
    """
    from tools.protected_runtime_probe.capsule import capture_capsule
    from tools.protected_runtime_probe.instrumentation import build_instrumentation_content
    from tools.protected_runtime_probe.instrumentation_artifacts import (
        capture_instrumentation_wheel,
    )
    from tools.protected_runtime_probe.prepare import prepare_packet
    from tools.protected_runtime_probe.runtime import capture_runtime_content

    packet = directory / "production-packet"
    review = prepare_packet(source, packet, directory / "unused-backing")
    base = capture_capsule(packet, review["manifest_sha256"])
    image_directory = directory / "runtime"
    image_directory.mkdir()
    image, manifest, digest = materialize_runtime_image(
        source, dependency, image_directory, base.manifest.source_head
    )
    content = dict(base.content)
    content.update(capture_runtime_content(image, manifest, digest))
    content = build_instrumentation_content(
        content,
        capture_instrumentation_wheel(
            source / "tools/protected_runtime_probe/native-dependencies.lock.json", artifact
        ),
    )
    frozen = CapsuleManifest(
        base.manifest.source_head,
        str(directory / "unused-backing"),
        tuple(
            FilePin(name, hashlib.sha256(raw).hexdigest(), len(raw))
            for name, raw in sorted(content.items())
        ),
    )
    view = directory / "view"
    view.mkdir()
    for name, raw in content.items():
        path = view / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o400)
    encoded = frozen.canonical_bytes()
    (view / "manifest.json").write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    capture_capsule(view, digest)
    return view, digest


def materialize_instrumented_packet(
    source: Path,
    dependency: Path,
    directory: Path,
    collector_artifact: Path,
    dependency_artifact: Path,
) -> tuple[Path, dict[str, str]]:
    """Prepare a complete real instrumented packet without privileged effects.

    Parameters
    ----------
    source, dependency:
        Actual checkout and installed websockets package whose bytes are captured.
    directory:
        Existing ordinary-owned Samsung fixture directory.
    collector_artifact, dependency_artifact:
        Original wheels verified by the candidate's source-controlled native lock.

    Returns
    -------
    tuple[Path, dict[str, str]]
        Complete public-preparer packet and its review metadata. No printed command
        or captured interpreter/loader is executed during this preparation.
    """
    import subprocess

    from tools.protected_runtime_probe.instrumentation_artifacts import (
        capture_instrumentation_wheel,
    )
    from tools.protected_runtime_probe.prepare import prepare_packet

    wheel = capture_instrumentation_wheel(
        source / "tools/protected_runtime_probe/native-dependencies.lock.json",
        collector_artifact,
    )
    library = directory / "collector-source"
    for name, raw in wheel.members.items():
        path = library / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(raw)
        path.chmod(0o400)
    head = subprocess.run(
        ["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout.strip()
    image_parent = directory / "captured-input"
    image_parent.mkdir(mode=0o700)
    image, manifest, digest = materialize_runtime_image(
        source,
        dependency,
        image_parent,
        head,
        instrumentation_root=library / "coverage",
        instrumentation_wheel=collector_artifact,
    )
    packet = directory / "instrumented-packet"
    review = prepare_packet(
        source,
        packet,
        directory / "runs",
        runtime_image=image,
        runtime_manifest=manifest,
        runtime_sha256=digest,
        dependency_wheel=dependency_artifact,
        instrumentation_root=library / "coverage",
        instrumentation_wheel=collector_artifact,
    )
    return packet, review


def samsung_backing_or_refusal(directory: Path) -> bool:
    """Exercise the public storage refusal when a test has no Samsung backing.

    Parameters
    ----------
    directory:
        Actual pytest temporary directory chosen by the runner.

    Returns
    -------
    bool
        Whether the native packet case can use the approved storage path.
        Other ABI profiles continue to their existing public ABI refusal.

    Notes
    -----
    An off-Samsung native run executes the real preparer CLI and checks its
    exact refusal without changing production policy or creating a packet.
    Samsung-only execution observations remain local qualification evidence.
    """
    import platform
    import subprocess
    import sys

    if (sys.platform, platform.machine(), sys.version_info[:2]) != ("linux", "x86_64", (3, 12)):
        return True
    if directory.is_relative_to("/media/anulum/GOTM"):
        return True
    source = Path(__file__).resolve().parents[2]
    packet = directory / "scope-refusal-packet"
    backing = directory / "scope-refusal-backing"
    assert not packet.exists() and not backing.exists()
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-W",
            "error::DeprecationWarning",
            "-W",
            "error::ResourceWarning",
            "-m",
            "tools.protected_runtime_probe.prepare",
            "--source",
            str(source),
            "--bundle",
            str(packet),
            "--backing-parent",
            str(backing),
        ],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=_PUBLIC_REFUSAL_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 1 and not result.stdout, result.stdout + result.stderr
    assert result.stderr.strip().splitlines()[-1] == (
        "ValueError: prerequisite backing must remain in the approved Samsung scope"
    ), result.stderr
    assert not packet.exists() and not backing.exists()
    return False
