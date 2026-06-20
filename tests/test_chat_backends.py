# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the pluggable chat backends

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from synapse_channel.chat_backends import (
    OpenAIChatClient,
    RuleBasedClient,
    sanitize_text,
)

# --- sanitize_text -----------------------------------------------------------


def test_sanitize_collapses_whitespace() -> None:
    assert sanitize_text("  a\n\t b   c  ") == "a b c"


def test_sanitize_truncates_to_max_len() -> None:
    assert sanitize_text("abcdef", max_len=3) == "abc"


def test_sanitize_coerces_non_string() -> None:
    assert sanitize_text(123) == "123"  # type: ignore[arg-type]


# --- RuleBasedClient ---------------------------------------------------------


def test_rule_based_client_returns_canned_reply() -> None:
    client = RuleBasedClient(agent_name="ALPHA")
    reply = client.generate(system_prompt="s", user_prompt="u")
    assert reply.startswith("ALPHA:")
    assert "active on-channel" in reply


# --- OpenAIChatClient construction ------------------------------------------


def test_openai_client_strips_base_url_and_clamps_timeout() -> None:
    client = OpenAIChatClient(
        api_key="k", model="m", base_url="http://h/v1/", timeout_seconds=0.1
    )
    assert client.base_url == "http://h/v1"
    assert client.timeout_seconds == 3.0


# --- OpenAIChatClient.generate ----------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    monkeypatch.setattr("urllib.request.urlopen", handler)


def test_generate_returns_sanitised_content(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"choices": [{"message": {"content": "  hello   world  "}}]}

    def handler(req: Any, timeout: float) -> _FakeResponse:
        assert timeout == 3.0
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    _patch_urlopen(monkeypatch, handler)
    client = OpenAIChatClient(api_key="k", model="m", base_url="http://h/v1", timeout_seconds=3.0)
    assert client.generate(system_prompt="s", user_prompt="u") == "hello world"


def test_generate_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: Any, timeout: float) -> _FakeResponse:
        raise urllib.error.HTTPError(
            url="http://h/v1/chat/completions",
            code=500,
            msg="boom",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"server detail"),
        )

    _patch_urlopen(monkeypatch, handler)
    client = OpenAIChatClient(api_key="k", model="m", base_url="http://h/v1", timeout_seconds=3.0)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        client.generate(system_prompt="s", user_prompt="u")


def test_generate_raises_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: Any, timeout: float) -> _FakeResponse:
        raise urllib.error.URLError("dns failure")

    _patch_urlopen(monkeypatch, handler)
    client = OpenAIChatClient(api_key="k", model="m", base_url="http://h/v1", timeout_seconds=3.0)
    with pytest.raises(RuntimeError, match="connection error"):
        client.generate(system_prompt="s", user_prompt="u")


def test_generate_raises_on_unexpected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: Any, timeout: float) -> _FakeResponse:
        return _FakeResponse(json.dumps({"unexpected": True}).encode("utf-8"))

    _patch_urlopen(monkeypatch, handler)
    client = OpenAIChatClient(api_key="k", model="m", base_url="http://h/v1", timeout_seconds=3.0)
    with pytest.raises(RuntimeError, match="parse error"):
        client.generate(system_prompt="s", user_prompt="u")
