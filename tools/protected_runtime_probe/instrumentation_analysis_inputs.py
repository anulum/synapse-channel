# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — ordinary analysis input and physical source binding
"""Verify parent report associations and raw hashes before child dataset parsing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import require_native_linux
from .capsule import CapturedCapsule, capture_capsule, read_relative_file
from .instrumentation import CONFIGURATION, RECORDING_ROLES, instrumentation_selected
from .instrumentation_recording import MAX_RECORDING_BYTES
from .roles import NAMES

_METADATA_MAX = 2 * 1024 * 1024
_DATASET_FIELDS = {
    "role",
    "filename",
    "host_pid",
    "producer_uid",
    "producer_gid",
    "sha256",
    "size_bytes",
    "source_device",
    "source_inode",
    "sealed_device",
    "sealed_inode",
    "sealed_uid",
    "sealed_gid",
}


def require_ordinary_analysis() -> None:
    """Refuse root identities before opening or interpreting child-written data.

    Raises
    ------
    OSError
        Unless the native descriptor and credential interfaces are available.
    PermissionError
        If any real, effective or saved UID or GID is root.
    """
    require_native_linux(sys.platform, operation="ordinary recording analysis")
    if 0 in (*os.getresuid(), *os.getresgid()):
        raise PermissionError("recording analysis requires an ordinary nonroot user")


def canonical_analysis_metadata(value: object) -> bytes:
    """Encode parent-owned metadata using the producer's exact canonical JSON form.

    Parameters
    ----------
    value:
        Detached parent metadata, never executable input or child SQLite.

    Returns
    -------
    bytes
        ASCII canonical JSON; non-finite numbers are refused.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse ambiguous repeated metadata keys before interpreting parent associations."""
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate recording analysis metadata key")
        result[name] = value
    return result


def _invalid_constant(value: str) -> None:
    """Refuse JSON constants outside the finite metadata contract."""
    raise ValueError("non-finite recording metadata: " + value)


def _decode(raw: bytes) -> dict[str, Any]:
    """Decode bounded parent metadata as one unambiguous object."""
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_constant)
    if type(value) is not dict:
        raise ValueError("recording analysis metadata must be an object")
    return value


def _digest(value: object) -> str:
    """Require the literal full SHA256 carried by a parent association."""
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("invalid recording analysis digest")
    return value


def _read(path: Path, maximum: int, *, readonly: bool = False) -> bytes:
    """Capture one bounded regular input without following its final directory or leaf."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("recording analysis input must be an absolute canonical path")
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = read_relative_file(directory, path.name, maximum, require_single_link=True)
        if (
            readonly
            and stat.S_IMODE(os.stat(path.name, dir_fd=directory, follow_symlinks=False).st_mode)
            != 0o444
        ):
            raise ValueError("recording analysis requires readonly sealed copies")
        return raw
    finally:
        os.close(directory)


def _report(path: Path, approved_sha256: str) -> dict[str, Any]:
    """Bind the parent result before accepting any manifest or producer dataset."""
    raw = _read(path, _METADATA_MAX)
    if hashlib.sha256(raw).hexdigest() != _digest(approved_sha256):
        raise ValueError("recording report differs from its independent digest")
    value = _decode(raw)
    if (
        value.get("schema") != "synapse-prerequisite-result.v3"
        or value.get("interpreter_profile") != "captured-runtime"
        or type(value.get("component_checks_passed")) is not bool
        or value.get("quiescent") is not True
        or value.get("authority_recording_graceful_exit") is not True
        or type(value.get("instrumentation")) is not dict
        or value["instrumentation"].get("collection_complete") is not True
    ):
        raise ValueError("recording report lacks complete quiescent successful producers")
    _require_successful_producers(value.get("children"))
    if type(value.get("roles")) is not dict or set(value["roles"]) != set(NAMES):
        raise ValueError("recording report lacks parent role bindings")
    _digest(value.get("manifest_sha256"))
    return value


def _require_successful_producers(children: object) -> None:
    """Require four original producer exits before raw recording analysis."""
    if (
        type(children) is not list
        or len(children) != 4
        or any(
            type(row) is not dict
            or type(row.get("returncode")) is not int
            or row["returncode"] != 0
            for row in children
        )
    ):
        raise ValueError("recording report lacks complete quiescent successful producers")


def _manifest(directory: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Bind the exact parent-generated manifest and finite campaign identity."""
    raw = _read(directory / "manifest.json", _METADATA_MAX, readonly=True)
    instrumentation = report["instrumentation"]
    if hashlib.sha256(raw).hexdigest() != _digest(instrumentation.get("manifest_sha256")):
        raise ValueError("recording manifest differs from the parent report")
    value = _decode(raw)
    if value.keys() != {
        "schema",
        "run_id",
        "capsule_manifest_sha256",
        "configuration_sha256",
        "source_entries_sha256",
        "sources",
        "code_view",
        "writer_code_view",
        "datasets",
    } or raw != canonical_analysis_metadata(value):
        raise ValueError("recording manifest shape or canonical encoding differs")
    if (
        value["schema"] != "synapse.runtime-recordings.v1"
        or value["run_id"] != report.get("run_id")
        or type(value["run_id"]) is not str
        or re.fullmatch("run-[0-9a-f]{16}", value["run_id"]) is None
        or value["capsule_manifest_sha256"] != report["manifest_sha256"]
        or value["source_entries_sha256"] != instrumentation.get("source_entries_sha256")
        or value["datasets"] != instrumentation.get("datasets")
        or value["writer_code_view"] != "/code"
    ):
        raise ValueError("recording manifest association differs from the parent report")
    view = value["code_view"]
    if (
        type(view) is not str
        or not Path(view).is_absolute()
        or str(Path(view)) != view
        or ".." in Path(view).parts
    ):
        raise ValueError("recording source view is not a canonical absolute path")
    return value


def _producer_numbers(row: dict[str, Any]) -> None:
    """Require bounded exact integer process and immutable source/copy metadata."""
    numeric = _DATASET_FIELDS - {"role", "filename", "sha256"}
    if any(type(row[name]) is not int or row[name] < 0 for name in numeric):
        raise ValueError("recording dataset binding requires exact nonnegative integers")
    if (
        not 0 < row["host_pid"] < 2**31
        or not 0 < row["size_bytes"] <= MAX_RECORDING_BYTES
        or not row["source_inode"]
        or not row["sealed_inode"]
        or row["sealed_uid"] != 0
        or row["sealed_gid"] != 0
    ):
        raise ValueError("recording dataset process or sealed metadata differs")


def _producer(row: object, role: str, report: dict[str, Any]) -> dict[str, Any]:
    """Require the exact parent-recorded role, file, process and principal association."""
    if type(row) is not dict or row.keys() != _DATASET_FIELDS:
        raise ValueError("recording dataset binding shape differs")
    if row["role"] != role or row["filename"] != role + ".sqlite":
        raise ValueError("recording dataset role or filename differs")
    _digest(row["sha256"])
    _producer_numbers(row)
    expected = (
        {"uid": 0, "gid": 0}
        if role in {"controller", "namespace-wrapper"}
        else report["roles"]["synapse-" + role]
    )
    if (
        type(expected) is not dict
        or expected.keys() != {"uid", "gid"}
        or any(
            type(expected[key]) is not int or not 0 <= expected[key] < 2**32 - 1
            for key in ("uid", "gid")
        )
        or (row["producer_uid"], row["producer_gid"]) != (expected["uid"], expected["gid"])
    ):
        raise ValueError("recording dataset differs from its parent-enrolled principal")
    return row


def _datasets(
    directory: Path, manifest: dict[str, Any], report: dict[str, Any]
) -> dict[str, bytes]:
    """Capture all six readonly byte copies before any child database is parsed."""
    rows = manifest["datasets"]
    if type(rows) is not list or len(rows) != len(RECORDING_ROLES):
        raise ValueError("recording analysis requires exactly six dataset bindings")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if set(os.listdir(descriptor)) != {
            "manifest.json",
            *(role + ".sqlite" for role in RECORDING_ROLES),
        }:
            raise ValueError("recording directory inventory differs from its fixed manifest")
    finally:
        os.close(descriptor)
    result = {}
    pids = set()
    for role, value in zip(RECORDING_ROLES, rows, strict=True):
        row = _producer(value, role, report)
        if row["host_pid"] in pids:
            raise ValueError("recording producers do not have distinct parent-observed PIDs")
        pids.add(row["host_pid"])
        raw = _read(directory / row["filename"], MAX_RECORDING_BYTES, readonly=True)
        if len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("sealed recording bytes differ from their parent binding")
        result[role] = raw
    return result


def verify_analysis_sources(capsule: CapturedCapsule, source: Path) -> dict[str, str]:
    """Require exact current physical Python inventory and every captured source byte.

    Parameters
    ----------
    capsule:
        Complete already admitted instrumentation capsule.
    source:
        Actual candidate checkout; coverage aliases are mapped only to its files.

    Returns
    -------
    dict[str, str]
        Captured logical source names mapped to byte-identical current physical files.

    Raises
    ------
    ValueError, OSError
        On missing/extra/changed files, symlinks or unavailable current source.
    """
    source = source.resolve(strict=True)
    expected = {
        pin.path: pin
        for pin in capsule.manifest.files
        if pin.path.endswith(".py")
        and pin.path.startswith(("app/synapse_channel/", "probe/protected_runtime_probe/"))
    }
    actual = {}
    for package, prefix in [
        (source / "src/synapse_channel", "app/synapse_channel/"),
        (source / "tools/protected_runtime_probe", "probe/protected_runtime_probe/"),
    ]:
        for path in package.rglob("*"):
            relative = path.relative_to(package)
            if "__pycache__" in relative.parts:
                continue
            if path.is_symlink():
                raise ValueError("current analysis source contains a symlink")
            if path.suffix == ".py" and path.is_file():
                actual[prefix + relative.as_posix()] = str(path)
    if actual.keys() != expected.keys():
        raise ValueError("current analysis source inventory differs from the capsule")
    for name, path_string in actual.items():
        pin = expected[name]
        path = Path(path_string)
        descriptor = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            raw = read_relative_file(descriptor, path.name, pin.size_bytes)
        finally:
            os.close(descriptor)
        if len(raw) != pin.size_bytes or hashlib.sha256(raw).hexdigest() != pin.sha256:
            raise ValueError("current analysis source bytes differ from the capsule")
    return actual


@dataclass(frozen=True)
class AnalysisInputs:
    """Detached raw inputs verified before any child-written SQLite is parsed."""

    capsule: CapturedCapsule
    manifest: Mapping[str, Any]
    datasets: Mapping[str, bytes]
    source_paths: Mapping[str, str]
    report_sha256: str
    manifest_sha256: str
    producer_component_checks_passed: bool


def capture_analysis_inputs(
    report: Path, report_sha256: str, capsule_directory: Path, datasets: Path, source: Path
) -> AnalysisInputs:
    """Capture an exact parent-report, capsule, raw dataset and current-source association.

    Parameters
    ----------
    report, report_sha256:
        Report or ordinary copy and independently retained digest. Authenticating
        the operator's report origin remains the caller's responsibility.
    capsule_directory:
        Complete capsule whose manifest digest is bound by that report.
    datasets:
        Readonly sealed dataset directory or exact readonly ordinary copies.
    source:
        Current byte-identical source checkout.

    Returns
    -------
    AnalysisInputs
        Detached verified raw bytes. No child SQLite parser or collector import ran.

    Raises
    ------
    PermissionError, ValueError, OSError
        On root context or any bounded metadata, byte or source disagreement.

    Notes
    -----
    Recorded producer/source inode identities describe parent observations.
    Ordinary copies may have different local inodes; their hashes remain exact.
    This diagnostic association grants no runtime or consumer acceptance.
    """
    require_ordinary_analysis()
    parent = _report(report, report_sha256)
    manifest = _manifest(datasets, parent)
    capsule = capture_capsule(capsule_directory, parent["manifest_sha256"])
    instrumentation_selected(capsule, requested=True)
    configuration = _decode(capsule.content[CONFIGURATION])
    if (
        manifest["sources"] != configuration["sources"]
        or hashlib.sha256(canonical_analysis_metadata(manifest["sources"])).hexdigest()
        != _digest(manifest["source_entries_sha256"])
        or hashlib.sha256(capsule.content[CONFIGURATION]).hexdigest()
        != _digest(manifest["configuration_sha256"])
    ):
        raise ValueError("recording source/configuration binding differs from its capsule")
    current = verify_analysis_sources(capsule, source)
    raw = _datasets(datasets, manifest, parent)
    manifest_digest = _digest(parent["instrumentation"]["manifest_sha256"])
    return AnalysisInputs(
        capsule,
        MappingProxyType(manifest),
        MappingProxyType(raw),
        MappingProxyType(current),
        _digest(report_sha256),
        manifest_digest,
        parent["component_checks_passed"],
    )
