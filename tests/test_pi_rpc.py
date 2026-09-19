# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pi RPC transport contract tests
"""Pin strict frame boundaries and acceptance versus settled results."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from synapse_channel.participants.pi_rpc import (
    MAX_PENDING_BYTES,
    MAX_RECORD_BYTES,
    PiRpcDecoder,
    PiRpcError,
    assistant_metrics,
    assistant_text,
    response_for,
)


def test_partial_lf_frame_keeps_unicode_separators_inside_text() -> None:
    decoder = PiRpcDecoder()
    frame = (
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "stop",
                    "content": [{"type": "text", "text": "alpha\u2028beta\u2029gamma"}],
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\r\n"
    )
    assert decoder.feed(frame[:17]) == ()
    assert decoder.feed(frame[17:-1]) == ()
    records = decoder.feed(frame[-1:])
    assert len(records) == 1
    assert assistant_text(records[0]) == ("alpha\u2028beta\u2029gamma", False)
    decoder.finish()


def test_response_acceptance_does_not_imply_turn_completion() -> None:
    decoder = PiRpcDecoder()
    records = decoder.feed(
        b'{"type":"response","id":"req-1","command":"prompt","success":true}\n'
        b'{"type":"agent_start"}\n'
    )
    assert response_for(records[0], "req-1", "prompt") is True
    assert response_for(records[1], "req-1", "prompt") is None
    assert not any(record["type"] == "agent_end" for record in records)
    settled = decoder.feed(b'{"type":"agent_end","messages":[],"willRetry":false}\n')
    assert settled[0]["type"] == "agent_end"


@pytest.mark.parametrize(
    "raw",
    [b'{"type":"message_end"}\xe2\x80\xa8\n', b'{"type":4}\n', b"42\n", b"\xff\n"],
)
def test_malformed_or_non_json_record_is_refused(raw: bytes) -> None:
    with pytest.raises(PiRpcError):
        PiRpcDecoder().feed(raw)


def test_large_and_truncated_frames_fail_closed() -> None:
    decoder = PiRpcDecoder()
    with pytest.raises(PiRpcError, match="byte limit"):
        decoder.feed(b"x" * (MAX_RECORD_BYTES + 1))
    decoder = PiRpcDecoder()
    decoder.feed(b'{"type":"message_')
    with pytest.raises(PiRpcError, match="inside a record"):
        decoder.finish()
    with pytest.raises(PiRpcError, match="chunks must be bytes"):
        PiRpcDecoder().feed(cast(Any, "not bytes"))
    with pytest.raises(PiRpcError, match="pending output"):
        PiRpcDecoder().feed(b"x" * (MAX_PENDING_BYTES + 1))
    with pytest.raises(PiRpcError, match="record exceeded"):
        PiRpcDecoder().feed(b"x" * (MAX_RECORD_BYTES + 1) + b"\n")


def test_wrong_request_id_and_ambiguous_acceptance_are_refused() -> None:
    with pytest.raises(PiRpcError, match="correlation"):
        response_for(
            {"type": "response", "id": "other", "command": "prompt", "success": True},
            "req-1",
            "prompt",
        )
    with pytest.raises(PiRpcError, match="boolean"):
        response_for(
            {"type": "response", "id": "req-1", "command": "prompt", "success": 1},
            "req-1",
            "prompt",
        )


def test_final_message_usage_is_distinct_from_stream_estimates() -> None:
    update = {"type": "message_update", "usage": {"input": 1, "output": 1}}
    assert assistant_metrics(update) is None
    final: dict[str, Any] = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "stopReason": "stop",
            "content": [],
            "usage": {"input": 12, "output": 4, "cost": {"total": 0}},
        },
    }
    assert assistant_metrics(final) == (12, 4, 0.0, "stop")
    final["message"]["usage"]["input"] = True
    with pytest.raises(PiRpcError, match="token counts"):
        assistant_metrics(final)


@pytest.mark.parametrize(
    ("message", "error"),
    [
        ({"role": "assistant", "content": None, "stopReason": "stop"}, "content blocks"),
        ({"role": "assistant", "content": [4], "stopReason": "stop"}, "content block"),
        (
            {"role": "assistant", "content": [{"type": "text", "text": 4}], "stopReason": "stop"},
            "text block",
        ),
        ({"role": "assistant", "content": [], "stopReason": None}, "stop reason"),
    ],
)
def test_bad_final_assistant_content_is_refused(message: dict[str, Any], error: str) -> None:
    """A malformed final host message cannot become a successful empty answer."""
    with pytest.raises(PiRpcError, match=error):
        assistant_text({"type": "message_end", "message": message})


@pytest.mark.parametrize(
    ("usage", "error"),
    [
        (None, "final usage"),
        ({"input": -1, "output": 1, "cost": {"total": 0}}, "token counts"),
        ({"input": 1, "output": 1, "cost": {"total": float("nan")}}, "invalid cost"),
        ({"input": 1, "output": 1, "cost": {"total": True}}, "invalid cost"),
        ({"input": 1, "output": 1, "cost": {}}, "invalid cost"),
    ],
)
def test_bad_final_usage_is_refused(usage: object, error: str) -> None:
    """Pi's reported cost and counts must be finite, numeric and present."""
    record = {
        "type": "message_end",
        "message": {"role": "assistant", "content": [], "stopReason": "stop", "usage": usage},
    }
    with pytest.raises(PiRpcError, match=error):
        assistant_metrics(record)


def test_non_assistant_events_are_ignored_and_missing_usage_stop_is_refused() -> None:
    """User/tool messages cannot be counted as pi's final assistant usage."""
    user = {"type": "message_end", "message": {"role": "user", "content": []}}
    assert assistant_text(user) is None
    assert assistant_metrics(user) is None
    final = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "hidden"}],
            "usage": {"input": 1, "output": 1, "cost": {"total": 0}},
        },
    }
    with pytest.raises(PiRpcError, match="stop reason"):
        assistant_metrics(final)
