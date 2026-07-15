# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ordered Zed ACP readiness regression contracts
"""Verify fail-closed Zed ACP session-readiness evidence."""

from __future__ import annotations

import json
from pathlib import Path

from e2e.opencode_editors.zed_readiness import has_ready_session


def test_zed_session_readiness_requires_ordered_correlated_success(tmp_path: Path) -> None:
    """Reject missing, malformed, failed, uncorrelated, or out-of-order evidence."""
    trace = tmp_path / "trace.jsonl"
    assert has_ready_session(trace) is False

    request: dict[str, object] = {
        "direction": "client_to_agent",
        "id": "session-1",
        "method": "session/new",
    }
    response: dict[str, object] = {
        "direction": "agent_to_client",
        "error": False,
        "id": "session-1",
        "response_to": "session/new",
        "session_id_present": True,
    }
    update: dict[str, object] = {
        "direction": "agent_to_client",
        "method": "session/update",
    }
    valid_session = [
        {"direction": "agent_to_client", "method": "initialize"},
        request,
        response,
        update,
    ]

    def changed_event(original: dict[str, object], **changes: object) -> dict[str, object]:
        event = dict(original)
        event.update(changes)
        return event

    invalid_sessions: tuple[tuple[object, ...], ...] = (
        (),
        (*valid_session, "not-an-event"),
        (update, request, response),
        (request, changed_event(response, error=True), update),
        (request, changed_event(response, session_id_present=False), update),
        (request, changed_event(response, id="wrong"), update),
        (changed_event(request, direction="agent_to_client"), response, update),
        (changed_event(request, id=True), response, update),
        (request, request, response, update),
        (request, response, response, update),
        (response, update),
        (request, changed_event(response, direction="client_to_agent"), update),
        (request, response, changed_event(update, direction="client_to_agent")),
        (request, response),
    )
    for events in invalid_sessions:
        trace.write_text(
            "".join(f"{json.dumps(event)}\n" for event in events),
            encoding="utf-8",
        )
        assert has_ready_session(trace) is False

    trace.write_text("{invalid\n", encoding="utf-8")
    assert has_ready_session(trace) is False
    trace.write_text(
        "".join(f"{json.dumps(event)}\n" for event in valid_session),
        encoding="utf-8",
    )
    assert has_ready_session(trace) is True
