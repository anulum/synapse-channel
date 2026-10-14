# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — order, marks and fallback of waking one seat for one message
"""Wake one seat for one message, over the first adapter that takes it.

The rule that everything here serves: **an effect whose outcome is unknown
is not repeated.**

* Before an adapter is asked, its attempt is marked on disk. After it
  answered, the outcome is marked.
* ``accepted`` or ``uncertain`` ends the work for that message: no other
  adapter, and no retry after a restart or a second delivery of the message.
* Only ``refused`` lets the next adapter run. A refused adapter is not asked
  again for the same message.
* An attempt left without an outcome, by a process that died, is uncertain.
* One wake of a seat is at work at a time; a second relay for the seat waits
  and then finds complete marks.
* When every adapter refused, the result is ``wake_failed``. The message
  stays in the mailbox either way; a wake only says that it is there.

An adapter that is not available for the target is passed over without a
mark: nothing happened, and it may be tried when the message is relayed again.

The relay never sees a message. It hands every adapter the same fixed prompt,
built from the seat's identity alone.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from synapse_channel import wake_marks
from synapse_channel.wake_adapters import (
    REFUSED,
    STATES,
    UNCERTAIN,
    WakeAdapter,
    WakeOutcome,
    WakeTarget,
    validate_deadline,
)

WAKE_FAILED = "wake_failed"
"""Every adapter refused or was unavailable."""

DEFAULT_DEADLINE_SECONDS = 30.0
"""Time one adapter gets for one wake."""


@dataclass(frozen=True, slots=True)
class RelayResult:
    """What relaying one message's wake came to.

    ``state`` is ``accepted``, ``uncertain`` or ``wake_failed``. ``wake_path``
    names the adapter that ended the work, and ``proves`` what its acceptance
    shows. ``sent_now`` is false when earlier
    marks decided the result and no adapter was asked in this call.
    ``passed_over`` lists adapters that were not available, with the reason.
    """

    state: str
    wake_path: str = ""
    proves: str = ""
    attempts: tuple[wake_marks.Mark, ...] = ()
    passed_over: tuple[tuple[str, str], ...] = ()
    sent_now: bool = False


def _fixed_prompt(seat: str) -> str:
    # Imported here: the pane bridge's module is large and is itself a caller.
    from synapse_channel.agent_tmux import build_wake_prompt

    return build_wake_prompt(seat)


def _settled(marks: Sequence[wake_marks.Mark]) -> RelayResult | None:
    """Return the result that earlier marks already decide, if they do."""
    for mark in marks:
        if mark.state == wake_marks.OPEN:
            return RelayResult(UNCERTAIN, mark.adapter, "", tuple(marks))
        if mark.state != REFUSED:
            return RelayResult(mark.state, mark.adapter, mark.proves, tuple(marks))
    return None


def _ask(
    adapter: WakeAdapter, target: WakeTarget, wake_id: int, prompt: str, seconds: float
) -> WakeOutcome:
    """Ask one adapter; whatever is not a clear answer is uncertain."""
    try:
        outcome = adapter.wake(target, wake_id, prompt, seconds)
    except Exception as exc:  # noqa: BLE001 - the effect may have happened before the error
        return WakeOutcome(UNCERTAIN, "", f"the adapter raised {type(exc).__name__}")
    if not isinstance(outcome, WakeOutcome) or outcome.state not in STATES:
        return WakeOutcome(UNCERTAIN, "", "the adapter gave no known state")
    return outcome


def relay(
    marks_dir: Path,
    target: WakeTarget,
    wake_id: int,
    adapters: Sequence[WakeAdapter],
    *,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    prompt_for: Callable[[str], str] = _fixed_prompt,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> RelayResult:
    """Wake ``target`` for the message ``wake_id`` at most once.

    Parameters
    ----------
    marks_dir : pathlib.Path
        Directory of the durable marks.
    target : WakeTarget
        The exact seat and session.
    wake_id : int
        The hub's sequence number of the message.
    adapters : sequence of WakeAdapter
        Adapters in the order to try them.
    deadline_seconds : float
        Time each adapter gets.
    prompt_for : callable
        Builds the fixed prompt from the seat's identity.
    now : callable
        Source of the current UTC time.

    Returns
    -------
    RelayResult
        The state of this message's wake after the call.
    """
    validate_deadline(deadline_seconds)
    with wake_marks.seat_lock(marks_dir, target.seat):
        earlier = wake_marks.read(marks_dir, target.seat, wake_id)
        settled = _settled(earlier)
        if settled is not None:
            return settled
        refused = {mark.adapter for mark in earlier}
        prompt = prompt_for(target.seat)
        passed_over = []
        # Observing an already completed passive wake precedes any new effect.
        # Stable sorting preserves the configured/ranked order of active paths.
        for adapter in sorted(adapters, key=lambda candidate: candidate.kind != "harness_waiter"):
            if adapter.kind in refused:
                continue
            availability = adapter.available(target)
            if not availability.available:
                passed_over.append((adapter.kind, availability.detail))
                continue
            order = wake_marks.begin(marks_dir, target.seat, wake_id, adapter.kind, now=now())
            outcome = _ask(adapter, target, wake_id, prompt, deadline_seconds)
            wake_marks.finish(
                marks_dir, target.seat, wake_id, adapter.kind, order, outcome, now=now()
            )
            if outcome.state != REFUSED:
                marks = wake_marks.read(marks_dir, target.seat, wake_id)
                return RelayResult(
                    outcome.state, adapter.kind, outcome.proves, marks, tuple(passed_over), True
                )
            refused.add(adapter.kind)
        marks = wake_marks.read(marks_dir, target.seat, wake_id)
        return RelayResult(
            WAKE_FAILED, "", "", marks, tuple(passed_over), len(marks) > len(earlier)
        )
