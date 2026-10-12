# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""Preserve public facade documentation while table families own its bodies."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])


def inherit_documentation(method: Callable[..., object] | str) -> Callable[[F], F]:
    """Copy the original table method's documentation, retaining facade identity.

    Parameters
    ----------
    method : callable or str
        Documented owning method, or the retained facade-only documentation.
        No call or signature mutation is performed.

    Returns
    -------
    callable
        Decorator copying only the docstring; names, module, signature and
        annotations of the facade method remain its own.
    """

    def document(function: F) -> F:
        """Attach the owning method's full native documentation."""
        function.__doc__ = method if isinstance(method, str) else method.__doc__
        return function

    return document


APPEND_DOCUMENTATION = (
    "Append one event to the log and return its assigned sequence number.\n"
    "\n"
    "        Parameters\n"
    "        ----------\n"
    "        kind : str\n"
    "            Event kind tag.\n"
    "        payload : dict[str, Any]\n"
    "            JSON-serialisable event body.\n"
    "        ts : float or None, optional\n"
    "            Event timestamp, in seconds; the system clock is used "
    "when ``None``.\n"
    "        durable : bool, optional\n"
    "            When ``True`` the commit is synced at "
    "``synchronous=FULL`` so it\n"
    "            survives an OS crash; when ``False`` it commits at "
    "``NORMAL`` (durable\n"
    "            only against an application crash). Defaults to "
    "``False``.\n"
    "\n"
    "        Returns\n"
    "        -------\n"
    "        int\n"
    "            The monotonic ``seq`` the row was assigned (the "
    "autoincrement primary\n"
    "            key). It is durable and never reused across restarts — "
    "unlike the\n"
    "            in-memory per-hub ``msg_id`` — so it is the stable cursor "
    "a reconnecting\n"
    "            client resumes a directed-message backlog from.\n"
    "\n"
    "        Notes\n"
    "        -----\n"
    "        A failed database write is rolled back. Durable attempts "
    "restore the\n"
    "        connection to ``synchronous=NORMAL``. If that cleanup alone "
    "fails after\n"
    "        COMMIT, the append remains successful and the cleanup failure "
    "is logged;\n"
    "        reporting a write failure at that point would contradict "
    "durable truth.\n"
    "        "
)


READ_ALL_DOCUMENTATION = (
    "Return every event in insertion order.\n"
    "\n"
    "        Returns\n"
    "        -------\n"
    "        list[StoredEvent]\n"
    "            All persisted events, ordered by ascending sequence "
    "number.\n"
    "        "
)
