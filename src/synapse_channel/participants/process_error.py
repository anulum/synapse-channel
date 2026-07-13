# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — safe participant subprocess failure summaries
"""Render bounded, non-sensitive diagnostics for failed provider processes.

Provider CLIs may write prompts, policy files, local paths, credentials, or other
operator data to stderr.  A :class:`~synapse_channel.participants.envelope.TurnResult`
can be relayed or persisted, so raw stderr must never cross that boundary.  This
module exposes only the exit status, a safe classification for explicitly known
failures, or the size of diagnostic output that was withheld.
"""

from __future__ import annotations

from typing import Final

_GEMINI_CONSUMER_UNAVAILABLE = (
    "Gemini CLI no longer serves consumer accounts; use Antigravity CLI or an "
    "eligible enterprise/API-key configuration"
)

_SAFE_DIAGNOSTICS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "gemini": (
        ("IneligibleTierError", _GEMINI_CONSUMER_UNAVAILABLE),
        (
            "no longer supported for Gemini Code Assist for individuals",
            _GEMINI_CONSUMER_UNAVAILABLE,
        ),
    ),
}


def format_process_failure(
    *,
    provider: str,
    binary: str,
    returncode: int,
    stderr: str,
) -> str:
    """Return a safe failure reason without copying provider stderr.

    Parameters
    ----------
    provider : str
        Stable provider key used only to select built-in safe classifications.
    binary : str
        Configured executable name, included for operator orientation.
    returncode : int
        Completed process exit status.
    stderr : str
        Provider diagnostic stream. Its contents are classified locally and are
        otherwise withheld; they are never interpolated into the returned value.

    Returns
    -------
    str
        A bounded reason suitable for a relayed or durable turn result.
    """
    diagnostic = stderr.strip()
    if not diagnostic:
        detail = "no diagnostic output"
    else:
        detail = next(
            (
                safe_message
                for marker, safe_message in _SAFE_DIAGNOSTICS.get(provider, ())
                if marker in diagnostic
            ),
            f"provider diagnostic withheld ({len(diagnostic)} characters)",
        )
    return f"{binary!r} exited {returncode}: {detail}"


def format_process_start_failure(*, binary: str, error: BaseException) -> str:
    """Return a safe startup failure without reflecting exception text.

    ``OSError`` text often contains workstation paths, while injected process
    runners may raise a ``SubprocessError`` carrying arbitrary provider output.
    Only a small built-in classification crosses the turn-result boundary.
    """
    if isinstance(error, FileNotFoundError):
        detail = "executable not found"
    elif isinstance(error, PermissionError):
        detail = "permission denied"
    elif isinstance(error, OSError):
        detail = "operating-system error"
    else:
        detail = "subprocess error"
    return f"failed to run {binary!r}: {detail}"
