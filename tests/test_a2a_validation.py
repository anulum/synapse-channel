# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A bridge validation policy

from __future__ import annotations

from synapse_channel.a2a_validation import (
    is_supported_json_media_type,
    marker_task_id,
    strip_task_marker,
    validate_bridge_id,
    validate_webhook_url,
)


def test_marker_task_id_extracts_task_id_without_leaking_context() -> None:
    assert marker_task_id("reply\n[A2A-TASK:task-a contextId=ctx-a]") == "task-a"


def test_strip_task_marker_removes_bridge_marker() -> None:
    assert strip_task_marker("answer\n[A2A-TASK:task-a contextId=ctx-a]") == "answer"


def test_validate_bridge_id_rejects_path_separator() -> None:
    try:
        validate_bridge_id("../task", field="taskId")
    except ValueError as exc:
        assert str(exc) == "message.taskId contains unsupported characters"
    else:
        raise AssertionError("path separator was accepted")


def test_validate_webhook_url_requires_http_scheme_and_host() -> None:
    assert validate_webhook_url("https://example.test/hook") == "https://example.test/hook"
    for value, expected in (
        ("file:///tmp/hook", "pushNotificationConfig.webhookUrl must use http or https"),
        ("https:///hook", "pushNotificationConfig.webhookUrl must include a host"),
    ):
        try:
            validate_webhook_url(value)
        except ValueError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError(f"invalid webhook URL was accepted: {value}")


def test_is_supported_json_media_type_allows_a2a_json_with_charset() -> None:
    assert is_supported_json_media_type("application/a2a+json; charset=utf-8") is True
    assert is_supported_json_media_type("application/json") is True
    assert is_supported_json_media_type("") is True
    assert is_supported_json_media_type("text/plain") is False
