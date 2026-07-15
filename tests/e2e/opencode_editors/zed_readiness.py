# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ordered Zed ACP session-readiness evidence
"""Prove a successful ordered Zed ACP session from bounded JSONL evidence."""

from __future__ import annotations

import json
from pathlib import Path


def has_ready_session(trace: Path) -> bool:
    """Return whether one trace proves an ordered successful ACP session.

    Parameters
    ----------
    trace:
        Content-minimised JSONL evidence written by the ACP trace proxy.

    Returns
    -------
    bool
        ``True`` only after a correlated successful ``session/new`` response
        with a session identifier precedes the first inbound session update.
    """
    if not trace.is_file():
        return False
    try:
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not events or not all(isinstance(event, dict) for event in events):
        return False

    request_id: int | str | None = None
    response_seen = False
    for event in events:
        direction = event.get("direction")
        method = event.get("method")
        if method == "session/new":
            if direction != "client_to_agent" or request_id is not None:
                return False
            candidate = event.get("id")
            if isinstance(candidate, bool) or not isinstance(candidate, (int, str)):
                return False
            request_id = candidate
            continue
        if event.get("response_to") == "session/new":
            response_id = event.get("id")
            if (
                direction != "agent_to_client"
                or response_seen
                or request_id is None
                or isinstance(response_id, bool)
                or not isinstance(response_id, (int, str))
                or type(response_id) is not type(request_id)
                or response_id != request_id
                or event.get("error") is not False
                or event.get("session_id_present") is not True
            ):
                return False
            response_seen = True
            continue
        if method == "session/update":
            if direction != "agent_to_client" or not response_seen:
                return False
            return True
    return False
