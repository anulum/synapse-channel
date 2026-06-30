# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — distil the plain-text reply from `ollama run`
"""Distil the plain-text reply a local ``ollama run`` turn prints to stdout.

Unlike the Claude, Codex, and Kimi drivers, the Ollama CLI does not emit a JSON event stream
in its headless ``run`` mode: ``ollama run MODEL "PROMPT"`` simply prints the model's reply as
plain text to stdout (its progress spinner goes to stderr and is ignored). The schema this
function targets was captured from a real invocation (Ollama 0.20.2): stdout is the reply, an
exit code of zero means success, and a non-zero exit with empty stdout means the model could
not be run.

A local Ollama turn carries no provider session token (the ``run`` CLI is stateless between
invocations), no disclosed rationale, and no monetary cost (the model runs locally), so those
fields are empty or zero. Continuity for an Ollama participant therefore comes from the
conversation's fenced context, not from provider-side memory. The result is normalised into
the same :class:`~synapse_channel.participants.stream_json.StreamOutcome` the other parsers
produce, so every provider feeds one envelope builder.
"""

from __future__ import annotations

from synapse_channel.participants.stream_json import NO_RESULT_SUBTYPE, StreamOutcome


def parse_ollama_output(text: str) -> StreamOutcome:
    """Distil a local ``ollama run`` stdout into a :class:`StreamOutcome`.

    Parameters
    ----------
    text : str
        The provider's captured stdout — the model's plain-text reply.

    Returns
    -------
    StreamOutcome
        ``answer`` is the stripped reply; ``rationale``, ``session_id`` and ``stop_reason``
        are empty and ``cost_usd`` is ``0.0`` (a local turn has none of these). Empty output
        is an error carrying :data:`~synapse_channel.participants.stream_json.NO_RESULT_SUBTYPE`,
        so a silent failure never reads as a blank answer.
    """
    answer = text.strip()
    is_error = answer == ""
    return StreamOutcome(
        answer=answer,
        rationale="",
        session_id="",
        is_error=is_error,
        subtype=NO_RESULT_SUBTYPE if is_error else "success",
        cost_usd=0.0,
        num_turns=0,
        stop_reason="",
    )
