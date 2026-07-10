# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A bridge validation policy

from __future__ import annotations

from synapse_channel.a2a_errors import A2AValidationError
from synapse_channel.a2a_validation import (
    MAX_A2A_MESSAGE_PARTS,
    is_supported_json_media_type,
    validate_bridge_id,
    validate_message_parts,
    validate_webhook_url,
)


def test_validate_bridge_id_rejects_path_separator() -> None:
    validate_bridge_id(None, field="taskId")
    validate_bridge_id("task-1", field="taskId")
    try:
        validate_bridge_id("../task", field="taskId")
    except A2AValidationError as exc:
        assert str(exc) == "message.taskId contains unsupported characters"
    else:
        raise AssertionError("path separator was accepted")


def test_validate_message_parts_rejects_oversized_part_array() -> None:
    assert validate_message_parts([{"text": "x"}]) == [{"text": "x"}]
    invalid_values: tuple[object, ...] = (None, [], "bad")
    for value in invalid_values:
        try:
            validate_message_parts(value)
        except ValueError as exc:
            assert str(exc) == "message.parts must be a non-empty array"
        else:
            raise AssertionError(f"invalid message.parts was accepted: {value!r}")
    try:
        validate_message_parts([{"text": "x"}] * (MAX_A2A_MESSAGE_PARTS + 1))
    except ValueError as exc:
        assert str(exc) == "message.parts exceeds maximum supported length"
    else:
        raise AssertionError("oversized message.parts array was accepted")


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


def test_validate_webhook_url_rejects_local_network_hosts() -> None:
    for value in (
        "http://localhost/hook",
        "http://127.0.0.1/hook",
        "http://10.0.0.5/hook",
        "http://[::1]/hook",
    ):
        try:
            validate_webhook_url(value)
        except ValueError as exc:
            assert str(exc) == "pushNotificationConfig.webhookUrl must not target local networks"
        else:
            raise AssertionError(f"local webhook URL was accepted: {value}")


def test_validate_webhook_url_rejects_embedded_credentials() -> None:
    try:
        validate_webhook_url("https://user:secret@example.test/hook")
    except ValueError as exc:
        assert str(exc) == "pushNotificationConfig.webhookUrl must not include credentials"
    else:
        raise AssertionError("webhook URL with embedded credentials was accepted")


def test_is_supported_json_media_type_allows_a2a_json_with_charset() -> None:
    assert is_supported_json_media_type("application/a2a+json; charset=utf-8") is True
    assert is_supported_json_media_type("application/json") is True
    assert is_supported_json_media_type("") is True
    assert is_supported_json_media_type("text/plain") is False
