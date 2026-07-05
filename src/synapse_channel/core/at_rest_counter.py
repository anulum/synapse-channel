# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — count AES-GCM messages under a key, in memory or durably across restarts
"""Count the AES-GCM messages sealed under one at-rest key — in memory, or durably.

:class:`~synapse_channel.core.at_rest.AtRestCipher` refuses to encrypt past the AES-GCM per-key
message limit, so a fresh random 96-bit nonce cannot collide with an earlier one. That guard is
only as good as its counter. A plain in-memory counter (:class:`InMemoryMessageCounter`) resets
whenever the cipher is rebuilt — fine for a single process, but a long-lived store reloaded across
restarts would forget how many messages a key has ever sealed and could encrypt past the safe
bound over its lifetime.

:class:`PersistentMessageCounter` makes the count survive restarts, crash-safe by *reserving
ahead*: before the count enters a new batch it persists an upper bound on the messages that batch
covers, and on load it resumes from that persisted reservation. A crash therefore leaves the
persisted value at or above the true count, never below — the cipher over-counts by less than one
batch and rekeys a touch early, never under-counts and risks a nonce collision. A clean
:meth:`~PersistentMessageCounter.close` records the exact count so the next start resumes precisely.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

DEFAULT_COUNTER_BATCH = 1024
"""Messages reserved per persisted write: the crash over-count ceiling and the fsync stride."""


@runtime_checkable
class MessageCounter(Protocol):
    """Counts messages sealed under a key: read the running total, advance it by one."""

    @property
    def count(self) -> int:  # pragma: no cover - structural
        """The number of messages sealed so far."""
        ...

    def increment(self) -> int:  # pragma: no cover - structural
        """Advance the count by one and return the new total."""
        ...


class InMemoryMessageCounter:
    """A per-process message counter that starts at ``initial`` and forgets on rebuild.

    The default an :class:`~synapse_channel.core.at_rest.AtRestCipher` uses: it guards one
    long-running process. ``initial`` seeds it for a caller resuming a known count (or a test
    exercising the near-limit behaviour) without reaching into a private attribute.
    """

    def __init__(self, initial: int = 0) -> None:
        if initial < 0:
            raise ValueError("message count must not be negative")
        self._count = int(initial)

    @property
    def count(self) -> int:
        """The number of messages counted so far."""
        return self._count

    def increment(self) -> int:
        """Advance the count by one and return the new total."""
        self._count += 1
        return self._count


class PersistentMessageCounter:
    """A crash-safe message counter that persists its total to a sidecar file.

    The count survives a restart so the AES-GCM per-key limit is enforced over a key's whole
    lifetime, not just one process. It is durable by reserving ahead: crossing into a new batch
    of ``batch_size`` writes the batch's upper bound to the file before those messages are sealed,
    so a crash resumes from a value at or above the true count. Reads happen once at construction;
    a write happens only at a batch boundary (and on :meth:`close`), so the amortised cost is one
    file write per ``batch_size`` messages.

    Parameters
    ----------
    path : str or pathlib.Path
        Sidecar file holding the persisted count. Created on first write; its parent must exist.
    batch_size : int, optional
        Messages reserved per persisted write. Larger trades a bigger post-crash over-count for
        fewer writes. Defaults to :data:`DEFAULT_COUNTER_BATCH`.
    """

    def __init__(self, path: str | Path, *, batch_size: int = DEFAULT_COUNTER_BATCH) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._path = Path(path)
        self._batch_size = int(batch_size)
        self._count = self._load()
        self._reserved = self._count

    def _load(self) -> int:
        """Return the persisted count, or 0 when no sidecar exists yet.

        A sidecar that exists but does not hold a single non-negative integer is a corruption
        the counter must not paper over — resuming from 0 would under-count and defeat the
        limit — so it raises rather than guess.
        """
        try:
            raw = self._path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return 0
        try:
            value = int(raw)
        except ValueError as exc:
            msg = f"message-counter file {self._path} is not an integer: {raw!r}"
            raise ValueError(msg) from exc
        if value < 0:
            raise ValueError(f"message-counter file {self._path} holds a negative count: {value}")
        return value

    def _persist(self, value: int) -> None:
        """Atomically write ``value`` to the sidecar with an owner-only, fsynced replace."""
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, str(value).encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._path)

    @property
    def count(self) -> int:
        """The number of messages counted so far (at least the true count after a crash)."""
        return self._count

    def increment(self) -> int:
        """Advance the count by one, reserving and persisting a new batch when one is crossed."""
        self._count += 1
        if self._count > self._reserved:
            self._reserved += self._batch_size
            self._persist(self._reserved)
        return self._count

    def close(self) -> None:
        """Persist the exact count so the next start resumes precisely, not over-reserved."""
        self._persist(self._count)
