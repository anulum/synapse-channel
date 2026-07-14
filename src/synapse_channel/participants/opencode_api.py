# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated OpenCode server participant
"""Drive the pinned OpenCode server API with bounded stdlib HTTP requests."""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from synapse_channel.core.errors import SynapseError
from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
    stamp_model,
)
from synapse_channel.participants.headless_opencode import compose_opencode_prompt
from synapse_channel.participants.opencode_auth import (
    basic_authorization,
    load_password_file,
    validate_endpoint,
)
from synapse_channel.participants.opencode_stream import (
    OPENCODE_SCHEMA_VERIFIED,
    OPENCODE_SCHEMA_VERSION,
    parse_opencode_api_response,
)
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth

DEFAULT_ENDPOINT = "http://127.0.0.1:4096"
DEFAULT_TIMEOUT = 600.0
MAX_RESPONSE_BYTES = 8 * 1_048_576


class OpenCodeApiError(SynapseError, RuntimeError):
    """The OpenCode API failed negotiation, transport, or schema validation."""

    code = "opencode_api"


class HttpRequester(Protocol):
    """Injectable bounded HTTP request boundary."""

    def __call__(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        *,
        timeout: float,
    ) -> tuple[int, bytes]:
        """Return an HTTP status and bounded response body."""


def _read_bounded(response: Any) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) > MAX_RESPONSE_BYTES:
                raise OpenCodeApiError("OpenCode response exceeds the bounded size limit.")
        except ValueError as exc:
            raise OpenCodeApiError("OpenCode returned an invalid Content-Length.") from exc
    data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise OpenCodeApiError("OpenCode response exceeds the bounded size limit.")
    return bytes(data)


def _default_requester(
    method: str,
    url: str,
    body: bytes | None,
    headers: Mapping[str, str],
    *,
    timeout: float,
) -> tuple[int, bytes]:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise OpenCodeApiError("OpenCode requester received an invalid HTTP(S) URL.")
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    try:
        port = parsed.port
    except ValueError as exc:
        raise OpenCodeApiError("OpenCode requester received an invalid endpoint port.") from exc
    connection = connection_type(parsed.hostname, port=port, timeout=timeout)
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request(method, target, body=body, headers=dict(headers))
        response = connection.getresponse()
        return int(response.status), _read_bounded(response)
    finally:
        connection.close()


def _model_ref(model: str) -> dict[str, str] | None:
    if not model:
        return None
    provider, separator, model_id = model.partition("/")
    if not separator or not provider or not model_id:
        raise OpenCodeApiError("OpenCode API model must be provider/model.")
    return {"providerID": provider, "modelID": model_id}


class OpenCodeApiParticipant:
    """An OpenCode session driven through the source-pinned server API."""

    def __init__(
        self,
        identity: str,
        *,
        directory: str | Path = ".",
        model: str = "",
        endpoint: str = DEFAULT_ENDPOINT,
        username: str = "opencode",
        password_file: str | None = None,
        allow_insecure_http: bool = False,
        requester: HttpRequester = _default_requester,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._identity = identity
        self._directory = Path(directory).expanduser().resolve()
        self._model = model
        self._endpoint = validate_endpoint(endpoint, allow_insecure_http=allow_insecure_http)
        self._username = username
        self._password_file = password_file
        self._requester = requester
        self._timeout = timeout
        self._active_lock = threading.Lock()
        self._active_session = ""

    @property
    def identity(self) -> str:
        """Return the participant identity."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return the API transport channel."""
        return ParticipantChannel.API

    def _headers(self, *, body: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if body:
            headers["Content-Type"] = "application/json"
        if self._password_file:
            headers["Authorization"] = basic_authorization(
                self._username, load_password_file(self._password_file)
            )
        return headers

    def _url(self, path: str, *, directory: bool = False) -> str:
        url = f"{self._endpoint}{path}"
        return f"{url}?{urlencode({'directory': str(self._directory)})}" if directory else url

    def _json_request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        directory: bool = False,
    ) -> Any:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        )
        status, raw = self._requester(
            method,
            self._url(path, directory=directory),
            body,
            self._headers(body=payload is not None),
            timeout=self._timeout,
        )
        if status < 200 or status >= 300:
            raise OpenCodeApiError(f"OpenCode API returned HTTP {status}.")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OpenCodeApiError("OpenCode API returned a non-JSON response.") from exc

    def _negotiate(self) -> None:
        health = self._json_request("GET", "/global/health")
        if not isinstance(health, dict):
            raise OpenCodeApiError("OpenCode health response has an invalid shape.")
        if health.get("healthy") is not True or health.get("version") != OPENCODE_SCHEMA_VERSION:
            raise OpenCodeApiError(
                f"OpenCode server is not verified schema {OPENCODE_SCHEMA_VERSION}."
            )
        if not OPENCODE_SCHEMA_VERIFIED:
            raise OpenCodeApiError("OpenCode server schema gate is disabled.")

    def health(self) -> ParticipantHealth:
        """Probe exact server health and schema version without taking a turn."""
        try:
            self._negotiate()
        except (OSError, ValueError, OpenCodeApiError) as exc:
            return ParticipantHealth(self._identity, self.channel, False, str(exc))
        return ParticipantHealth(
            self._identity,
            self.channel,
            True,
            f"OpenCode API {OPENCODE_SCHEMA_VERSION} at {self._endpoint}",
        )

    def _create_session(self) -> str:
        response = self._json_request("POST", "/session", {}, directory=True)
        if not isinstance(response, dict):
            raise OpenCodeApiError("OpenCode session-create response has an invalid shape.")
        session_id: object = response.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise OpenCodeApiError("OpenCode session-create response has an invalid shape.")
        return session_id

    def _abort(self, session_id: str) -> None:
        self._json_request("POST", f"/session/{quote(session_id, safe='')}/abort", directory=True)

    def _prompt(self, session_id: str, request: TurnRequest) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "parts": [
                {
                    "type": "text",
                    "text": compose_opencode_prompt(request.context, request.prompt),
                }
            ]
        }
        model = _model_ref(self._model)
        if model is not None:
            payload["model"] = model
        response = self._json_request(
            "POST",
            f"/session/{quote(session_id, safe='')}/message",
            payload,
            directory=True,
        )
        if not isinstance(response, dict):
            raise OpenCodeApiError("OpenCode prompt response has an invalid shape.")
        return response

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Negotiate, create or resume a session, and run one bounded prompt."""
        session_id = request.resume_session
        try:
            self._negotiate()
            if not session_id:
                session_id = self._create_session()
            with self._active_lock:
                self._active_session = session_id
            outcome = parse_opencode_api_response(self._prompt(session_id, request))
            return build_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                outcome=outcome,
            )
        except (OSError, ValueError, OpenCodeApiError) as exc:
            return error_turn_result(
                participant=self._identity,
                channel=self.channel,
                request=request,
                reason=str(exc),
            )
        finally:
            with self._active_lock:
                if self._active_session == session_id:
                    self._active_session = ""

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Run HTTP off-loop and best-effort abort the server session on cancellation."""
        task = asyncio.create_task(asyncio.to_thread(self.run_turn, request))
        try:
            return stamp_model(await task, self._model)
        except asyncio.CancelledError:
            with self._active_lock:
                session_id = self._active_session
            if session_id:
                try:
                    await asyncio.to_thread(self._abort, session_id)
                except (OSError, ValueError, OpenCodeApiError):
                    pass
            raise
