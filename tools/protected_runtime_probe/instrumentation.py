# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — explicit instrumentation profile and exact source binding
"""Bind collector bytes and source aliases without starting a collector."""

from __future__ import annotations

import errno
import hashlib
import json
import signal
import sys
from collections.abc import Mapping
from typing import Any

from .capsule import CapturedCapsule, FilePin
from .instrumentation_artifacts import (
    InstrumentationWheel,
    decode_instrumentation_wheel,
    instrumentation_runtime_content,
)
from .native_provenance import NativeWheelArtifact
from .runtime import captured_runtime_selected

CONFIGURATION = "runtime/config/instrumentation.json"
_RUNTIME_SITE = "runtime/python/lib/python3.12/dist-packages/"
_LOCK = "probe/protected_runtime_probe/native-dependencies.lock.json"
#: Exact process roles enrolled by the finite instrumentation profile.
RECORDING_ROLES = ("controller", "namespace-wrapper", "authority", "client", "operator", "writer")
_SCHEMA = "synapse.protected-runtime-instrumentation.v1"
_UNMEASURED = (
    "initial-gate-and-sealed-bootstrap",
    "readonly-view-recapture",
    "collector-admission-and-startup",
    "dataset-sealing-and-report-publication",
)


_FILE_LIMIT_ERROR_EVENT = '{"event":"instrumentation-file-limit-error","errno":27}'


def emit_instrumentation_file_limit_error(error: OSError) -> None:
    """Report an actual EFBIG as a measurement artifact on the child's error stream.

    Parameters
    ----------
    error:
        Actual OS exception from the instrumented process or its terminal save.

    Notes
    -----
    This is a negative diagnostic. It grants no execution or successful product
    result; the original exception must still propagate to the supervisor.
    """
    if error.errno == errno.EFBIG:
        print(_FILE_LIMIT_ERROR_EVENT, file=sys.stderr, flush=True)


def instrumentation_file_limit_failure(
    *, error: OSError | None = None, returncode: int | None = None, stderr: str = ""
) -> dict[str, object] | None:
    """Grade observed file-limit failures as instrumentation ERROR, never product evidence.

    Parameters
    ----------
    error:
        Actual controller OS exception, when available.
    returncode, stderr:
        Actual retained instrumented child outcome and bounded error stream.

    Returns
    -------
    dict[str, object] or None
        Negative artifact grading for EFBIG or SIGXFSZ; no grade otherwise.
        The caller must separately establish the instrumented profile.
    """
    native_signal = getattr(signal, "SIGXFSZ", None)
    signal_number = native_signal if isinstance(native_signal, int) else None
    killed = signal_number is not None and returncode == -signal_number
    too_large = (error is not None and error.errno == errno.EFBIG) or (
        _FILE_LIMIT_ERROR_EVENT in stderr.splitlines()
    )
    if not killed and not too_large:
        return None
    return {
        "grade": "ERROR",
        "origin": "instrumentation-artifact",
        "product_result": False,
        "errno": errno.EFBIG if too_large else None,
        "signal": signal_number if killed else None,
    }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _pins(content: Mapping[str, bytes]) -> list[dict[str, str | int]]:
    return [
        {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        for name, raw in sorted(content.items())
    ]


def _sources(content: Mapping[str, bytes]) -> dict[str, bytes]:
    sources = {
        name: raw
        for name, raw in content.items()
        if name.endswith(".py")
        and name.startswith(("app/synapse_channel/", "probe/protected_runtime_probe/"))
    }
    if (
        not {
            "app/synapse_channel/core/protected_write_kernel_boundary.py",
            "probe/protected_runtime_probe/controller.py",
        }
        <= sources.keys()
    ):
        raise ValueError("instrumentation source scope is incomplete")
    for name, raw in sources.items():
        if name.startswith("app/") and content.get(_RUNTIME_SITE + name[4:]) != raw:
            raise ValueError("instrumentation runtime source alias differs")
    return sources


def _collector_payload(name: str) -> bool:
    return (
        name == CONFIGURATION
        or name.startswith("runtime/artifacts/coverage-")
        or name.startswith(_RUNTIME_SITE + "coverage/")
        or name.startswith(_RUNTIME_SITE + "coverage-")
        or name == _RUNTIME_SITE + "a1_coverage.pth"
    )


def require_production_payload(content: Mapping[str, bytes]) -> None:
    """Refuse any instrumentation configuration, distribution or original archive.

    Parameters
    ----------
    content:
        Complete detached runtime byte inventory.

    Raises
    ------
    ValueError
        When any complete or partial instrumentation payload is present.

    Notes
    -----
    Performs no imports, filesystem operations or collector startup.
    """
    if any(_collector_payload(name) for name in content):
        raise ValueError("production runtime refuses instrumentation payloads")


def build_instrumentation_content(
    content: Mapping[str, bytes], wheel: InstrumentationWheel
) -> dict[str, bytes]:
    """Prepare a detached explicit profile bound to complete admitted wheel bytes.

    Parameters
    ----------
    content:
        Captured application, probe and runtime bytes before instrumentation.
    wheel:
        Complete wheel observation from the source-controlled collector lock.

    Returns
    -------
    dict[str, bytes]
        Original content, complete collector and canonical hash-bound configuration.

    Notes
    -----
    Preparation does not authorise execution or collect data. The operator
    command must separately select the profile after full capsule recapture.
    """
    require_production_payload(content)
    collector = instrumentation_runtime_content(wheel)
    sources = _sources(content)
    result = dict(content)
    result.update(collector)
    result[CONFIGURATION] = _canonical(
        {
            "schema": _SCHEMA,
            "collector_version": "7.17.0",
            "collector": _pins(collector),
            "sources": _pins(sources),
            "recorders": list(RECORDING_ROLES),
            "start_after": "readonly-capsule-recapture",
            "stop_before": "dataset-sealing-and-report-publication",
            "unmeasured": list(_UNMEASURED),
            "writer_extra_root": "/instrumentation-data",
        }
    )
    return result


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate instrumentation configuration key")
        result[key] = value
    return result


def _verify_collector(content: Mapping[str, bytes], rows: object) -> None:
    if type(rows) is not list or not 1 <= len(rows) <= 1024:
        raise ValueError("instrumentation collector inventory is unbounded or malformed")
    names = []
    for row in rows:
        if type(row) is not dict or row.keys() != {"path", "sha256", "size_bytes"}:
            raise ValueError("instrumentation collector pin shape differs")
        pin = FilePin(row["path"], row["sha256"], row["size_bytes"])
        if not _collector_payload(pin.path) or pin.path == CONFIGURATION:
            raise ValueError("instrumentation collector pin scope differs")
        raw = content.get(pin.path)
        if (
            raw is None
            or len(raw) != pin.size_bytes
            or hashlib.sha256(raw).hexdigest() != pin.sha256
        ):
            raise ValueError("instrumentation collector bytes differ")
        names.append(pin.path)
    if names != sorted(set(names)):
        raise ValueError("instrumentation collector pins must be unique and sorted")
    actual = {name for name in content if _collector_payload(name) and name != CONFIGURATION}
    if actual != set(names):
        raise ValueError("instrumentation collector payload inventory differs")
    _verify_original_collector(content, names)


def _verify_original_collector(content: Mapping[str, bytes], names: list[str]) -> None:
    """Bind the complete member tree to the original source-locked archive."""
    lock = json.loads(content[_LOCK])
    artifact = lock["coverage"]
    archive = "runtime/artifacts/" + artifact["filename"]
    raw = content.get(archive)
    if (
        artifact["version"] != "7.17.0"
        or raw is None
        or len(raw) != artifact["size_bytes"]
        or hashlib.sha256(raw).hexdigest() != artifact["sha256"]
        or _RUNTIME_SITE + "coverage/tracer.cpython-312-x86_64-linux-gnu.so" not in names
        or _RUNTIME_SITE + "coverage/__init__.py" not in names
    ):
        raise ValueError("instrumentation collector differs from source-controlled lock")
    original = decode_instrumentation_wheel(
        NativeWheelArtifact(artifact["filename"], artifact["sha256"], raw)
    )
    complete = instrumentation_runtime_content(original)
    if complete != {name: content[name] for name in names}:
        raise ValueError("instrumentation collector differs from complete original wheel")


def instrumentation_selected(capsule: CapturedCapsule, *, requested: bool) -> bool:
    """Require exact agreement between the operator selector and captured profile.

    Parameters
    ----------
    capsule:
        Already captured complete byte inventory, not an execution grant.
    requested:
        Exact boolean selected by the independently reviewed operator command.

    Returns
    -------
    bool
        True only for a complete explicit instrumentation profile.

    Raises
    ------
    ValueError
        On selector disagreement, changed boundaries, source aliases or wheel bytes.

    Notes
    -----
    Production refuses every collector payload. No collector module is imported
    or started. Trusted captured archive/configuration bytes may be decoded;
    child-written datasets are never accepted by this admission surface.
    """
    if type(requested) is not bool:
        raise ValueError("invalid instrumentation profile selector")
    if not requested:
        require_production_payload(capsule.content)
        return False
    if not captured_runtime_selected(capsule) or CONFIGURATION not in capsule.content:
        raise ValueError(
            "instrumentation requires an explicitly captured runtime and configuration"
        )
    record = _instrumentation_configuration(capsule.content[CONFIGURATION])
    if record["sources"] != _pins(_sources(capsule.content)):
        raise ValueError("instrumentation source pins differ")
    _verify_collector(capsule.content, record["collector"])
    return True


def _instrumentation_configuration(raw: bytes) -> dict[str, Any]:
    """Decode only the canonical fixed collection boundaries."""
    if not 0 < len(raw) <= 2 * 1024 * 1024:
        raise ValueError("instrumentation configuration byte bound exceeded")
    record = json.loads(raw, object_pairs_hook=_closed_object)
    if type(record) is not dict or record.keys() != {
        "schema",
        "collector_version",
        "collector",
        "sources",
        "recorders",
        "start_after",
        "stop_before",
        "unmeasured",
        "writer_extra_root",
    }:
        raise ValueError("instrumentation configuration shape differs")
    if raw != _canonical(record):
        raise ValueError("instrumentation configuration must be canonical")
    if (
        record["schema"] != _SCHEMA
        or record["collector_version"] != "7.17.0"
        or record["recorders"] != list(RECORDING_ROLES)
        or record["start_after"] != "readonly-capsule-recapture"
        or record["stop_before"] != "dataset-sealing-and-report-publication"
        or record["unmeasured"] != list(_UNMEASURED)
        or record["writer_extra_root"] != "/instrumentation-data"
    ):
        raise ValueError("instrumentation execution boundary differs")
    return record
