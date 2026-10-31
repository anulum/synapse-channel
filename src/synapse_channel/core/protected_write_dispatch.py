# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated protected-write verb dispatch
"""Route authenticated requests through an immutable complete protected verb map."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType

from synapse_channel.core.protected_write_session_auth import AuthenticatedProtectedRequest

ProtectedHandler = Callable[[AuthenticatedProtectedRequest], Awaitable[bytes]]
"""Bound concrete protected authority handler."""

_MESSAGE_TYPES = frozenset(
    {
        "protected_write_prepare",
        "protected_write_admit",
        "protected_write_begin",
        "protected_write_settle",
        "protected_write_revoke",
        "protected_write_status",
        "protected_write_recover",
    }
)


class ProtectedWriteDispatch:
    """Route all protected verbs through already bound concrete handlers.

    Parameters
    ----------
    handlers : mapping of str to callable
        Complete trusted seven-verb handler set. Construction copies the map
        before retaining it read-only; caller mutation cannot replace a route.

    Raises
    ------
    ValueError
        If a handler is missing or a message type is unsupported.

    Notes
    -----
    Use assemble_protected_write_dispatch to bind concrete handlers to one
    current authority policy. This router neither authenticates an arbitrary
    request nor constructs transport or grants execution authority.
    """

    def __init__(self, handlers: Mapping[str, ProtectedHandler]) -> None:
        """Retain an immutable copy of all seven protected verb handlers.

        Raise ValueError for a missing or additional verb before any dispatch.
        Constructing the dispatcher invokes none of the supplied handlers.
        """
        if frozenset(handlers) != _MESSAGE_TYPES:
            raise ValueError("protected dispatch requires all seven verbs")
        self._handlers: Mapping[str, ProtectedHandler] = MappingProxyType(dict(handlers))

    async def __call__(self, authenticated: AuthenticatedProtectedRequest) -> bytes:
        """Dispatch a server-retained authenticated request to its bound handler.

        Parameters
        ----------
        authenticated : AuthenticatedProtectedRequest
            Ingress result whose parser already restricted its message type.

        Returns
        -------
        bytes
            Concrete handler's signed and request-bound response. Every handler
            retains its own current enrollment and authority checks.
        """
        source = json.loads(authenticated.parsed.canonical_bytes)
        return await self._handlers[source["type"]](authenticated)
