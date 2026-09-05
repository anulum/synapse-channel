# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — independent Fleet mirror wire contract
"""Validate the opt-in version-one export without importing the Fleet package."""

from __future__ import annotations

import json
import math
import re
from typing import Any

MAX_MIRROR_BYTES = 4 * 1024 * 1024


class MirrorVersionError(ValueError):
    """The export uses an unsupported bridge version."""


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


def parse_mirror(raw: str) -> dict[str, Any]:
    """Validate and retain a complete advisory export.

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
        If JSON, bounds, identity or observation field types are invalid.
    """
    if len(raw.encode()) > MAX_MIRROR_BYTES:
        raise ValueError("mirror exceeds byte limit")
    try:
        doc = json.loads(raw, object_pairs_hook=_unique)
        _finite(doc)
    except (RecursionError, OverflowError) as exc:
        raise ValueError("mirror nesting or number exceeds limit") from exc
    if not isinstance(doc, dict) or set(doc) != {
        "version",
        "source_id",
        "exported_at",
        "advisory",
        "snapshot",
    }:
        raise ValueError("invalid mirror envelope")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise MirrorVersionError("unsupported mirror version")
    if (
        not isinstance(doc["source_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", doc["source_id"])
        or doc["advisory"] is not True
        or not _number(doc["exported_at"])
    ):
        raise ValueError("invalid mirror identity or time")
    snapshot = doc["snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "advisory",
        "generated_at",
        "peers",
        "tasks",
        "progress_notes",
    }:
        raise ValueError("invalid mirror snapshot")
    if (
        not isinstance(snapshot["advisory"], str)
        or not _number(snapshot["generated_at"])
        or not _number(snapshot["progress_notes"], integer=True)
    ):
        raise ValueError("invalid snapshot metadata")
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
