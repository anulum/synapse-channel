# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — independent Fleet mirror wire contract
"""Validate the opt-in version-one export without importing the Fleet package.

Version-one erratum (2026-11-22)
--------------------------------
Daemon-written version-one exports carry an optional ``writer_generation``
object in both the outer envelope and the inner ``snapshot``. When present,
``writer_generation`` must appear at both levels with equal values and exact
keys ``{"version", "device", "inode", "marker"}`` where ``version`` is integer
``1``, ``device`` and ``inode`` are non-negative integers up to ``2**64 - 1``,
and ``marker`` is 32 lower-case hexadecimal characters. Every other extra key
remains refused at both levels, and version one is now closed: any subsequent
mirror field requires a version bump.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from synapse_channel.core.errors import SynapseError

MIRROR_CONTRACT_VERSION = 1
"""Supported advisory Fleet mirror export contract version."""

MAX_MIRROR_BYTES = 4 * 1024 * 1024
_MAX_UNSIGNED_INT64 = 2**64 - 1
_ENVELOPE_REQUIRED = frozenset({"version", "source_id", "exported_at", "advisory", "snapshot"})
_SNAPSHOT_REQUIRED = frozenset({"advisory", "generated_at", "peers", "tasks", "progress_notes"})
_WRITER_GENERATION_KEYS = frozenset({"version", "device", "inode", "marker"})
_WRITER_GENERATION_MARKER_RE = re.compile(r"[0-9a-f]{32}")


class MirrorVersionError(SynapseError, ValueError):
    """The export uses an unsupported bridge version."""

    code = "mirror_version"


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate mirror key")
        result[key] = value
    return result


def _number(value: object, *, integer: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if isinstance(value, int):
        return 0 <= value <= 2**63 - 1
    return not integer and math.isfinite(value) and value >= 0


def _finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite mirror number")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    if isinstance(value, list):
        for item in value:
            _finite(item)


def _mapping_with_optional_generation(
    value: object, required: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"invalid mirror {label}")
    keys = set(value)
    if keys != required and keys != (required | {"writer_generation"}):
        raise ValueError(f"invalid mirror {label}")
    return value


def _writer_generation(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _WRITER_GENERATION_KEYS:
        raise ValueError("invalid mirror writer generation")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("invalid mirror writer generation version")
    for field_name in ("device", "inode"):
        field_value = value[field_name]
        if type(field_value) is not int or not (0 <= field_value <= _MAX_UNSIGNED_INT64):
            raise ValueError(f"invalid mirror writer generation {field_name}")
    marker = value["marker"]
    if not isinstance(marker, str) or _WRITER_GENERATION_MARKER_RE.fullmatch(marker) is None:
        raise ValueError("invalid mirror writer generation marker")
    return value


def _validate_writer_generation_pair(doc: dict[str, Any], snapshot: dict[str, Any]) -> None:
    envelope_has = "writer_generation" in doc
    snapshot_has = "writer_generation" in snapshot
    if envelope_has != snapshot_has:
        raise ValueError("mirror writer generation must appear in both envelope and snapshot")
    if envelope_has:
        envelope_gen = _writer_generation(doc["writer_generation"])
        snapshot_gen = _writer_generation(snapshot["writer_generation"])
        if envelope_gen != snapshot_gen:
            raise ValueError("mirror writer generation disagrees between envelope and snapshot")


def _validate_rows(snapshot: dict[str, Any]) -> None:
    for key, identifier, limit in (("peers", "peer_id", 4096), ("tasks", "task_id", 10000)):
        rows = snapshot[key]
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("invalid mirror row count")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get(identifier), str):
                raise ValueError("invalid mirror row")
            name = row[identifier]
            if not name or name in seen:
                raise ValueError("duplicate or empty mirror row identity")
            seen.add(name)
            if key == "peers":
                _peer(row)
            else:
                _task(row)


def parse_mirror(raw: str) -> dict[str, Any]:
    """Validate and retain a complete advisory export.

    Version-one erratum (2026-11-22): an optional ``writer_generation`` object
    is accepted when present with identical validated values in both the
    envelope and ``snapshot``. Version one accepts no other extra keys.

    Parameters
    ----------
    raw : str
        Owner-only UTF-8 JSON, at most four MiB.

    Returns
    -------
    dict
        Original versioned envelope and complete provenance/conflict evidence.

    Raises
    ------
    MirrorVersionError
        If version is not the supported integer one.
    ValueError
        If JSON, bounds, identity, writer generation or observation fields are invalid.
    """
    if len(raw.encode()) > MAX_MIRROR_BYTES:
        raise ValueError("mirror exceeds byte limit")
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique)
        _finite(parsed)
    except (RecursionError, OverflowError) as exc:
        raise ValueError("mirror nesting or number exceeds limit") from exc
    doc = _mapping_with_optional_generation(parsed, _ENVELOPE_REQUIRED, "envelope")
    if type(doc["version"]) is not int or doc["version"] != MIRROR_CONTRACT_VERSION:
        raise MirrorVersionError("unsupported mirror version")
    if (
        not isinstance(doc["source_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", doc["source_id"])
        or doc["advisory"] is not True
        or not _number(doc["exported_at"])
    ):
        raise ValueError("invalid mirror identity or time")
    snapshot = _mapping_with_optional_generation(doc["snapshot"], _SNAPSHOT_REQUIRED, "snapshot")
    _validate_writer_generation_pair(doc, snapshot)
    if (
        not isinstance(snapshot["advisory"], str)
        or not _number(snapshot["generated_at"])
        or not _number(snapshot["progress_notes"], integer=True)
    ):
        raise ValueError("invalid snapshot metadata")
    _validate_rows(snapshot)
    return doc


def _peer(row: dict[str, Any]) -> None:
    required = {
        "peer_id",
        "cursor",
        "events",
        "last_success_at",
        "consecutive_failures",
        "status_written_at",
        "caught_up",
        "budget_exhausted_reason",
    }
    if set(row) != required:
        raise ValueError("invalid peer fields")
    for key in ("cursor", "events", "consecutive_failures"):
        if key == "consecutive_failures" and row[key] is None:
            continue
        if not _number(row[key], integer=True):
            raise ValueError("invalid peer count")
    for key in ("last_success_at", "status_written_at"):
        if row[key] is not None and not _number(row[key]):
            raise ValueError("invalid peer timestamp")
    if row["caught_up"] is not None and type(row["caught_up"]) is not bool:
        raise ValueError("invalid drain completion")
    if row["budget_exhausted_reason"] not in (None, "pages", "events", "wall_time"):
        raise ValueError("invalid drain limit")


def _task(row: dict[str, Any]) -> None:
    if set(row) != {
        "task_id",
        "status",
        "title",
        "claimed_by",
        "claim_hub",
        "board_provenance",
        "board_conflict",
    }:
        raise ValueError("invalid task fields")
    if any(not isinstance(row[key], str) for key in ("status", "title")):
        raise ValueError("invalid task text")
    for key in ("claimed_by", "claim_hub"):
        if row[key] is not None and not isinstance(row[key], str):
            raise ValueError("invalid claim text")
    for key in ("board_provenance", "board_conflict"):
        if row[key] is not None and not isinstance(row[key], dict):
            raise ValueError("invalid board evidence")
