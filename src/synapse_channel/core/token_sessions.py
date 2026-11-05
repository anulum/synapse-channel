# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sessions admitted by an expiring connect token
"""Follow sessions whose connect token has an end of validity.

Expiry refuses new connections. What happens to a session that is already
open when its token expires depends on the token set:

* by default the session stays open, and when it ends the journal records
  that it outlived its token (``closed`` false);
* with ``expiry_closes_sessions`` the hub closes it at the token's
  ``not_after`` with close code :data:`EXPIRY_CLOSE_CODE`, and the journal
  records that the hub ended it (``closed`` true).

The end of validity is the one in force when the session was admitted; a
later change to the file does not move it. Only sessions admitted by a token
with an end of validity are followed, so memory grows with open sessions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.token_audit import TokenAudit
from synapse_channel.core.token_set import TokenOutcome, TokenSetAuthenticator

EXPIRY_CLOSE_CODE = 4401
"""WebSocket close code for a session ended because its token expired."""

EXPIRY_CLOSE_REASON = "connect token expired"
"""Close reason sent with :data:`EXPIRY_CLOSE_CODE`."""


@dataclass
class _Session:
    """One followed session."""

    token_id: str | None
    agent: str
    not_after: float
    timer: asyncio.TimerHandle | None
    closed_by_hub: bool = False


class TokenSessions:
    """Track sessions admitted by expiring tokens and act at their expiry.

    Parameters
    ----------
    authenticator : TokenSetAuthenticator
        The hub's token set; its clock and its expiry policy are used.
    audit : TokenAudit or None
        Journal writer; ``None`` records nothing.
    close : Callable[[Any], Coroutine[Any, Any, None]]
        Closes one socket with the expiry code and reason.
    """

    def __init__(
        self,
        authenticator: TokenSetAuthenticator,
        audit: TokenAudit | None,
        close: Callable[[Any], Coroutine[Any, Any, None]],
    ) -> None:
        self._authenticator = authenticator
        self._audit = audit
        self._close = close
        self._sessions: dict[Any, _Session] = {}
        self._closing: set[asyncio.Task[None]] = set()

    def __len__(self) -> int:
        """Return the number of sessions followed now."""
        return len(self._sessions)

    def admitted(self, websocket: Any, outcome: TokenOutcome, agent: str) -> None:
        """Follow a session admitted by ``outcome`` when its token can expire."""
        if outcome.not_after is None:
            return
        timer = None
        if self._authenticator.expiry_closes_sessions:
            delay = max(0.0, outcome.not_after - self._authenticator.now())
            timer = asyncio.get_running_loop().call_later(delay, self._expire, websocket)
        self._sessions[websocket] = _Session(outcome.token_id, agent, outcome.not_after, timer)

    def _expire(self, websocket: Any) -> None:
        """Close a followed session whose token reached its end of validity.

        The timer is cancelled when the session closes first, and a cancelled
        timer never runs, so the session is still followed here.
        """
        self._sessions[websocket].closed_by_hub = True
        task = asyncio.get_running_loop().create_task(self._close(websocket))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    def closed(self, websocket: Any) -> None:
        """Stop following a socket that closed; record it if it outlived its token."""
        session = self._sessions.pop(websocket, None)
        if session is None:
            return
        if session.timer is not None:
            session.timer.cancel()
        outlived = session.closed_by_hub or self._authenticator.now() >= session.not_after
        if outlived and self._audit is not None:
            self._audit.session_expired(
                session.token_id, session.agent, closed=session.closed_by_hub
            )


async def _close_expired(websocket: Any) -> None:
    """Close ``websocket`` with the expiry code and reason."""
    await websocket.close(code=EXPIRY_CLOSE_CODE, reason=EXPIRY_CLOSE_REASON)


def token_sessions_for(
    authenticator: TokenAuthenticator | None, audit: TokenAudit | None
) -> TokenSessions | None:
    """Return a session tracker for a token-set hub, or ``None`` for any other.

    Parameters
    ----------
    authenticator : TokenAuthenticator or None
        The hub's connect authenticator.
    audit : TokenAudit or None
        The hub's token audit, if it has a journal.

    Returns
    -------
    TokenSessions or None
        A tracker when sessions can outlive a token and either the journal
        records it or the hub closes them; otherwise ``None``.
    """
    if not isinstance(authenticator, TokenSetAuthenticator):
        return None
    if audit is None and not authenticator.expiry_closes_sessions:
        return None
    return TokenSessions(authenticator, audit, _close_expired)
