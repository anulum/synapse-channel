# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — canonical wire codec for an opt-in session-telemetry note
"""Canonical text codec for an opt-in operational session-metric note.

Where :mod:`synapse_channel.core.accounting` defines the canonical body for a *model-usage*
note, this defines the canonical body for a *session-telemetry snapshot* — the running
operational totals of a participant session (turns, error and abstention counts, cumulative
token pressure, spend, latency, and the highest rate-limit utilisation seen). It rides on the
same progress-ledger channel as the usage note — a ``LEDGER_PROGRESS`` note with
``kind="session_metric"`` and a stable ``key=value`` text body — so no new wire message, hub
handler, or stored-event kind is introduced, and the hub core stays a no-telemetry substrate.

The body is a snapshot of a :class:`~synapse_channel.participants.session_telemetry.SessionMetrics`.
Because that object is *cumulative* — each emission supersedes the prior one for the same session —
a reader keeps the latest snapshot per session rather than summing snapshots. The session identity
is carried by the progress note's ``task_id`` (as the usage note carries its task id), not by the
body, so the body holds only the metric figures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synapse_channel.participants.session_telemetry import SessionMetrics

SESSION_METRIC_NOTE_KIND = "session_metric"
"""Progress-note ``kind`` marking a structured operational session-metric snapshot."""

SESSION_METRIC_PREFIX = "session_metric"
"""Leading token of a canonical session-metric note text body."""

_INT_FIELDS = (
    "turns",
    "errors",
    "abstentions",
    "input_tokens",
    "output_tokens",
    "last_input_tokens",
)
"""Non-negative integer fields carried in the note body, in canonical order."""


def format_session_metric_note(metrics: SessionMetrics) -> str:
    """Return the canonical text body for an operational session-metric snapshot.

    Emit the result as a ``LEDGER_PROGRESS`` note with ``kind="session_metric"`` (see
    :data:`SESSION_METRIC_NOTE_KIND`). The format is a stable, client-agnostic ``key=value``
    line so Python, Go, and JavaScript clients can record identical snapshots. The highest
    rate-limit utilisation is included only when one was observed (mirroring the usage note's
    optional cost), so its absence is distinguishable from a recorded zero.

    Parameters
    ----------
    metrics : SessionMetrics
        The running session totals to snapshot.

    Returns
    -------
    str
        Canonical session-metric note text body.

    Raises
    ------
    ValueError
        If any count, the spend, or the latency is negative, or a present utilisation is
        outside the ``[0, 1]`` range.
    """
    counts = (
        metrics.turns,
        metrics.errors,
        metrics.abstentions,
        metrics.input_tokens,
        metrics.output_tokens,
        metrics.last_input_tokens,
    )
    if min(counts) < 0:
        msg = "session-metric counts must not be negative"
        raise ValueError(msg)
    if metrics.cost_usd < 0 or metrics.total_latency_seconds < 0:
        msg = "session-metric spend and latency must not be negative"
        raise ValueError(msg)
    utilisation = metrics.max_rate_limit_utilisation
    if utilisation is not None and not 0.0 <= utilisation <= 1.0:
        msg = "session-metric rate-limit utilisation must be within [0, 1]"
        raise ValueError(msg)
    fields = [
        SESSION_METRIC_PREFIX,
        f"turns={int(metrics.turns)}",
        f"errors={int(metrics.errors)}",
        f"abstentions={int(metrics.abstentions)}",
        f"input_tokens={int(metrics.input_tokens)}",
        f"output_tokens={int(metrics.output_tokens)}",
        f"cost_usd={float(metrics.cost_usd):.6f}",
        f"total_latency_seconds={float(metrics.total_latency_seconds):.6f}",
        f"last_input_tokens={int(metrics.last_input_tokens)}",
    ]
    if utilisation is not None:
        fields.append(f"max_rate_limit_utilisation={float(utilisation):.6f}")
    return " ".join(fields)


def parse_session_metric_note(text: str) -> dict[str, Any] | None:
    """Parse a canonical session-metric note body into its fields.

    Parameters
    ----------
    text : str
        Progress-note text body.

    Returns
    -------
    dict[str, Any] or None
        Parsed fields (the six integer counts, ``cost_usd``, ``total_latency_seconds``, and an
        optional ``max_rate_limit_utilisation``), or ``None`` when the body is not a
        session-metric note. Missing numeric fields default to zero so an older or partial
        body still yields a usable snapshot.
    """
    tokens = text.split()
    if not tokens or tokens[0] != SESSION_METRIC_PREFIX:
        return None
    pairs: dict[str, str] = {}
    for token in tokens[1:]:
        key, separator, value = token.partition("=")
        if separator:
            pairs[key] = value
    parsed: dict[str, Any] = {
        field: _coerce_int(pairs.get(field), default=0) for field in _INT_FIELDS
    }
    parsed["cost_usd"] = _coerce_float(pairs.get("cost_usd"), default=0.0)
    parsed["total_latency_seconds"] = _coerce_float(pairs.get("total_latency_seconds"), default=0.0)
    parsed["max_rate_limit_utilisation"] = _coerce_optional_float(
        pairs.get("max_rate_limit_utilisation")
    )
    return parsed


def _coerce_int(value: str | None, *, default: int) -> int:
    """Return a non-negative integer parsed from ``value`` or ``default``."""
    if value is None:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _coerce_float(value: str | None, *, default: float) -> float:
    """Return a non-negative float parsed from ``value`` or ``default``."""
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed >= 0.0 else default


def _coerce_optional_float(value: str | None) -> float | None:
    """Return a ``[0, 1]`` float parsed from ``value`` or ``None`` when absent or invalid."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if 0.0 <= parsed <= 1.0 else None
