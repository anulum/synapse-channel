# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in bridge from a Fabric turn to the core usage accounting
"""Emit one opt-in model-usage note for a completed participant turn.

The hub core already aggregates model cost and tokens, entirely opt-in, from a progress-ledger
note with ``kind="usage"`` and a canonical text body (:mod:`synapse_channel.core.accounting`).
Synapse itself never collects telemetry — the figures exist only when something *chooses* to
record them. The Participant Fabric is exactly such a chooser: it is the layer that actually drives
a model, so it sees the token counts that the bus core never would. Until now those counts were
parsed and discarded; this module is the bridge that records them instead.

Given a finished :class:`~synapse_channel.participants.envelope.TurnResult` and a progress-note
poster, :func:`emit_usage` formats the canonical usage note from the result's model, token split,
and cost, and posts it with :data:`~synapse_channel.core.accounting.USAGE_NOTE_KIND` so the
existing accounting report sees the spend. It is **opt-in**: callers wire it behind a flag (default
off), honouring the core's "never collects telemetry" stance. A turn with no usable model id cannot
form a valid note and is skipped silently rather than raising, so emission never disturbs the turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from synapse_channel.core.accounting import USAGE_NOTE_KIND, format_usage_note

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from synapse_channel.participants.envelope import TurnResult


class ProgressPoster(Protocol):
    """A progress-note poster, matching ``SynapseAgent.post_progress``.

    The bus client's :meth:`post_progress` appends a structured note to the progress ledger; this
    protocol lets :func:`emit_usage` accept it (or a fake) without importing the client.
    """

    def __call__(self, task_id: str, text: str, *, kind: str = ...) -> Awaitable[None]:
        """Append a progress note ``text`` for ``task_id`` under the given ``kind``."""


def _is_usable_model(model: str) -> bool:
    """Return whether ``model`` can label a usage note (non-empty, no whitespace)."""
    return bool(model) and not any(character.isspace() for character in model)


async def emit_usage(result: TurnResult, *, post_progress: ProgressPoster) -> bool:
    """Record one turn's model usage as an opt-in accounting note, if it can be attributed.

    The note is built from the result's ``model``, ``input_tokens`` / ``output_tokens``, and
    ``cost_usd`` (a positive cost is recorded; a zero or absent cost is omitted so a local pricing
    table can estimate it instead). The result's ``topic_id`` is used as the note's task id so the
    spend correlates to the conversation. A result with no usable model id is skipped, because the
    accounting note format requires one; an error turn with a model is still recorded (calls=1,
    zero tokens) so a failed-but-attempted turn is visible.

    Parameters
    ----------
    result : TurnResult
        The finished turn whose usage is recorded.
    post_progress : ProgressPoster
        Poster that appends the note to the progress ledger (e.g. ``SynapseAgent.post_progress``).

    Returns
    -------
    bool
        ``True`` when a usage note was posted; ``False`` when the turn had no usable model id and
        was skipped.
    """
    model = result["model"].strip()
    if not _is_usable_model(model):
        return False
    cost = result["cost_usd"] if result["cost_usd"] > 0 else None
    note = format_usage_note(
        model=model,
        calls=1,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost=cost,
    )
    await post_progress(result["topic_id"], note, kind=USAGE_NOTE_KIND)
    return True
