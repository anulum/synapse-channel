# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the pluggable chat backends

from __future__ import annotations

import json

import pytest

from http_server_helpers import LocalHttpResponder
from hub_e2e_helpers import _free_port
from synapse_channel.client.chat_backends import (
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
    reply = RuleBasedClient().generate(system_prompt="s", user_prompt="u")
    assert reply == "message received via Synapse. I am active on-channel."
    # The reply carries no sender prefix; the envelope already records the author.
    assert not reply.startswith(("ALPHA:", "FAST:"))


# --- OpenAIChatClient construction ------------------------------------------


def test_openai_client_refuses_non_http_schemes() -> None:
    # a file:// or custom scheme smuggled in through configuration must be
    # refused at construction, not silently opened at request time
    for base_url in ("file:///etc/passwd", "ftp://host/v1", "unix:///tmp/sock", "host/v1"):
        with pytest.raises(ValueError, match="must be http"):
            OpenAIChatClient(api_key="k", model="m", base_url=base_url, timeout_seconds=1.0)


def test_openai_client_accepts_both_http_schemes() -> None:
    for base_url in ("http://h/v1", "https://h/v1", "HTTPS://h/v1"):
        client = OpenAIChatClient(api_key="k", model="m", base_url=base_url, timeout_seconds=1.0)
        assert client.base_url == base_url


def test_openai_client_strips_base_url_and_clamps_timeout() -> None:
    client = OpenAIChatClient(api_key="k", model="m", base_url="http://h/v1/", timeout_seconds=0.1)
    assert client.base_url == "http://h/v1"
    assert client.timeout_seconds == 3.0


# --- OpenAIChatClient.generate ----------------------------------------------


def test_generate_returns_sanitised_content() -> None:
    payload = {"choices": [{"message": {"content": "  hello   world  "}}]}
    with LocalHttpResponder(body=json.dumps(payload).encode("utf-8")) as server:
        client = OpenAIChatClient(
            api_key="k",
            model="m",
            base_url=f"{server.url}/v1",
            timeout_seconds=3.0,
        )

        assert client.generate(system_prompt="s", user_prompt="u") == "hello world"

    request = server.requests[0]
    assert request.method == "POST"
    assert request.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer k"
    body = json.loads(request.body.decode("utf-8"))
    assert body["model"] == "m"
    assert body["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_generate_raises_on_http_error() -> None:
    with LocalHttpResponder(body=b"server detail", status=500) as server:
        client = OpenAIChatClient(
            api_key="k",
            model="m",
            base_url=f"{server.url}/v1",
            timeout_seconds=3.0,
        )
        with pytest.raises(RuntimeError, match="HTTP 500"):
            client.generate(system_prompt="s", user_prompt="u")


def test_generate_raises_on_connection_error() -> None:
    client = OpenAIChatClient(
        api_key="k",
        model="m",
        base_url=f"http://127.0.0.1:{_free_port()}/v1",
        timeout_seconds=3.0,
    )
    with pytest.raises(RuntimeError, match="connection error"):
        client.generate(system_prompt="s", user_prompt="u")


def test_generate_raises_on_unexpected_shape() -> None:
    with LocalHttpResponder(body=json.dumps({"unexpected": True}).encode("utf-8")) as server:
        client = OpenAIChatClient(
            api_key="k",
            model="m",
            base_url=f"{server.url}/v1",
            timeout_seconds=3.0,
        )
        with pytest.raises(RuntimeError, match="parse error"):
            client.generate(system_prompt="s", user_prompt="u")
