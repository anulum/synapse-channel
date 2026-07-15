# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Ollama REST API participant driver
"""Drive a local Ollama model over its REST API as a bus participant.

The first ``API``-channel :class:`~synapse_channel.participants.participant.Participant`: instead of
spawning a CLI, it POSTs to the Ollama server's ``/api/generate`` endpoint and reads the JSON reply.
The API channel is more robust than the headless one — there is no subprocess to spawn and no binary
to resolve on ``PATH`` — and it is the natural first API target because Ollama is **keyless, local,
and reports token counts** (``prompt_eval_count`` / ``eval_count``), which feed the opt-in usage
accounting directly.

The HTTP transport is the Python standard library (``urllib``), so the driver adds **no
dependency**. The blocking request is made through an injected poster, so tests drive a turn with a
fake responder and never touch the network. As with the CLI driver a model name is mandatory, there
is no provider session (continuity rides the conversation's fenced context), and a local turn has
no monetary cost. A transport failure or a malformed body becomes an error result, never a raised
exception.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any, Protocol

from synapse_channel.core.http_response import read_bounded
from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
    stamp_model,
)
from synapse_channel.participants.headless_ollama import compose_ollama_prompt
from synapse_channel.participants.ollama_api_output import parse_ollama_api_response
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)

DEFAULT_ENDPOINT = "http://localhost:11434/api/generate"
"""Default Ollama REST generate endpoint on the local host."""

DEFAULT_TIMEOUT = 600.0
"""Default wall-clock ceiling, in seconds, for one API turn."""


class HttpPoster(Protocol):
    """A blocking JSON-over-HTTP POST, injectable so tests avoid the network."""

    def __call__(self, url: str, body: bytes, *, timeout: float) -> bytes:
        """POST ``body`` to ``url`` and return the raw response bytes."""


def _default_poster(url: str, body: bytes, *, timeout: float) -> bytes:
    """POST ``body`` to ``url`` with the standard library and return the response bytes.

    The scheme is constrained to HTTP(S) so the opener cannot be steered to a local-file or other
    URL scheme by a misconfigured endpoint.
    """
    if not url.startswith(("http://", "https://")):
        msg = f"ollama endpoint must be an http(s) URL, got {url!r}"
        raise ValueError(msg)
    # Scheme guarded to http(s) above; this POSTs to an operator-configured model endpoint.
    request = urllib.request.Request(  # noqa: S310
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  # nosec B310
        return read_bounded(response, purpose="ollama API response")


def build_ollama_api_body(*, model: str, prompt: str) -> bytes:
    """Build the JSON request body for one non-streaming ``/api/generate`` call.

    Parameters
    ----------
    model : str
        The Ollama model to load (required).
    prompt : str
        The fully composed prompt (see
        :func:`~synapse_channel.participants.headless_ollama.compose_ollama_prompt`).

    Returns
    -------
    bytes
        UTF-8 JSON requesting a single, non-streamed completion.
    """
    payload = {"model": model, "prompt": prompt, "stream": False}
    return json.dumps(payload).encode("utf-8")


class OllamaApiParticipant:
    """A local Ollama model driven over its REST API as a uniform bus participant.

    Parameters
    ----------
    identity : str
        The participant's bus identity.
    model : str
        The Ollama model to load for every turn (required).
    endpoint : str, optional
        The ``/api/generate`` URL; defaults to the local Ollama server.
    poster : HttpPoster, optional
        Blocking HTTP poster; injectable so tests drive turns with a fake, never a real request.
    timeout : float, optional
        Per-turn wall-clock ceiling, in seconds.
    """

    def __init__(
        self,
        identity: str,
        *,
        model: str,
        endpoint: str = DEFAULT_ENDPOINT,
        poster: HttpPoster = _default_poster,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._identity = identity
        self._model = model
        self._endpoint = endpoint
        self._poster = poster
        self._timeout = timeout

    @property
    def identity(self) -> str:
        """Return the participant's bus identity."""
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.API`."""
        return ParticipantChannel.API

    def health(self) -> ParticipantHealth:
        """Report the seat as configured without probing the server.

        Returns
        -------
        ParticipantHealth
            ``available`` is true: the API seat owns no local binary to resolve, and whether the
            server is actually up surfaces as an error turn when a request fails, rather than being
            probed here with a blocking call.
        """
        return ParticipantHealth(
            identity=self._identity,
            channel=ParticipantChannel.API,
            available=True,
            detail=f"ollama API at {self._endpoint} (model {self._model})",
        )

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn synchronously against the REST endpoint and return its typed result.

        Parameters
        ----------
        request : TurnRequest
            The turn to run. Its ``context`` is prepended to the prompt (the API has no system
            channel); its ``resume_session`` is ignored (the endpoint is stateless here).

        Returns
        -------
        TurnResult
            The parsed outcome, or an error result when the request could not be made or its
            response could not be read.
        """
        body = build_ollama_api_body(
            model=self._model,
            prompt=compose_ollama_prompt(request.context, request.prompt),
        )
        try:
            raw = self._poster(self._endpoint, body, timeout=self._timeout)
        except (OSError, ValueError) as exc:
            return error_turn_result(
                participant=self._identity,
                channel=ParticipantChannel.API,
                request=request,
                reason=f"failed to reach {self._endpoint!r}: {exc}",
            )
        try:
            decoded: Any = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return error_turn_result(
                participant=self._identity,
                channel=ParticipantChannel.API,
                request=request,
                reason=f"{self._endpoint!r} returned a non-JSON body",
            )
        if not isinstance(decoded, dict):
            return error_turn_result(
                participant=self._identity,
                channel=ParticipantChannel.API,
                request=request,
                reason=f"{self._endpoint!r} returned an unexpected response shape",
            )
        outcome = parse_ollama_api_response(decoded)
        return build_turn_result(
            participant=self._identity,
            channel=ParticipantChannel.API,
            request=request,
            outcome=outcome,
        )

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Take one turn off the event loop via :meth:`run_turn`.

        Parameters
        ----------
        request : TurnRequest
            The turn to run.

        Returns
        -------
        TurnResult
            The result :meth:`run_turn` produces, restamped with the configured model, computed in
            a worker thread so the blocking request never stalls the bus event loop.
        """
        result = await asyncio.to_thread(self.run_turn, request)
        return stamp_model(result, self._model)
