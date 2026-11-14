# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — immutable privileged qualification packet preparation
"""Freeze a complete reviewed prerequisite packet without performing privileged effects."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import shlex
import sys
from pathlib import Path

import websockets
from websockets.version import version as websockets_version

from .capsule import MAX_FILE_BYTES, CapsuleManifest, FilePin, capture_capsule, read_relative_file
from .instrumentation import build_instrumentation_content, require_production_payload
from .instrumentation_artifacts import (
    InstrumentationWheel,
    capture_instrumentation_wheel,
    instrumentation_runtime_content,
)
from .native_closure import require_native_runtime_abi, verify_native_runtime_content
from .roles import ROOT_COMMAND_ENV
from .runtime import capture_runtime_content

_MODULES = frozenset(
    {
        "__init__.py",
        "capsule.py",
        "bootstrap.py",
        "storage.py",
        "roles.py",
        "resources.py",
        "reports.py",
        "authority_child.py",
        "namespace_child.py",
        "controller.py",
        "prepare.py",
        "runtime.py",
        "inventory.py",
        "instrumentation_artifacts.py",
        "instrumentation.py",
        "instrumentation_recording.py",
        "instrumentation_launch.py",
        "instrumentation_outputs.py",
        "instrumentation_retention.py",
        "instrumentation_campaign.py",
        "instrumentation_analysis.py",
        "instrumentation_analysis_inputs.py",
        "instrumentation_analysis_worker.py",
        "native_closure.py",
        "native_provenance.py",
    }
)
# The packaged cockpit build (dashboard_assets/cockpit/, written by
# tools/vendor_cockpit_build.py) adds its entry page, icons, web manifest and the
# WOFF fallbacks of its two font families to the reviewed package resources.
_APP_SUFFIXES = frozenset(
    {
        ".py",
        ".json",
        ".css",
        ".js",
        ".woff2",
        ".md",
        ".license",
        ".html",
        ".png",
        ".webmanifest",
        ".woff",
    }
)
_DEPENDENCY_SUFFIXES = frozenset({".py", ".pyi", ".c", ".so"})

# This exact literal is copied into the operator's reviewable command. The
# independently approved bootstrap digest is checked BEFORE importing any
# staging code. The executed bytes come from the same irreversibly sealed FD.
_INITIAL_GATE = """import os,sys,stat,hashlib,fcntl,importlib.machinery,importlib.util
p,h=sys.argv[1:3]
f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
try:
 s=os.fstat(f)
 if not stat.S_ISREG(s.st_mode) or not 0<s.st_size<1048576: raise ValueError('bootstrap size/type')
 b=os.read(f,1048576)
finally: os.close(f)
if hashlib.sha256(b).hexdigest()!=h: raise ValueError('bootstrap approval differs')
d=os.memfd_create('approved-synapse-bootstrap',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
try:
 if os.write(d,b)!=len(b): raise OSError('bootstrap capture incomplete')
 os.fchmod(d,256)
 seals=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL
 fcntl.fcntl(d,fcntl.F_ADD_SEALS,seals)
 if fcntl.fcntl(d,fcntl.F_GET_SEALS)!=seals: raise ValueError('bootstrap seals differ')
 class ApprovedLoader(importlib.machinery.SourceFileLoader):
  def get_code(self,fullname):
   return self.source_to_code(self.get_data(self.path),'<approved-root-bootstrap>')
 loader=ApprovedLoader('synapse_approved_bootstrap','/proc/self/fd/'+str(d))
 spec=importlib.util.spec_from_loader(loader.name,loader)
 if spec is None: raise ValueError('bootstrap import unavailable')
 module=importlib.util.module_from_spec(spec)
 sys.modules[loader.name]=module
 loader.exec_module(module)
 result=module.main(sys.argv[3:])
finally: os.close(d)
raise SystemExit(result)
"""


def _collect(root: Path, prefix: str, suffixes: frozenset[str]) -> dict[str, bytes]:
    """Capture every declared package resource without caches or descendant links."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    captured: dict[str, bytes] = {}
    try:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts:
                continue
            if path.is_symlink():
                raise ValueError("prerequisite source package contains a symlink")
            if path.is_dir():
                continue
            if path.suffix not in suffixes and path.name != "py.typed":
                raise ValueError("unreviewed prerequisite package resource: " + str(relative))
            captured[prefix + relative.as_posix()] = read_relative_file(
                descriptor, relative.as_posix(), 64 * 1024 * 1024
            )
        return captured
    finally:
        os.close(descriptor)


async def _source_head(source: Path) -> str:
    """Read the actual base HEAD without treating it as the uncommitted payload hash."""
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/git",
        "-C",
        str(source),
        "rev-parse",
        "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ROOT_COMMAND_ENV,
    )
    try:
        raw, _error = await asyncio.wait_for(process.communicate(), 5)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode or len(raw) > 128:
        raise ValueError("prerequisite source HEAD is unavailable")
    return raw.decode("ascii").strip()


def _capture_licence(path: Path) -> bytes:
    """Read bounded stable licence bytes without following the parent or leaf symlink."""
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        return read_relative_file(parent, path.name, MAX_FILE_BYTES)
    finally:
        os.close(parent)


def capture_websockets_package(
    package: Path, version: str, licences: tuple[Path, ...]
) -> dict[str, bytes]:
    """Capture the observed dependency only under its supported package profile.

    Parameters
    ----------
    package:
        Actual filesystem package root to capture without descendant symlinks.
    version:
        Version observed from the selected dependency.
    licences:
        Actual licence sources observed in that distribution's file metadata.

    Returns
    -------
    dict[str, bytes]
        Captured dependency files and the one unambiguous licence.

    Raises
    ------
    ValueError, OSError
        For an unsupported version, ambiguous licence, unsafe or oversized licence
        source, or unavailable package data. Stable ordinary cache hardlinks remain valid.
    """
    if version != "17.4":
        raise ValueError("prerequisite packet requires the reviewed websockets17.4 dependency")
    if len(licences) != 1:
        raise ValueError("prerequisite dependency licence provenance is unavailable")
    content = _collect(package, "app/websockets/", _DEPENDENCY_SUFFIXES)
    content["licenses/websockets.txt"] = _capture_licence(licences[0])
    return content


def _content(source: Path) -> dict[str, bytes]:
    """Capture the actual application, complete probe and installed native dependency."""
    require_native_runtime_abi(
        sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1
    )
    probe = source / "tools/protected_runtime_probe"
    actual = {path.name for path in probe.iterdir() if path.suffix in {".py", ".json", ".license"}}
    if actual != _MODULES | {
        "native-dependencies.lock.json",
        "native-dependencies.lock.json.license",
    }:
        raise ValueError("prerequisite packet module closure is incomplete or unreviewed")
    content = _collect(source / "src/synapse_channel", "app/synapse_channel/", _APP_SUFFIXES)
    content.update(
        _collect(probe, "probe/protected_runtime_probe/", frozenset({".py", ".json", ".license"}))
    )
    distribution = importlib.metadata.distribution("websockets")
    licences = tuple(
        Path(str(distribution.locate_file(entry)))
        for entry in distribution.files or ()
        if entry.name == "LICENSE"
    )
    content.update(
        capture_websockets_package(Path(websockets.__file__).parent, websockets_version, licences)
    )
    content["licenses/synapse-channel.txt"] = _capture_licence(source / "LICENSE")
    return content


def _write_packet(bundle: Path, manifest: CapsuleManifest, content: dict[str, bytes]) -> None:
    """Create a new owner-only staging packet and reverify its actual complete bytes."""
    storage = Path("/media/anulum/GOTM")
    if not bundle.is_absolute() or not bundle.is_relative_to(storage) or ".." in bundle.parts:
        raise ValueError("prerequisite staging must be a canonical Samsung path")
    if not storage.is_mount() or bundle.parent.stat().st_dev != storage.stat().st_dev:
        raise ValueError("prerequisite staging backing device is not the Samsung mount")
    bundle.mkdir(mode=0o700)
    for name, raw in content.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o400)
    raw = manifest.canonical_bytes()
    with (bundle / "manifest.json").open("xb") as handle:
        handle.write(raw)
    capture_capsule(bundle, hashlib.sha256(raw).hexdigest())


def _verify_runtime_input(
    runtime: dict[str, bytes],
    source: Path,
    dependency_wheel: Path,
    instrumentation_root: Path | None,
    instrumentation_wheel: Path | None,
) -> tuple[dict[str, object], InstrumentationWheel | None]:
    """Observe native imports over the exact optional collector's complete verified tree."""
    require_production_payload(runtime)
    collector = None
    observed = runtime
    if instrumentation_wheel is not None:
        collector = capture_instrumentation_wheel(
            source / "tools/protected_runtime_probe/native-dependencies.lock.json",
            instrumentation_wheel,
        )
        observed = {**runtime, **instrumentation_runtime_content(collector)}
    provenance = verify_native_runtime_content(
        observed,
        source,
        Path(websockets.__file__).parent,
        dependency_wheel,
        instrumentation_root=instrumentation_root,
        instrumentation_wheel=instrumentation_wheel,
    )
    return provenance, collector


def _add_captured_runtime(
    content: dict[str, bytes],
    source: Path,
    runtime_image: Path | None,
    runtime_manifest: Path | None,
    runtime_sha256: str | None,
    dependency_wheel: Path | None,
    instrumentation_root: Path | None,
    instrumentation_wheel: Path | None,
) -> None:
    """Add the optional runtime only after complete profile and native-byte verification."""
    supplied = (
        runtime_image is not None,
        runtime_manifest is not None,
        runtime_sha256 is not None,
        dependency_wheel is not None,
    )
    collector_supplied = instrumentation_root is not None or instrumentation_wheel is not None
    if collector_supplied and (instrumentation_root is None or instrumentation_wheel is None):
        raise ValueError("instrumentation requires both collector root and original wheel")
    if not any(supplied) and not collector_supplied:
        return
    if (
        runtime_image is None
        or runtime_manifest is None
        or runtime_sha256 is None
        or dependency_wheel is None
    ):
        raise ValueError(
            "captured runtime requires image, manifest, independent digest "
            "and pinned dependency wheel"
        )
    runtime = capture_runtime_content(runtime_image, runtime_manifest, runtime_sha256)
    provenance, collector = _verify_runtime_input(
        runtime, source, dependency_wheel, instrumentation_root, instrumentation_wheel
    )
    runtime["runtime/config/native-provenance.json"] = (
        json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    for name, data in content.items():
        if name.startswith("app/") and not name.endswith(".license"):
            installed = "runtime/python/lib/python3.12/dist-packages/" + name[4:]
            if runtime.get(installed) != data:
                raise ValueError("captured runtime differs from current application/dependency")
    content.update(runtime)
    if collector is not None:
        content.update(build_instrumentation_content(content, collector))


def _guest_operator_command(
    bundle: Path, bootstrap: str, bootstrap_sha: str, digest: str, *, instrumented: bool
) -> str:
    """Build the exact guest-side hash-bound loader command without host privilege escalation."""
    return shlex.join(
        [
            "/usr/bin/python3.12",
            "-I",
            "-B",
            "-S",
            "-c",
            _INITIAL_GATE,
            str(bundle / bootstrap),
            bootstrap_sha,
            "--bundle",
            str(bundle),
            "--manifest-sha256",
            digest,
            *(["--instrumentation"] if instrumented else []),
        ]
    )


def _operator_review(
    bundle: Path, manifest: CapsuleManifest, content: dict[str, bytes], *, instrumented: bool
) -> dict[str, str]:
    """Bind the guest command, native provenance and precise retained effects for review."""
    digest = hashlib.sha256(manifest.canonical_bytes()).hexdigest()
    bootstrap = "probe/protected_runtime_probe/bootstrap.py"
    bootstrap_sha = hashlib.sha256(content[bootstrap]).hexdigest()
    command = _guest_operator_command(
        bundle, bootstrap, bootstrap_sha, digest, instrumented=instrumented
    )
    result = {
        "bundle": str(bundle),
        "manifest_sha256": digest,
        "bootstrap_sha256": bootstrap_sha,
        "guest_uuid": manifest.guest_uuid,
        "execution_scope": "exact independently reviewed disposable guest only",
        "operator_command": command,
        "retention": (
            "Each attempt creates one fresh root backing directory inside the disposable guest. "
            "The guest's private disk is mounted at /media/anulum/GOTM; this is not a host share. "
            "All four dedicated accounts, /opt directories, mounts and cgroups are guest-only. "
            "Normal finalization retains one readonly evidence bind and backing inside that guest; "
            "its report records exact identities and logical size. The code view is removed "
            "and verified before publication. Failed cleanup is a hold. Guest poweroff ends "
            "processes, mounts and cgroups; account metadata and retained backing remain in the "
            "held guest disk. Host artifacts are the fresh disk, readonly seed and returned raw "
            "evidence/logs; no workstation account, /opt, mount or cgroup is changed. "
            "Retire held disks and evidence only through owner-authorized exact-identity "
            "retirement; no automatic deletion. Preparing this packet grants no launch authority."
        ),
    }
    provenance_name = "runtime/config/native-provenance.json"
    if provenance_name in content:
        result["native_provenance_file"] = str(bundle / provenance_name)
        result["native_provenance_sha256"] = hashlib.sha256(content[provenance_name]).hexdigest()
    (bundle / "operator-review.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def prepare_packet(
    source: Path,
    bundle: Path,
    backing_parent: Path,
    *,
    runtime_image: Path | None = None,
    runtime_manifest: Path | None = None,
    runtime_sha256: str | None = None,
    dependency_wheel: Path | None = None,
    instrumentation_root: Path | None = None,
    instrumentation_wheel: Path | None = None,
) -> dict[str, str]:
    """Freeze a complete component qualification packet without root effects.

    Parameters
    ----------
    source:
        Actual candidate checkout containing all reviewed application/probe modules.
    bundle:
        Fresh nonexistent Samsung staging directory beneath an existing parent.
    backing_parent:
        Approved path on the guest-only /media/anulum/GOTM mount for fresh run files.
    runtime_image, runtime_manifest, runtime_sha256, dependency_wheel:
        All four select the captured interpreter profile with an independently
        pinned runtime-only capsule manifest and locked dependency wheel. All
        absent retains the explicitly limited host-tools prerequisite profile,
        not installed-runtime evidence.
    instrumentation_root, instrumentation_wheel:
        Optional all-or-none complete collector package tree and original locked
        archive. Requires the captured runtime profile, binds the instrumentation
        configuration into the capsule and adds its literal operator selector.
        Preparation never starts a recording or executes the operator command.

    Returns
    -------
    dict[str, str]
        Manifest/bootstrap hashes, per-attempt guest UUID and guest-only command.

    Raises
    ------
    ValueError, OSError
        On missing closure, unsupported native dependencies, unsafe storage or any
        mismatched captured byte. Creates no account, mount, service or privileged runtime.

    Notes
    -----
    The manifest source HEAD identifies the base commit. Its exact payload pins
    also cover uncommitted source; no equality with committed source is claimed.
    The printed command contains no sudo and is exclusively guest-side. Its
    pinned bootstrap verifies the manifest-enrolled guest before setup effects.
    Preparation does not approve or execute that command.
    """
    content = _content(source)
    _add_captured_runtime(
        content,
        source,
        runtime_image,
        runtime_manifest,
        runtime_sha256,
        dependency_wheel,
        instrumentation_root,
        instrumentation_wheel,
    )
    pins = tuple(
        FilePin(name, hashlib.sha256(raw).hexdigest(), len(raw))
        for name, raw in sorted(content.items())
    )
    manifest = CapsuleManifest(asyncio.run(_source_head(source)), str(backing_parent), pins)
    _write_packet(bundle, manifest, content)
    return _operator_review(
        bundle, manifest, content, instrumented=instrumentation_wheel is not None
    )


def main(argv: list[str] | None = None) -> int:
    """Prepare and print the complete immutable packet for operator review.

    Parameters
    ----------
    argv:
        Candidate source, new staging path and future Samsung backing parent.

    Returns
    -------
    int
        Zero after full local byte recapture; no privileged command is executed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--backing-parent", type=Path, required=True)
    parser.add_argument("--runtime-image", type=Path)
    parser.add_argument("--runtime-manifest", type=Path)
    parser.add_argument("--runtime-sha256")
    parser.add_argument("--dependency-wheel", type=Path)
    parser.add_argument("--instrumentation-root", type=Path)
    parser.add_argument("--instrumentation-wheel", type=Path)
    arguments = parser.parse_args(argv)
    result = prepare_packet(
        arguments.source,
        arguments.bundle,
        arguments.backing_parent,
        runtime_image=arguments.runtime_image,
        runtime_manifest=arguments.runtime_manifest,
        runtime_sha256=arguments.runtime_sha256,
        dependency_wheel=arguments.dependency_wheel,
        instrumentation_root=arguments.instrumentation_root,
        instrumentation_wheel=arguments.instrumentation_wheel,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
