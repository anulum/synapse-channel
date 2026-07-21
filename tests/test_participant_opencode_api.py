# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path

import pytest

from synapse_channel.participants import opencode_api
from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.opencode_api import (
    MAX_RESPONSE_BYTES,
    OpenCodeApiError,
    OpenCodeApiParticipant,
)


class FakeRequester:
    def __init__(self, *, version: str = "1.17.20") -> None:
        self.version = version
        self.calls: list[tuple[str, str, bytes | None, Mapping[str, str]]] = []

    def __call__(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        *,
        timeout: float,
    ) -> tuple[int, bytes]:
        del timeout
        self.calls.append((method, url, body, headers))
        if url.endswith("/global/health"):
            return 200, json.dumps({"healthy": True, "version": self.version}).encode()
        if "/abort?" in url:
            return 200, b"true"
        if url.split("?", 1)[0].endswith("/session"):
            return 200, b'{"id":"ses-api"}'
        if "/message?" in url:
            return 200, json.dumps(
                {
                    "info": {
                        "role": "assistant",
                        "sessionID": "ses-api",
                        "cost": 0.1,
                        "finish": "stop",
                        "tokens": {"input": 5, "output": 2},
                    },
                    "parts": [{"type": "text", "text": "answer"}],
                }
            ).encode()
        raise AssertionError(url)


def _password(tmp_path: Path) -> tuple[Path, str]:
    """Write a workspace password and return its file path and secret value.

    The secret is a collision-resistant SHA-256 digest of the workspace path, so
    an accidental match with any part of that path is cryptographically
    negligible. A fixed word such as "private" would false-positive the no-leak
    assertion whenever it appears in the workspace path — macOS resolves temp
    directories under "/private/var/...", and no fixed word is truly path-free
    since a crafted TMPDIR could embed any literal. The caller asserts the secret
    is absent from the path, so the no-leak check's soundness is verified for
    each concrete run rather than assumed.
    """
    secret = "leak-probe-" + hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()
    path = tmp_path / "password"
    path.write_text(f"{secret}\n")
    os.chmod(path, 0o600)
    return path, secret


def test_api_turn_negotiates_authenticates_routes_workspace_and_parses(tmp_path: Path) -> None:
    requester = FakeRequester()
    password_file, secret = _password(tmp_path)
    # Precondition: verify the secret is absent from the workspace path for this
    # run, so its absence from every request URL below is a genuine no-leak
    # signal rather than a path-substring collision (URL-encoding a path cannot
    # manufacture the secret's lowercase-hex run once it is absent from the path).
    assert secret not in str(tmp_path)
    participant = OpenCodeApiParticipant(
        "seat/api",
        directory=tmp_path,
        model="provider/model",
        endpoint="https://example.test",
        password_file=str(password_file),
        requester=requester,
    )
    result = participant.run_turn(TurnRequest("topic", "prompt", context="rules"))
    assert result["answer"] == "answer"
    assert result["session"] == "ses-api"
    assert result["input_tokens"] == 5
    assert [call[0] for call in requester.calls] == ["GET", "POST", "POST"]
    assert all(secret not in call[1] for call in requester.calls)
    assert all(call[3].get("Authorization", "").startswith("Basic ") for call in requester.calls)
    assert "%2F" in requester.calls[1][1]
    prompt = json.loads(requester.calls[2][2] or b"{}")
    assert prompt["model"] == {"providerID": "provider", "modelID": "model"}
    assert "----- TASK -----" in prompt["parts"][0]["text"]


def test_api_resume_skips_session_creation(tmp_path: Path) -> None:
    requester = FakeRequester()
    participant = OpenCodeApiParticipant("seat/api", directory=tmp_path, requester=requester)
    result = participant.run_turn(TurnRequest("topic", "prompt", resume_session="ses-api"))
    assert result["is_error"] is False
    assert [call[0] for call in requester.calls] == ["GET", "POST"]


def test_api_version_drift_and_invalid_model_fail_closed(tmp_path: Path) -> None:
    drift = OpenCodeApiParticipant(
        "seat/api", directory=tmp_path, requester=FakeRequester(version="1.18.0")
    ).run_turn(TurnRequest("topic", "prompt"))
    assert drift["is_error"] is True
    assert "verified schema 1.17.20" in drift["reason"]

    invalid = OpenCodeApiParticipant(
        "seat/api", directory=tmp_path, model="invalid", requester=FakeRequester()
    ).run_turn(TurnRequest("topic", "prompt"))
    assert invalid["is_error"] is True
    assert "provider/model" in invalid["reason"]


class BlockingRequester(FakeRequester):
    def __init__(self) -> None:
        super().__init__()
        self.prompt_started = threading.Event()
        self.release_prompt = threading.Event()
        self.abort_seen = threading.Event()

    def __call__(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        *,
        timeout: float,
    ) -> tuple[int, bytes]:
        if "/message?" in url:
            self.prompt_started.set()
            self.release_prompt.wait(timeout=2)
        elif "/abort?" in url:
            self.abort_seen.set()
            self.release_prompt.set()
        return super().__call__(method, url, body, headers, timeout=timeout)


@pytest.mark.asyncio
async def test_api_cancellation_sends_best_effort_abort(tmp_path: Path) -> None:
    requester = BlockingRequester()
    participant = OpenCodeApiParticipant("seat/api", directory=tmp_path, requester=requester)
    task = asyncio.create_task(participant.take_turn(TurnRequest("topic", "prompt")))
    assert await asyncio.to_thread(requester.prompt_started.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert requester.abort_seen.is_set()


def test_api_identity_channel_and_health(tmp_path: Path) -> None:
    participant = OpenCodeApiParticipant("seat/api", directory=tmp_path, requester=FakeRequester())
    assert participant.identity == "seat/api"
    assert participant.channel.value == "api"
    assert participant.health().available is True
    failed = OpenCodeApiParticipant(
        "seat/api", directory=tmp_path, requester=FakeRequester(version="old")
    )
    assert failed.health().available is False


class FixedRequester:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def __call__(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        *,
        timeout: float,
    ) -> tuple[int, bytes]:
        del method, url, body, headers, timeout
        return self.status, self.body


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, b"{}", "HTTP 401"),
        (200, b"not-json", "non-JSON"),
        (200, b"[]", "invalid shape"),
    ],
)
def test_api_transport_and_health_shapes_fail_closed(
    tmp_path: Path, status: int, body: bytes, message: str
) -> None:
    participant = OpenCodeApiParticipant(
        "seat/api", directory=tmp_path, requester=FixedRequester(status, body)
    )
    result = participant.run_turn(TurnRequest("topic", "prompt"))
    assert result["is_error"] is True
    assert message in result["reason"]


class InvalidSessionRequester(FakeRequester):
    def __init__(self, response: bytes) -> None:
        super().__init__()
        self.response = response

    def __call__(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        *,
        timeout: float,
    ) -> tuple[int, bytes]:
        if url.split("?", 1)[0].endswith("/session"):
            return 200, self.response
        return super().__call__(method, url, body, headers, timeout=timeout)


@pytest.mark.parametrize("response", [b"[]", b'{"id":""}', b'{"other":1}'])
def test_api_invalid_session_create_shape_is_typed_error(tmp_path: Path, response: bytes) -> None:
    result = OpenCodeApiParticipant(
        "seat/api", directory=tmp_path, requester=InvalidSessionRequester(response)
    ).run_turn(TurnRequest("topic", "prompt"))
    assert result["is_error"] is True
    assert "session-create" in result["reason"]


class InvalidPromptRequester(FakeRequester):
    def __call__(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        *,
        timeout: float,
    ) -> tuple[int, bytes]:
        if "/message?" in url:
            return 200, b"[]"
        return super().__call__(method, url, body, headers, timeout=timeout)


def test_api_invalid_prompt_shape_is_typed_error(tmp_path: Path) -> None:
    result = OpenCodeApiParticipant(
        "seat/api", directory=tmp_path, requester=InvalidPromptRequester()
    ).run_turn(TurnRequest("topic", "prompt"))
    assert result["is_error"] is True
    assert "prompt response" in result["reason"]


class Response:
    def __init__(self, data: bytes, length: str | None = None) -> None:
        self.data = data
        self.headers = {"Content-Length": length} if length is not None else {}

    def read(self, limit: int) -> bytes:
        return self.data[:limit]


def test_response_reader_enforces_declared_and_actual_bounds() -> None:
    assert opencode_api._read_bounded(Response(b"ok", "2")) == b"ok"
    with pytest.raises(OpenCodeApiError, match="invalid Content-Length"):
        opencode_api._read_bounded(Response(b"x", "bad"))
    with pytest.raises(OpenCodeApiError, match="bounded size"):
        opencode_api._read_bounded(Response(b"x", str(MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(OpenCodeApiError, match="bounded size"):
        opencode_api._read_bounded(Response(b"x" * (MAX_RESPONSE_BYTES + 1)))


@pytest.mark.asyncio
async def test_api_async_success_stamps_model(tmp_path: Path) -> None:
    participant = OpenCodeApiParticipant(
        "seat/api",
        directory=tmp_path,
        model="provider/model",
        requester=FakeRequester(),
    )
    result = await participant.take_turn(TurnRequest("topic", "prompt"))
    assert result["model"] == "provider/model"
