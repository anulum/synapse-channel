# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake adapter that types the fixed prompt into an idle tmux pane
"""The pane bridge as a wake adapter.

It wraps :func:`synapse_channel.agent_tmux.inject_wake` and changes nothing
in it. The bridge types its own fixed prompt into the provider's composer
and reports success only after it saw the prompt consumed.

The bridge keeps a wake of its own: whenever it is asked and cannot finish
(pane busy, a dialog open, the submit not acknowledged), it marks a pending
wake in its registry and delivers it later by itself. That decides what this
adapter may say:

* It asks the bridge **only** when the session exists, is bound to the seat,
  runs the agent and shows an idle composer. Otherwise it is *unavailable*:
  the bridge was not touched, nothing is pending, another adapter may run.
* Once the bridge was asked, the answer is ``accepted`` (consumption seen)
  or ``uncertain``. It is never ``refused``: the bridge may still deliver
  the wake it now holds, and a second adapter would wake the seat twice.
* ``refused`` is said only when the bridge was not asked at all: the seat or
  the prompt handed over is not the bridge's own.
"""

from __future__ import annotations

import subprocess  # nosec B404
import time
from collections.abc import Callable

from synapse_channel.agent_tmux import (
    AgentTmuxConfig,
    CommandRunner,
    Sleeper,
    build_wake_prompt,
    inject_wake,
    status,
)
from synapse_channel.wake_adapters import (
    ACCEPTED,
    REFUSED,
    UNCERTAIN,
    Availability,
    WakeOutcome,
    WakeTarget,
)

KIND = "pane_bridge"
"""The adapter's name in marks and receipts."""

PROVES = "typed_into_idle_composer"
"""What an accepted wake of this adapter shows."""

IDLE = "idle"
"""Pane state in which the bridge is asked."""

ConfigFor = Callable[[WakeTarget], AgentTmuxConfig | None]
"""Returns the bridge configuration of a seat, or None when it has no pane."""


class PaneBridgeAdapter:
    """Wake a seat by letting the pane bridge type its fixed prompt."""

    def __init__(
        self,
        config_for: ConfigFor,
        *,
        runner: CommandRunner = subprocess.run,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self._config_for = config_for
        self._runner = runner
        self._sleeper = sleeper

    @property
    def kind(self) -> str:
        """Return ``pane_bridge``."""
        return KIND

    def _config(self, target: WakeTarget) -> AgentTmuxConfig | None:
        config = self._config_for(target)
        return config if config is not None and config.identity == target.seat else None

    def available(self, target: WakeTarget) -> Availability:
        """Say whether the bridge can be asked without leaving a pending wake."""
        config = self._config(target)
        if config is None:
            return Availability(False, "no tmux pane is configured for exactly this seat")
        snapshot = status(config, runner=self._runner)
        if not snapshot.session_exists:
            return Availability(False, "the tmux session is missing")
        if not snapshot.binding_valid:
            return Availability(False, f"session binding: {snapshot.binding_detail}")
        if not snapshot.agent_active:
            return Availability(False, "the pane does not run the agent")
        if snapshot.pane_state != IDLE and not (
            config.opencode_editor_readback and snapshot.pane_state == "readback-required"
        ):
            return Availability(False, f"the pane is not idle ({snapshot.pane_state})")
        return Availability(True)

    def wake(
        self, target: WakeTarget, wake_id: int, prompt: str, deadline_seconds: float
    ) -> WakeOutcome:
        """Let the bridge type its prompt once and say what it reported.

        The bridge builds its own prompt from the seat's identity. The relay's
        prompt is only compared with it; when they differ nothing is typed.
        """
        del wake_id, deadline_seconds
        config = self._config(target)
        if config is None or prompt != build_wake_prompt(config.identity):
            return WakeOutcome(
                REFUSED, "", "the bridge was not asked: seat or prompt is not its own"
            )
        result = inject_wake(config, runner=self._runner, sleeper=self._sleeper)
        if result.injected:
            return WakeOutcome(ACCEPTED, PROVES, result.detail)
        return WakeOutcome(
            UNCERTAIN, "", f"the bridge holds the wake and may deliver it: {result.detail}"
        )
