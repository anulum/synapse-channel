# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — retained qualification report publication
"""Publish finite component results and record their exact retained storage."""

from __future__ import annotations

import json
import os
import re
import sys
from contextlib import ExitStack
from pathlib import Path

from . import require_native_linux
from .storage import (
    bind_readonly,
    describe_retained_attempt,
    measure_backing_tree,
    open_directory,
    unbind,
)

_REPORTS = Path("/opt/synapse-channel/protected-runtime-qualification-evidence")


def _encode_retained_report(
    backing: int, report: dict[str, object], retention: dict[str, object]
) -> tuple[bytes, int | None]:
    """Include final report bytes in the logical retained-tree size without recursion."""
    report["retention"] = retention
    try:
        measured = measure_backing_tree(backing)
    except (ValueError, OSError) as error:
        report["component_checks_passed"] = False
        retention["size"] = {
            "status": "unavailable",
            "directory_size_bytes": os.fstat(backing).st_size,
            "error": f"{type(error).__name__}: {error}",
        }
        raw = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "ascii"
        )
        return raw, None
    size_record: dict[str, int | str] = {
        **measured,
        "status": "complete",
        "meaning": "logical file and directory bytes, including this report",
        "allocated_bytes_scope": "before this report content is written",
    }
    retention["size"] = size_record
    size_record["size_bytes"] = 0
    raw = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    base = measured["size_bytes"] + len(raw) - 1
    # Adding the decimal width can cross at most one power-of-ten boundary.
    initial = base + len(str(base))
    size = base + len(str(initial))
    size_record["size_bytes"] = size
    raw = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    return raw, size


def _retire_failed_report(
    backing: int,
    source: int,
    target: Path,
    placeholder: tuple[int, int],
    mounted: bool,
    observed_bind: object,
    code_view_cleanup: object,
    instrumentation: object = None,
) -> None:
    """Detach a failed publication and explicitly retain its actual recovery backing."""
    removed = False
    try:
        if mounted:
            original = os.fstat(source)
            unbind(target, expected_identity=(original.st_dev, original.st_ino))
        current = target.lstat()
        if (current.st_dev, current.st_ino) != placeholder:
            raise ValueError("failed evidence placeholder identity changed")
        target.unlink()
        removed = True
    finally:
        root = os.fstat(backing)
        info = os.fstat(source)
        failure: dict[str, object] = {
            "schema": "synapse-prerequisite-retention-failure.v1",
            "run_id": target.stem,
            "evidence_bind": observed_bind,
            "code_view_cleanup": code_view_cleanup,
            "evidence_bind_remaining": False if removed else "unknown; owner inspection required",
            "target": str(target),
            "source": os.readlink(f"/proc/self/fd/{source}"),
            "report_device": info.st_dev,
            "report_inode": info.st_ino,
            "report_size_bytes": info.st_size,
            "backing": {
                "path": os.readlink(f"/proc/self/fd/{backing}"),
                "device": root.st_dev,
                "inode": root.st_ino,
                "directory_size_bytes": root.st_size,
            },
            "retention_reason": "publication failure; retained for owner recovery",
            "whole_runtime_accepted": False,
        }
        if instrumentation is not None:
            failure["instrumentation"] = instrumentation
        print(
            json.dumps(failure),
            file=sys.stderr,
            flush=True,
        )


def publish_probe_result(backing: int, run_id: str, report: dict[str, object]) -> Path:
    """Publish one completed finite result with exact retained storage identities.

    Parameters
    ----------
    backing:
        Borrowed fresh root-owned backing directory after campaign cleanup.
    run_id:
        Fresh bootstrap-selected run identifier shared by only this attempt.
    report:
        Mutable component result. Retention observations are added in place;
        an unavailable tree measurement preserves an explicit failed result.

    Returns
    -------
    Path
        Readonly evidence bind after the complete report writer is closed.

    Raises
    ------
    PermissionError, ValueError, OSError
        On absent native root context, changed identities or failed publication.

    Notes
    -----
    The caller owns actor quiescence and code-view retirement. This function
    grants no operation or deletion. On publication failure, detach the exact
    newly created bind and retain backing with an operator recovery record.
    """
    require_native_linux(sys.platform, operation="qualification report publication")
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PermissionError("qualification report publication requires native root context")
    if re.fullmatch(r"run-[0-9a-f]{16}", run_id) is None:
        raise ValueError("invalid qualification report run identifier")
    with ExitStack() as handles:
        descriptor = os.open(
            "report.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
            dir_fd=backing,
        )
        writer = handles.enter_context(os.fdopen(descriptor, "wb", buffering=0))
        source = os.open("report.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=backing)
        handles.callback(os.close, source)
        parent = open_directory(_REPORTS, trusted=True, create=True)
        handles.callback(os.close, parent)
        target = _REPORTS / (run_id + ".json")
        placeholder = os.open(
            target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444, dir_fd=parent
        )
        placeholder_info = os.fstat(placeholder)
        os.close(placeholder)
        mounted = False
        retention: dict[str, object] | None = None
        try:
            bind_readonly(source, target, executable=False)
            mounted = True
            retention = describe_retained_attempt(backing, source, target)
            raw, size = _encode_retained_report(backing, report, retention)
            if writer.write(raw) != len(raw):
                raise OSError("prerequisite report write incomplete")
            os.fsync(writer.fileno())
            if size is not None and measure_backing_tree(backing)["size_bytes"] != size:
                raise ValueError("retained backing size changed during report publication")
            writer.close()
            os.fchmod(source, 0o444)
            return target
        except BaseException:
            _retire_failed_report(
                backing,
                source,
                target,
                (placeholder_info.st_dev, placeholder_info.st_ino),
                mounted,
                None if retention is None else retention.get("evidence_bind"),
                report.get("code_view_cleanup"),
                report.get("instrumentation"),
            )
            raise
