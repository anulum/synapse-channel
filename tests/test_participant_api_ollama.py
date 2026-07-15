# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Ollama REST API participant
"""Tests for :mod:`synapse_channel.participants.api_ollama`.

Turns are driven through an injected HTTP poster, so the request body and the response handling are
exercised without touching the network. A separate test monkeypatches ``urllib`` to cover the
default poster's request construction and scheme guard.
"""

from __future__ import annotations

import json
import urllib.request
from types import TracebackType
from typing import Any

import pytest

from synapse_channel.participants import api_ollama
from synapse_channel.participants.api_ollama import (
    DEFAULT_ENDPOINT,
    OllamaApiParticipant,
    build_ollama_api_body,
)
from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.participant import ParticipantChannel

_IDENTITY = "SC/ollama-api"
_MODEL = "gemma3:1b"


def _ok_body(answer: str = "pong") -> bytes:
    return json.dumps(
        {
            "model": _MODEL,
            "response": answer,
            "prompt_eval_count": 11,
            "eval_count": 25,
            "done_reason": "stop",
        }
    ).encode("utf-8")


class _Poster:
    """Records the POST and returns a scripted body (or raises a scripted error)."""

    def __init__(self, *, body: bytes = b"", error: Exception | None = None) -> None:
        self._body = body
        self._error = error
        self.calls: list[tuple[str, bytes, float]] = []

    def __call__(self, url: str, body: bytes, *, timeout: float) -> bytes:
        self.calls.append((url, body, timeout))
        if self._error is not None:
            raise self._error
        return self._body


def _seat(poster: _Poster, *, endpoint: str = DEFAULT_ENDPOINT) -> OllamaApiParticipant:
    return OllamaApiParticipant(_IDENTITY, model=_MODEL, endpoint=endpoint, poster=poster)


def test_build_body_requests_a_non_streamed_completion() -> None:
    body = json.loads(build_ollama_api_body(model=_MODEL, prompt="hello"))
    assert body == {"model": _MODEL, "prompt": "hello", "stream": False}


def test_run_turn_parses_a_successful_response() -> None:
    poster = _Poster(body=_ok_body("pong"))
    result = _seat(poster).run_turn(TurnRequest(topic_id="t", prompt="ping"))
    assert result["answer"] == "pong"
    assert result["channel"] == ParticipantChannel.API.value
    assert result["input_tokens"] == 11
    assert result["output_tokens"] == 25
    assert result["is_error"] is False
    # The composed prompt and model went out on the request.
    url, sent, _ = poster.calls[0]
    assert url == DEFAULT_ENDPOINT
    assert json.loads(sent)["model"] == _MODEL


async def test_take_turn_stamps_the_configured_model() -> None:
    poster = _Poster(body=_ok_body())
    result = await _seat(poster).take_turn(TurnRequest(topic_id="t", prompt="ping"))
    assert result["model"] == _MODEL


def test_transport_failure_becomes_an_error_result() -> None:
    poster = _Poster(error=OSError("connection refused"))
    result = _seat(poster).run_turn(TurnRequest(topic_id="t", prompt="ping"))
    assert result["is_error"] is True
    assert "failed to reach" in result["reason"]


def test_non_json_body_is_an_error() -> None:
    poster = _Poster(body=b"not json")
    result = _seat(poster).run_turn(TurnRequest(topic_id="t", prompt="ping"))
    assert result["is_error"] is True
    assert "non-JSON" in result["reason"]


def test_non_object_json_is_an_error() -> None:
    poster = _Poster(body=b"[1, 2, 3]")
    result = _seat(poster).run_turn(TurnRequest(topic_id="t", prompt="ping"))
    assert result["is_error"] is True
    assert "unexpected response shape" in result["reason"]


def test_health_reports_configured_api_seat() -> None:
    health = _seat(_Poster()).health()
    assert health.available is True
    assert health.channel is ParticipantChannel.API
    assert DEFAULT_ENDPOINT in health.detail
    assert _seat(_Poster()).identity == _IDENTITY
    assert _seat(_Poster()).channel is ParticipantChannel.API


class _FakeResponse:
    """A minimal context-manager stand-in for an ``http.client`` response."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._payload if amount < 0 else self._payload[:amount]


def test_default_poster_posts_json_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["method"] = request.get_method()
        captured["content_type"] = request.get_header("Content-type")
        captured["timeout"] = timeout
        return _FakeResponse(_ok_body("from urlopen"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = api_ollama._default_poster(DEFAULT_ENDPOINT, b'{"x": 1}', timeout=5.0)
    assert json.loads(out)["response"] == "from urlopen"
    assert captured["url"] == DEFAULT_ENDPOINT
    assert captured["method"] == "POST"
    assert captured["content_type"] == "application/json"
    assert captured["timeout"] == 5.0


def test_default_poster_rejects_a_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="http"):
        api_ollama._default_poster("file:///etc/passwd", b"{}", timeout=1.0)


def test_bad_endpoint_scheme_surfaces_as_an_error_turn() -> None:
    # The default poster's scheme guard travels back as an error result, not an exception.
    seat = OllamaApiParticipant(_IDENTITY, model=_MODEL, endpoint="ftp://nope")
    result = seat.run_turn(TurnRequest(topic_id="t", prompt="ping"))
    assert result["is_error"] is True
    assert "failed to reach" in result["reason"]
