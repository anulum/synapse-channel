# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — refuse a peer URI at startup that a connection could never use
"""Check a configured peer URI the way the WebSocket client will read it.

A peer URI that the client refuses (a fragment, no host, a port out of range, a
scheme other than ``ws``/``wss``) used to pass configuration and fail only when a
forward, claim or poll first connected, with an error the transport did not expect.
:func:`require_peer_uri` refuses it when the hub starts instead.
"""

from __future__ import annotations

from websockets.exceptions import InvalidURI
from websockets.uri import parse_uri


def require_peer_uri(flag: str, name: str, uri: str) -> None:
    """Raise :class:`ValueError` unless the WebSocket client can open ``uri``.

    Parameters
    ----------
    flag : str
        The option that configured the URI, for the message (``--claim-peer``).
    name : str
        The peer the URI belongs to, for the message.
    uri : str
        The configured URI.

    Raises
    ------
    ValueError
        If ``uri`` holds a character that would need escaping in a Python string
        literal (a backslash, an unprintable character, or both kinds of quote mark),
        or the WebSocket client cannot parse it. The message names the reason, not the URI,
        so a credential written into it is not echoed.
    """
    if repr(uri)[1:-1] != uri:
        reason = (
            "it holds a character that needs escaping "
            "(a backslash, an unprintable character or both kinds of quote mark)"
        )
    else:
        try:
            parse_uri(uri)
            return
        except InvalidURI as exc:
            reason = exc.msg
        except ValueError as exc:
            reason = str(exc)
    raise ValueError(f"{flag} URI for {name!r} is not a usable WebSocket URI: {reason}")
