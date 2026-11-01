# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owned protected operator requests
"""Bound and drain operator request tasks before authority listener shutdown."""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_operator_socket import handle_operator_connection
from synapse_channel.core.protected_write_unix_exchange import require_native_unix_credentials

OperatorConnectionHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], None]
_LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def retain_operator_connections(
    *,
    control: ProtectedEnrollmentControl,
    operator_uid: int,
    max_connections: int = 32,
) -> AsyncIterator[OperatorConnectionHandler]:
    """Retain accepted operator tasks and cancel unfinished requests on exit.

    Parameters
    ----------
    control:
        Enrollment control bound to the sole durable authority actor.
    operator_uid:
        Independently selected peer UID checked by the real connection handler.
        Dedicated deployment roles and root listener custody are enforced by
        ``serve_operator_listener`` before it opens this context.
    max_connections:
        Positive concurrent request bound, at most 128. Excess connections close
        before reading a command or issuing an enrollment mutation.

    Yields
    ------
    OperatorConnectionHandler
        Synchronous asyncio accept callback owning all spawned request tasks.
        A retained callback refuses connections after its context has exited.

    Raises
    ------
    ValueError
        If the concurrent request bound is invalid.
    OSError
        Unless the actual host supplies Linux SO_PEERCRED. No accept callback or
        request task is created on an unsupported native credential backend.

    Notes
    -----
    Exit cancels and awaits every pending request. Cancellation is not rollback
    of an already durable actor mutation; callers must inspect the journal after
    an interrupted response. Persistent stores stay caller-owned and must remain
    open until this context drains. No listener or filesystem path is created.
    Handler failures are reported without exception values or request contents.
    """
    if type(max_connections) is not int or not 0 < max_connections <= 128:
        raise ValueError("invalid operator connection bound")
    require_native_unix_credentials(sys.platform, hasattr(socket, "SO_PEERCRED"))
    tasks: set[asyncio.Task[None]] = set()
    accepting = True

    def completed(task: asyncio.Task[None]) -> None:
        """Forget a finished owned request and log failures without command values."""
        tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOGGER.error("protected operator request failed")

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Own one bounded authenticated request or close a late/excess connection."""
        if not accepting or len(tasks) >= max_connections:
            writer.close()
            return
        task = asyncio.create_task(
            handle_operator_connection(reader, writer, control=control, operator_uid=operator_uid)
        )
        tasks.add(task)
        task.add_done_callback(completed)

    try:
        yield accept
    finally:
        accepting = False
        pending = tuple(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
