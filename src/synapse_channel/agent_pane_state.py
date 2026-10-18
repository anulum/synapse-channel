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
