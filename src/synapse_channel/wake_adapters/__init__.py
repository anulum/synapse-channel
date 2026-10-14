# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the contract between the wake relay and one way of waking a seat
"""What a wake adapter is, and what it may say about one wake.

A wake is routing metadata. It tells a session that its inbox has something;
the session reads the message from the hub. An adapter is one way of telling
it: typing a fixed prompt into an idle composer, queueing it through a
vendor's own daemon, or a waiter whose exit the client's harness reports.

An adapter answers with one of three states and never with more:

``accepted``
    The transport took the wake. ``proves`` says what exactly that shows.
``refused``
    The transport definitely did not take it. Nothing happened; another
    adapter may be tried.
``uncertain``
    The adapter cannot tell. The wake may have happened. Nothing else is
    tried for this message.

Accepted is not read. No state here says that the recipient saw or acted on
anything.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final, Protocol

__all__ = [
    "ACCEPTED",
    "KIND_PATTERN",
    "PROVES",
    "REFUSED",
    "STATES",
    "UNCERTAIN",
    "Availability",
    "WakeAdapter",
    "WakeOutcome",
    "WakeTarget",
    "validate_deadline",
]

ACCEPTED: Final = "accepted"
REFUSED: Final = "refused"
UNCERTAIN: Final = "uncertain"

STATES: Final = frozenset({ACCEPTED, REFUSED, UNCERTAIN})
"""Every state an adapter may report."""

PROVES: Final = frozenset(
    {
        "queued_by_native_daemon",
        "typed_into_idle_composer",
        "written_to_passive_receiver",
        "acknowledged_by_passive_receiver",
        "harness_notice_written",
    }
)
"""What an accepted wake shows, per kind of transport.

``written_to_passive_receiver`` says that the hub wrote the message to a
receiver that declared itself passive, and nothing about what watches that
receiver. ``harness_notice_written`` is the stronger statement that a client's
harness was told; no adapter may say it until a harness managed by the
product gives evidence of that.
"""

KIND_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
"""Shape of an adapter's kind; it is part of file names and receipts."""


@dataclass(frozen=True, slots=True)
class WakeTarget:
    """The exact session to wake: never the most recent one, never a guess."""

    seat: str
    native_session: str = ""
    execution_host: str = ""


@dataclass(frozen=True, slots=True)
class Availability:
    """Whether an adapter can reach a target right now, and why not."""

    available: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class WakeOutcome:
    """What one wake through one adapter gave."""

    state: str
    proves: str = ""
    detail: str = ""


class WakeAdapter(Protocol):
    """One way of waking a seat."""

    @property
    def kind(self) -> str:
        """Return the adapter's name, as it appears in marks and receipts."""

    def available(self, target: WakeTarget) -> Availability:
        """Return whether the exact target can be reached; no effect on it."""

    def wake(
        self, target: WakeTarget, wake_id: int, prompt: str, deadline_seconds: float
    ) -> WakeOutcome:
        """Deliver the fixed prompt once and say what happened.

        ``prompt`` is the relay's fixed text. An adapter never receives the
        text of a message.
        """


def validate_deadline(seconds: float) -> float:
    """Return a finite non-negative timeout before any wake effect or mark.

    Raises
    ------
    ValueError
        The timeout is boolean, non-numeric, negative or non-finite.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        raise ValueError("wake deadline must be a finite non-negative number")
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("wake deadline must be a finite non-negative number")
    return float(seconds)
