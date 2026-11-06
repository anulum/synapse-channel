# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real localhost HTTP helpers for network-client tests

from __future__ import annotations

import contextlib
import gc
import threading
import warnings
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError

from hub_e2e_helpers import _free_port


@contextlib.contextmanager
def unclosed_resources() -> Iterator[list[str]]:
    """Collect every ResourceWarning raised in the block and by a final collection.

    The block should drop its references to what it opened, then the garbage
    collector frees them: a socket or file still open at that point warns, and
    the warning text lands in the yielded list. The list stays empty when
    everything was closed explicitly.
    """
    found: list[str] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        yield found
        gc.collect()
    found.extend(str(item.message) for item in caught if item.category is ResourceWarning)


@dataclass(frozen=True)
class KeptFailure:
    """What a kept error showed before it was dropped.

    ``http_errors`` counts the HTTP errors in its context and cause chain;
    ``open_http_errors`` counts those whose response was still open.
    """

    text: str
    http_errors: int
    open_http_errors: int


def _kept(error: BaseException) -> KeptFailure:
    links: list[HTTPError] = []
    seen: set[int] = set()
    link: BaseException | None = error
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        if isinstance(link, HTTPError):
            links.append(link)
        link = link.__cause__ or link.__context__
    still_open = [item for item in links if item.fp is not None and not item.fp.closed]
    return KeptFailure(str(error), len(links), len(still_open))


def keep_failure(call: Callable[[], object], expected: type[Exception]) -> KeptFailure:
    """Run a call that must fail and keep its error, as a caller that stores it would.

    The kept error and this frame refer to each other through the traceback, so
    once this returns only the garbage collector frees them. Whatever the error
    still holds open then shows up in :func:`unclosed_resources`, when the
    collector happens to free the socket before its reader.
    """
    try:
        call()
    except expected as error:
        kept = error
        return _kept(kept)
    raise AssertionError(f"{expected.__name__} was not raised")


async def keep_async_failure(
    call: Callable[[], Awaitable[object]], expected: type[Exception]
) -> KeptFailure:
    """Await a call that must fail and keep its error; see :func:`keep_failure`."""
    try:
        await call()
    except expected as error:
        kept = error
        return _kept(kept)
    raise AssertionError(f"{expected.__name__} was not raised")


@dataclass(frozen=True)
class RecordedHttpRequest:
    """One request received by the local test HTTP server."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


class LocalHttpResponder:
    """Serve one configured response over a real localhost HTTP socket."""

    def __init__(
        self,
        *,
        body: bytes,
        status: int = 200,
        content_type: str = "application/json",
        response_headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.content_type = content_type
        self.response_headers = response_headers or {}
        self.requests: list[RecordedHttpRequest] = []
        self.port = _free_port()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner._handle(self)

            def do_POST(self) -> None:
                owner._handle(self)

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """Return the base URL for this local server."""
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> LocalHttpResponder:
        """Start serving requests."""
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Stop the server and close its socket."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length") or "0")
        body = handler.rfile.read(length) if length else b""
        self.requests.append(
            RecordedHttpRequest(
                method=handler.command,
                path=handler.path,
                headers={str(key): str(value) for key, value in handler.headers.items()},
                body=body,
            )
        )
        handler.send_response(self.status)
        handler.send_header("Content-Type", self.content_type)
        for key, value in self.response_headers.items():
            handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(self.body)))
        handler.end_headers()
        handler.wfile.write(self.body)
