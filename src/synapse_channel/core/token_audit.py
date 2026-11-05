# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — journal rows for connect-token admissions and token-set changes
"""Record which connect token admitted a session and when the token set changed.

A hub that follows a token set and has a journal writes five audit-only kinds.
Coordination replay skips them; they hold no secret and no digest.

``connect_token_set_applied``
    The file now in force, with its token identifiers.
``connect_token_set_rejected``
    A file that was not applied and why; the previous set stays in force.
``connect_token_admitted``
    One row per admitted session: the token identifier and the agent name.
``connect_token_session_ended``
    A session that outlived its token, with ``reason`` ``expired`` or
    ``retired``: ``closed`` is true when the hub ended it, false when it
    stayed open until the client left.
``connect_token_refused``
    Refusals, aggregated per reason and token identifier. The first refusal
    of a window is written at once; the others in the next
    :data:`REFUSAL_WINDOW_SECONDS` are counted and written as one row when the
    window closes, so a flood of bad tokens cannot flood the journal.

Agent names come from the connecting client and are cut to
:data:`AGENT_FIELD_LIMIT` characters.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.token_set import TokenSetAuthenticator

ADMITTED_KIND = "connect_token_admitted"
REFUSED_KIND = "connect_token_refused"
SET_APPLIED_KIND = "connect_token_set_applied"
SET_REJECTED_KIND = "connect_token_set_rejected"
SESSION_ENDED_KIND = "connect_token_session_ended"

REFUSAL_WINDOW_SECONDS = 60.0
"""Length of one refusal aggregation window."""

AGENT_FIELD_LIMIT = 128
"""Most characters of a client-supplied agent name kept in a row."""


def _agent_field(agent: str) -> str:
    """Cut a client-supplied agent name to the stored length."""
    return agent[:AGENT_FIELD_LIMIT]


@dataclass
class _Window:
    """An open refusal window for one reason and token identifier."""

    start: float
    agent: str
    suppressed: int = 0


class TokenAudit:
    """Write connect-token audit rows to a hub journal.

    Parameters
    ----------
    journal : EventStore
        The hub's durable event store.
    clock : Callable[[], float], optional
        Source of the current POSIX time for refusal windows.
    """

    def __init__(self, journal: EventStore, *, clock: Callable[[], float] = time.time) -> None:
        self._journal = journal
        self._clock = clock
        self._windows: dict[tuple[str, str | None], _Window] = {}

    def set_applied(self, path: Path, token_ids: tuple[str, ...]) -> None:
        """Record that the set at ``path`` is in force."""
        self._journal.append(
            SET_APPLIED_KIND,
            {"path": str(path), "token_ids": list(token_ids)},
            durable=True,
        )

    def set_rejected(self, path: Path, reason: str) -> None:
        """Record that the set at ``path`` was not applied."""
        self._journal.append(SET_REJECTED_KIND, {"path": str(path), "reason": reason}, durable=True)

    def admitted(self, token_id: str | None, agent: str) -> None:
        """Record the token that admitted one session."""
        self._close_expired(self._clock())
        self._journal.append(ADMITTED_KIND, {"token_id": token_id, "agent": _agent_field(agent)})

    def session_ended(self, token_id: str | None, agent: str, *, closed: bool, reason: str) -> None:
        """Record a session that was still open when its token stopped admitting it."""
        self._journal.append(
            SESSION_ENDED_KIND,
            {
                "token_id": token_id,
                "agent": _agent_field(agent),
                "closed": closed,
                "reason": reason,
            },
            durable=True,
        )

    def refused(self, token_id: str | None, code: str, agent: str) -> None:
        """Count one refusal, writing a row only when a window opens or closes.

        Parameters
        ----------
        token_id : str or None
            The matched entry, or ``None`` for a token that matched nothing.
        code : str
            The stable reason code of the refusal.
        agent : str
            The agent name the connection claimed.
        """
        now = self._clock()
        self._close_expired(now)
        key = (code, token_id)
        window = self._windows.get(key)
        if window is not None:
            window.suppressed += 1
            return
        self._windows[key] = _Window(now, _agent_field(agent))
        self._write_refusal(key, count=1, start=now, end=now, agent=_agent_field(agent))

    def flush(self) -> None:
        """Write every open window that counted refusals after its first row."""
        for key in list(self._windows):
            self._close(key, self._clock())

    def _close_expired(self, now: float) -> None:
        """Close each window that ended before ``now``."""
        for key, window in list(self._windows.items()):
            if now - window.start >= REFUSAL_WINDOW_SECONDS:
                self._close(key, now)

    def _close(self, key: tuple[str, str | None], now: float) -> None:
        """Remove one window and write its count when it suppressed rows."""
        window = self._windows.pop(key)
        if window.suppressed:
            end = min(now, window.start + REFUSAL_WINDOW_SECONDS)
            self._write_refusal(
                key, count=window.suppressed, start=window.start, end=end, agent=window.agent
            )

    def _write_refusal(
        self, key: tuple[str, str | None], *, count: int, start: float, end: float, agent: str
    ) -> None:
        """Append one aggregated refusal row."""
        code, token_id = key
        payload: dict[str, Any] = {
            "reason": code,
            "token_id": token_id,
            "agent": agent,
            "count": count,
            "window_start": start,
            "window_end": end,
        }
        self._journal.append(REFUSED_KIND, payload, durable=True)


def token_audit_for(
    authenticator: TokenAuthenticator | None, journal: EventStore | None
) -> TokenAudit | None:
    """Attach an audit to a token-set authenticator when the hub has a journal.

    Parameters
    ----------
    authenticator : TokenAuthenticator or None
        The hub's connect authenticator.
    journal : EventStore or None
        The hub's durable event store.

    Returns
    -------
    TokenAudit or None
        The attached audit, already holding the set in force; ``None`` for a
        hub without a token set or without a journal.
    """
    if journal is None or not isinstance(authenticator, TokenSetAuthenticator):
        return None
    audit = TokenAudit(journal)
    authenticator.observe(audit)
    return audit
