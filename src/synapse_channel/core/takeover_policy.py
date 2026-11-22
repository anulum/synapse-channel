# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — takeover cooldown, oscillation and quarantine
"""The decision that spaces and then stops a name being taken over repeatedly."""

from __future__ import annotations

from collections.abc import Callable

from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.numeric_coercion import safe_float, safe_int


class TakeoverPolicy:
    """Own the takeover durations, the stamp maps, and the verdict.

    ``classify`` is the only writer of those maps. A caller passes ``now`` from
    its own clock, once per attempt, and then only acts on the verdict.
    """

    def __init__(
        self,
        *,
        cooldown: float,
        window: float,
        threshold: int,
        quarantine: float,
        counters: HubCounters,
        clock: Callable[[], float],
    ) -> None:
        self._cooldown = max(safe_float(cooldown, default=0.0), 0.0)
        self._window = max(safe_float(window, default=30.0), 0.0)
        self._threshold = safe_int(threshold, default=5, min_value=2)
        self._quarantine = max(safe_float(quarantine, default=60.0), 0.0)
        self._counters = counters
        self._clock = clock
        self._last_takeover: dict[str, float] = {}
        self._takeover_times: dict[str, list[float]] = {}
        self._quarantine_until: dict[str, float] = {}

    @property
    def cooldown(self) -> float:
        """Minimum gap between accepted takeovers of one name."""
        return self._cooldown

    @property
    def window(self) -> float:
        """How far back oscillation stamps still count."""
        return self._window

    @property
    def threshold(self) -> int:
        """Accepted takeovers inside the window that open a quarantine."""
        return self._threshold

    @property
    def quarantine(self) -> float:
        """How long a quarantine pins the current owner."""
        return self._quarantine

    @property
    def counters(self) -> HubCounters:
        """The hub counters this policy increments."""
        return self._counters

    @property
    def clock(self) -> Callable[[], float]:
        """The clock the registry reads once before ``classify``."""
        return self._clock

    def classify(self, sender: str, now: float) -> str:
        """Decide one takeover attempt and record only what that verdict owns.

        An active quarantine returns ``quarantine_active``. A lapsed quarantine
        forgets the until-time and the stamp list, and leaves the last accepted
        time in place. A cooldown returns ``cooldown`` and appends no stamp.
        Otherwise the window is pruned, ``now`` is appended, and the threshold
        either opens ``quarantine_enter`` or records ``accept``.
        """
        until = self._quarantine_until.get(sender)
        if until is not None:
            if now < until:
                return "quarantine_active"
            self._quarantine_until.pop(sender, None)
            self._takeover_times.pop(sender, None)
        last = self._last_takeover.get(sender)
        if last is not None and now - last < self._cooldown:
            return "cooldown"
        cutoff = now - self._window
        recent = [stamp for stamp in self._takeover_times.get(sender, []) if stamp >= cutoff]
        recent.append(now)
        if len(recent) >= self._threshold:
            self._quarantine_until[sender] = now + self._quarantine
            self._takeover_times.pop(sender, None)
            self._counters.takeover_quarantines += 1
            return "quarantine_enter"
        self._takeover_times[sender] = recent
        self._counters.takeovers += 1
        self._last_takeover[sender] = now
        return "accept"

    def stamp_count(self, sender: str) -> int:
        """Return how many oscillation stamps ``sender`` still holds."""
        return len(self._takeover_times.get(sender, []))
