# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the A2A task-shape helpers

from __future__ import annotations

import json
import uuid

import pytest

from synapse_channel import a2a_task_flow
from synapse_channel.a2a_errors import A2AValidationError


class TestRenderMessageText:
    """Cover every message-part branch of :func:`render_message_text`."""

    def test_text_part_is_rendered_verbatim(self) -> None:
        message = {"parts": [{"text": "hello world"}]}
        assert a2a_task_flow.render_message_text(message) == "hello world"

    def test_data_part_is_json_encoded_with_sorted_keys(self) -> None:
        message = {"parts": [{"data": {"b": 2, "a": 1}}]}
        assert a2a_task_flow.render_message_text(message) == json.dumps(
            {"a": 1, "b": 2}, sort_keys=True
        )

    def test_url_part_is_stringified(self) -> None:
        message = {"parts": [{"url": "https://example.test/x"}]}
        assert a2a_task_flow.render_message_text(message) == "https://example.test/x"

    def test_file_part_with_all_fields_is_summarised(self) -> None:
        message = {"parts": [{"file": {"name": "a.png", "mimeType": "image/png", "uri": "u://a"}}]}
        assert a2a_task_flow.render_message_text(message) == "[file: a.png; image/png; u://a]"

    def test_file_part_keeps_only_truthy_fields(self) -> None:
        message = {"parts": [{"file": {"name": "solo.bin", "mimeType": "", "uri": None}}]}
        assert a2a_task_flow.render_message_text(message) == "[file: solo.bin]"

    def test_file_part_with_no_truthy_fields_renders_nothing(self) -> None:
        # Exercises the ``if file_bits:`` false branch: an all-empty file dict.
        message = {"parts": [{"file": {"name": "", "mimeType": None, "uri": ""}}]}
        assert a2a_task_flow.render_message_text(message) == ""

    def test_file_field_that_is_not_a_dict_is_ignored(self) -> None:
        # ``file`` present but not a mapping — falls through with nothing rendered.
        message = {"parts": [{"file": "not-a-dict"}]}
        assert a2a_task_flow.render_message_text(message) == ""

    def test_raw_part_is_masked(self) -> None:
        message = {"parts": [{"raw": "secret-bytes"}]}
        assert a2a_task_flow.render_message_text(message) == "[raw omitted]"

    def test_non_dict_part_is_skipped(self) -> None:
        message = {"parts": ["not-a-part", None, 42, {"text": "kept"}]}
        assert a2a_task_flow.render_message_text(message) == "kept"

    def test_unknown_dict_part_renders_nothing(self) -> None:
        message = {"parts": [{"unsupported": "value"}]}
        assert a2a_task_flow.render_message_text(message) == ""

    def test_missing_parts_key_renders_empty_string(self) -> None:
        assert a2a_task_flow.render_message_text({}) == ""

    def test_empty_text_fragments_are_dropped_before_joining(self) -> None:
        # The final comprehension drops falsy fragments; a "" text keeps company
        # with a real fragment so both the true and false ``if text`` arms run.
        message = {"parts": [{"text": ""}, {"text": "kept"}]}
        assert a2a_task_flow.render_message_text(message) == "kept"

    def test_multiple_parts_are_newline_joined_and_stripped(self) -> None:
        message = {
            "parts": [
                {"text": "  line one"},
                {"url": "u://two"},
                {"raw": "x"},
            ]
        }
        assert a2a_task_flow.render_message_text(message) == "line one\nu://two\n[raw omitted]"


class TestResolveTarget:
    """Cover metadata resolution and fallback in :func:`resolve_target`."""

    def test_explicit_target_wins(self) -> None:
        message = {"metadata": {"target": "AGENT-A"}}
        assert a2a_task_flow.resolve_target(message, default="D") == "AGENT-A"

    def test_synapse_target_alias_is_used(self) -> None:
        message = {"metadata": {"synapseTarget": "AGENT-B"}}
        assert a2a_task_flow.resolve_target(message, default="D") == "AGENT-B"

    def test_empty_target_falls_through_to_alias(self) -> None:
        # ``target`` present but falsy → the ``or`` picks up ``synapseTarget``.
        message = {"metadata": {"target": "", "synapseTarget": "AGENT-C"}}
        assert a2a_task_flow.resolve_target(message, default="D") == "AGENT-C"

    def test_no_target_keys_returns_default(self) -> None:
        message = {"metadata": {"unrelated": "x"}}
        assert a2a_task_flow.resolve_target(message, default="D") == "D"

    def test_non_dict_metadata_returns_default(self) -> None:
        assert a2a_task_flow.resolve_target({"metadata": "nope"}, default="D") == "D"

    def test_missing_metadata_returns_default(self) -> None:
        assert a2a_task_flow.resolve_target({}, default="D") == "D"


class TestBuildWorkingTask:
    """Verify the internal task record shape from :func:`build_working_task`."""

    def test_full_record_is_assembled(self) -> None:
        message = {"messageId": "m-1", "parts": [{"text": "hi"}]}
        task = a2a_task_flow.build_working_task(
            message,
            task_id="task-1",
            context_id="ctx-1",
            target="AGENT-A",
            now=1234.5,
        )
        assert task["id"] == "task-1"
        assert task["contextId"] == "ctx-1"
        assert task["status"]["state"] == "TASK_STATE_SUBMITTED"
        assert task["status"]["message"] == {
            "messageId": "m-1",
            "role": "ROLE_USER",
            "parts": [{"text": "hi"}],
        }
        assert task["history"] == [message]
        assert task["artifacts"] == []
        assert task["metadata"] == {
            "synapseTarget": "AGENT-A",
            "a2aTaskId": "task-1",
            "a2aContextId": "ctx-1",
            "createdAt": 1234.5,
            "updatedAt": 1234.5,
        }


class TestPrepareContinuation:
    """Cover continuation binding and validation in :func:`prepare_continuation`."""

    def test_binds_task_and_context_when_context_omitted(self) -> None:
        task = {"id": "task-1", "contextId": "ctx-1"}
        message = {"messageId": "m-2", "parts": [{"text": "more"}]}
        continued = a2a_task_flow.prepare_continuation(task, message)
        assert continued["taskId"] == "task-1"
        assert continued["contextId"] == "ctx-1"
        assert continued["messageId"] == "m-2"
        assert continued["parts"] == [{"text": "more"}]
        # The source message is copied, not mutated in place.
        assert "taskId" not in message

    def test_matching_context_is_accepted(self) -> None:
        task = {"id": "task-1", "contextId": "ctx-1"}
        message = {"contextId": "ctx-1", "messageId": "m-3"}
        continued = a2a_task_flow.prepare_continuation(task, message)
        assert continued["contextId"] == "ctx-1"
        assert continued["taskId"] == "task-1"

    def test_mismatched_context_raises_validation_error(self) -> None:
        task = {"id": "task-1", "contextId": "ctx-1"}
        message = {"contextId": "ctx-OTHER"}
        with pytest.raises(A2AValidationError):
            a2a_task_flow.prepare_continuation(task, message)

    def test_absent_task_identifiers_default_to_empty_strings(self) -> None:
        continued = a2a_task_flow.prepare_continuation({}, {"messageId": "m-4"})
        assert continued["taskId"] == ""
        assert continued["contextId"] == ""


class TestUserStatusMessage:
    """Cover message-id handling in :func:`user_status_message`."""

    def test_supplied_message_id_is_preserved(self) -> None:
        status = a2a_task_flow.user_status_message(
            {"messageId": "keep-me", "parts": [{"text": "x"}]}
        )
        assert status == {
            "messageId": "keep-me",
            "role": "ROLE_USER",
            "parts": [{"text": "x"}],
        }

    def test_missing_message_id_is_synthesised(self) -> None:
        status = a2a_task_flow.user_status_message({})
        # A fresh UUID string is minted; ``parts`` defaults to an empty list.
        assert status["role"] == "ROLE_USER"
        assert status["parts"] == []
        # The synthesised id parses as a UUID.
        assert uuid.UUID(str(status["messageId"]))

    def test_synthesised_ids_are_unique_per_call(self) -> None:
        first = a2a_task_flow.user_status_message({})
        second = a2a_task_flow.user_status_message({})
        assert first["messageId"] != second["messageId"]


class TestStoredTaskTarget:
    """Cover stored-target recovery in :func:`stored_task_target`."""

    def test_stored_target_is_returned(self) -> None:
        task = {"metadata": {"synapseTarget": "AGENT-A"}}
        assert a2a_task_flow.stored_task_target(task, default="D") == "AGENT-A"

    def test_empty_stored_target_returns_default(self) -> None:
        task = {"metadata": {"synapseTarget": ""}}
        assert a2a_task_flow.stored_task_target(task, default="D") == "D"

    def test_non_dict_metadata_returns_default(self) -> None:
        assert a2a_task_flow.stored_task_target({"metadata": None}, default="D") == "D"

    def test_missing_metadata_returns_default(self) -> None:
        assert a2a_task_flow.stored_task_target({}, default="D") == "D"
