# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sessions whose connect token expires or is retired
"""Follow sessions admitted by a token set after their token stops admitting.

Expiry and retirement refuse new connections. What happens to a session that
is already open when its token expires or leaves the set depends on the set:

* by default the session stays open, and when it ends the journal records
  that it outlived its token (``closed`` false, ``reason`` ``expired`` or
  ``retired``);
* with ``expiry_closes_sessions`` the hub closes it with close code
  :data:`EXPIRY_CLOSE_CODE` when the token reaches its ``not_after`` or when
  a set is applied that no longer admits it, and the journal records that the
  hub ended it (``closed`` true).

A token no longer admits a session when no entry has its identifier and
digest, or when that entry no longer names the session's agent. A changed
``not_after`` moves the session's end to the new value, earlier or later.
In closing mode the hub checks the file every :data:`WATCH_INTERVAL_SECONDS`
as well as at each authentication, so a retire takes effect without waiting
for a new connection. Every admitted session is followed while it is open,
so memory grows with open sessions only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.token_audit import TokenAudit
from synapse_channel.core.token_set import TokenEntry, TokenSetAuthenticator

EXPIRY_CLOSE_CODE = 4401
"""WebSocket close code for a session ended because its token stopped admitting it."""

EXPIRY_CLOSE_REASON = "connect token expired"
"""Close reason for a session whose token reached its end of validity."""

RETIRED_CLOSE_REASON = "connect token retired"
"""Close reason for a session whose token left the set or no longer names its agent."""

WATCH_INTERVAL_SECONDS = 1.0
"""How often a closing hub checks the token-set file between authentications."""

_LOG = logging.getLogger(__name__)


@dataclass
class _Session:
    """One followed session."""

    entry: TokenEntry
    agent: str
    timer: asyncio.TimerHandle | None = None
    ended: str | None = None
    closed_by_hub: bool = False


def _still_admits(entry: TokenEntry, session: _Session) -> bool:
    """Whether ``entry`` is the session's token and still names its agent."""
    return entry.digest == session.entry.digest and (
        not entry.agents or session.agent in entry.agents
    )


class TokenSessions:
    """Track token-set sessions and act when their token stops admitting them.

    Parameters
    ----------
    authenticator : TokenSetAuthenticator
        The hub's token set; its entries, clock and closing policy are used.
    audit : TokenAudit or None
        Journal writer; ``None`` records nothing.
    close : Callable[[Any, str], Coroutine[Any, Any, None]]
        Closes one socket with :data:`EXPIRY_CLOSE_CODE` and the given reason.
    """

    def __init__(
        self,
        authenticator: TokenSetAuthenticator,
        audit: TokenAudit | None,
        close: Callable[[Any, str], Coroutine[Any, Any, None]],
    ) -> None:
        self._authenticator = authenticator
        self._audit = audit
        self._close = close
        self._closes = authenticator.expiry_closes_sessions
        self._sessions: dict[Any, _Session] = {}
        self._closing: set[asyncio.Task[None]] = set()

    def __len__(self) -> int:
        """Return the number of sessions followed now."""
        return len(self._sessions)

    @property
    def watches(self) -> bool:
        """Whether this tracker needs :meth:`watch` running (closing mode)."""
        return self._closes

    def admitted(self, websocket: Any, entry: TokenEntry, agent: str) -> None:
        """Follow a session that ``entry`` admitted for ``agent``."""
        session = _Session(entry, agent)
        self._sessions[websocket] = session
        self._arm(websocket, session)

    def _arm(self, websocket: Any, session: _Session) -> None:
        """Set the session's expiry timer from its entry, replacing any old one."""
        if session.timer is not None:
            session.timer.cancel()
            session.timer = None
        not_after = session.entry.not_after
        if self._closes and not_after is not None:
            delay = max(0.0, not_after - self._authenticator.now())
            session.timer = asyncio.get_running_loop().call_later(delay, self._expire, websocket)

    def _expire(self, websocket: Any) -> None:
        """Close a session whose token reached its end of validity.

        A timer is cancelled whenever its session closes or is ended first, and
        a cancelled timer never runs, so the session is still followed here.
        """
        session = self._sessions[websocket]
        session.ended = "expired"
        self._end(websocket, session, EXPIRY_CLOSE_REASON)

    def _end(self, websocket: Any, session: _Session, reason: str) -> None:
        """Close a followed session once and log a close that fails."""
        if session.timer is not None:
            session.timer.cancel()
            session.timer = None
        session.closed_by_hub = True
        task = asyncio.get_running_loop().create_task(self._close(websocket, reason))
        self._closing.add(task)
        task.add_done_callback(self._closed)

    def _closed(self, task: asyncio.Task[None]) -> None:
        """Forget a finished close task and log its failure."""
        self._closing.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOG.error("cannot close a connect-token session", exc_info=task.exception())

    def set_applied(self, path: Path, token_ids: tuple[str, ...]) -> None:
        """Re-judge every followed session against the set now in force."""
        current = {entry.token_id: entry for entry in self._authenticator.entries}
        for websocket, session in list(self._sessions.items()):
            if session.closed_by_hub:
                continue
            entry = current.get(session.entry.token_id)
            if entry is None or not _still_admits(entry, session):
                session.ended = session.ended or "retired"
                if self._closes:
                    self._end(websocket, session, RETIRED_CLOSE_REASON)
            elif entry != session.entry:
                session.entry = entry
                self._arm(websocket, session)

    def set_rejected(self, path: Path, reason: str) -> None:
        """Keep every session as it is; a rejected file changes nothing in force."""

    def closed(self, websocket: Any) -> None:
        """Stop following a socket that closed; record it if it outlived its token."""
        session = self._sessions.pop(websocket, None)
        if session is None:
            return
        if session.timer is not None:
            session.timer.cancel()
        reason = session.ended
        not_after = session.entry.not_after
        if reason is None and not_after is not None and self._authenticator.now() >= not_after:
            reason = "expired"
        if reason is not None and self._audit is not None:
            self._audit.session_ended(
                session.entry.token_id, session.agent, closed=session.closed_by_hub, reason=reason
            )

    async def watch(self, interval: float = WATCH_INTERVAL_SECONDS) -> None:
        """Check the token-set file every ``interval`` seconds while serving.

        A failed check is logged and the next one runs; the loop never ends
        on its own, so it cannot stop the hub.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                self._authenticator.refresh()
            except Exception:
                _LOG.exception("cannot check the token-set file")


async def _close_session(websocket: Any, reason: str) -> None:
    """Close ``websocket`` with the token close code and ``reason``."""
    await websocket.close(code=EXPIRY_CLOSE_CODE, reason=reason)


def token_sessions_for(
    authenticator: TokenAuthenticator | None, audit: TokenAudit | None
) -> TokenSessions | None:
    """Return a session tracker for a token-set hub, or ``None`` for any other.

    The tracker observes the token set, so each applied set re-judges the
    sessions it follows.

    Parameters
    ----------
    authenticator : TokenAuthenticator or None
        The hub's connect authenticator.
    audit : TokenAudit or None
        The hub's token audit, if it has a journal.

    Returns
    -------
    TokenSessions or None
        A tracker when the journal records outliving sessions or the hub
        closes them; otherwise ``None``.
    """
    if not isinstance(authenticator, TokenSetAuthenticator):
        return None
    if audit is None and not authenticator.expiry_closes_sessions:
        return None
    sessions = TokenSessions(authenticator, audit, _close_session)
    authenticator.observe(sessions)
    return sessions
