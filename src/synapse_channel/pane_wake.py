# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified staged pane wake orchestration
"""Stage and submit one verified pane wake without repasting uncertain work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.agent_tmux import (
        AgentTmuxConfig,
        AgentTmuxWakeResult,
        CommandRunner,
        Sleeper,
    )

    class PaneSafety(Protocol):
        """Probe the current pane, optionally requiring the staged text."""

        def __call__(self, *, required_text: str | None = None) -> tuple[bool, str]:
            """Return the pane safety verdict and its existing local reason."""
            ...

    class ScreenSafety(Protocol):
        """Classify an already captured provider pane."""

        def __call__(
            self, provider: str, screen: str, *, required_text: str | None = None
        ) -> tuple[bool, str]:
            """Return the captured screen safety verdict without disclosing it."""
            ...

    class WakeRegistryWriter(Protocol):
        """Update only the existing wake registry fields."""

        def __call__(
            self,
            *,
            last_inject_returncode: int | None = None,
            pending_wake: bool | None = None,
            wake_prompt_staged: bool | None = None,
        ) -> None:
            """Persist the supplied wake fields while retaining omitted fields."""
            ...


@dataclass(frozen=True)
class PaneWakeBindings:
    """Late-bound owner callbacks for one exact pane wake.

    Parameters
    ----------
    binding, provider, prompt, staged, capture
        Existing owner probes, bound to the exact configuration and runner.
    consumed, screen_safe, pane_safe
        Existing provider classifiers; captured text remains local.
    submit, record
        Existing submission and durable registry callbacks in their old order.
    buffer_key
        Deferred safe buffer-name calculation, evaluated only before staging.
    result, binding_refusal_code
        Existing public result type and identity-binding refusal code.
    """

    binding: Callable[[], tuple[bool, str]]
    provider: Callable[[], str | None]
    prompt: Callable[[], str]
    staged: Callable[[], bool]
    capture: Callable[[], tuple[str | None, str]]
    consumed: Callable[[str, str, str], bool]
    screen_safe: ScreenSafety
    pane_safe: PaneSafety
    submit: Callable[[str, str], AgentTmuxWakeResult]
    record: WakeRegistryWriter
    buffer_key: Callable[[], str]
    result: type[AgentTmuxWakeResult]
    binding_refusal_code: int


@dataclass(frozen=True)
class _ReadyPane:
    """Exact provider and fixed prompt admitted by the local probes."""

    provider: str
    prompt: str
    staged: bool


def _probe_prompt(
    ports: PaneWakeBindings, provider: str, prompt: str, staged: bool
) -> AgentTmuxWakeResult | None:
    """Refuse an unsafe pane or settle an already consumed staged wake."""
    if staged:
        screen, capture_detail = ports.capture()
        if screen is None:
            safe, safety_detail = False, capture_detail
        elif ports.consumed(provider, screen, prompt):
            ports.record(last_inject_returncode=0, pending_wake=False, wake_prompt_staged=False)
            return ports.result(
                injected=True,
                started=False,
                returncode=0,
                detail="staged wake consumption observed; not repasted",
            )
        else:
            safe, safety_detail = ports.screen_safe(provider, screen, required_text=prompt)
    else:
        safe, safety_detail = ports.pane_safe()
    if not safe:
        ports.record(last_inject_returncode=0, pending_wake=True, wake_prompt_staged=staged)
        return ports.result(
            injected=False, started=False, returncode=0, detail=f"wake queued: {safety_detail}"
        )
    return None


def _admit_pane(ports: PaneWakeBindings) -> _ReadyPane | AgentTmuxWakeResult:
    """Prove the exact session and provider before preparing any pane mutation."""
    valid, detail = ports.binding()
    if not valid:
        ports.record(pending_wake=True)
        return ports.result(
            injected=False,
            started=False,
            returncode=ports.binding_refusal_code,
            detail=f"refusing wake injection: {detail}",
        )
    provider = ports.provider()
    if provider is None:
        ports.record(last_inject_returncode=0, pending_wake=True)
        return ports.result(
            injected=False,
            started=False,
            returncode=0,
            detail="wake queued: provider has no fail-closed idle profile",
        )
    prompt, staged = ports.prompt(), ports.staged()
    outcome = _probe_prompt(ports, provider, prompt, staged)
    return outcome if outcome is not None else _ReadyPane(provider, prompt, staged)


def _paste_prompt(
    config: AgentTmuxConfig, ports: PaneWakeBindings, prompt: str, runner: CommandRunner
) -> AgentTmuxWakeResult | None:
    """Set the fixed buffer and paste it once, retaining exact failure state."""
    buffer_name = f"synapse-wake-{ports.buffer_key()}"
    process = runner(
        [config.tmux_bin, "set-buffer", "-b", buffer_name, "--", prompt],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        ports.record(
            last_inject_returncode=process.returncode, pending_wake=True, wake_prompt_staged=False
        )
        return ports.result(
            injected=False,
            started=False,
            returncode=process.returncode,
            detail=(process.stderr or process.stdout).strip() or "buffer setup failed",
        )
    ports.record(last_inject_returncode=0, pending_wake=True, wake_prompt_staged=True)
    process = runner(
        [config.tmux_bin, "paste-buffer", "-b", buffer_name, "-d", "-p", "-t", config.session],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        ports.record(
            last_inject_returncode=process.returncode, pending_wake=True, wake_prompt_staged=True
        )
        return ports.result(
            injected=False,
            started=False,
            returncode=process.returncode,
            detail=(process.stderr or process.stdout).strip() or "paste failed",
        )
    ports.record(last_inject_returncode=0, pending_wake=True, wake_prompt_staged=True)
    return None


def inject_pane_wake(
    config: AgentTmuxConfig,
    ports: PaneWakeBindings,
    *,
    runner: CommandRunner,
    sleeper: Sleeper,
) -> AgentTmuxWakeResult:
    """Submit an admitted staged prompt, or paste and persist it before reproving.

    Parameters
    ----------
    config
        Exact existing tmux configuration whose session and delay are used.
    ports
        Owner callbacks captured when the public wake entrypoint is invoked.
    runner, sleeper
        Existing process and pacing boundaries; no payload is accepted here.

    Returns
    -------
    AgentTmuxWakeResult
        Existing refusal, pending or observed-consumption result. An uncertain
        staged prompt is never pasted again.
    """
    ready = _admit_pane(ports)
    if not isinstance(ready, _ReadyPane):
        return ready
    if ready.staged:
        return ports.submit(ready.provider, ready.prompt)
    failure = _paste_prompt(config, ports, ready.prompt, runner)
    if failure is not None:
        return failure
    sleeper(max(config.submit_delay, 0.0))
    safe, detail = ports.pane_safe(required_text=ready.prompt)
    if not safe:
        ports.record(last_inject_returncode=0, pending_wake=True, wake_prompt_staged=True)
        return ports.result(
            injected=False,
            started=False,
            returncode=0,
            detail=f"wake queued after paste: {detail}",
        )
    return ports.submit(ready.provider, ready.prompt)
