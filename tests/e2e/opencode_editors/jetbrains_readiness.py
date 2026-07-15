# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — JetBrains ACP readiness event contract
"""Match prerequisite-gated readiness events without inventing their order."""

from __future__ import annotations


def prerequisite_then_all(
    contents: str,
    prerequisite: str,
    completions: tuple[str, ...],
) -> bool:
    """Return whether every completion follows one prerequisite event.

    Parameters
    ----------
    contents:
        Append-only IDEA log contents captured after agent selection.
    prerequisite:
        Event that must occur before every completion event.
    completions:
        Distinct completion events whose relative order is unspecified.

    Returns
    -------
    bool
        ``True`` only when the prerequisite exists and every completion has
        an occurrence after it.

    Raises
    ------
    ValueError
        If the event contract is empty, ambiguous, or duplicated.
    """
    if not prerequisite:
        raise ValueError("JetBrains readiness prerequisite must be non-empty")
    if not completions or any(not marker for marker in completions):
        raise ValueError("JetBrains readiness completions must be non-empty")
    if prerequisite in completions or len(set(completions)) != len(completions):
        raise ValueError("JetBrains readiness events must be distinct")
    prerequisite_position = contents.find(prerequisite)
    if prerequisite_position < 0:
        return False
    completion_start = prerequisite_position + len(prerequisite)
    return all(contents.find(marker, completion_start) >= 0 for marker in completions)
