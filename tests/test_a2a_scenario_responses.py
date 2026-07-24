# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure A2A scenario response builders
"""Unit coverage for structured Message/Artifact scenario encoding."""

from __future__ import annotations

import base64

import pytest

from synapse_channel.a2a_scenario_responses import (
    TCK_DATA_ARTIFACT_BODY,
    TCK_DIRECT_MESSAGE_BODY,
    TCK_FILE_MEDIA_TYPE,
    TCK_FILE_NAME,
    TCK_FILE_RAW_BYTES,
    TCK_FILE_URL,
    TCK_TEXT_ARTIFACT_BODY,
    ScenarioKind,
    build_completed_task_with_artifact,
    build_direct_message_response,
    build_scenario_artifact,
    resolve_scenario,
)


@pytest.mark.parametrize(
    ("message_id", "expected"),
    [
        ("tck-artifact-text-abc12345", ScenarioKind.ARTIFACT_TEXT),
        ("tck-artifact-file-abc12345", ScenarioKind.ARTIFACT_FILE),
        ("tck-artifact-file-url-abc12345", ScenarioKind.ARTIFACT_FILE_URL),
        ("tck-artifact-data-abc12345", ScenarioKind.ARTIFACT_DATA),
        ("tck-message-response-abc12345", ScenarioKind.MESSAGE_RESPONSE),
        ("ordinary-id", None),
        ("tck-other-xyz", None),
    ],
)
def test_resolve_scenario_from_tck_message_id(
    message_id: str, expected: ScenarioKind | None
) -> None:
    assert resolve_scenario({"messageId": message_id}) is expected


def test_resolve_scenario_prefers_metadata_over_message_id() -> None:
    message = {
        "messageId": "tck-artifact-text-session",
        "metadata": {"a2aScenario": "message-response"},
    }
    assert resolve_scenario(message) is ScenarioKind.MESSAGE_RESPONSE


def test_resolve_scenario_from_configuration() -> None:
    message = {"messageId": "ordinary"}
    configuration = {"synapseScenario": "artifact-data"}
    assert resolve_scenario(message, configuration) is ScenarioKind.ARTIFACT_DATA


def test_build_direct_message_response_shape() -> None:
    body = build_direct_message_response(
        {"messageId": "u1", "contextId": "ctx-1", "taskId": "task-1"}
    )
    assert "task" not in body
    message = body["message"]
    assert message["role"] == "ROLE_AGENT"
    assert message["parts"][0]["text"] == TCK_DIRECT_MESSAGE_BODY
    assert message["contextId"] == "ctx-1"
    assert message["taskId"] == "task-1"
    assert message["messageId"]


def test_artifact_text_part() -> None:
    artifact = build_scenario_artifact(ScenarioKind.ARTIFACT_TEXT)
    assert artifact["artifactId"]
    assert artifact["parts"][0]["text"] == TCK_TEXT_ARTIFACT_BODY


def test_artifact_file_raw_part() -> None:
    artifact = build_scenario_artifact(ScenarioKind.ARTIFACT_FILE)
    part = artifact["parts"][0]
    assert part["filename"] == TCK_FILE_NAME
    assert part["mediaType"] == TCK_FILE_MEDIA_TYPE
    assert base64.b64decode(part["raw"]) == TCK_FILE_RAW_BYTES


def test_artifact_file_url_part() -> None:
    artifact = build_scenario_artifact(ScenarioKind.ARTIFACT_FILE_URL)
    part = artifact["parts"][0]
    assert part["url"] == TCK_FILE_URL
    assert part["filename"] == TCK_FILE_NAME
    assert part["mediaType"] == TCK_FILE_MEDIA_TYPE


def test_artifact_data_part() -> None:
    artifact = build_scenario_artifact(ScenarioKind.ARTIFACT_DATA)
    assert artifact["parts"][0]["data"] == TCK_DATA_ARTIFACT_BODY


def test_completed_task_carries_artifact_and_terminal_state() -> None:
    task = build_completed_task_with_artifact(
        {
            "messageId": "tck-artifact-text-session",
            "role": "ROLE_USER",
            "parts": [{"text": "TCK artifact test"}],
        },
        kind=ScenarioKind.ARTIFACT_TEXT,
        task_id="task-1",
        context_id="ctx-1",
        target="WORKER",
        now=1.5,
    )
    assert task["id"] == "task-1"
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(task["artifacts"]) == 1
    assert task["artifacts"][0]["parts"][0]["text"] == TCK_TEXT_ARTIFACT_BODY
    assert task["metadata"]["a2aScenario"] == "artifact-text"


def test_message_response_cannot_build_artifact() -> None:
    with pytest.raises(ValueError, match="MESSAGE_RESPONSE"):
        build_scenario_artifact(ScenarioKind.MESSAGE_RESPONSE)
