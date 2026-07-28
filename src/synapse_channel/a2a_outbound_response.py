# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded A2A responses and owner-only receipt writes
"""Shared fail-closed boundaries for outbound A2A clients."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.http_response import read_bounded
from synapse_channel.core.protocol import loads_bounded

A2A_MAX_RESPONSE_BYTES = 1_048_576
"""Maximum bytes buffered from one outbound A2A HTTP response."""

A2A_MAX_JSON_MEMBERS = 4_096
"""Maximum cumulative object members and array elements in one response."""

_EMPTY_RESPONSE = "empty"
_NON_JSON_RESPONSE = "non_json"
_JSON_ARRAY_RESPONSE = "json_array"
_JSON_SCALAR_RESPONSE = "json_scalar"


class A2AResponseShapeError(SynapseError, ValueError):
    """Raised when decoded A2A JSON exceeds the cumulative shape ceiling."""

    code = "a2a_response_shape"


class A2AReceiptWriteError(SynapseError, RuntimeError):
    """Raised when an A2A receipt cannot be written atomically."""

    code = "a2a_receipt_write"


def _validate_json_shape(value: Any, *, max_members: int = A2A_MAX_JSON_MEMBERS) -> None:
    """Refuse a decoded JSON value with more than ``max_members`` children."""
    pending = [value]
    member_count = 0
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            member_count += len(current)
            pending.extend(current.values())
        elif isinstance(current, list):
            member_count += len(current)
            pending.extend(current)
        if member_count > max_members:
            raise A2AResponseShapeError(
                f"A2A response exceeds the {max_members}-member JSON shape limit"
            )


def read_a2a_response(
    response: Any,
    *,
    purpose: str,
    max_bytes: int = A2A_MAX_RESPONSE_BYTES,
    max_members: int = A2A_MAX_JSON_MEMBERS,
) -> tuple[dict[str, Any] | None, str]:
    """Read and decode one A2A response without retaining hostile text.

    Returns the decoded object and ``"object"`` for a valid JSON object.
    Empty, malformed/binary, array, and scalar bodies are reduced to fixed
    response-kind strings, so a caller can report the failure without echoing
    peer-controlled bytes.
    """
    raw = read_bounded(response, limit=max_bytes, purpose=f"{purpose} body")
    if not raw:
        return None, _EMPTY_RESPONSE
    try:
        decoded = loads_bounded(raw)
    except json.JSONDecodeError:
        return None, _NON_JSON_RESPONSE
    _validate_json_shape(decoded, max_members=max_members)
    if isinstance(decoded, dict):
        return cast(dict[str, Any], decoded), "object"
    if isinstance(decoded, list):
        return None, _JSON_ARRAY_RESPONSE
    return None, _JSON_SCALAR_RESPONSE


def _fsync_parent(path: Path) -> None:
    """Best-effort fsync for the directory containing ``path``."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with suppress(OSError):
        fd = os.open(path.parent, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def serialize_a2a_receipt(receipt: Mapping[str, Any]) -> str:
    """Serialize one receipt as strict RFC 8259 JSON without non-finite values."""
    try:
        return (
            json.dumps(
                dict(receipt),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        )
    except Exception as exc:
        raise A2AReceiptWriteError("A2A receipt serialization failed") from exc


def write_a2a_receipt(path: str | Path, receipt: Mapping[str, Any]) -> Path:
    """Atomically replace ``path`` with an owner-only A2A receipt."""
    target = Path(path)
    fd: int | None = None
    temporary_name: str | None = None
    try:
        document = serialize_a2a_receipt(receipt)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        handle = os.fdopen(fd, "w", encoding="utf-8")
        fd = None
        with handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(Path(temporary_name), target)
        temporary_name = None
        _fsync_parent(target)
    except BaseException as exc:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
        if temporary_name is not None:
            with suppress(OSError):
                Path(temporary_name).unlink(missing_ok=True)
        if isinstance(exc, A2AReceiptWriteError):
            raise
        if isinstance(exc, Exception):
            raise A2AReceiptWriteError("A2A receipt write failed") from exc
        raise
    return target
