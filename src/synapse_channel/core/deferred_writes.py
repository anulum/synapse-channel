# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — writes queued where they arise and written later, in order, off the serving loop
"""Queue writes where they arise and write them later, in order, by one writer.

A rotating secret file (a per-message key set, a connect or metrics token set)
produces journal rows and state files where the hub must not wait for them:
inside a verification, an authentication, or the step that puts a changed file
in force. Each such write is put on a :class:`DeferredWrites` queue, which
never blocks, and the file's watcher writes the queue in a worker thread at its
next check. A write that fails stays first in the queue and its error is
raised, so the next flush writes it before the writes queued after it. The
queue holds at most :data:`AUDIT_QUEUE_LIMIT` writes; further writes are
counted, and the count is handed to the queue's overflow writer once the
writes before it have gone through. The hub flushes every queue once more when
it stops serving.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Protocol

AUDIT_QUEUE_LIMIT = 4096
"""Most writes a queue holds while they cannot be written; further writes are counted."""

Write = Callable[[], None]
"""One queued write, with everything it writes already bound."""


class SetObserver(Protocol):
    """Receives each change in which a set file is in force (a key or token set)."""

    def set_applied(self, path: Path, ids: tuple[str, ...], /) -> None:
        """Report that the set at ``path``, with these entry ids, is now in force."""

    def set_rejected(self, path: Path, reason: str, /) -> None:
        """Report that the set at ``path`` was not applied and why."""


class DeferredWrites:
    """A bounded queue of writes with one writer at a time.

    Parameters
    ----------
    label : str
        What the queue holds, for its log line when it is full (for example
        ``"message-auth key-set audit"``).
    log : logging.Logger
        The owner's logger, so the line names the file it belongs to.
    """

    def __init__(self, label: str, log: logging.Logger) -> None:
        self._label = label
        self._log = log
        self._queue: deque[Write] = deque()
        self._dropped = 0
        self._overflow: Callable[[int], None] | None = None
        self._counting = threading.Lock()
        self._writing = threading.Lock()

    def count_overflow_with(self, overflow: Callable[[int], None]) -> None:
        """Hand the number of writes that were not kept to ``overflow`` at each flush."""
        self._overflow = overflow

    def put(self, write: Write) -> None:
        """Queue ``write``, or count it when the queue is full; never block on a write."""
        with self._counting:
            if len(self._queue) < AUDIT_QUEUE_LIMIT:
                self._queue.append(write)
                return
            self._dropped += 1
            first = self._dropped == 1
        if first:
            self._log.error(
                "the %s queue is full (%d rows); further rows are counted and the count "
                "is journalled later",
                self._label,
                AUDIT_QUEUE_LIMIT,
            )

    def flush(self) -> None:
        """Write every queued write in order, then how many writes were not kept.

        One writer runs at a time. A write that fails stays first in the queue
        and its error is raised. Without an overflow writer the count is kept
        and the log line of a full queue is the only record.
        """
        with self._writing:
            while True:
                with self._counting:
                    if not self._queue:
                        break
                    write = self._queue[0]
                write()
                with self._counting:
                    self._queue.popleft()
            overflow = self._overflow
            if overflow is None:
                return
            with self._counting:
                dropped = self._dropped
            if dropped:
                overflow(dropped)
                with self._counting:
                    self._dropped -= dropped


class DeferredSetObserver:
    """Pass each change in which a set is in force to ``observer`` through a queue.

    The set calls this where it puts a change in force, on the serving loop;
    the observer's own write (a journal row, a state file) runs when the queue
    is flushed, in the watcher's worker thread.

    Parameters
    ----------
    observer : SetObserver
        The writer whose rows are deferred.
    writes : DeferredWrites
        The queue its writes go to.
    """

    def __init__(self, observer: SetObserver, writes: DeferredWrites) -> None:
        self._observer = observer
        self._writes = writes

    def set_applied(self, path: Path, ids: tuple[str, ...]) -> None:
        """Queue the observer's record of the set now in force."""
        self._writes.put(partial(self._observer.set_applied, path, ids))

    def set_rejected(self, path: Path, reason: str) -> None:
        """Queue the observer's record of a set that was not applied."""
        self._writes.put(partial(self._observer.set_rejected, path, reason))
