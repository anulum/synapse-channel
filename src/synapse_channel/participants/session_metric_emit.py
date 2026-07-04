# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in bridge from a session's running telemetry to the durable ledger
"""Emit one opt-in operational session-metric snapshot to the progress ledger.

:mod:`synapse_channel.participants.session_telemetry` keeps a session's running totals in memory;
:mod:`synapse_channel.participants.session_advisor` reads them to recommend when to log, compact,
or ease off a provider. Those decisions are confined to one process. This module is the bridge
that makes the running totals **durable**: given a snapshot of the metrics and a progress-note
poster, :func:`emit_session_metric` records the snapshot as a ``session_metric`` note so an
advisor — or an operator's report — can read a session's state across processes and sessions.

It mirrors :mod:`synapse_channel.participants.usage_emit`: the same opt-in stance (callers wire it
behind a flag, default off, so the hub core stays a no-telemetry substrate), the same
canonical-note channel, and the same silent-skip discipline. A snapshot with no turns carries no
signal — an empty session — and is skipped rather than written, so the ledger is never polluted
with empty markers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    format_session_metric_note,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from synapse_channel.participants.session_telemetry import SessionMetrics


class ProgressPoster(Protocol):
    """A progress-note poster, matching ``SynapseAgent.post_progress``.

    The bus client's :meth:`post_progress` appends a structured note to the progress ledger; this
    protocol lets :func:`emit_session_metric` accept it (or a fake) without importing the client.
    """

    def __call__(self, task_id: str, text: str, *, kind: str = ...) -> Awaitable[None]:
        """Append a progress note ``text`` for ``task_id`` under the given ``kind``."""


async def emit_session_metric(
    metrics: SessionMetrics,
    *,
    post_progress: ProgressPoster,
    session_id: str,
    task_id: str = "",
) -> bool:
    """Record one session's running telemetry as an opt-in durable snapshot, if it carries signal.

    The snapshot is the cumulative state of ``metrics``; because each emission supersedes the
    prior one for the same session, a reader keeps the latest snapshot per session rather than
    summing them. ``session_id`` becomes the note's task id (mirroring how the usage note carries
    its task id) so a reader can correlate snapshots to a session. A snapshot with no turns is an
    empty session and is skipped — emission never writes an empty marker and never raises so it
    cannot disturb the session it observes.

    Parameters
    ----------
    metrics : SessionMetrics
        The running session totals to snapshot.
    post_progress : ProgressPoster
        Poster that appends the note to the progress ledger (e.g. ``SynapseAgent.post_progress``).
    session_id : str
        Identifier correlating snapshots of the same session; recorded as the note's task id.
    task_id : str, optional
        The coordination task the session is advancing (the claim or board task id), carried in
        the note body so a reader can correlate the session's telemetry to the coordination work.
        Empty (the default) omits it, leaving the body unchanged.

    Returns
    -------
    bool
        ``True`` when a snapshot was posted; ``False`` when the session had no turns and was
        skipped.
    """
    if metrics.turns <= 0:
        return False
    note = format_session_metric_note(metrics, task_id=task_id)
    await post_progress(session_id, note, kind=SESSION_METRIC_NOTE_KIND)
    return True
