# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — end a refused HTTP connection without a reset
"""Close a connection whose request was not read to its end, without a reset.

A server that answers before it has read the whole request, for example a
refusal sent before the body, and then closes the socket leaves bytes of the
request unread. The operating system then ends the connection with a reset
instead of an orderly close. On Windows a reset discards what the client had
received and not yet read, so the client loses the very answer that told it
why it was refused; elsewhere the answer usually survives and only the end of
the connection is abrupt.

The remedy is the usual one: send the end of the answer, keep the socket open
for reading, discard what still arrives, and close when the client closes.
Here that waiting is done by one thread per server for all connections, so
that neither the accepting thread nor a request thread, nor an admission slot,
is held while it lasts. It is bounded three ways: a time per connection, a
number of bytes per connection, and a number of connections waiting at once.
A connection over a bound is closed at once, as before.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time

LINGER_SECONDS = 2.0
"""Longest time one connection is kept open for its remaining request bytes."""

LINGER_BYTES = 2 * 1024 * 1024
"""Most request bytes discarded for one connection before it is closed."""

LINGER_CONNECTIONS = 64
"""Most connections kept open at once; a further one is closed at once."""

_POLL_SECONDS = 0.05


class LingeringCloser:
    """Discard the unread rest of refused requests and close each connection in order.

    Parameters
    ----------
    seconds, max_bytes, max_connections
        The three bounds; the module's defaults when not given.
    """

    def __init__(
        self,
        *,
        seconds: float = LINGER_SECONDS,
        max_bytes: int = LINGER_BYTES,
        max_connections: int = LINGER_CONNECTIONS,
    ) -> None:
        self.seconds = float(seconds)
        self.max_bytes = int(max_bytes)
        self.max_connections = int(max_connections)
        self._lock = threading.Lock()
        # Each connection's deadline and the bytes seen so far; the thread alone changes the count.
        self._waiting: dict[socket.socket, list[float]] = {}
        self._thread: threading.Thread | None = None
        self._closed = False

    @property
    def pending(self) -> int:
        """Return how many connections are being kept open now."""
        with self._lock:
            return len(self._waiting)

    def take(self, sock: socket.socket) -> bool:
        """Take over a connection whose answer was sent, and close it when the client is done.

        The sending side is shut down here, which tells the client that the
        answer is complete. Returns ``False``, leaving the socket to the
        caller, when the closer has ended, when too many connections wait
        already, or when the socket can no longer be shut down.
        """
        with self._lock:
            if self._closed or len(self._waiting) >= self.max_connections:
                return False
            try:
                sock.shutdown(socket.SHUT_WR)
                sock.settimeout(0.0)
            except OSError:
                return False
            self._waiting[sock] = [time.monotonic() + self.seconds, 0.0]
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="synapse-lingering-close", daemon=True
                )
                self._thread.start()
        return True

    def close(self) -> None:
        """Close every waiting connection and end the thread. Further takes are declined."""
        with self._lock:
            self._closed = True
            waiting, self._waiting = list(self._waiting), {}
            thread = self._thread
        for sock in waiting:
            _close(sock)
        if thread is not None:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._closed:
                    return
                waiting = list(self._waiting.items())
            now = time.monotonic()
            for sock, state in waiting:
                seen = _discard(sock, int(state[1]), self.max_bytes)
                state[1] = float(seen)
                if seen < 0 or seen > self.max_bytes or now >= state[0]:
                    with self._lock:
                        self._waiting.pop(sock, None)
                    # Closing twice is harmless: close() may have closed it already.
                    _close(sock)
            time.sleep(_POLL_SECONDS)


def _discard(sock: socket.socket, seen: int, limit: int) -> int:
    """Read what has arrived without waiting; return the bytes seen so far, or -1 at the end.

    The end is the client's own close, or any error of the connection. Reading
    stops once more than ``limit`` bytes were seen, so that one fast sender
    cannot keep the thread from the other connections.
    """
    while seen <= limit:
        try:
            data = sock.recv(65536)
        except BlockingIOError:
            return seen
        except OSError:
            return -1
        if not data:
            return -1
        seen += len(data)
    return seen


def _close(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.close()
