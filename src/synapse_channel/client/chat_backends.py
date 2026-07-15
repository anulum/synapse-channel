# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pluggable chat backends that turn a prompt into a reply
"""Chat backends used by the on-channel worker to produce a reply.

A backend is anything that maps a ``(system_prompt, user_prompt)`` pair to a
single reply string. Two are shipped:

* :class:`OpenAIChatClient` — talks to any OpenAI-compatible ``/v1`` endpoint,
  including a local Ollama server, over plain ``urllib`` (no third-party HTTP
  dependency);
* :class:`RuleBasedClient` — a deterministic fallback that never makes a network
  call, useful for smoke tests and offline operation.

Both satisfy the :class:`ChatBackend` protocol, so the worker depends only on
the protocol, never on a concrete backend.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from synapse_channel.core.http_response import read_bounded


def sanitize_text(text: str, max_len: int = 400) -> str:
    """Collapse runs of whitespace and truncate to ``max_len`` characters.

    Parameters
    ----------
    text : str
        Raw text to clean. Non-string input is coerced via ``str``.
    max_len : int, optional
        Maximum length of the returned string. Defaults to ``400``.

    Returns
    -------
    str
        Single-spaced, length-bounded text.
    """
    compact = " ".join(str(text).split())
    return compact[:max_len]


class ChatBackend(Protocol):
    """Structural type for anything that generates a reply from two prompts."""

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return a reply for the given system and user prompts."""


class RuleBasedClient:
    """Deterministic offline backend that acknowledges receipt.

    The reply carries no sender prefix: the wire envelope already records the
    author, so the hub and every reader render the name once.
    """

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return a fixed acknowledgement, ignoring both prompts.

        Parameters
        ----------
        system_prompt : str
            Unused; present to satisfy :class:`ChatBackend`.
        user_prompt : str
            Unused; present to satisfy :class:`ChatBackend`.

        Returns
        -------
        str
            A constant on-channel acknowledgement.
        """
        del system_prompt, user_prompt
        return "message received via Synapse. I am active on-channel."


class OpenAIChatClient:
    """Backend for any OpenAI-compatible ``/v1/chat/completions`` endpoint.

    Parameters
    ----------
    api_key : str
        Bearer token. Local servers (e.g. Ollama) accept any non-empty value.
    model : str
        Model identifier passed in the request body.
    base_url : str
        Base URL of the OpenAI-compatible API; a trailing slash is stripped.
        Only ``http``/``https`` schemes are accepted — a ``file://`` or
        custom scheme smuggled in through configuration is refused at
        construction rather than silently opened.
    timeout_seconds : float
        Per-request timeout, clamped up to ``3.0`` seconds.

    Raises
    ------
    ValueError
        If ``base_url`` carries a scheme other than ``http`` or ``https``.
    """

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout_seconds: float) -> None:
        scheme = urllib.parse.urlsplit(base_url).scheme.lower()
        if scheme not in {"http", "https"}:
            msg = f"chat backend URL must be http(s), got scheme '{scheme or '(none)'}'"
            raise ValueError(msg)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(float(timeout_seconds), 3.0)

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """Request a completion and return the sanitised reply text.

        Parameters
        ----------
        system_prompt : str
            System role content steering the model.
        user_prompt : str
            User role content the model responds to.

        Returns
        -------
        str
            The assistant message content, whitespace-collapsed and truncated.

        Raises
        ------
        RuntimeError
            On an HTTP error status, a connection failure, or a response whose
            shape does not contain the expected completion content.
        """
        url = f"{self.base_url}/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            # scheme constrained to http/https at construction
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:  # nosec B310
                raw = read_bounded(resp, purpose="chat backend response").decode(
                    "utf-8", errors="replace"
                )
        except urllib.error.HTTPError as exc:
            detail = read_bounded(exc, purpose="chat backend error body").decode(
                "utf-8", errors="replace"
            )
            raise RuntimeError(f"chat backend HTTP {exc.code}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"chat backend connection error: {exc}") from exc

        data = json.loads(raw)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"chat backend response parse error: {raw[:300]}") from exc
        return sanitize_text(content, max_len=1000)
