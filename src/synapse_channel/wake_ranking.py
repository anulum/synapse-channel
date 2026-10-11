# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — order of wake adapters by the time they were measured to take
"""Order a seat's wake adapters by how fast each was measured to be.

For every accepted wake the caller records the time from the hub's event to
the adapter's acceptance. Once an adapter has enough samples for a seat, its
median counts. The order is then:

* adapters with enough samples are sorted among themselves by median, the
  fastest first, and take the places that measured adapters hold in the
  configured order;
* an adapter without enough samples keeps its configured place.

So the configuration decides until there is evidence, an unmeasured adapter
is neither pushed to the end (where it would never be measured) nor to the
front, and a tie keeps the configured order.

The samples of a seat are kept in ``<seat key>/ranking.json`` beside its
wake marks, the newest :data:`KEEP` per adapter, written under the seat's
lock by a rename. A file that cannot be read counts as no samples.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import statistics
from collections.abc import Sequence
from pathlib import Path

from synapse_channel.wake_adapters import KIND_PATTERN
from synapse_channel.wake_marks import WakeMarkError, seat_key, seat_lock

KEEP = 50
"""Newest samples kept per seat and adapter."""

DEFAULT_MIN_SAMPLES = 5
"""Samples an adapter needs before its median counts."""

_FILE = "ranking.json"


def _path(directory: Path, seat: str) -> Path:
    return directory / seat_key(seat) / _FILE


def samples(directory: Path, seat: str) -> dict[str, tuple[float, ...]]:
    """Return the recorded seconds per adapter; nothing readable gives nothing."""
    try:
        data = json.loads(_path(directory, seat).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    found = {}
    for adapter, values in data.items():
        if KIND_PATTERN.fullmatch(str(adapter)) is None or not isinstance(values, list):
            continue
        kept = tuple(
            float(value)
            for value in values
            if isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )
        if kept:
            found[str(adapter)] = kept
    return found


def record(directory: Path, seat: str, adapter: str, seconds: float) -> None:
    """Add one measured time from the hub's event to an adapter's acceptance.

    Raises
    ------
    WakeMarkError
        The adapter's kind or the time is not usable.
    """
    if KIND_PATTERN.fullmatch(adapter) is None:
        raise WakeMarkError("an adapter kind is lower-case letters, digits and underscores")
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        raise WakeMarkError("a measured time is a number of seconds")
    if not math.isfinite(seconds) or seconds < 0:
        raise WakeMarkError("a measured time is finite and not negative")
    with seat_lock(directory, seat):
        current = {name: list(values) for name, values in samples(directory, seat).items()}
        current.setdefault(adapter, []).append(float(seconds))
        current[adapter] = current[adapter][-KEEP:]
        path = _path(directory, seat)
        temporary = path.parent / f".tmp-{os.getpid()}-{secrets.token_hex(6)}"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(current, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def medians(
    directory: Path, seat: str, *, min_samples: int = DEFAULT_MIN_SAMPLES
) -> dict[str, float]:
    """Return the median seconds of every adapter that has enough samples."""
    return {
        adapter: statistics.median(values)
        for adapter, values in samples(directory, seat).items()
        if len(values) >= max(min_samples, 1)
    }


def order(
    directory: Path,
    seat: str,
    configured: Sequence[str],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> tuple[str, ...]:
    """Return the configured adapters in the order to try them for a seat."""
    measured = medians(directory, seat, min_samples=min_samples)
    ranked = sorted(
        (name for name in configured if name in measured),
        key=lambda name: (measured[name], configured.index(name)),
    )
    fastest = iter(ranked)
    return tuple(next(fastest) if name in measured else name for name in configured)
