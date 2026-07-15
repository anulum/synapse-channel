# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded reads for outbound HTTP responses
"""Read an outbound HTTP response body under a strict byte ceiling.

An outbound call to a model provider, an Ollama endpoint, or any operator-
configured HTTP service returns a body the process must buffer. Reading it with
a bare ``response.read()`` trusts the peer to be well-behaved: a compromised or
merely misconfigured endpoint can stream an arbitrarily large body and exhaust
memory even when the content is ultimately discarded.

This is the shared response floor. It pre-rejects an oversized ``Content-Length``
without reading a byte, then reads at most ``limit + 1`` bytes so it can prove
the body is within the ceiling before returning it — the same bounded pattern
the OpenCode client already uses, in one reusable place so every provider path
inherits it. It caps size; content-type and structural validation stay with the
caller that knows the expected shape.
"""

from __future__ import annotations

from typing import Protocol

from synapse_channel.core.errors import SynapseError

DEFAULT_RESPONSE_LIMIT = 8 * 1_048_576
"""Default ceiling (8 MiB) for one buffered outbound HTTP response body."""


class BoundedReadError(SynapseError, ValueError):
    """Raised when an HTTP response exceeds, or misdeclares, its byte ceiling.

    A :class:`ValueError` subclass so existing ``except ValueError`` and broad
    handlers around provider calls keep catching it. The message names the
    purpose and the limit, never the response body.
    """

    code = "bounded_read"


class _Readable(Protocol):
    """The minimal response surface the bounded reader needs."""

    def read(self, amount: int, /) -> bytes:  # pragma: no cover
        ...


def read_bounded(
    response: _Readable,
    *,
    limit: int = DEFAULT_RESPONSE_LIMIT,
    purpose: str = "HTTP response",
) -> bytes:
    """Return the response body, or raise if it exceeds ``limit`` bytes.

    Parameters
    ----------
    response : object
        An open HTTP response (``http.client.HTTPResponse``,
        ``urllib.error.HTTPError``, or any object with ``read(int)`` and an
        optional ``headers`` mapping).
    limit : int, optional
        Maximum bytes accepted; defaults to :data:`DEFAULT_RESPONSE_LIMIT`.
    purpose : str, optional
        Human name for the call, placed in every error (e.g. ``"chat backend
        response"``).

    Returns
    -------
    bytes
        The response body, guaranteed to be at most ``limit`` bytes.

    Raises
    ------
    BoundedReadError
        If the declared ``Content-Length`` exceeds ``limit``, the header is
        malformed, or the streamed body exceeds ``limit``.
    """
    headers = getattr(response, "headers", None)
    declared_raw = headers.get("Content-Length") if headers is not None else None
    if declared_raw is not None:
        try:
            declared = int(declared_raw)
        except (TypeError, ValueError) as exc:
            raise BoundedReadError(f"{purpose}: invalid Content-Length header") from exc
        if declared > limit:
            raise BoundedReadError(
                f"{purpose}: declared {declared} bytes exceeds the {limit}-byte limit"
            )
    data = response.read(limit + 1)
    if len(data) > limit:
        raise BoundedReadError(f"{purpose}: response exceeds the {limit}-byte limit")
    return bytes(data)
