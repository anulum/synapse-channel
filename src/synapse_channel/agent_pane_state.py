# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider composer classification
"""Classify provider input while keeping terminal transport orchestration small."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse_channel.agent_tmux import AgentTmuxConfig, CommandRunner

PROVIDER_IDLE_PATTERNS = {
    "codex": re.compile(r"(?m)^\s*›(?:\s|$)"),
    "claude": re.compile(r"(?m)^\s*❯(?:\s|$)"),
    "kimi": re.compile(r"(?m)^\s*(?:›|❯|>)\s*(?:$|Ask\b|Type\b)"),
    "grok": re.compile(r"(?m)^\s*(?:›|❯|>)\s*(?:$|Ask\b|Type\b)"),
    "gemini": re.compile(
        r"(?im)^\s*(?:›|❯|>)?\s*(?:Type your message|Type a message|Ask Gemini)\b"
    ),
    "opencode": re.compile(r"(?im)^\s*Ask anything\.\.\.\s*(?:\"|$)"),
}
"""Provider-specific idle composer markers; unknown providers fail closed."""

_UNSAFE_PANE_PATTERN = re.compile(
    r"(?im)(?:"
    r"^\s*(?:›|❯|>|→)?\s*\d+[.)]\s+"
    r"|\b(?:approve|approval|allow|deny|permission|confirm|confirmation|trust)\b"
    r"|\b(?:working|thinking|generating|running tool|press .*? to cancel|esc(?: to)? interrupt)\b"
    r")"
)
"""Conservative modal/busy markers that override every idle marker."""

_CODEX_UPDATE_MODAL_PATTERN = re.compile(
    r"(?is)Update available!\s*\S+\s*->\s*\S+.*Skip until next version.*"
    r"Press enter to continue"
)
"""Exact Codex update chooser that blocks the interactive composer."""


def pane_state(provider: str, screen: str) -> str:
    """Return the existing provider pane's operational wake state.

    Parameters
    ----------
    provider : str
        Supported terminal provider whose composer markers are used.
    screen : str
        Current visible terminal capture, retained only by the caller.

    Returns
    -------
    str
        ``update-required``, ``blocked``, ``idle`` or ``unknown``. Busy or
        modal markers take precedence over an idle marker.

    Raises
    ------
    KeyError
        An unsupported provider reaches idle-marker lookup.
    """
    if provider == "codex" and _CODEX_UPDATE_MODAL_PATTERN.search(screen):
        return "update-required"
    if _UNSAFE_PANE_PATTERN.search(screen):
        return "blocked"
    if PROVIDER_IDLE_PATTERNS[provider].search(screen):
        return "idle"
    return "unknown"


def _rendered_text_match(screen: str, required_text: str) -> re.Match[str] | None:
    """Return the last whitespace-tolerant match for terminal-rendered text."""
    pattern = r"\s+".join(re.escape(token) for token in required_text.split())
    matches = list(re.finditer(pattern, screen))
    return matches[-1] if matches else None


def _contains_rendered_text(screen: str, required_text: str) -> bool:
    """Return whether terminal wrapping preserved the complete required text."""
    return _rendered_text_match(screen, required_text) is not None


def screen_is_safe_for_submit(
    provider: str,
    screen: str,
    *,
    required_text: str | None = None,
) -> tuple[bool, str]:
    """Classify current terminal text before accepting a managed wake.

    Parameters
    ----------
    provider : str
        Supported terminal provider whose composer markers are used.
    screen : str
        Current visible terminal capture; never included in the reason.
    required_text : str or None, optional
        Complete previously staged hint to verify through terminal wrapping.

    Returns
    -------
    tuple[bool, str]
        Existing readiness verdict and payload-free explanation. This is a
        screen observation, not proof that a provider executed a model turn.

    Raises
    ------
    KeyError
        An unsupported provider reaches idle-marker lookup.
    """
    state = pane_state(provider, screen)
    if state == "update-required":
        return False, f"{provider} update chooser blocks the managed composer"
    if state == "blocked":
        return False, f"{provider} pane is busy, modal, or ambiguous"
    if state != "idle":
        # OpenCode mini removes its empty-composer placeholder after a paste.
        # Admit only our complete staged prompt immediately followed by the
        # actual composer footer; arbitrary typed text is never an idle marker.
        if provider == "opencode" and required_text is not None:
            match = _rendered_text_match(screen, required_text)
            if match is not None and re.fullmatch(
                r"\s*(?:BUILD|PLAN)\b[^\n]*\bctrl\+p cmd\s*", screen[match.end() :]
            ):
                return True, "opencode staged composer verified"
        return False, f"{provider} idle composer marker is absent"
    if required_text is not None and not _contains_rendered_text(screen, required_text):
        return False, "wake prompt was not accepted by the idle composer"
    return True, f"{provider} idle composer verified"


def staged_wake_was_consumed(
    provider: str,
    screen: str,
    prompt: str,
) -> bool:
    """Classify the terminal observation of a previously staged hint.

    A disappeared hint cannot safely be replayed. A visible hint is settled
    only by a later idle composer or the existing native streaming markers.
    This preserves the legacy observation contract; it is not model receipt
    or completion acknowledgement.

    Parameters
    ----------
    provider : str
        Supported terminal provider whose idle marker is used.
    screen : str
        Current visible terminal capture, retained only by the caller.
    prompt : str
        Fixed routing hint that the caller previously staged.

    Returns
    -------
    bool
        Whether existing terminal observations settle the staged attempt.

    Raises
    ------
    KeyError
        An unsupported provider reaches idle-marker lookup.
    """
    prompt_match = _rendered_text_match(screen, prompt)
    if prompt_match is None:
        return True
    if provider == "opencode":
        # Mini renders a submitted user turn with › and an interrupt footer
        # while streaming. The staged input has neither that turn prefix nor
        # the busy footer. Require both; busy text alone remains insufficient.
        prefix = screen[: prompt_match.start()].rsplit("\n", 1)[-1].strip()
        if prefix == "›" and re.search(
            r"(?m)^\s*(?:BUILD|PLAN)\b[^\n]*\besc interrupt\b[^\n]*\bctrl\+p cmd\s*$",
            screen[prompt_match.end() :],
        ):
            return True
    return PROVIDER_IDLE_PATTERNS[provider].search(screen, prompt_match.end()) is not None


def bound_screen_safety(
    config: AgentTmuxConfig,
    provider: str,
    screen: str,
    *,
    runner: CommandRunner,
    required_text: str | None = None,
) -> tuple[bool, str]:
    """Use the opted-in native input proof, retaining every legacy provider guard.

    Parameters
    ----------
    config : AgentTmuxConfig
        Terminal configuration selecting native or legacy input verification.
    provider : str
        Detected provider name.
    screen : str
        Current bounded pane capture.
    runner : CommandRunner
        Bound process runner used for native editor probes.
    required_text : str or None, optional
        Exact staged hint to verify; None requires empty native input.

    Returns
    -------
    tuple of bool, str
        Admission result and caller-safe reason. A matching staged hint also
        records its verification for later recovery.
    """
    if not config.opencode_editor_readback:
        return screen_is_safe_for_submit(provider, screen, required_text=required_text)
    if provider != "opencode":
        return False, "native input readback requires an OpenCode pane"
    from synapse_channel.opencode_composer_probe import inspect_editor_composer

    try:
        snapshot = inspect_editor_composer(config, screen, runner=runner)
    except (OSError, ValueError):
        return False, "native OpenCode input readback is unavailable"
    if not snapshot.matches(required_text):
        return False, "native OpenCode input contains another draft"
    if required_text is not None:
        from synapse_channel.opencode_composer_probe import set_stage_verified

        set_stage_verified(config, True)
    return True, "native OpenCode input verified"


def bound_staged_wake_was_consumed(
    config: AgentTmuxConfig,
    provider: str,
    screen: str,
    prompt: str,
    *,
    runner: CommandRunner,
) -> bool:
    """Settle a native paste only after its actual staged input was verified.

    Parameters
    ----------
    config : AgentTmuxConfig
        Terminal and persistent staged-wake configuration.
    provider : str
        Detected provider name.
    screen : str
        Current bounded pane capture.
    prompt : str
        Complete fixed wake hint previously staged.
    runner : CommandRunner
        Bound native process runner when the profile requires readback.

    Returns
    -------
    bool
        Whether terminal observations or an empty native readback show the
        previously verified hint was consumed. This is not model completion.
    """
    if not config.opencode_editor_readback:
        return staged_wake_was_consumed(provider, screen, prompt)
    from synapse_channel.opencode_composer_probe import (
        inspect_editor_composer,
        profile_is_bound,
        stage_is_verified,
    )

    if not stage_is_verified(config) or not profile_is_bound(config, runner):
        return False
    if staged_wake_was_consumed(provider, screen, prompt):
        return True
    try:
        return inspect_editor_composer(config, screen, runner=runner).matches(None)
    except (OSError, ValueError):
        return False


def bound_pane_state(
    config: AgentTmuxConfig,
    provider: str,
    screen: str,
    *,
    runner: CommandRunner,
) -> str:
    """Describe readiness without invoking any native editor during status.

    Parameters
    ----------
    config : AgentTmuxConfig
        Terminal configuration selecting the input verification profile.
    provider : str
        Detected provider name.
    screen : str
        Current bounded pane capture.
    runner : CommandRunner
        Observational native process runner; no editor keys are sent.

    Returns
    -------
    str
        Legacy readiness or readback-required, readback-unavailable, blocked
        or unknown for the opted-in native profile.
    """
    if not config.opencode_editor_readback:
        return pane_state(provider, screen)
    if provider != "opencode":
        return "unknown"
    from synapse_channel.opencode_composer_probe import footer_is_probeable, profile_is_bound

    if not profile_is_bound(config, runner):
        return "readback-unavailable"
    return "readback-required" if footer_is_probeable(screen) else "blocked"


def provider_compatibility(
    provider: str | None,
    state: str,
    *,
    active: bool,
    codex_policy_aligned: bool,
) -> tuple[bool, str]:
    """Describe launch/profile support separately from native wake acceptance.

    Parameters
    ----------
    provider : str or None
        Detected provider or None when no supported profile is present.
    state : str
        Current pane readiness classification.
    active : bool
        Whether the provider process is live.
    codex_policy_aligned : bool
        Whether the existing centrally managed Codex launch policy is aligned.

    Returns
    -------
    tuple of bool, str
        Compatibility assessment and its explanation. An aligned process still
        requires current input checks before a wake may be submitted.
    """
    aligned = (
        active
        and provider is not None
        and state in {"idle", "blocked", "readback-required"}
        and codex_policy_aligned
    )
    if not active:
        return aligned, "active provider pane was not verified"
    if provider is None:
        return aligned, "provider has no supported pane profile"
    if state == "update-required":
        return aligned, "provider update chooser blocks automatic wake delivery"
    if not codex_policy_aligned:
        return aligned, "Codex update checks are not centrally managed for this session"
    if state == "unknown":
        return aligned, "provider pane readiness is unknown"
    if state == "readback-unavailable":
        return aligned, "native editor profile is not bound to this running client"
    if state == "readback-required":
        return aligned, "native input readback is required before wake submission"
    return aligned, "provider launch and pane profile are aligned"
