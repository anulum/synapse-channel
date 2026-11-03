# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — finite in-memory instrumentation and descriptor-relative saving
"""Collect only from the admitted profile and save the process's own memory data."""

from __future__ import annotations

import hashlib
import importlib
import os
import platform
import re
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .capsule import capture_capsule
from .instrumentation import RECORDING_ROLES, instrumentation_selected
from .native_closure import require_native_runtime_abi

if TYPE_CHECKING:
    from coverage import Coverage, CoverageData

#: Maximum raw dataset and per-process file size for this finite profile.
MAX_RECORDING_BYTES = 16 * 1024 * 1024
#: The sole dataset entry admitted in a recording leaf.
RECORDING_FILENAME = "coverage.sqlite"


def snapshot_memory_coverage(
    data: CoverageData, *, maximum_bytes: int = MAX_RECORDING_BYTES
) -> bytes:
    """Snapshot only an existing, in-process, branch-coverage memory database.

    Parameters
    ----------
    data:
        This process's fresh CoverageData from the admitted collector. Never
        pass data loaded from a child or external dataset.
    maximum_bytes:
        Positive output bound, at most MAX_RECORDING_BYTES.

    Returns
    -------
    bytes
        Standard SQLite image, suitable for exclusive descriptor-relative saving.

    Raises
    ------
    ValueError
        For disk data, absent arcs, invalid bounds or excessive output.
    OSError, sqlite3.Error
        On an unsupported native producer or unavailable memory serialization.

    Notes
    -----
    The in-memory URI is intentionally bound to Coverage.py7.17.0. This uses
    its public data_filename and the standard sqlite3 serialization API,
    without accessing private coverage internals or executing SQL scripts.
    """
    require_native_runtime_abi(
        sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1
    )
    if type(maximum_bytes) is not int or not 0 < maximum_bytes <= MAX_RECORDING_BYTES:
        raise ValueError("invalid instrumentation dataset byte bound")
    uri = data.data_filename()
    pattern = (
        r"file:coverage-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"
        r"[?]mode=memory&cache=shared"
    )
    if re.fullmatch(pattern, uri) is None:
        raise ValueError("instrumentation snapshot requires the process's memory database")
    if not data.has_arcs():
        raise ValueError("instrumentation snapshot requires actual branch data")
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        # Native preparation also exercises this public SQLite serialization API.
        raw = connection.serialize()
    if len(raw) > maximum_bytes:
        raise ValueError("instrumentation dataset exceeds its byte bound")
    return raw


def _owned_output(role: str, descriptor: int, uid: int, gid: int) -> tuple[int, tuple[int, int]]:
    """Retain only an empty, private leaf with the exact enrolled owner."""
    if role not in RECORDING_ROLES:
        raise ValueError("unexpected instrumentation recording role")
    if type(descriptor) is not int or descriptor < 0:
        raise ValueError("invalid instrumentation directory descriptor")
    if any(type(value) is not int or not 0 <= value < 2**32 - 1 for value in (uid, gid)):
        raise ValueError("invalid instrumentation output principal")
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or (info.st_uid, info.st_gid) != (uid, gid)
    ):
        raise PermissionError("instrumentation output is not the private enrolled directory")
    if os.listdir(descriptor):
        raise ValueError("instrumentation output directory must be fresh and empty")
    return os.dup(descriptor), (info.st_dev, info.st_ino)


def _require_backend_origins(site: Path) -> None:
    """Refuse foreign cached collector modules before importing a captured package."""
    root = site / "coverage"
    for name, module in tuple(sys.modules.items()):
        if name == "coverage" or name.startswith("coverage."):
            origin = getattr(module, "__file__", None)
            if type(origin) is not str or not Path(origin).is_relative_to(root):
                raise ValueError("instrumentation collector module came from outside its view")


def _coverage_backend(view: Path, manifest_sha256: str) -> type[Coverage]:
    """Recapture the admitted view and select only its original collector package."""
    if not view.is_absolute() or ".." in view.parts:
        raise ValueError("invalid instrumentation code view")
    capsule = capture_capsule(view, manifest_sha256)
    instrumentation_selected(capsule, requested=True)
    site = view / "runtime/python/lib/python3.12/dist-packages"
    _require_backend_origins(site)
    sys.path.insert(0, str(site))
    module = importlib.import_module("coverage")
    _require_backend_origins(site)
    if getattr(module, "__version__", None) != "7.17.0":
        raise ValueError("instrumentation collector version differs")
    from coverage import Coverage

    return Coverage


def _file_limit() -> int:
    """Tighten this owned finite process's file limit without raising either bound."""
    import resource

    current = resource.getrlimit(resource.RLIMIT_FSIZE)
    maximum = min(
        MAX_RECORDING_BYTES,
        *(MAX_RECORDING_BYTES if value == resource.RLIM_INFINITY else value for value in current),
    )
    if maximum <= 0:
        raise ValueError("instrumentation process has no positive file budget")
    resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
    return maximum


def _save_output(directory: int, raw: bytes) -> os.stat_result:
    """Create one new regular dataset through the retained private directory."""
    descriptor = os.open(
        RECORDING_FILENAME,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=directory,
    )
    with os.fdopen(descriptor, "wb", buffering=0) as output:
        os.fchmod(output.fileno(), 0o600)
        if output.write(raw) != len(raw):
            raise OSError("instrumentation dataset write was incomplete")
        os.fsync(output.fileno())
        return os.fstat(output.fileno())


class CoverageRecording:
    """One process's collector and retained output custody.

    Notes
    -----
    Construct with prepare_recording in an owned isolated finite process.
    The fixed launcher owns the collector's public collect() context; call
    save only after that context has stopped collection.
    Recording observations are diagnostics, never role enrollment, root
    authority, protected-operation acceptance or whole-runtime qualification.
    """

    def __init__(
        self,
        collector: Coverage,
        directory: int,
        identity: tuple[int, int],
        role: str,
        uid: int,
        gid: int,
        manifest_sha256: str,
        maximum_bytes: int,
    ) -> None:
        """Own the admitted collector and duplicate directory until one terminal save."""
        self._collector = collector
        self._directory = directory
        self._identity = identity
        self._role = role
        self._uid = uid
        self._gid = gid
        self._manifest_sha256 = manifest_sha256
        self._maximum_bytes = maximum_bytes
        self._finished = False

    @property
    def collector(self) -> Coverage:
        """Return the admitted backend for the launcher's explicit collect context.

        Returns
        -------
        Coverage
            This process's fresh memory-only collector. It is not an external
            dataset reader and must never load child data.
        """
        return self._collector

    def save(self) -> dict[str, Any]:
        """Save once after collection, verifying the actual final credentials.

        Returns
        -------
        dict[str, Any]
            Actual file metadata and namespace-local PID diagnostics, not an
            independent parent observation.

        Raises
        ------
        PermissionError
            Unless all saved/effective/real UID-GID values and output ownership
            equal the independently enrolled target.
        ValueError, OSError
            On a repeated finish, changed directory, invalid memory data,
            existing destination or failed exclusive bounded write.

        Notes
        -----
        No child dataset is opened or parsed. Only this collector's own fresh
        memory database is serialized; output stays owned by its exact target
        principal until the parent independently seals it after quiescence.
        """
        if self._finished:
            raise ValueError("instrumentation recording is already terminal")
        self._finished = True
        try:
            self._require_output_identity()
            data = self._collector.get_data()
            try:
                raw = snapshot_memory_coverage(data, maximum_bytes=self._maximum_bytes)
            finally:
                data.close(force=True)
            info = _save_output(self._directory, raw)
            return {
                "role": self._role,
                "namespace_pid": os.getpid(),
                "manifest_sha256": self._manifest_sha256,
                "dataset_sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "uid": info.st_uid,
                "gid": info.st_gid,
                "device": info.st_dev,
                "inode": info.st_ino,
                "file_limit_bytes": self._maximum_bytes,
            }
        finally:
            os.close(self._directory)

    def _require_output_identity(self) -> None:
        """Bind the final writer credentials and still-private retained leaf."""
        if os.getresuid() != (self._uid,) * 3 or os.getresgid() != (self._gid,) * 3:
            raise PermissionError("instrumentation save did not reach its exact target principal")
        info = os.fstat(self._directory)
        if (
            (info.st_dev, info.st_ino) != self._identity
            or (info.st_uid, info.st_gid) != (self._uid, self._gid)
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise PermissionError("instrumentation retained output identity changed")


def prepare_recording(
    view: Path,
    manifest_sha256: str,
    *,
    role: str,
    output_descriptor: int,
    uid: int,
    gid: int,
) -> CoverageRecording:
    """Prepare an explicit memory collector in an isolated owned finite process.

    Parameters
    ----------
    view, manifest_sha256:
        Complete admitted readonly capsule view and independently retained digest.
        This factory recaptures it; caller must separately establish root custody.
    role:
        One of the six process roles in the captured profile.
    output_descriptor:
        Borrowed private empty output directory. A duplicate is owned until save.
    uid, gid:
        Independently enrolled output and final process identities.

    Returns
    -------
    CoverageRecording
        Memory-only collector whose activation boundary is owned by the launcher.
        Disk saving occurs only after its collect context has exited.

    Raises
    ------
    ValueError, OSError
        On unsupported ABI, unsafe output/view, foreign backend or file budget.
    PermissionError
        On an unsafe interpreter or output identity.

    Notes
    -----
    Requires -I-B-S, pins the complete profile, disables automatic data loading,
    saving and plugins, and tightens this finite process's RLIMIT_FSIZE. Does
    not create accounts, mounts or an operation grant. The launcher must use
    the returned collector's collect() context, then call save().
    """
    require_native_runtime_abi(
        sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1
    )
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise PermissionError("instrumentation requires isolated no-site no-bytecode flags")
    directory, identity = _owned_output(role, output_descriptor, uid, gid)
    try:
        backend = _coverage_backend(view, manifest_sha256)
        maximum = _file_limit()
        collector = backend(
            data_file=None,
            data_suffix=False,
            auto_data=False,
            branch=True,
            config_file=False,
            plugins=[],
            source_pkgs=["protected_runtime_probe", "synapse_channel"],
            context=role,
        )
        collector.set_option("run:core", "ctrace")
        return CoverageRecording(
            collector, directory, identity, role, uid, gid, manifest_sha256, maximum
        )
    except BaseException:
        os.close(directory)
        raise
