# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — outbound peer tokens read from a file at each connection attempt
"""Supply the token a hub sends to a peer, from a value or a file read per attempt.

A peer token given inline (``--claim-peer-token`` and the like) is fixed for the
life of the process. A token file (``--claim-peer-token-file`` and the like) is
read again at every connection attempt, so replacing the file rotates the token
without a restart: the next attempt sends the new value.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import TypeAlias

from synapse_channel.core.secret_files import read_secret_file

RECENT_TOKENS = 4
"""How many recently read values a token file remembers, so logs can mask them."""


class PeerTokenFile:
    """An owner-only file holding the token for one kind of peer.

    Parameters
    ----------
    path:
        The file; it must stay owner-only, as every secret file.
    flag:
        The option that named it, quoted in every error.

    Notes
    -----
    Each :meth:`read` opens the file again. The last few values read are kept
    in memory only so that log text can mask them (:meth:`mask`); they are never
    written anywhere.
    """

    def __init__(self, path: str | Path, *, flag: str) -> None:
        self._path = Path(path).expanduser()
        self._flag = flag
        self._recent: deque[str] = deque(maxlen=RECENT_TOKENS)

    @property
    def path(self) -> Path:
        """Return the token file's path."""
        return self._path

    @property
    def flag(self) -> str:
        """Return the option that named the file."""
        return self._flag

    def read(self) -> str:
        """Read the token now.

        Returns
        -------
        str
            The file's single value, without surrounding whitespace.

        Raises
        ------
        SecretFileError
            If the file is missing, empty, oversized or readable by others; the
            message names the option and path, never the content.
        """
        token = read_secret_file(self._path, flag=self._flag)
        if token not in self._recent:
            self._recent.append(token)
        return token

    def mask(self, text: str) -> str:
        """Return ``text`` with every recently read token replaced by ``<token>``."""
        for token in self._recent:
            text = text.replace(token, "<token>")
        return text

    def __eq__(self, other: object) -> bool:
        """Compare by file and option, not by content read so far."""
        if not isinstance(other, PeerTokenFile):
            return NotImplemented
        return (self.path, self.flag) == (other.path, other.flag)

    def __hash__(self) -> int:
        """Hash by file and option."""
        return hash((self.path, self.flag))

    def __repr__(self) -> str:
        """Name the file and option; never a token."""
        return f"PeerTokenFile({str(self.path)!r}, flag={self.flag!r})"


PeerToken: TypeAlias = str | PeerTokenFile | None
"""A fixed token, a token file read per attempt, or no token for an open peer."""


def current_peer_token(token: PeerToken) -> str | None:
    """Return the token to send on this connection attempt.

    Parameters
    ----------
    token:
        A fixed value, a token file or ``None``.

    Returns
    -------
    str or None
        The fixed value, the file's current value, or ``None``.

    Raises
    ------
    SecretFileError
        If a token file cannot be read as an owner-only secret.
    """
    if isinstance(token, PeerTokenFile):
        return token.read()
    return token


def mask_peer_token(text: str, token: PeerToken) -> str:
    """Replace the peer token, or every recently read value of its file, in ``text``."""
    if isinstance(token, PeerTokenFile):
        return token.mask(text)
    return text.replace(token, "<token>") if token else text
