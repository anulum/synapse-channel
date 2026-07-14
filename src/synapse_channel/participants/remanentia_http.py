# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded read-only client for REMANENTIA's stdlib recall API
"""Optional stdlib HTTP implementation of the Participant memory protocol."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field
from email.message import Message
from ipaddress import ip_address
from pathlib import Path
from typing import IO, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.participants.memory_contract import (
    MemoryHit,
    MemoryRecallResult,
)

DEFAULT_REQUEST_BYTES = 16 * 1024
DEFAULT_RESPONSE_BYTES = 1024 * 1024
MAX_TOKEN_BYTES = 8 * 1024
_PROVENANCE = "REMANENTIA stdlib HTTP /recall"
_SOURCE = "REMANENTIA stdlib HTTP"


class _NoRedirect(HTTPRedirectHandler):
    """Refuse redirects so a bearer credential never crosses origins."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        """Return no redirected request; urllib raises a bounded HTTPError."""
        return None


def _endpoint(base_url: str) -> str:
    """Validate an origin-only URL and bind it to the fixed recall path."""
    if (
        not isinstance(base_url, str)
        or not base_url.strip()
        or base_url != base_url.strip()
        or any(character.isspace() or ord(character) < 33 for character in base_url)
    ):
        raise ValueError("memory URL must be a non-empty HTTP(S) origin")
    try:
        parsed = urlsplit(base_url)
        _ = parsed.port
    except ValueError:
        raise ValueError("memory URL is malformed") from None
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("memory URL must use http or https with a host")
    if parsed.scheme == "http":
        try:
            loopback = ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("memory URL must use https outside a literal loopback host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("memory URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("memory URL must be an origin without path, query, or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, "/recall", "", ""))


def _reject_constant(value: str) -> NoReturn:
    """Reject JavaScript non-finite constants accepted by Python's JSON parser."""
    raise ValueError(f"invalid JSON number {value}")


def _required_string(entry: dict[str, object], key: str) -> str:
    """Return one required non-empty response string."""
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("memory service returned a malformed result")
    return value


@dataclass(frozen=True)
class RemanentiaHttpRecall:
    """Read bounded boundary-only hits from REMANENTIA's lightweight API."""

    base_url: str
    token_file: str | Path | None = None
    timeout_seconds: float = 2.0
    max_request_bytes: int = DEFAULT_REQUEST_BYTES
    max_response_bytes: int = DEFAULT_RESPONSE_BYTES
    endpoint: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate fixed-origin transport and finite resource ceilings."""
        timeout = self.timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(float(timeout))
            or not 0.0 < float(timeout) <= 30.0
        ):
            raise ValueError("memory HTTP timeout must be finite and in (0, 30]")
        for name, value, minimum, maximum in (
            ("max_request_bytes", self.max_request_bytes, 128, 1024 * 1024),
            ("max_response_bytes", self.max_response_bytes, 256, 8 * 1024 * 1024),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
        token_file = None if self.token_file is None else Path(self.token_file).expanduser()
        object.__setattr__(self, "token_file", token_file)
        object.__setattr__(self, "timeout_seconds", float(timeout))
        object.__setattr__(self, "endpoint", _endpoint(self.base_url))

    async def recall(self, query: str, *, top_k: int) -> MemoryRecallResult:
        """Run the blocking request in a worker thread and return fenced data."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("memory query must be a non-empty string")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise ValueError("memory top_k must be an integer in [1, 20]")
        return await asyncio.to_thread(self._recall_sync, query, top_k)

    def _recall_sync(self, query: str, top_k: int) -> MemoryRecallResult:
        body = json.dumps(
            {"query": query, "top_k": top_k},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > self.max_request_bytes:
            raise ValueError("memory recall request exceeds the configured size limit")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        token = self._token()
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with build_opener(_NoRedirect()).open(
                request, timeout=self.timeout_seconds
            ) as response:
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("memory service returned a non-JSON response")
                raw = response.read(self.max_response_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise RuntimeError("memory service request failed") from None
        if len(raw) > self.max_response_bytes:
            raise ValueError("memory service response exceeds the configured size limit")
        try:
            payload: object = json.loads(
                raw.decode("utf-8"),
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ValueError("memory service returned invalid JSON") from None
        return self._parse(payload, query, top_k)

    def _token(self) -> str | None:
        """Return the bearer from an owner-only token file (SCH-H-NEW-11).

        Uses the shared secret floor (O_NOFOLLOW, euid owner, group/other bits
        clear) so memory credentials match hub token-file discipline. Errors
        never echo the path, preserving the participant non-reflection contract.
        """
        path = self.token_file
        if path is None:
            return None
        try:
            token = read_secret_file(path, flag="memory-token-file")
        except SecretFileError as exc:
            message = str(exc)
            if "empty" in message:
                raise ValueError("memory token file is empty or exceeds its size limit") from None
            if "UTF-8" in message:
                raise ValueError("memory token file is not valid UTF-8") from None
            # Missing, symlink, wrong owner/mode, oversized raw open, etc.
            raise RuntimeError("memory token file is unavailable") from None
        if len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
            raise ValueError("memory token file is empty or exceeds its size limit")
        if any(character.isspace() or ord(character) < 33 for character in token):
            raise ValueError("memory token file contains an invalid bearer token")
        return token

    @staticmethod
    def _parse(payload: object, query: str, top_k: int) -> MemoryRecallResult:
        if not isinstance(payload, dict) or payload.get("query") != query:
            raise ValueError("memory service returned a mismatched response")
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ValueError("memory service returned a malformed results list")
        hits: list[MemoryHit] = []
        for entry in raw_results[:top_k]:
            if not isinstance(entry, dict):
                raise ValueError("memory service returned a malformed result")
            source = _required_string(entry, "name")
            kind = _required_string(entry, "type")
            snippet = _required_string(entry, "snippet")
            raw_score = entry.get("score")
            if raw_score is not None and (
                isinstance(raw_score, bool) or not isinstance(raw_score, int | float)
            ):
                raise ValueError("memory service returned a malformed score")
            score = None if raw_score is None else float(raw_score)
            hits.append(
                MemoryHit(
                    source=source,
                    kind=kind,
                    score=score,
                    snippet=snippet,
                    presentation="boundary",
                    provenance=_PROVENANCE,
                )
            )
        return MemoryRecallResult(
            query=query,
            hits=tuple(hits),
            abstained=not hits,
            source=_SOURCE,
            note=(
                "Current REMANENTIA HTTP results omit honesty axes; every hit is boundary data."
                if hits
                else "REMANENTIA returned no admissible hits."
            ),
        )
