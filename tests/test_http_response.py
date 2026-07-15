# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for bounded outbound HTTP response reads
"""Exercise the bounded HTTP response reader: ceilings, headers, and refusals."""

from __future__ import annotations

import pytest

from synapse_channel.core.http_response import (
    DEFAULT_RESPONSE_LIMIT,
    BoundedReadError,
    read_bounded,
)


class _Response:
    """Minimal ``read(int)`` + optional ``headers`` mapping response double."""

    def __init__(self, body: bytes, *, content_length: object = None, with_headers: bool = True):
        self._body = body
        self._pos = 0
        if with_headers:
            self.headers: dict[str, object] = {}
            if content_length is not None:
                self.headers["Content-Length"] = content_length

    def read(self, amount: int) -> bytes:
        chunk = self._body[self._pos : self._pos + amount]
        self._pos += len(chunk)
        return chunk


class _HeaderlessResponse:
    """A response object that exposes no ``headers`` attribute at all."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self, amount: int) -> bytes:
        return self._body[:amount]


def test_returns_a_body_within_the_limit() -> None:
    result = read_bounded(_Response(b"hello"), limit=1024)
    assert result == b"hello"
    assert isinstance(result, bytes)


def test_accepts_a_body_exactly_at_the_limit() -> None:
    assert read_bounded(_Response(b"x" * 10), limit=10) == b"x" * 10


def test_reads_when_the_response_has_no_headers_attribute() -> None:
    assert read_bounded(_HeaderlessResponse(b"hi"), limit=64) == b"hi"


def test_reads_when_headers_carry_no_content_length() -> None:
    assert read_bounded(_Response(b"body", with_headers=True), limit=64) == b"body"


def test_rejects_an_oversized_declared_content_length_without_reading() -> None:
    response = _Response(b"x" * 3, content_length="9999")
    with pytest.raises(BoundedReadError, match="declared 9999 bytes exceeds"):
        read_bounded(response, limit=10)
    # The pre-check fired before any body was consumed.
    assert response._pos == 0


def test_rejects_a_malformed_content_length() -> None:
    with pytest.raises(BoundedReadError, match="invalid Content-Length"):
        read_bounded(_Response(b"x", content_length="not-a-number"), limit=10)


def test_rejects_a_streamed_body_over_the_limit() -> None:
    with pytest.raises(BoundedReadError, match="exceeds the 10-byte limit"):
        read_bounded(_Response(b"x" * 20), limit=10)


def test_rejects_a_body_that_lies_below_its_content_length() -> None:
    # A truthful-looking small Content-Length passes the pre-check, but the
    # streamed body still gets bounded and refused.
    response = _Response(b"x" * 20, content_length="5")
    with pytest.raises(BoundedReadError, match="exceeds the 10-byte limit"):
        read_bounded(response, limit=10)


def test_names_the_purpose_in_the_error() -> None:
    with pytest.raises(BoundedReadError, match="ollama tags: response exceeds"):
        read_bounded(_Response(b"x" * 5), limit=2, purpose="ollama tags")


def test_default_limit_is_eight_mebibytes() -> None:
    assert DEFAULT_RESPONSE_LIMIT == 8 * 1_048_576


def test_bounded_read_error_is_a_value_error() -> None:
    # Existing broad ``except ValueError`` handlers around provider calls must
    # keep catching the refusal.
    assert issubclass(BoundedReadError, ValueError)
