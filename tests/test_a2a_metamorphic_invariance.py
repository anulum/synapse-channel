# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — metamorphic invariance of A2A work semantics
"""Inputs that differ only in a property that must not matter keep the same work.

Each case sends one base request and one variant through the shipped HTTP+JSON
edge of a production bridge and compares the *work semantics*: the returned
task state, whether artifacts were invented, and exactly what was forwarded
into SYNAPSE (target and text). Identifiers that are allocated per request are
not part of the work and are excluded from the comparison.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

V1_HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}


def _base_message() -> dict[str, Any]:
    return {
        "messageId": "base-message",
        "role": "ROLE_USER",
        "parts": [{"text": "summarise the release notes"}],
    }


def _work(body: dict[str, Any] | bytes, headers: dict[str, str]) -> dict[str, Any]:
    """Send one request to a fresh production bridge and project its work semantics."""
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "metamorphic-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
    )
    harness = HandlerHarness("POST", "/message:send", body=body, headers=headers, bridge=bridge)
    status, _headers, raw = harness._request()
    response = json.loads(raw.decode("utf-8"))
    task = response.get("task", {})
    return {
        "status": status,
        "kind": sorted(response),
        "state": task.get("status", {}).get("state"),
        "artifacts": bool(task.get("artifacts")),
        "forwarded": list(bridge.agent.messages),
    }


def _with_message(**changes: Any) -> dict[str, Any]:
    message = _base_message()
    message.update(changes)
    return {"message": message}


_VARIANTS: list[tuple[str, dict[str, Any] | bytes, dict[str, str]]] = [
    ("message id", _with_message(messageId="0f5d3c8e-7d3b-4a6a-9a55-2f1c7e9d1b20"), V1_HEADERS),
    ("tck-shaped message id", _with_message(messageId="tck-artifact-text-x"), V1_HEADERS),
    ("unrelated metadata", _with_message(metadata={"ticket": "R-17", "trace": "on"}), V1_HEADERS),
    (
        "scenario-looking metadata",
        _with_message(metadata={"scenario": "artifact-data", "a2aScenario": "message-response"}),
        V1_HEADERS,
    ),
    (
        "scenario-looking configuration",
        {"message": _base_message(), "configuration": {"synapseScenario": "artifact-file"}},
        V1_HEADERS,
    ),
    (
        "reversed field order",
        json.dumps(
            {"message": dict(reversed(list(_base_message().items())))}, sort_keys=False
        ).encode("utf-8"),
        V1_HEADERS,
    ),
    ("client name", {"message": _base_message()}, {**V1_HEADERS, "User-Agent": "other-client/9"}),
]


@pytest.mark.parametrize(("label", "body", "headers"), _VARIANTS)
def test_irrelevant_input_properties_keep_the_same_work(
    label: str, body: dict[str, Any] | bytes, headers: dict[str, str]
) -> None:
    """The variant must be forwarded exactly like the base request."""
    base = _work({"message": _base_message()}, V1_HEADERS)
    variant = _work(body, headers)

    assert base["status"] == HTTPStatus.OK
    assert base["state"] == "TASK_STATE_WORKING"
    assert base["artifacts"] is False
    assert base["forwarded"], "the base request must reach SYNAPSE"
    assert variant == base, label


def test_the_relation_detects_a_real_change_of_work() -> None:
    """A different request text is a different job; the comparison must see it."""
    base = _work({"message": _base_message()}, V1_HEADERS)
    changed = _work(
        _with_message(parts=[{"text": "delete the release notes"}]),
        V1_HEADERS,
    )

    assert changed["state"] == base["state"]
    assert changed["forwarded"] != base["forwarded"]
