# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real localhost contracts for provider transports
"""Exercise provider wire families through real localhost HTTP sockets."""

from __future__ import annotations

import json
import threading

import pytest

from http_server_helpers import LocalHttpResponder
from synapse_channel.client.provider_http import (
    ProviderHTTPClient,
    ProviderHTTPError,
    ProviderWorkerBackend,
)
from synapse_channel.client.provider_profiles import PROFILES
from synapse_channel.core.http_response import DEFAULT_RESPONSE_LIMIT

PROVIDER_NAMES = tuple(PROFILES)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://user:secret@peer.example",
        "https://peer.example?token=secret",
        "file:///tmp/provider",
    ],
)
def test_provider_client_refuses_credential_or_non_http_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="provider base URL"):
        ProviderHTTPClient(PROFILES["ollama"], api_key="", base_url=base_url)


def test_paid_provider_requires_key_secure_transport_and_budget() -> None:
    with pytest.raises(ValueError, match="API key is required"):
        ProviderHTTPClient(PROFILES["anthropic"], api_key="")
    with pytest.raises(ValueError, match="requires HTTPS"):
        ProviderHTTPClient(PROFILES["anthropic"], api_key="secret", base_url="http://peer.example")
    with pytest.raises(ValueError, match="timeout must be positive"):
        ProviderHTTPClient(PROFILES["ollama"], api_key="", timeout=0)
    client = ProviderHTTPClient(PROFILES["anthropic"], api_key="secret")
    with pytest.raises(ValueError, match="estimated budget"):
        ProviderWorkerBackend(client, model="reviewed-model")


def test_empty_successful_provider_reply_cannot_become_worker_text() -> None:
    with LocalHttpResponder(body=b'{"choices":[{"message":{"content":""}}]}') as server:
        client = ProviderHTTPClient(PROFILES["ollama"], api_key="", base_url=server.url)
        worker = ProviderWorkerBackend(client, model="local-model")
        with pytest.raises(ProviderHTTPError, match="no_text_content"):
            worker.generate(system_prompt="system", user_prompt="user")
    assert len(server.requests) == 1


def test_invalid_json_provider_reply_is_redacted() -> None:
    with LocalHttpResponder(body=b'{"private":"provider-secret"') as server:
        client = ProviderHTTPClient(PROFILES["ollama"], api_key="", base_url=server.url)
        with pytest.raises(ProviderHTTPError, match="invalid_json") as caught:
            client.complete(
                model="local-model", system_prompt="s", user_prompt="u", max_output_tokens=8
            )
    assert "provider-secret" not in str(caught.value)


def test_provider_client_refuses_empty_model_before_network() -> None:
    client = ProviderHTTPClient(PROFILES["ollama"], api_key="", base_url="http://127.0.0.1:1")
    with pytest.raises(ValueError, match="model and positive"):
        client.complete(model="", system_prompt="s", user_prompt="u", max_output_tokens=8)


@pytest.mark.parametrize(
    ("name", "payload", "expected_tools"),
    [
        (
            "openai",
            {"choices": [{"message": {"content": [{"type": "text", "text": "hello"}]}}]},
            [{"type": "function", "function": {"name": "lookup"}}],
        ),
        (
            "anthropic",
            {
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"cache_creation_input_tokens": 2},
            },
            [{"name": "lookup", "input_schema": {"type": "object"}}],
        ),
        (
            "google",
            {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]},
            [{"name": "lookup", "parameters": {"type": "object"}}],
        ),
    ],
)
def test_provider_tool_declaration_is_sent_on_each_real_wire_family(
    name: str, payload: dict[str, object], expected_tools: list[dict[str, object]]
) -> None:
    with LocalHttpResponder(body=json.dumps(payload).encode()) as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="test-key", base_url=server.url)
        reply = client.complete(
            model="model-one",
            system_prompt="s",
            user_prompt="u",
            max_output_tokens=8,
            tools=expected_tools,
        )
    assert reply.text == "hello"
    sent = json.loads(server.requests[0].body)
    assert (
        sent["tools"][0]["functionDeclarations"] if name == "google" else sent["tools"]
    ) == expected_tools
    if name == "anthropic":
        assert reply.usage["cache_creation_input_tokens"] == 2


@pytest.mark.parametrize(
    ("name", "payload", "expected_path", "auth_header"),
    [
        (
            "deepseek",
            {
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
            "/chat/completions",
            "Authorization",
        ),
        (
            "anthropic",
            {
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
            "/messages",
            "X-api-key",
        ),
        (
            "google",
            {
                "candidates": [{"content": {"parts": [{"text": "hello"}]}}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
            },
            "/models/model%2Fone:generateContent",
            "X-goog-api-key",
        ),
    ],
)
def test_family_request_and_usage(
    name: str, payload: dict[str, object], expected_path: str, auth_header: str
) -> None:
    """Native and compatible families send distinct envelopes and parse usage."""
    with LocalHttpResponder(body=json.dumps(payload).encode()) as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="test-key", base_url=server.url)
        reply = client.complete(
            model="model/one", system_prompt="s", user_prompt="u", max_output_tokens=10
        )
    assert reply.text == "hello"
    assert reply.usage == {"input_tokens": 3, "output_tokens": 2}
    assert server.requests[0].path == expected_path
    headers = {key.lower(): value for key, value in server.requests[0].headers.items()}
    assert headers[auth_header.lower()] == ("Bearer test-key" if name == "deepseek" else "test-key")


@pytest.mark.parametrize(
    ("name", "frames", "expected_tool"),
    [
        (
            "openai",
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "he",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c",
                                        "function": {"name": "lookup", "arguments": '{\\"x\\":'},
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "llo",
                                "tool_calls": [{"index": 0, "function": {"arguments": "1}"}}],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                },
            ],
            "lookup",
        ),
        (
            "anthropic",
            [
                {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "tool_use", "id": "c", "name": "lookup", "input": {}},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "hello"},
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"x":1}'},
                },
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 4},
                },
            ],
            "lookup",
        ),
        (
            "google",
            [
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "hello"},
                                    {"functionCall": {"name": "lookup", "args": {"x": 1}}},
                                ]
                            }
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
                },
            ],
            "lookup",
        ),
    ],
)
def test_stream_tools_and_usage(
    name: str, frames: list[dict[str, object]], expected_tool: str
) -> None:
    """SSE responses cross a real socket and retain tool and usage evidence."""
    body = (
        b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames)
        + b"data: [DONE]\n\n"
    )
    with LocalHttpResponder(body=body, content_type="text/event-stream") as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="test-key", base_url=server.url)
        reply = client.complete(
            model="m", system_prompt="s", user_prompt="u", max_output_tokens=8, stream=True
        )
    assert reply.text == "hello"
    assert reply.usage == {"input_tokens": 3, "output_tokens": 4}
    tool_name = (
        reply.tool_calls[0]["function"]["name"] if name == "openai" else reply.tool_calls[0]["name"]
    )
    assert tool_name == expected_tool
    assert server.requests[0].headers["Accept"] == "text/event-stream"


def test_error_body_and_secret_are_never_exposed() -> None:
    """Provider HTTP errors retain status but redact both response and key."""
    with LocalHttpResponder(
        body=b"secret-provider-response",
        status=429,
        response_headers={"Retry-After": "2", "X-RateLimit-Remaining-Requests": "0"},
    ) as server:
        client = ProviderHTTPClient(PROFILES["openai"], api_key="secret-key", base_url=server.url)
        with pytest.raises(ProviderHTTPError) as captured:
            client.complete(model="m", system_prompt="s", user_prompt="u", max_output_tokens=8)
    assert captured.value.status == 429
    assert captured.value.retry_after == "2"
    assert captured.value.rate_limits["x-ratelimit-remaining-requests"] == "0"
    assert "secret" not in str(captured.value)


def test_oversized_provider_response_has_a_stable_redacted_error() -> None:
    """A real peer's oversized declaration must not escape the provider error API."""
    with LocalHttpResponder(
        body=b"secret-provider-response",
        response_headers={"Content-Length": str(DEFAULT_RESPONSE_LIMIT + 1)},
    ) as server:
        client = ProviderHTTPClient(PROFILES["openai"], api_key="secret-key", base_url=server.url)
        with pytest.raises(ProviderHTTPError) as captured:
            client.complete(model="m", system_prompt="s", user_prompt="u", max_output_tokens=8)
    assert captured.value.code == "response_too_large"
    assert "secret" not in str(captured.value)


def test_precancel_sends_no_request() -> None:
    """A cancelled turn cannot send a paid request."""
    cancelled = threading.Event()
    cancelled.set()
    with LocalHttpResponder(body=b"{}") as server:
        client = ProviderHTTPClient(PROFILES["openai"], api_key="k", base_url=server.url)
        with pytest.raises(ProviderHTTPError, match="cancelled"):
            client.complete(
                model="m", system_prompt="s", user_prompt="u", max_output_tokens=8, cancel=cancelled
            )
    assert server.requests == []


@pytest.mark.parametrize(
    ("name", "frame"),
    [
        ("openai", {"choices": [42]}),
        ("openai", {"choices": [{"delta": {"tool_calls": ["bad"]}}]}),
        ("anthropic", {"type": "content_block_start", "content_block": {"type": "tool_use"}}),
        ("google", {"candidates": [42]}),
        ("google", {"candidates": [{"content": {"parts": [42]}}]}),
    ],
)
def test_malformed_stream_shape_is_a_redacted_provider_error(
    name: str, frame: dict[str, object]
) -> None:
    """Malformed provider frames cannot escape as raw parser exceptions."""
    body = b"data: " + json.dumps(frame).encode() + b"\n\n"
    with LocalHttpResponder(body=body, content_type="text/event-stream") as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="secret-key", base_url=server.url)
        with pytest.raises(ProviderHTTPError, match="invalid_shape") as captured:
            client.complete(
                model="m", system_prompt="s", user_prompt="u", max_output_tokens=8, stream=True
            )
    assert "secret-key" not in str(captured.value)


@pytest.mark.parametrize(
    ("name", "frame", "error"),
    [
        ("openai", {"error": {"message": "private"}}, "stream_error"),
        ("anthropic", {"type": "error", "error": {"message": "private"}}, "stream_error"),
        ("google", {"error": {"message": "private"}}, "stream_error"),
        ("openai", {"choices": [{"delta": {"tool_calls": [{"index": -1}]}}]}, "invalid_tool_delta"),
        (
            "anthropic",
            {
                "type": "content_block_delta",
                "index": 9,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            },
            "invalid_tool_delta",
        ),
        (
            "google",
            {"candidates": [{"content": {"parts": [{"functionCall": "private"}]}}]},
            "invalid_shape",
        ),
        (
            "anthropic",
            {
                "type": "content_block_delta",
                "index": "wrong",
                "delta": {"type": "input_json_delta"},
            },
            "invalid_tool_delta",
        ),
    ],
)
def test_stream_refusals_do_not_expose_provider_payload(
    name: str, frame: dict[str, object], error: str
) -> None:
    body = b"data: " + json.dumps(frame).encode() + b"\n\n"
    with LocalHttpResponder(body=body, content_type="text/event-stream") as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="secret-key", base_url=server.url)
        with pytest.raises(ProviderHTTPError, match=error) as captured:
            client.complete(
                model="m", system_prompt="s", user_prompt="u", max_output_tokens=8, stream=True
            )
    assert "private" not in str(captured.value)


@pytest.mark.parametrize("body", [b"data: [DONE]\n\n", b"data: []\n\n"])
def test_empty_or_nonobject_stream_is_rejected(body: bytes) -> None:
    with LocalHttpResponder(body=body, content_type="text/event-stream") as server:
        client = ProviderHTTPClient(PROFILES["openai"], api_key="k", base_url=server.url)
        with pytest.raises(ProviderHTTPError):
            client.complete(
                model="m", system_prompt="s", user_prompt="u", max_output_tokens=8, stream=True
            )


@pytest.mark.parametrize("name", ["openai", "anthropic", "google"])
def test_invalid_nonstream_provider_shape_is_redacted(name: str) -> None:
    with LocalHttpResponder(body=b'{"private":"provider-secret"}') as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="secret-key", base_url=server.url)
        with pytest.raises(ProviderHTTPError, match="invalid_shape") as captured:
            client.complete(model="m", system_prompt="s", user_prompt="u", max_output_tokens=8)
    assert "provider-secret" not in str(captured.value)
    assert "secret-key" not in str(captured.value)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("openai", {"choices": [{"message": {"content": "ok", "tool_calls": {"private": 1}}}]}),
        ("openai", {"choices": [{"message": {"content": "ok", "tool_calls": ["private"]}}]}),
        (
            "google",
            {"candidates": [{"content": {"parts": [{"functionCall": "private"}]}}]},
        ),
    ],
)
def test_malformed_nonstream_tool_calls_never_reach_the_worker(
    name: str, payload: dict[str, object]
) -> None:
    """A successful HTTP status cannot turn malformed tool calls into a reply."""
    with LocalHttpResponder(body=json.dumps(payload).encode()) as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="secret-key", base_url=server.url)
        with pytest.raises(ProviderHTTPError, match="invalid_shape") as captured:
            client.complete(model="m", system_prompt="s", user_prompt="u", max_output_tokens=8)
    assert "private" not in str(captured.value)


@pytest.mark.parametrize("name", ["openai", "anthropic", "google"])
def test_model_discovery_refuses_missing_list(name: str) -> None:
    with LocalHttpResponder(body=b"{}") as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="k", base_url=server.url)
        with pytest.raises(ProviderHTTPError, match="invalid_model_list"):
            client.models()


@pytest.mark.parametrize("name", ["openai", "anthropic", "google"])
def test_model_discovery(name: str) -> None:
    """Each family uses its own documented model list response field."""
    data = {"models": [{"name": "models/m"}]} if name == "google" else {"data": [{"id": "m"}]}
    with LocalHttpResponder(body=json.dumps(data).encode()) as server:
        client = ProviderHTTPClient(PROFILES[name], api_key="k", base_url=server.url)
        assert client.models() == (("models/m",) if name == "google" else ("m",))
    assert server.requests[0].method == "GET"


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_each_provider_local_wire_contract(name: str) -> None:
    """Exercise auth, discovery, stream, tool, usage and rate headers per profile."""
    profile = PROFILES[name]
    if profile.family == "anthropic":
        frames = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 2}}},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "name": "lookup", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "ok"},
            },
            {"type": "message_delta", "usage": {"output_tokens": 3}},
        ]
    elif profile.family == "google":
        frames = [
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "ok"},
                                {"functionCall": {"name": "lookup", "args": {}}},
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3},
            }
        ]
    else:
        frames = [
            {
                "choices": [
                    {
                        "delta": {
                            "content": "ok",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c",
                                    "function": {"name": "lookup", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            }
        ]
    body = b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames)
    with LocalHttpResponder(
        body=body,
        content_type="text/event-stream",
        response_headers={"X-RateLimit-Remaining-Requests": "42"},
    ) as server:
        client = ProviderHTTPClient(profile, api_key="k", base_url=server.url)
        reply = client.complete(
            model="m", system_prompt="s", user_prompt="u", max_output_tokens=8, stream=True
        )
    assert reply.text == "ok"
    assert reply.tool_calls
    assert reply.usage == {"input_tokens": 2, "output_tokens": 3}
    assert reply.rate_limits["x-ratelimit-remaining-requests"] == "42"
    headers = {key.lower(): value for key, value in server.requests[0].headers.items()}
    assert (
        "x-goog-api-key"
        if name == "google"
        else "x-api-key"
        if name == "anthropic"
        else "authorization"
    ) in headers

    model_body = {"models": [{"name": "models/m"}]} if name == "google" else {"data": [{"id": "m"}]}
    with LocalHttpResponder(body=json.dumps(model_body).encode()) as model_server:
        client = ProviderHTTPClient(profile, api_key="k", base_url=model_server.url)
        assert client.models()


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_each_provider_error_and_cancellation(name: str) -> None:
    """No profile leaks error bodies or sends a pre-cancelled request."""
    profile = PROFILES[name]
    cancelled = threading.Event()
    cancelled.set()
    with LocalHttpResponder(body=b"provider-secret-body", status=429) as server:
        client = ProviderHTTPClient(profile, api_key="k", base_url=server.url)
        with pytest.raises(ProviderHTTPError, match="cancelled"):
            client.complete(
                model="m", system_prompt="s", user_prompt="u", max_output_tokens=8, cancel=cancelled
            )
        assert server.requests == []
        with pytest.raises(ProviderHTTPError) as captured:
            client.complete(model="m", system_prompt="s", user_prompt="u", max_output_tokens=8)
    assert captured.value.status == 429
    assert "provider-secret-body" not in str(captured.value)


def test_redirect_does_not_forward_api_key() -> None:
    """A 3xx response cannot send the bearer token to a second authority."""
    with LocalHttpResponder(body=b"{}") as destination:
        with LocalHttpResponder(
            body=b"redirect",
            status=302,
            response_headers={"Location": f"{destination.url}/stolen"},
        ) as source:
            client = ProviderHTTPClient(
                PROFILES["openai"], api_key="sensitive", base_url=source.url
            )
            with pytest.raises(ProviderHTTPError) as captured:
                client.complete(model="m", system_prompt="s", user_prompt="u", max_output_tokens=8)
    assert captured.value.status == 302
    assert destination.requests == []
