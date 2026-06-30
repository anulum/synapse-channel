# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — conversation modes and dynamic mode selection
"""The modes of a moderated multi-party conversation, and how one is chosen.

A multi-party conversation is not one fixed shape but a **mode** selected for the session at
hand. The three modes are distinct protocol shapes, not synonyms:

- **Colloquy** — a small, focused exchange: few participants, a deeper back-and-forth, no
  privileged chair.
- **Roundtable** — equal participants, a single broad fan-out and one refinement pass, no
  chair; synthesis by merge.
- **Symposium** — a larger, staged gathering with a moderator that synthesises a final answer
  from the panel.

Each mode is reduced to two policy knobs — how many cross-critique rounds follow the opening
fan-out, and whether a moderator synthesises — so one orchestrator (:func:`convene`) runs any
mode. :func:`select_mode` picks the mode from the session shape (participant count and whether
a moderator is available), which is the dynamic selection the modes exist to support.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

COLLOQUY_MAX_PARTICIPANTS = 2
"""At most this many participants selects :attr:`ConversationMode.COLLOQUY`."""

ROUNDTABLE_MAX_PARTICIPANTS = 4
"""Above :data:`COLLOQUY_MAX_PARTICIPANTS` and up to this many selects a roundtable."""


class ConversationMode(str, Enum):
    """The shape of a moderated multi-party conversation.

    The value is a stable lowercase string so a mode survives a round trip through a JSON bus
    envelope or a transcript record.
    """

    COLLOQUY = "colloquy"
    ROUNDTABLE = "roundtable"
    SYMPOSIUM = "symposium"


@dataclass(frozen=True)
class ModePolicy:
    """The execution policy a mode reduces to.

    Attributes
    ----------
    critique_rounds : int
        Cross-critique rounds run after the opening fan-out. Each round lets every participant
        refine its answer having seen the panel's previous answers as data.
    uses_moderator : bool
        Whether a moderator participant synthesises a final answer from the last round.
    """

    critique_rounds: int
    uses_moderator: bool


MODE_POLICIES: dict[ConversationMode, ModePolicy] = {
    ConversationMode.COLLOQUY: ModePolicy(critique_rounds=2, uses_moderator=False),
    ConversationMode.ROUNDTABLE: ModePolicy(critique_rounds=1, uses_moderator=False),
    ConversationMode.SYMPOSIUM: ModePolicy(critique_rounds=1, uses_moderator=True),
}
"""The policy each mode reduces to: a small colloquy goes deeper (two critique rounds), a
roundtable does one broad pass, and a symposium adds a moderator synthesis."""


def policy_for(mode: ConversationMode) -> ModePolicy:
    """Return the execution policy for ``mode``."""
    return MODE_POLICIES[mode]


def select_mode(
    participant_count: int,
    *,
    moderator_available: bool = False,
) -> ConversationMode:
    """Choose a conversation mode from the session shape.

    Parameters
    ----------
    participant_count : int
        How many participants will take part.
    moderator_available : bool, optional
        Whether a moderator participant can synthesise. When true and there are enough
        participants to warrant a chair, a symposium is chosen.

    Returns
    -------
    ConversationMode
        A colloquy for a small set, a symposium when a moderator is available for three or more
        (or for a large set regardless), and a roundtable otherwise.
    """
    if participant_count <= COLLOQUY_MAX_PARTICIPANTS:
        return ConversationMode.COLLOQUY
    if moderator_available or participant_count > ROUNDTABLE_MAX_PARTICIPANTS:
        return ConversationMode.SYMPOSIUM
    return ConversationMode.ROUNDTABLE
