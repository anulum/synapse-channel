# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a rotating set of per-message HMAC keys
"""Per-message HMAC keys that rotate while the hub runs.

A key set is an owner-only JSON file that names each key and points at the
owner-only file holding its secret, so the set file itself holds no secret::

    {"version": 1, "keys": [
        {"key_id": "agent-a-2026-11", "secret_file": "agent-a-2026-11.key",
         "senders": ["PROJECT/agent-a"], "not_after": "2026-12-01T00:00:00Z"}
    ]}

A relative ``secret_file`` is resolved against the set file's directory. The
set is re-read when the status of the set file or of any secret file it names
changes (device, inode, size, modification time, mode, owner, link count,
change time), so replacing only a secret file rotates that key. A set that fails
validation is not applied: the last accepted set stays in force and observers
are told why.

The set in force is one immutable snapshot, and a lookup reads it and nothing
else: no file status, no file and no observer that writes. Checking the files
is a separate step. :meth:`MessageAuthKeySet.prepare` reads them and returns
what it found without changing the set in force, so the hub runs it in a worker
thread; :meth:`MessageAuthKeySet.apply` then puts the result in force in one
assignment. A lookup therefore sees the old snapshot or the new one, never a
mixture, and a change takes effect at the next check.

As a mapping from key id to :class:`~synapse_channel.core.message_auth.MessageAuthKey`
the set offers every key whose ``not_before`` has passed. A key past its
``not_after`` is still offered, carrying that end, so verification refuses it
``expired``; a key not yet valid, or no longer in the file, is unknown.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.core.token_set import status_digest
from synapse_channel.core.utc_instants import parse_utc_instant

KEY_SET_FLAG = "--message-auth-key-set"
"""The hub option that names a key-set file."""

KEY_SET_LIMIT = 262_144
"""Largest accepted key-set file, in bytes."""

_LOG = logging.getLogger(__name__)
_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_ENTRY_KEYS = frozenset({"key_id", "secret_file", "senders", "not_before", "not_after"})


class MessageAuthKeySetError(SynapseError, ValueError):
    """Raised when a key-set document is not a valid version-1 set.

    The message names the offending entry by its key id or position, never a
    secret.
    """

    code = "message_auth_key_set_invalid"


@dataclass(frozen=True)
class MessageAuthKeyEntry:
    """One key of a set, without its secret.

    Parameters
    ----------
    key_id : str
        The key id clients carry as ``auth.kid``.
    secret_file : pathlib.Path
        The owner-only file holding the secret.
    senders : frozenset of str
        Sender names that may sign with the key.
    not_before, not_after : float or None
        Validity window in POSIX seconds; ``None`` leaves that side open.
    """

    key_id: str
    secret_file: Path
    senders: frozenset[str]
    not_before: float | None
    not_after: float | None


class MessageAuthKeySetObserver(Protocol):
    """Receives each change in which key set is in force."""

    def set_applied(self, path: Path, key_ids: tuple[str, ...], /) -> None:
        """Report that the set at ``path`` is now in force."""

    def set_rejected(self, path: Path, reason: str, /) -> None:
        """Report that the set at ``path`` was not applied and why."""


@dataclass(frozen=True)
class _KeySnapshot:
    """The set in force: its entries in file order and each entry's secret."""

    entries: tuple[MessageAuthKeyEntry, ...]
    secrets: Mapping[str, bytes]


def _snapshot(entries: tuple[MessageAuthKeyEntry, ...], secrets: dict[str, bytes]) -> _KeySnapshot:
    """Freeze ``entries`` and a read-only view of ``secrets`` into one snapshot."""
    return _KeySnapshot(entries, MappingProxyType(dict(secrets)))


@dataclass(frozen=True)
class PreparedKeySet:
    """What one check of the files found, before it is put in force.

    Parameters
    ----------
    signature : tuple of str
        The status digests of the set file and the secret files to watch from
        now on; the next check compares against them.
    watched : tuple of pathlib.Path
        The secret files watched from now on.
    snapshot : _KeySnapshot or None
        The new set, or ``None`` when the files on disk cannot be put in force.
    reason : str or None
        Why the files on disk cannot be put in force, when ``snapshot`` is
        ``None``.
    """

    signature: tuple[str, ...]
    watched: tuple[Path, ...]
    snapshot: _KeySnapshot | None
    reason: str | None


def _status_of(path: Path, watched: tuple[Path, ...]) -> tuple[str, ...]:
    """Return the status digests of the set file and every watched secret file."""
    return (status_digest(path), *(status_digest(secret) for secret in watched))


def message_auth_key_configured(args: Any) -> bool:
    """Return whether hub arguments configure per-message authentication keys.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed hub arguments; ``message_auth_key`` holds the static keys from
        argv and ``--message-auth-key-file``, ``message_auth_key_set`` the
        optional key-set path.

    Returns
    -------
    bool
        ``True`` when static keys or a key set are configured.
    """
    return bool(
        getattr(args, "message_auth_key", None) or getattr(args, "message_auth_key_set", None)
    )


def _instant(value: object, *, field: str, label: str) -> float | None:
    """Parse an optional strict UTC instant of one entry."""
    if value is None:
        return None
    try:
        return parse_utc_instant(value)
    except ValueError as exc:
        raise MessageAuthKeySetError(f"key {label}: {field} {exc}") from exc


def _entry(raw: object, position: int, base: Path) -> MessageAuthKeyEntry:
    """Validate one raw entry; ``position`` names it until its key id is known."""
    if not isinstance(raw, dict):
        raise MessageAuthKeySetError(f"key #{position}: an entry must be an object")
    unknown = set(raw) - _ENTRY_KEYS
    if unknown:
        raise MessageAuthKeySetError(f"key #{position}: unknown fields {sorted(unknown)}")
    key_id = raw.get("key_id")
    if not isinstance(key_id, str) or not _ID.fullmatch(key_id):
        raise MessageAuthKeySetError(f"key #{position}: key_id must match [A-Za-z0-9._-]{{1,64}}")
    secret_file = raw.get("secret_file")
    if not isinstance(secret_file, str) or not secret_file:
        raise MessageAuthKeySetError(f"key {key_id}: secret_file must name a file")
    senders = raw.get("senders")
    if (
        not isinstance(senders, list)
        or not senders
        or not all(isinstance(name, str) and name for name in senders)
    ):
        raise MessageAuthKeySetError(f"key {key_id}: senders must be a non-empty list of names")
    not_before = _instant(raw.get("not_before"), field="not_before", label=key_id)
    not_after = _instant(raw.get("not_after"), field="not_after", label=key_id)
    if not_before is not None and not_after is not None and not_after <= not_before:
        raise MessageAuthKeySetError(f"key {key_id}: not_after must be later than not_before")
    path = Path(secret_file).expanduser()
    return MessageAuthKeyEntry(
        key_id,
        path if path.is_absolute() else base / path,
        frozenset(senders),
        not_before,
        not_after,
    )


def parse_message_auth_key_set(text: str, *, base: Path) -> tuple[MessageAuthKeyEntry, ...]:
    """Validate a version-1 key-set document.

    Parameters
    ----------
    text : str
        The JSON document.
    base : pathlib.Path
        Directory against which a relative ``secret_file`` is resolved.

    Returns
    -------
    tuple of MessageAuthKeyEntry
        The entries in file order.

    Raises
    ------
    MessageAuthKeySetError
        On malformed JSON, an unknown version, a bad entry or a duplicate key id.
    """
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MessageAuthKeySetError(f"key set is not valid JSON (line {exc.lineno})") from None
    if not isinstance(document, dict) or set(document) != {"version", "keys"}:
        raise MessageAuthKeySetError("key set must be an object with exactly version and keys")
    if document["version"] != 1 or isinstance(document["version"], bool):
        raise MessageAuthKeySetError("key set version must be 1")
    raw_entries = document["keys"]
    if not isinstance(raw_entries, list):
        raise MessageAuthKeySetError("keys must be a list")
    entries = tuple(_entry(raw, position, base) for position, raw in enumerate(raw_entries))
    if len({entry.key_id for entry in entries}) != len(entries):
        raise MessageAuthKeySetError("key ids must be unique")
    return entries


def _read_manifest(path: Path) -> tuple[MessageAuthKeyEntry, ...]:
    """Read and validate the owner-only set file."""
    text = read_secret_file(path, flag=KEY_SET_FLAG, require_single_link=True, limit=KEY_SET_LIMIT)
    return parse_message_auth_key_set(text, base=path.parent)


def _read_secrets(entries: tuple[MessageAuthKeyEntry, ...]) -> dict[str, bytes]:
    """Read every entry's owner-only secret file."""
    return {
        entry.key_id: read_secret_file(
            entry.secret_file, flag=f"{KEY_SET_FLAG} key {entry.key_id}", require_single_link=True
        ).encode("utf-8")
        for entry in entries
    }


class MessageAuthKeySet(Mapping[str, MessageAuthKey]):
    """A live, rotating mapping of per-message HMAC keys.

    Parameters
    ----------
    path : str or pathlib.Path
        The key-set file. It is loaded at construction; a set or secret file
        that cannot be accepted at that point is an error.
    clock : Callable[[], float], optional
        Source of the current POSIX time for validity windows.

    Raises
    ------
    SecretFileError, MessageAuthKeySetError
        If the initial set cannot be accepted.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path).expanduser()
        self._clock = clock
        entries = _read_manifest(self._path)
        self._snapshot = _snapshot(entries, _read_secrets(entries))
        self._watched = tuple(entry.secret_file for entry in entries)
        self._signature = _status_of(self._path, self._watched)
        self._stale_reason: str | None = None
        self._observers: list[MessageAuthKeySetObserver] = []
        self._lookup_observers: list[Callable[[], None]] = []
        self._admission_observers: list[Callable[[MessageAuthKeyEntry, str], None]] = []

    @property
    def path(self) -> Path:
        """The key-set file this set follows."""
        return self._path

    @property
    def entries(self) -> tuple[MessageAuthKeyEntry, ...]:
        """The set in force (the last accepted file), in file order."""
        return self._snapshot.entries

    @property
    def stale_reason(self) -> str | None:
        """Why the files on disk are not in force, or ``None`` when they are."""
        return self._stale_reason

    def now(self) -> float:
        """Return the current time on the clock that judges validity windows."""
        return self._clock()

    def observe(self, observer: MessageAuthKeySetObserver) -> None:
        """Send every later change to ``observer``, starting with the set in force now."""
        self._observers.append(observer)
        observer.set_applied(self._path, self._key_ids())
        if self._stale_reason is not None:
            observer.set_rejected(self._path, self._stale_reason)

    def observe_lookups(self, observer: Callable[[], None]) -> None:
        """Call ``observer`` at every key lookup.

        It runs inside a verification, so it must not block.
        """
        self._lookup_observers.append(observer)

    def observe_admissions(self, observer: Callable[[MessageAuthKeyEntry, str], None]) -> None:
        """Call ``observer`` with the entry and sender of every verified frame."""
        self._admission_observers.append(observer)

    def admitted(self, key_id: str, sender: str) -> None:
        """Report that a frame from ``sender`` signed with ``key_id`` was verified.

        A failing observer is logged; the frame stays admitted. The observers run
        inside the frame's verification, so they must not block.
        """
        entry = next((item for item in self._snapshot.entries if item.key_id == key_id), None)
        if entry is None:
            return
        for observer in self._admission_observers:
            try:
                observer(entry, sender)
            except Exception:
                _LOG.exception("a message-auth key-set admission observer failed")

    def _key_ids(self) -> tuple[str, ...]:
        """Return the key ids of the set in force, in file order."""
        return tuple(entry.key_id for entry in self._snapshot.entries)

    def prepare(self) -> PreparedKeySet | None:
        """Check the files and read them if they changed, without changing the set in force.

        It reads only the set's path, the secret files it watches and the
        status it last saw, and mutates nothing, so the hub runs it in a
        worker thread while lookups go on. One check runs at a time: the
        caller passes its result to :meth:`apply` before the next one.

        Returns
        -------
        PreparedKeySet or None
            ``None`` when no file changed; otherwise the new set, or why the
            files on disk cannot be put in force. A set file that parses names
            the secret files watched from then on, even if one of them cannot
            be read yet, so repairing that file is noticed.
        """
        current = _status_of(self._path, self._watched)
        if current == self._signature:
            return None
        try:
            entries = _read_manifest(self._path)
        except (SecretFileError, MessageAuthKeySetError) as exc:
            return PreparedKeySet(current, self._watched, None, str(exc))
        watched = tuple(entry.secret_file for entry in entries)
        signature = _status_of(self._path, watched)
        try:
            secrets = _read_secrets(entries)
        except SecretFileError as exc:
            return PreparedKeySet(signature, watched, None, str(exc))
        return PreparedKeySet(signature, watched, _snapshot(entries, secrets), None)

    def apply(self, prepared: PreparedKeySet | None) -> None:
        """Put what :meth:`prepare` found in force; call it where lookups run.

        A new set replaces the snapshot in one assignment and is announced to
        the observers. Files that cannot be put in force keep the last set and
        are announced with their reason.
        """
        if prepared is None:
            return
        self._watched, self._signature = prepared.watched, prepared.signature
        if prepared.snapshot is None:
            self._mark_stale(str(prepared.reason))
            return
        self._snapshot, self._stale_reason = prepared.snapshot, None
        for observer in self._observers:
            try:
                observer.set_applied(self._path, self._key_ids())
            except Exception:
                _LOG.exception("a message-auth key-set observer failed on an applied set")

    def refresh(self) -> None:
        """Check the files and put any change in force now, on the calling thread.

        This is :meth:`prepare` followed by :meth:`apply`, for callers that may
        block; the hub's watcher runs the two apart so the read stays off its
        serving loop.
        """
        self.apply(self.prepare())

    def _mark_stale(self, reason: str) -> None:
        """Record and log that the files on disk are not in force."""
        self._stale_reason = reason
        _LOG.warning("message-auth key set not applied (%s); the last set stays in force", reason)
        for observer in self._observers:
            try:
                observer.set_rejected(self._path, reason)
            except Exception:
                _LOG.exception("a message-auth key-set observer failed on a rejected set")

    def _offered(self, snapshot: _KeySnapshot) -> list[MessageAuthKeyEntry]:
        """Return the entries of ``snapshot`` whose ``not_before`` has passed."""
        now = self._clock()
        return [
            entry
            for entry in snapshot.entries
            if entry.not_before is None or now >= entry.not_before
        ]

    def __getitem__(self, key_id: str) -> MessageAuthKey:
        """Return the key ``key_id`` from the set in force; no file is read."""
        snapshot = self._snapshot
        for observer in self._lookup_observers:
            try:
                observer()
            except Exception:
                _LOG.exception("a message-auth key-set lookup observer failed")
        for entry in self._offered(snapshot):
            if entry.key_id == key_id:
                return MessageAuthKey(
                    key_id=entry.key_id,
                    secret=snapshot.secrets[entry.key_id],
                    senders=entry.senders,
                    not_after=entry.not_after,
                )
        raise KeyError(key_id)

    def __iter__(self) -> Iterator[str]:
        """Iterate the key ids the set in force offers now, in file order."""
        return iter([entry.key_id for entry in self._offered(self._snapshot)])

    def __len__(self) -> int:
        """Return how many keys the set in force offers now."""
        return len(self._offered(self._snapshot))
