# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — isolated ordinary dataset combination worker
"""Read verified private working copies with the complete original collector API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

_ROLES = ("controller", "namespace-wrapper", "authority", "client", "operator", "writer")
_METADATA_MAX = 8 * 1024 * 1024
_DATASET_MAX = 16 * 1024 * 1024
_COMBINED_MAX = 128 * 1024 * 1024


def _ordinary_worker() -> None:
    """Require ordinary native isolated execution before any dataset parser import."""
    if (sys.platform, platform.machine(), sys.version_info[:2]) != ("linux", "x86_64", (3, 12)):
        raise OSError("recording analysis worker requires native Linux x86_64 CPython3.12")
    import resource

    if 0 in (*os.getresuid(), *os.getresgid()):
        raise PermissionError("recording analysis worker requires an ordinary nonroot user")
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise PermissionError(
            "recording analysis worker requires isolated no-site no-bytecode flags"
        )
    os.umask(0o077)
    memory = resource.getrlimit(resource.RLIMIT_AS)
    budget = min((1536 * 1024 * 1024, *(v for v in memory if v != resource.RLIM_INFINITY)))
    resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    maximum = min((_COMBINED_MAX, *(v for v in (soft, hard) if v != resource.RLIM_INFINITY)))
    resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))


def _private_bytes(path: Path, maximum: int) -> bytes:
    """Read one caller-owned bounded regular private copy without following its leaf."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) not in {0o400, 0o600}
            or not 0 < before.st_size <= maximum
        ):
            raise ValueError("analysis working copy is not private bounded regular input")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise ValueError("analysis working copy changed during capture")
        return raw
    finally:
        os.close(descriptor)


def _collector_member(directory: Path, row: object) -> str:
    """Verify one canonical private original member and return its exact relative name."""
    if type(row) is not dict or row.keys() != {"path", "sha256", "size_bytes"}:
        raise ValueError("analysis collector pin shape differs")
    name = row["path"]
    if (
        type(name) is not str
        or str(PurePosixPath(name)) != name
        or PurePosixPath(name).is_absolute()
        or ".." in PurePosixPath(name).parts
    ):
        raise ValueError("analysis collector member path differs")
    raw = _private_bytes(directory / name, _METADATA_MAX)
    if len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ValueError("analysis collector differs from its original verified bytes")
    return name


def _collector(directory: Path, rows: object) -> None:
    """Verify the complete private collector inventory before its first ordinary import."""
    if type(rows) is not list or not 0 < len(rows) <= 1024:
        raise ValueError("analysis collector inventory differs")
    names = [_collector_member(directory, row) for row in rows]
    expected = set(names)
    if len(expected) != len(names):
        raise ValueError("analysis collector members must be unique")
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("analysis collector contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != expected:
        raise ValueError("analysis collector complete tree differs")


def _mapped_source(filename: str, configuration: dict[str, Any]) -> str:
    """Map only the parent's byte-bound physical source aliases to current files."""
    for root in (configuration["code_view"], "/code"):
        for prefix, logical in (
            (root + "/app/synapse_channel/", "app/synapse_channel/"),
            (root + "/probe/protected_runtime_probe/", "probe/protected_runtime_probe/"),
            (
                root + "/runtime/python/lib/python3.12/dist-packages/synapse_channel/",
                "app/synapse_channel/",
            ),
        ):
            if filename.startswith(prefix):
                name = logical + filename[len(prefix) :]
                result = configuration["source_paths"].get(name)
                if type(result) is str:
                    return result
    raise ValueError("recording contains an unknown or unbound physical source alias")


def _source_summary(
    files: set[str],
    arcs: Callable[[str], list[tuple[int, int]] | None],
    contexts: set[str],
) -> dict[str, object]:
    """Distinguish registered files from actual executed arcs in the public data API."""
    registered = sorted(files)
    return {
        "registered_files": registered,
        "executed_files": [name for name in registered if arcs(name)],
        "contexts": sorted(contexts),
        "arc_count": sum(len(arcs(name) or ()) for name in registered),
    }


def _combine(directory: Path, configuration: dict[str, Any]) -> dict[str, object]:
    """Combine real role contexts through public CoverageData APIs over verified copies."""
    from coverage import CoverageData

    rows = configuration["datasets"]
    if type(rows) is not list or [row.get("role") for row in rows] != list(_ROLES):
        raise ValueError("analysis requires the exact six ordered producer roles")
    combined = CoverageData(basename=str(directory / "combined.sqlite"))
    try:
        for row in rows:
            role = row["role"]
            path = directory / "inputs" / (role + ".sqlite")
            raw = _private_bytes(path, _DATASET_MAX)
            if len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("analysis working dataset differs from its sealed bytes")
            data = CoverageData(basename=str(path))
            try:
                data.read()
                files = data.measured_files()
                if not data.has_arcs() or data.measured_contexts() != {role} or not files:
                    raise ValueError("recording lacks its exact role context or actual branch data")
                if not any(data.arcs(name) for name in files):
                    raise ValueError("recording contains no actual source arcs")
                for name in files:
                    _mapped_source(name, configuration)
                    if data.file_tracer(name):
                        raise ValueError("recording contains an unadmitted file tracer")
                combined.update(data, map_path=lambda name: _mapped_source(name, configuration))
            finally:
                data.close(force=True)
        combined.write()
        return _source_summary(
            combined.measured_files(), combined.arcs, combined.measured_contexts()
        )
    finally:
        combined.close(force=True)


def main(argv: list[str] | None = None) -> int:
    """Combine parent-verified inputs only in the fixed isolated ordinary worker.

    Parameters
    ----------
    argv:
        Exact private configuration path and independently retained metadata hash.

    Returns
    -------
    int
        Zero after a fresh combined dataset is written and every role is verified.

    Raises
    ------
    PermissionError, ValueError, OSError
        On unsafe execution or any byte/context/source disagreement. Child parser
        failures propagate; a failed output directory is never a successful result.
    """
    _ordinary_worker()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    arguments = parser.parse_args(argv)
    raw = _private_bytes(arguments.configuration, _METADATA_MAX)
    if hashlib.sha256(raw).hexdigest() != arguments.sha256:
        raise ValueError("analysis worker configuration digest differs")
    configuration = json.loads(raw)
    if (
        type(configuration) is not dict
        or configuration.keys()
        != {
            "schema",
            "collector",
            "datasets",
            "source_paths",
            "code_view",
        }
        or configuration["schema"] != "synapse.recording-analysis-inputs.v1"
    ):
        raise ValueError("analysis worker configuration shape differs")
    directory = arguments.configuration.parent
    if (directory / "combined.sqlite").exists():
        raise ValueError("analysis combined output must be fresh")
    _collector(directory / "collector", configuration["collector"])
    sys.path.insert(0, str(directory / "collector"))
    import coverage

    if coverage.__version__ != "7.17.0":
        raise ValueError("analysis collector version differs")
    result = _combine(directory, configuration)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
