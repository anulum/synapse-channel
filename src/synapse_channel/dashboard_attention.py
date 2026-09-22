# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — authenticated local attention feed
"""Serve the same owner-local attention queue the CLI reads."""

from __future__ import annotations

import sqlite3
import time
from http import HTTPStatus
from pathlib import Path

from synapse_channel.core.attention_store import AttentionStoreError, queue_view
from synapse_channel.dashboard_feed_serving import FeedResponse, json_response, plain_response


def serve_attention(path: Path | None) -> FeedResponse:
    """Return a bounded attention projection or fail visibly when unconfigured.

    Parameters
    ----------
    path : pathlib.Path or None
        Explicit owner-local attention store configured for this dashboard.

    Returns
    -------
    FeedResponse
        Authenticated JSON, an absent-feed 404 or a storage-failure 503.
    """
    if path is None:
        return plain_response(HTTPStatus.NOT_FOUND, "attention store not configured")
    if not path.is_file():
        return plain_response(HTTPStatus.SERVICE_UNAVAILABLE, "attention store unavailable")
    try:
        report = queue_view(path, now=time.time())
    except (AttentionStoreError, OSError, sqlite3.DatabaseError):
        return plain_response(HTTPStatus.SERVICE_UNAVAILABLE, "attention store unavailable")
    rows = report["alerts"]
    # No source payload, task body, account label or filesystem path leaves this route.
    alerts = [
        {
            "key": row["key"],
            "kind": row["kind"],
            "subject": row["subject"],
            "severity": row["severity"],
            "state": row["state"],
            "action": row["action"],
            "observed_at": row["observed_at"],
            "expires_at": row["expires_at"],
        }
        for row in rows[:200]
    ]
    return json_response(
        {
            "version": 1,
            "state": report["state"],
            "alerts": alerts,
            "remaining": max(0, len(rows) - len(alerts)),
            "snoozed_count": report["snoozed_count"],
            "observer_last_success": report["observers"],
            "authority": "owner_local_advisory",
        }
    )
