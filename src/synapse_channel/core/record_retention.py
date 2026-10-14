# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — observed calendar retention window
"""Measure retained timestamps without inferring deployment age or pruning."""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass

from synapse_channel.core.record_keeping import RecordKeepingExport, utc_event_time

MINIMUM_RETENTION_MONTHS = 6
RETENTION_SCOPE_NOTE = (
    "This is a UTC observation comparator, not a determination of legal deadlines. "
    "The six-calendar-month anniversary clamps an absent day to month end. "
    "A short window cannot distinguish a young deployment from deleted records. "
    "An old event does not prove continuous logging or retention of every record. "
    "Articles 19(1) and 26(6) concern applicable high-risk systems and logs under "
    "the provider's or deployer's control; other law and data protection may alter "
    "the appropriate retention period. No data is deleted by this command."
)


@dataclass(frozen=True, slots=True)
class RetentionPosture:
    """Observed extent and reference clock of a retained snapshot.

    Attributes
    ----------
    minimum_months : int
        Six-calendar-month observation comparator.
    as_of, anniversary : str, str or None
        UTC reference time and the oldest event's six-month anniversary.
    retained_span_days, oldest_event_age_days : float or None
        Chronological extent and age of the oldest retained event.
    reaches_floor : bool
        The reference clock reaches the anniversary, with no future events.
    future_events : bool
        At least one selected timestamp lies after the reference clock.
    event_count : int
        Number of selected retained events.
    note : str
        Observational reading that makes no compliance judgement.
    """

    minimum_months: int
    as_of: str
    anniversary: str | None
    retained_span_days: float | None
    oldest_event_age_days: float | None
    reaches_floor: bool
    future_events: bool
    event_count: int
    note: str


def assess_retention(export: RecordKeepingExport, *, now: float) -> RetentionPosture:
    """Compare the oldest retained event's calendar anniversary with a clock.

    Parameters
    ----------
    export : RecordKeepingExport
        Complete selected snapshot returned by ``build_export``.
    now : float
        Finite reference Unix timestamp, represented in UTC.

    Returns
    -------
    RetentionPosture
        Explicit empty, short, reached or future-clock observation.

    Raises
    ------
    ValueError
        The reference time or anniversary exceeds the supported UTC calendar.
    """
    clock = utc_event_time(now)
    if export.first_ts is None or export.last_ts is None:
        return RetentionPosture(
            MINIMUM_RETENTION_MONTHS,
            clock.isoformat(),
            None,
            None,
            None,
            False,
            False,
            0,
            "The selected log is empty; no retention window can be measured.",
        )
    oldest = utc_event_time(export.first_ts)
    year, month_index = divmod(oldest.year * 12 + oldest.month - 1 + 6, 12)
    month = month_index + 1
    day = min(oldest.day, calendar.monthrange(year, month)[1])
    try:
        anniversary = oldest.replace(year=year, month=month, day=day)
    except ValueError as exc:
        raise ValueError("retention anniversary is outside the supported UTC calendar") from exc
    future = export.last_ts > now
    reached = not future and clock >= anniversary
    note = "The retained window reaches the six-calendar-month comparator."
    if future:
        note = "Selected events lie after the reference clock; retention reach is unconfirmed."
    elif not reached:
        note = "The window is short; deployment age and past pruning remain unknown."
    return RetentionPosture(
        MINIMUM_RETENTION_MONTHS,
        clock.isoformat(),
        anniversary.isoformat(),
        (export.last_ts - export.first_ts) / 86_400,
        (now - export.first_ts) / 86_400,
        reached,
        future,
        len(export.entries),
        note,
    )


def retention_to_json(posture: RetentionPosture) -> dict[str, object]:
    """Return observed calendar fields and their interpretation limits."""
    return {**asdict(posture), "scope_note": RETENTION_SCOPE_NOTE}


def render_retention(posture: RetentionPosture) -> str:
    """Render the reference clock, anniversary and observed retention reach."""
    return "\n".join(
        [
            "Retained-log observation (Articles 19(1) / 26(6))",
            f"Comparator: {posture.minimum_months} calendar months",
            f"Reference clock: {posture.as_of}",
            f"Oldest event anniversary: {posture.anniversary or 'unavailable'}",
            f"Retained events: {posture.event_count}",
            f"Reaches comparator: {'yes' if posture.reaches_floor else 'unconfirmed'}",
            f"Reading: {posture.note}",
            "",
            RETENTION_SCOPE_NOTE,
        ]
    )
