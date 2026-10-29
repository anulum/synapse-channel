# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — complete discovery scheduled by bounded historical durations
"""Balance current test modules without turning historical timings into an allowlist."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath


def validate_module_durations(value: object) -> dict[str, float]:
    """Validate finite nonnegative module durations as scheduling advice.

    Parameters
    ----------
    value : object
        Mapping from relative pytest module paths to observed elapsed seconds.

    Returns
    -------
    dict[str, float]
        Copied durations, bounded to one day per module.

    Raises
    ------
    ValueError
        If paths, numeric types or duration bounds are invalid.
    """
    if not isinstance(value, dict):
        raise ValueError("timing durations must be an object")
    result: dict[str, float] = {}
    for name, weight in value.items():
        if (
            not isinstance(name, str)
            or not name.endswith(".py")
            or "\\" in name
            or "\0" in name
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or type(weight) not in (int, float)
            or not 0 <= weight <= 86400
            or not math.isfinite(weight)
        ):
            raise ValueError("invalid module timing path or finite duration")
        result[name] = float(weight)
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON fields before a shared timing snapshot is used."""
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate timing snapshot field")
    return result


def load_shard_timings(path: Path, python: str) -> tuple[str, dict[str, float]]:
    """Read one immutable bounded timing snapshot shared by every worker.

    Parameters
    ----------
    path : Path
        Ordinary JSON file containing schema1 and per-interpreter profiles.
    python : str
        Interpreter major/minor whose observations should guide scheduling.

    Returns
    -------
    tuple[str, dict[str, float]]
        Exact snapshot SHA256 and this interpreter's validated historical weights.
        An absent interpreter profile yields empty weights and complete cold scheduling.

    Raises
    ------
    OSError
        If the supplied snapshot cannot be read.
    ValueError
        If its type, size, schema or any profile is malformed.
    """
    if path.is_symlink() or not path.is_file():
        raise ValueError("timing snapshot must be a bounded ordinary file")
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("timing snapshot exceeds its byte budget")
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "profiles"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or not isinstance(value["profiles"], dict)
    ):
        raise ValueError("invalid timing snapshot schema")
    profiles = {}
    for name, weights in value["profiles"].items():
        if re.fullmatch(r"[0-9]+\.[0-9]+", name) is None:
            raise ValueError("invalid timing interpreter profile")
        profiles[name] = validate_module_durations(weights)
    return hashlib.sha256(raw).hexdigest(), profiles.get(python, {})


def partition_test_modules(
    inventory: Sequence[str], count: int, durations: Mapping[str, float]
) -> tuple[tuple[str, ...], ...]:
    """Assign every currently collected test using deterministic longest-first modules.

    Parameters
    ----------
    inventory : sequence of str
        Complete unique pytest node IDs from current collection.
    count : int
        Bounded worker count between1 and128.
    durations : mapping of str to float
        Historical module weights. Removed modules are ignored; unknown modules
        use their current collected test count. Zero observations retain a positive weight.

    Returns
    -------
    tuple[tuple[str, ...], ...]
        Sorted disjoint node-ID partitions in worker-index order. Modules stay together.

    Raises
    ------
    ValueError
        On duplicate/empty node IDs, invalid worker counts or malformed durations.
    """
    if type(count) is not int or not 1 <= count <= 128:
        raise ValueError("invalid timing shard count")
    if any(not isinstance(node, str) or not node for node in inventory) or len(
        set(inventory)
    ) != len(inventory):
        raise ValueError("invalid timing test inventory")
    weights = validate_module_durations(dict(durations))
    modules: dict[str, list[str]] = {}
    for node in sorted(inventory):
        modules.setdefault(node.split("::", 1)[0], []).append(node)
    costs = {
        name: max(weights.get(name, float(len(nodes))), 1e-9) for name, nodes in modules.items()
    }
    workers: list[list[str]] = [[] for _ in range(count)]
    queue = [(0.0, index) for index in range(count)]
    heapq.heapify(queue)
    for name in sorted(modules, key=lambda name: (-costs[name], name)):
        load, index = heapq.heappop(queue)
        workers[index].extend(modules[name])
        heapq.heappush(queue, (load + costs[name], index))
    return tuple(tuple(sorted(nodes)) for nodes in workers)
