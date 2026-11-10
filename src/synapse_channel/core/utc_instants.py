# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — strict UTC instants in operator-written secret sets
"""Parse the strict ``YYYY-MM-DDTHH:MM:SSZ`` instants of operator-written sets.

Token sets and per-message key sets state validity windows in one strict form:
whole seconds, UTC, a literal ``Z``. Anything else, including an offset or a
fraction, is refused, so a window cannot be read two ways.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def parse_utc_instant(value: object) -> float:
    """Return the POSIX seconds of a strict ``YYYY-MM-DDTHH:MM:SSZ`` instant.

    Parameters
    ----------
    value : object
        The value read from the set file.

    Returns
    -------
    float
        Seconds since the epoch.

    Raises
    ------
    ValueError
        ``must be YYYY-MM-DDTHH:MM:SSZ`` for any other shape, ``is not a real
        date`` for a well-formed string naming no real date. Callers prefix the
        field and entry.
    """
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        raise ValueError("must be YYYY-MM-DDTHH:MM:SSZ")
    try:
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("is not a real date") from exc
    return moment.timestamp()
