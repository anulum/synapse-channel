# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hash-bound root prerequisite bootstrap
"""Run only captured approved code; never import from the caller's staging tree."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import os
import platform
import re
import secrets
import stat
import sys
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .capsule import CapturedCapsule
    from .instrumentation_campaign import InstrumentationCampaign

_CODEC = "probe/protected_runtime_probe/capsule.py"
_VIEW_PARENT = Path("/opt/synapse-channel/protected-runtime-qualification")
_MANIFEST_MAX = 2 * 1024 * 1024
_FILE_MAX = 64 * 1024 * 1024


def require_native_bootstrap_abi(
    observed_platform: str, machine: str, pointer_bytes: int, python_version: tuple[int, int]
) -> None:
    """Require the observed standalone operator ABI before imports or effects.

    Parameters
    ----------
    observed_platform, machine:
        Actual platform and native machine names supplied by the host adapter.
    pointer_bytes, python_version:
        Observed native pointer width and interpreter major/minor pair.

    Raises
    ------
    OSError
        Unless these observations identify Linux x86_64 CPython 3.12.
    """
    if (
        observed_platform != "linux"
        or machine != "x86_64"
        or pointer_bytes != 8
        or python_version != (3, 12)
    ):
        raise OSError("prerequisite proof requires native Linux x86_64 CPython3.12")


def require_disposable_guest(expected_uuid: str) -> None:
    """Refuse privileged setup outside the exact manifest-enrolled Linux guest.

    Parameters
    ----------
    expected_uuid:
        Per-attempt canonical UUID4 from the independently hash-approved manifest.
        No caller-supplied positive environment observation is accepted.

    Raises
    ------
    PermissionError
        Unless the full UID map is the initial namespace mapping, every observed
        CPU advertises a hypervisor and the native DMI UUID matches enrollment.
    OSError, ValueError
        On unavailable native identity or malformed enrollment, before effects.

    Notes
    -----
    This prevents accidental execution on the host. It does not establish
    resistance to a malicious host administrator or authorize a launch.
    """
    if (
        type(expected_uuid) is not str
        or re.fullmatch(
            "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            expected_uuid,
        )
        is None
    ):
        raise ValueError("disposable guest requires a canonical per-attempt UUID4")
    if sys.platform != "linux":
        raise OSError("disposable guest identity requires native Linux")
    if Path("/proc/self/uid_map").read_bytes() != b"         0          0 4294967295\n":
        raise PermissionError("disposable guest requires the exact initial user namespace")
    flags = [
        line.partition(":")[2].split()
        for line in Path("/proc/cpuinfo").read_text(encoding="ascii").splitlines()
        if line.partition(":")[0].strip() == "flags"
    ]
    if len(flags) != 1 or "hypervisor" not in flags[0]:
        raise PermissionError("disposable guest requires one hypervisor CPU")
    observed = Path("/sys/class/dmi/id/product_uuid").read_text(encoding="ascii").strip().lower()
    if observed != expected_uuid:
        raise PermissionError("disposable guest DMI UUID differs from the approved attempt")


def _require_root_context() -> None:
    """Refuse unavailable ABI, unsafe interpreter and absent mount privilege before effects."""
    require_native_bootstrap_abi(
        sys.platform, platform.machine(), ctypes.sizeof(ctypes.c_void_p), sys.version_info[:2]
    )
    if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise PermissionError("prerequisite proof requires the trusted operator root context")
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode or not sys.flags.no_site:
        raise ValueError("prerequisite bootstrap requires isolated no-bytecode no-site flags")
    if sys.prefix != sys.base_prefix or Path(sys.executable).resolve() != Path(
        "/usr/bin/python3.12"
    ):
        raise ValueError("prerequisite bootstrap requires the system root interpreter")
    capabilities = next(
        line.partition(":")[2].strip()
        for line in Path("/proc/self/status").read_text().splitlines()
        if line.startswith("CapEff:")
    )
    required = sum(1 << number for number in (0, 1, 6, 7, 8, 21))
    if int(capabilities, 16) & required != required:
        raise PermissionError("prerequisite proof requires the actual root setup capabilities")
    if len(tuple(Path("/proc/self/task").iterdir())) != 1:
        raise ValueError("prerequisite bootstrap requires a single-threaded root parent")


def _read_regular(root: int, relative: str, maximum: int) -> bytes:
    """Capture bounded regular bytes without following staging descendants."""
    current = os.dup(root)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = child
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=current,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
                raise ValueError("bootstrap input is not a bounded regular file")
            chunks = bytearray()
            while len(chunks) <= maximum:
                chunk = os.read(descriptor, min(262144, maximum + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            if len(chunks) > maximum:
                raise ValueError("bootstrap input exceeds its byte bound")
            return bytes(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(current)


def _manifest_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated manifest keys before selecting or executing captured code."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("bootstrap manifest contains a repeated object key")
        result[key] = value
    return result


def _approved_capture(bundle: Path, approved_digest: str) -> CapturedCapsule:
    """Compile only the approved captured codec before capturing the whole payload."""
    if re.fullmatch("[0-9a-f]{64}", approved_digest) is None:
        raise ValueError("invalid independently approved capsule digest")
    root = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        manifest_raw = _read_regular(root, "manifest.json", _MANIFEST_MAX)
        if hashlib.sha256(manifest_raw).hexdigest() != approved_digest:
            raise ValueError("bootstrap manifest differs from independent approval")
        manifest = json.loads(manifest_raw, object_pairs_hook=_manifest_object)
        pins = [row for row in manifest["files"] if type(row) is dict and row.get("path") == _CODEC]
        if len(pins) != 1 or type(pins[0].get("size_bytes")) is not int:
            raise ValueError("approved capsule requires one exact bounded codec pin")
        size = pins[0]["size_bytes"]
        if not 0 < size <= _FILE_MAX:
            raise ValueError("approved capsule codec exceeds byte bound")
        raw = _read_regular(root, _CODEC, size)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != pins[0]["sha256"]:
            raise ValueError("bootstrap codec differs from approved captured bytes")
    finally:
        os.close(root)
    return _capture_with_sealed_codec(raw, bundle, approved_digest)


class _ApprovedSourceLoader(importlib.machinery.SourceFileLoader):
    """Compile sealed bytes with a stable filename, never a recycled proc FD path."""

    def get_code(self, fullname: str) -> types.CodeType:
        """Avoid bytecode-cache lookups and use an immutable diagnostic filename."""
        return self.source_to_code(self.get_data(self.path), f"<approved-{fullname}>")


@contextmanager
def _sealed_module(raw: bytes, module_name: str) -> Iterator[types.ModuleType]:
    """Import an exact captured module from an irreversibly sealed kernel file."""
    import fcntl

    if module_name in sys.modules:
        raise ValueError("prerequisite module namespace already exists")
    descriptor = os.memfd_create(
        "synapse-approved-prerequisite", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
    )
    try:
        view = memoryview(raw)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("approved prerequisite memory copy made no progress")
            offset += written
        os.fchmod(descriptor, 0o400)
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        if fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) != seals:
            raise ValueError("approved prerequisite module seals differ")
        loader = _ApprovedSourceLoader(module_name, f"/proc/self/fd/{descriptor}")
        spec = importlib.util.spec_from_loader(module_name, loader)
        if spec is None:
            raise ValueError("approved prerequisite module has no import specification")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            loader.exec_module(module)
            yield module
        finally:
            del sys.modules[module_name]
    finally:
        os.close(descriptor)


def _capture_with_sealed_codec(raw: bytes, bundle: Path, approved_digest: str) -> CapturedCapsule:
    """Retain the verified codec while capturing every approved payload entry."""
    with _sealed_module(raw, "synapse_verified_prerequisite_capsule") as codec:
        capture = cast("Callable[[Path, str], CapturedCapsule]", codec.capture_capsule)
        return capture(bundle, approved_digest)


def _require_profile_selector(capsule: CapturedCapsule, instrumented: bool) -> None:
    """Check the literal selector without importing any caller-controlled staging code."""
    if type(instrumented) is not bool:
        raise ValueError("invalid root instrumentation selector")
    site = "runtime/python/lib/python3.12/dist-packages/"
    configuration = "runtime/config/instrumentation.json"
    supplied = any(
        name == configuration
        or name.startswith("runtime/artifacts/coverage-")
        or name.startswith(site + "coverage/")
        or name.startswith(site + "coverage-")
        or name == site + "a1_coverage.pth"
        for name in capsule.content
    )
    if (not instrumented and supplied) or (instrumented and configuration not in capsule.content):
        raise ValueError("root instrumentation selector differs from the pinned profile")


def _run_captured(capsule: CapturedCapsule, run_id: str, *, instrumented: bool = False) -> int:
    """Copy and verify the whole immutable view before importing its controller."""
    with _sealed_module(
        capsule.content["probe/protected_runtime_probe/storage.py"],
        "synapse_verified_prerequisite_storage",
    ) as storage:
        create = cast(Callable[[Path, str], int], storage.create_backing)
        write = cast("Callable[[int, CapturedCapsule], int]", storage.write_payload)
        bind = cast(Callable[[int, Path], None], storage.bind_readonly)
        backing = create(Path(capsule.manifest.backing_parent), run_id)
        payload: int | None = None
        target = _VIEW_PARENT / run_id
        mounted = False
        try:
            payload = write(backing, capsule)
            # Global executable ancestry is created/checked by the sealed root module.
            parent = storage.open_directory(_VIEW_PARENT, trusted=True, create=True)
            try:
                os.mkdir(run_id, 0o755, dir_fd=parent)
            finally:
                os.close(parent)
            bind(payload, target)
            mounted = True
            verified = _approved_capture(target, capsule.manifest_sha256)

            def retire_view() -> dict[str, object]:
                nonlocal mounted
                if payload is None or not mounted:
                    raise ValueError("prerequisite code view retirement was not enrolled")
                expected = os.fstat(payload)
                storage.unbind(target, expected_identity=(expected.st_dev, expected.st_ino))
                mounted = False
                target.rmdir()
                if os.path.lexists(target):
                    raise ValueError("prerequisite code view remains after retirement")
                return {
                    "path": str(target),
                    "removed": True,
                    "source_device": expected.st_dev,
                    "source_inode": expected.st_ino,
                }

            return _execute_controller(
                verified, backing, target, run_id, retire_view, instrumented=instrumented
            )
        finally:
            try:
                if mounted and payload is not None:
                    expected = os.fstat(payload)
                    storage.unbind(target, expected_identity=(expected.st_dev, expected.st_ino))
                    target.rmdir()
            finally:
                if payload is not None:
                    os.close(payload)
                os.close(backing)


def _execute_controller(
    verified: CapturedCapsule,
    backing: int,
    target: Path,
    run_id: str,
    retire_view: Callable[[], dict[str, object]],
    *,
    instrumented: bool,
) -> int:
    """Own recorder startup/closure around the actual readonly controller import."""
    sys.path[:0] = [str(target / "probe"), str(target / "app")]
    recordings: InstrumentationCampaign | None = None
    try:
        if instrumented:
            module = importlib.import_module("protected_runtime_probe.instrumentation_campaign")
            factory = cast("type[InstrumentationCampaign]", module.InstrumentationCampaign)
            recordings = factory(verified, backing, target, run_id)
        module = importlib.import_module("protected_runtime_probe.controller")
        run = cast(
            "Callable[[CapturedCapsule, int, Path, str, Callable[[], dict[str, object]], "
            "InstrumentationCampaign | None], int]",
            module.run,
        )
        return run(verified, backing, target, run_id, retire_view, recordings)
    finally:
        if recordings is not None:
            recordings.close()


def main(argv: list[str] | None = None) -> int:
    """Run an explicitly approved frozen prerequisite proof inside its disposable guest.

    Parameters
    ----------
    argv:
        Untrusted staging directory and independently approved canonical manifest
        SHA256. Initial operator loading must separately pin these bootstrap bytes.

    Returns
    -------
    int
        Controller result; zero describes component checks and quiescence only.

    Raises
    ------
    PermissionError
        Unless the real root operator has all required setup capabilities.
    OSError, ValueError
        On unsafe interpreter, unsupported profile or mismatched captured bytes.

    Notes
    -----
    Installs reviewed prerequisites only inside the exact approved guest. Failure
    never becomes an execution grant or whole-runtime acceptance.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--instrumentation", action="store_true")
    arguments = parser.parse_args(argv)
    _require_root_context()
    capsule = _approved_capture(arguments.bundle, arguments.manifest_sha256)
    _require_profile_selector(capsule, arguments.instrumentation)
    require_disposable_guest(capsule.manifest.guest_uuid)
    return _run_captured(
        capsule, "run-" + secrets.token_hex(8), instrumented=arguments.instrumentation
    )


if __name__ == "__main__":
    raise SystemExit(main())
