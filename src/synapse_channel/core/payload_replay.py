# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — receiver-side replay ledger for encrypted payload envelopes (K3-F5)
"""Refuse an encrypted payload envelope that a receiver has already opened.

The route-bound AAD stops an envelope from being moved to another route. It does
not stop an attacker who captured one envelope from sending it again on the same
route later: the hub never decrypts, so only the receiver can tell. Version 2
envelopes carry a random ``message_id`` and the sender's ``created_at_ms`` inside
the AAD. The receiver admits each one once into a durable, owner-only ledger:

* a ``message_id`` already admitted from that sender under that key is a replay;
* an envelope older than the replay window is stale, because the ledger forgets
  identities after that window;
* an envelope dated further ahead than the allowed clock skew is refused, so an
  attacker cannot pre-date one past the window.

The ledger reuses :class:`~synapse_channel.core.message_auth_durable.DurableMessageAuthReplayStore`.
Its identity triple is the receiver's key fingerprint, the hub-visible sender and
the ``message_id``, so an unauthenticated field cannot open a second slot for the
same envelope. Admission happens only after the AAD and the GCM tag verified, so
nobody without the key can fill the ledger. A full ledger refuses new envelopes
rather than forgetting live identities. Version 1 envelopes carry no identity;
they open with ``replay_protected=False`` unless the caller requires protection.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from synapse_channel.core.message_auth_durable import (
    DurableAdmitResult,
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.payload_crypto import (
    LEGACY_PAYLOAD_ENVELOPE_VERSION,
    PayloadContext,
    PayloadCryptoError,
    authenticate_payload,
    payload_key_fingerprint,
)

DEFAULT_PAYLOAD_REPLAY_WINDOW_SECONDS = 86_400.0
"""How long an envelope stays acceptable, and so how long its id is remembered.

It matches the default cross-hub forward TTL, so a chat delivered late from a
forward outbox or a reconnect backlog is still accepted once.
"""

DEFAULT_PAYLOAD_REPLAY_CAPACITY = 100_000
"""Largest number of remembered envelope ids before new envelopes are refused."""

DEFAULT_PAYLOAD_FUTURE_SKEW_SECONDS = 300.0
"""How far ahead of the receiver clock a sender clock may run."""


class PayloadReplayError(PayloadCryptoError):
    """An authenticated envelope was refused by the receiver's replay ledger.

    Attributes
    ----------
    reason : str
        ``replayed``, ``stale``, ``future``, ``capacity`` or ``unprotected``.
    """

    code = "payload_replay"

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class OpenedPayload:
    """A decrypted payload and whether the replay ledger admitted it.

    Attributes
    ----------
    plaintext : str
        The decrypted text.
    version : int
        Envelope version.
    message_id : str or None
        Authenticated message id (version 2).
    replay_protected : bool
        ``True`` only when the ledger admitted this envelope for the first time.
    """

    plaintext: str
    version: int
    message_id: str | None
    replay_protected: bool


def default_payload_replay_ledger(receiver: str, *, base: Path | None = None) -> Path:
    """Return the default ledger file for one receiving identity.

    Parameters
    ----------
    receiver : str
        The listener's name. Each receiver keeps its own ledger, so two local
        listeners that both receive one envelope do not refuse each other.
    base : pathlib.Path or None, optional
        Data-home override. ``None`` reads ``$XDG_DATA_HOME`` and falls back to
        ``~/.local/share``.

    Returns
    -------
    pathlib.Path
        ``<data-home>/synapse/payload-replay/<sha256(receiver)[:16]>.db``.
    """
    if base is None:
        raw = os.environ.get("XDG_DATA_HOME", "").strip()
        base = Path(raw) if raw else Path.home() / ".local" / "share"
    digest = hashlib.sha256(receiver.encode("utf-8")).hexdigest()[:16]
    return base / "synapse" / "payload-replay" / f"{digest}.db"


class PayloadReplayGuard:
    """Durable, owner-only ledger of opened envelope ids for one receiver.

    Parameters
    ----------
    path : str or pathlib.Path
        Ledger file. Missing parent directories are created owner-only.
    window_seconds : float, optional
        Replay window; see :data:`DEFAULT_PAYLOAD_REPLAY_WINDOW_SECONDS`.
    max_entries : int, optional
        Ledger capacity; see :data:`DEFAULT_PAYLOAD_REPLAY_CAPACITY`.
    future_skew_seconds : float, optional
        Allowed sender-clock lead; see :data:`DEFAULT_PAYLOAD_FUTURE_SKEW_SECONDS`.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        window_seconds: float = DEFAULT_PAYLOAD_REPLAY_WINDOW_SECONDS,
        max_entries: int = DEFAULT_PAYLOAD_REPLAY_CAPACITY,
        future_skew_seconds: float = DEFAULT_PAYLOAD_FUTURE_SKEW_SECONDS,
    ) -> None:
        if not window_seconds > 0 or window_seconds == float("inf"):
            raise ValueError("payload replay window must be positive and finite")
        if not 0 <= future_skew_seconds < float("inf"):
            raise ValueError("payload replay future skew must be finite and nonnegative")
        target = Path(path).expanduser()
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.window_seconds = float(window_seconds)
        self.future_skew_seconds = float(future_skew_seconds)
        self._store = DurableMessageAuthReplayStore(
            target, max_entries=max_entries, window_seconds=self.window_seconds
        )

    def admit(
        self,
        *,
        key_fingerprint: str,
        sender: str,
        message_id: str,
        created_at_ms: int,
        now: float,
    ) -> None:
        """Admit one authenticated envelope identity, or raise why it is refused.

        Raises
        ------
        PayloadReplayError
            With reason ``future``, ``stale``, ``replayed`` or ``capacity``.
        """
        created = created_at_ms / 1000.0
        if created > now + self.future_skew_seconds:
            raise PayloadReplayError(
                "future",
                f"encrypted payload is dated {created - now:.0f}s ahead of this receiver",
            )
        if created < now - self.window_seconds:
            raise PayloadReplayError(
                "stale",
                f"encrypted payload is older than the {self.window_seconds:.0f}s replay window",
            )
        result = self._store.admit(
            key_id=key_fingerprint,
            sender=sender or "-",
            nonce=message_id,
            sequence=1,
            timestamp=created,
            now=now,
            mode=SequenceFloorMode.OFF,
        )
        if result is DurableAdmitResult.REPLAYED:
            raise PayloadReplayError(
                "replayed", f"encrypted payload {message_id} was already opened (replay)"
            )
        if result is DurableAdmitResult.CAPACITY:
            raise PayloadReplayError(
                "capacity", "encrypted payload replay ledger is full; refusing new envelopes"
            )

    def close(self) -> None:
        """Close the ledger."""
        self._store.close()

    def __enter__(self) -> PayloadReplayGuard:
        """Return this open guard for a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the ledger when leaving a context manager."""
        self.close()


def open_payload(
    envelope: Mapping[str, object],
    key: bytes,
    *,
    context: PayloadContext,
    replay_guard: PayloadReplayGuard | None,
    require_replay_protection: bool = False,
    now: float | None = None,
) -> OpenedPayload:
    """Decrypt an envelope and admit it once into the receiver's replay ledger.

    Parameters
    ----------
    envelope : collections.abc.Mapping[str, object]
        Version 1 or version 2 envelope.
    key : bytes
        Raw 32-byte payload key.
    context : PayloadContext
        Visible route metadata from the received hub frame.
    replay_guard : PayloadReplayGuard or None
        The receiver's ledger; ``None`` decrypts without replay protection.
    require_replay_protection : bool, optional
        Refuse a version 1 envelope, which cannot be checked, instead of
        opening it with ``replay_protected=False``.
    now : float or None, optional
        Receiver wall clock; ``None`` reads it.

    Returns
    -------
    OpenedPayload
        The plaintext and whether the ledger admitted it.

    Raises
    ------
    PayloadCryptoError
        When the envelope does not authenticate (see
        :func:`~synapse_channel.core.payload_crypto.authenticate_payload`).
    PayloadReplayError
        When the ledger refuses it, or protection is required for a version 1
        envelope (reason ``unprotected``).
    """
    opened = authenticate_payload(envelope, key, context=context)
    legacy = opened.version == LEGACY_PAYLOAD_ENVELOPE_VERSION
    if legacy and require_replay_protection:
        raise PayloadReplayError(
            "unprotected", "version 1 encrypted payload carries no replay identity; refused"
        )
    if replay_guard is None or opened.message_id is None or opened.created_at_ms is None:
        return OpenedPayload(opened.plaintext, opened.version, opened.message_id, False)
    replay_guard.admit(
        key_fingerprint=payload_key_fingerprint(key),
        sender=context.sender,
        message_id=opened.message_id,
        created_at_ms=opened.created_at_ms,
        now=time.time() if now is None else now,
    )
    return OpenedPayload(opened.plaintext, opened.version, opened.message_id, True)
