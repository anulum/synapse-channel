# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded provider HTTP transport and response normalization
"""Read documented provider HTTP shapes without conflating them with live support.

All public methods are synchronous. Callers can pass a cancellation event; its
next check closes the response. Network reads also have a finite timeout.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.client.provider_budget import EstimatedSpendGuard
from synapse_channel.client.provider_profiles import ProviderProfile
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.http_response import (
    DEFAULT_RESPONSE_LIMIT,
    BoundedReadError,
    read_bounded,
)


class ProviderHTTPError(SynapseError, RuntimeError):
    """An HTTP or response-contract error with no untrusted body or URL text."""

    code = "provider_http"

    def __init__(
        self,
        code: str,
        *,
        status: int | None = None,
        retry_after: str = "",
        rate_limits: Mapping[str, str] | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.retry_after = retry_after
        self.rate_limits = rate_limits or {}
        suffix = f" ({status})" if status is not None else ""
        super().__init__(f"provider {code}{suffix}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent a provider redirect from forwarding an API credential elsewhere."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """Leave 3xx responses as HTTP errors for the caller."""
        return None


def open_provider_request(request: urllib.request.Request, *, timeout: float) -> Any:
    """Open HTTP without credential-forwarding redirects."""
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)  # nosec B310


@dataclass(frozen=True)
class ProviderReply:
    """Normalized text, tool calls, token usage and rate-limit metadata."""

    text: str
    tool_calls: tuple[dict[str, Any], ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)
    rate_limits: Mapping[str, str] = field(default_factory=dict)
    finish_reason: str = ""


def _json_object(raw: bytes) -> dict[str, Any]:
    """Decode an object while hiding provider-controlled bytes on failure."""
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ProviderHTTPError("invalid_json") from exc
    if not isinstance(value, dict):
        raise ProviderHTTPError("invalid_shape")
    return value


def _text(value: Any) -> str:
    """Collect provider text chunks and leave non-text content out of replies."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text", ""))
            for item in value
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def _validated_calls(value: Any) -> tuple[dict[str, Any], ...]:
    """Accept only provider tool-call objects at the public reply boundary."""
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(call, dict) for call in value):
        raise ProviderHTTPError("invalid_shape")
    return tuple(value)


def _usage(value: Any, family: str) -> dict[str, int]:
    """Normalize usage fields when they are present and nonnegative."""
    if not isinstance(value, dict):
        return {}
    fields = (
        ("prompt_tokens", "completion_tokens")
        if family == "openai"
        else ("input_tokens", "output_tokens")
        if family == "anthropic"
        else ("promptTokenCount", "candidatesTokenCount")
    )
    result: dict[str, int] = {}
    for target, source in zip(("input_tokens", "output_tokens"), fields, strict=True):
        count = value.get(source)
        if type(count) is int and count >= 0:
            result[target] = count
    for source in ("cache_creation_input_tokens", "cache_read_input_tokens", "thoughtsTokenCount"):
        count = value.get(source)
        if type(count) is int and count >= 0:
            result[source] = count
    return result


def _rate_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Keep bounded rate-limit fields without forwarding arbitrary headers."""
    return {
        key.lower(): value[:128]
        for key, value in headers.items()
        if key.lower().startswith(("x-ratelimit-", "anthropic-ratelimit-", "ratelimit-"))
        or key.lower() == "retry-after"
    }


def _normalize(data: dict[str, Any], family: str, headers: Mapping[str, str]) -> ProviderReply:
    """Normalize one non-stream response for a supported wire family."""
    try:
        if family == "openai":
            choice = data["choices"][0]
            message = choice["message"]
            return ProviderReply(
                text=_text(message.get("content")),
                tool_calls=_validated_calls(message.get("tool_calls")),
                usage=_usage(data.get("usage"), family),
                rate_limits=_rate_headers(headers),
                finish_reason=str(choice.get("finish_reason") or ""),
            )
        if family == "anthropic":
            blocks = data["content"]
            return ProviderReply(
                text="".join(
                    str(block.get("text", "")) for block in blocks if block.get("type") == "text"
                ),
                tool_calls=tuple(block for block in blocks if block.get("type") == "tool_use"),
                usage=_usage(data.get("usage"), family),
                rate_limits=_rate_headers(headers),
                finish_reason=str(data.get("stop_reason") or ""),
            )
        choice = data["candidates"][0]
        parts = choice["content"]["parts"]
        return ProviderReply(
            text="".join(str(part.get("text", "")) for part in parts if "text" in part),
            tool_calls=_validated_calls(
                [part["functionCall"] for part in parts if "functionCall" in part]
            ),
            usage=_usage(data.get("usageMetadata"), family),
            rate_limits=_rate_headers(headers),
            finish_reason=str(choice.get("finishReason") or ""),
        )
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ProviderHTTPError("invalid_shape") from exc


def _sse_events(raw: bytes) -> list[dict[str, Any]]:
    """Parse bounded SSE data frames, including multiline data and [DONE]."""
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines() + [""]:
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                if payload == "[DONE]":
                    break
                events.append(_json_object(payload.encode("utf-8")))
                data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if not events:
        raise ProviderHTTPError("empty_stream")
    return events


def _merge_openai(events: list[dict[str, Any]], headers: Mapping[str, str]) -> ProviderReply:
    """Join OpenAI-style content deltas and indexed tool-call argument fragments."""
    content: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] = {}
    finish = ""
    for event in events:
        if "error" in event:
            raise ProviderHTTPError("stream_error")
        usage.update(_usage(event.get("usage"), "openai"))
        for choice in event.get("choices") or ():
            delta = choice.get("delta") or {}
            content.append(_text(delta.get("content")))
            finish = str(choice.get("finish_reason") or finish)
            for fragment in delta.get("tool_calls") or ():
                index = fragment.get("index")
                if type(index) is not int or index < 0:
                    raise ProviderHTTPError("invalid_tool_delta")
                call = calls.setdefault(
                    index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                call["id"] += fragment.get("id") or ""
                function = fragment.get("function") or {}
                call["function"]["name"] += function.get("name") or ""
                call["function"]["arguments"] += function.get("arguments") or ""
    return ProviderReply(
        "".join(content),
        tuple(calls[i] for i in sorted(calls)),
        usage,
        _rate_headers(headers),
        finish,
    )


def _merge_anthropic(events: list[dict[str, Any]], headers: Mapping[str, str]) -> ProviderReply:
    """Join Anthropic text and tool JSON deltas by content-block index."""
    text_parts: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] = {}
    finish = ""
    for event in events:
        kind = event.get("type")
        if kind == "error":
            raise ProviderHTTPError("stream_error")
        if kind == "message_start":
            usage.update(_usage((event.get("message") or {}).get("usage"), "anthropic"))
        elif kind == "message_delta":
            usage.update(_usage(event.get("usage"), "anthropic"))
            finish = str((event.get("delta") or {}).get("stop_reason") or finish)
        elif kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                calls[event["index"]] = dict(block)
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                text_parts.append(str(delta.get("text") or ""))
            elif delta.get("type") == "input_json_delta":
                index = event.get("index")
                if type(index) is not int:
                    raise ProviderHTTPError("invalid_tool_delta")
                call = calls.get(index)
                if call is None:
                    raise ProviderHTTPError("invalid_tool_delta")
                call["partial_json"] = call.get("partial_json", "") + str(
                    delta.get("partial_json") or ""
                )
    for call in calls.values():
        if "partial_json" in call:
            call["input"] = _json_object(call.pop("partial_json").encode("utf-8"))
    return ProviderReply(
        "".join(text_parts),
        tuple(calls[i] for i in sorted(calls)),
        usage,
        _rate_headers(headers),
        finish,
    )


def _merge_google(events: list[dict[str, Any]], headers: Mapping[str, str]) -> ProviderReply:
    """Join Gemini incremental parts and retain the final usage metadata."""
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    finish = ""
    for event in events:
        if "error" in event:
            raise ProviderHTTPError("stream_error")
        usage.update(_usage(event.get("usageMetadata"), "google"))
        for candidate in event.get("candidates") or ():
            finish = str(candidate.get("finishReason") or finish)
            for part in (candidate.get("content") or {}).get("parts") or ():
                if "text" in part:
                    text_parts.append(str(part["text"]))
                if "functionCall" in part:
                    calls.append(part["functionCall"])
    return ProviderReply(
        "".join(text_parts), _validated_calls(calls), usage, _rate_headers(headers), finish
    )


class ProviderHTTPClient:
    """Explicit provider HTTP client with bounded responses and secret-safe errors."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.profile = profile
        self.api_key = api_key
        self.base_url = (base_url or profile.base_url).rstrip("/")
        split = urllib.parse.urlsplit(self.base_url)
        if (
            split.scheme not in {"http", "https"}
            or not split.netloc
            or split.username
            or split.password
            or split.query
            or split.fragment
        ):
            raise ValueError("provider base URL must be an http(s) authority without credentials")
        if profile.paid and not api_key:
            raise ValueError(f"{profile.name} API key is required")
        if (
            profile.paid
            and split.scheme != "https"
            and split.hostname
            not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }
        ):
            raise ValueError("paid provider endpoint requires HTTPS outside loopback")
        if timeout <= 0:
            raise ValueError("provider timeout must be positive")
        self.timeout = timeout

    def _request(
        self, path: str, body: dict[str, Any] | None, *, stream: bool = False
    ) -> urllib.request.Request:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if self.profile.family == "anthropic":
            headers.update({"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        elif self.profile.family == "google":
            headers["x-goog-api-key"] = self.api_key
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return urllib.request.Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
            method="POST" if body is not None else "GET",
        )

    def _send(
        self,
        request: urllib.request.Request,
        cancel: threading.Event | None,
        *,
        stream: bool = False,
    ) -> tuple[bytes, dict[str, str]]:
        if cancel is not None and cancel.is_set():
            raise ProviderHTTPError("cancelled")
        try:
            with open_provider_request(request, timeout=self.timeout) as response:
                if stream:
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise ProviderHTTPError("cancelled")
                        chunk = response.read(min(8192, DEFAULT_RESPONSE_LIMIT + 1 - total))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > DEFAULT_RESPONSE_LIMIT:
                            raise ProviderHTTPError("response_too_large")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                else:
                    try:
                        raw = read_bounded(
                            response, limit=DEFAULT_RESPONSE_LIMIT, purpose="provider response"
                        )
                    except BoundedReadError:
                        raise ProviderHTTPError("response_too_large") from None
                headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After", "")[:128]
            raise ProviderHTTPError(
                "http_error",
                status=exc.code,
                retry_after=retry_after,
                rate_limits=_rate_headers(dict(exc.headers.items())),
            ) from None
        except urllib.error.URLError:
            raise ProviderHTTPError("connection_error") from None
        if cancel is not None and cancel.is_set():
            raise ProviderHTTPError("cancelled")
        return raw, headers

    def models(self) -> tuple[str, ...]:
        """Discover model IDs when the selected wire family has a list endpoint."""
        raw, _ = self._send(self._request("models", None), None)
        data = _json_object(raw)
        entries = data.get("models" if self.profile.family == "google" else "data")
        if not isinstance(entries, list):
            raise ProviderHTTPError("invalid_model_list")
        key = "name" if self.profile.family == "google" else "id"
        return tuple(
            item[key]
            for item in entries
            if isinstance(item, dict) and isinstance(item.get(key), str)
        )

    def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        cancel: threading.Event | None = None,
    ) -> ProviderReply:
        """Request one completion; no tool execution or retry is performed."""
        if not model or max_output_tokens < 1:
            raise ValueError("model and positive max_output_tokens are required")
        family = self.profile.family
        if family == "openai":
            path = "chat/completions"
            body: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_output_tokens,
                "stream": stream,
            }
            if stream:
                body["stream_options"] = {"include_usage": True}
            if tools:
                body["tools"] = tools
        elif family == "anthropic":
            path = "messages"
            body = {
                "model": model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "max_tokens": max_output_tokens,
                "stream": stream,
            }
            if tools:
                body["tools"] = tools
        else:
            encoded_model = urllib.parse.quote(model.removeprefix("models/"), safe="")
            path = (
                f"models/{encoded_model}:streamGenerateContent?alt=sse"
                if stream
                else f"models/{encoded_model}:generateContent"
            )
            body = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"maxOutputTokens": max_output_tokens},
            }
            if tools:
                body["tools"] = [{"functionDeclarations": tools}]
        raw, headers = self._send(self._request(path, body, stream=stream), cancel, stream=stream)
        if stream:
            events = _sse_events(raw)
            merge = {
                "openai": _merge_openai,
                "anthropic": _merge_anthropic,
                "google": _merge_google,
            }[family]
            try:
                return merge(events, headers)
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                raise ProviderHTTPError("invalid_shape") from exc
        return _normalize(_json_object(raw), family, headers)


class ProviderWorkerBackend:
    """Two-prompt worker adapter with mandatory local budget for paid profiles."""

    def __init__(
        self,
        client: ProviderHTTPClient,
        *,
        model: str,
        budget: EstimatedSpendGuard | None = None,
        max_output_tokens: int = 1024,
    ) -> None:
        if client.profile.paid and budget is None:
            raise ValueError("paid provider requires an explicit estimated budget")
        self.client = client
        self.model = model
        self.budget = budget
        self.max_output_tokens = max_output_tokens

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """Reserve a call estimate and return bounded reply text."""
        if self.budget is not None:
            input_bytes = len((system_prompt + user_prompt).encode("utf-8"))
            self.budget.reserve(
                provider=self.client.profile.name,
                model=self.model,
                input_bytes=input_bytes,
                max_output_tokens=self.max_output_tokens,
            )
        reply = self.client.complete(
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=self.max_output_tokens,
        )
        from synapse_channel.client.chat_backends import sanitize_text

        if not reply.text:
            raise ProviderHTTPError("no_text_content")
        return sanitize_text(reply.text, max_len=1000)
