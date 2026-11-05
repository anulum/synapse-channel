# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — create, retire and list entries of a token-set file
"""Change a token-set file safely: add a token, retire one, list them.

Every change takes an exclusive lock, builds the complete new set, checks it
with the parser the hub uses, and only then replaces the file in one rename.
A change whose result does not validate writes nothing.

That order matters for retirement. The hub keeps its last accepted set when
the file on disk fails validation, so an invalid file written by a retire
would leave the retired token in force. Validating before the rename keeps a
retire from ever producing such a file.

The lock lives in an owner-only directory next to the set, named after it, so
the directory that holds the set keeps its own permissions.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import string
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from synapse_channel.advisory_lock import locked
from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.core.token_set import (
    TokenEntry,
    TokenSetError,
    connect_token_digest,
    load_token_set,
    parse_token_set,
    state_directory,
)
from synapse_channel.durable_dir import sync_directory

SECRET_BYTES = 32
"""Random bytes in a generated secret (256 bits)."""

MIN_SECRET_BITS = 128
"""Least estimated strength accepted for a secret the operator supplies."""

MIN_DISTINCT_CHARACTERS = 10
"""Least number of different characters in a supplied secret."""

LOCK_TIMEOUT_SECONDS = 10.0
"""Longest wait for another change to the same set to finish."""

_LOCK_POLL_SECONDS = 0.05

_INSTANT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_POOLS = (
    (frozenset(string.ascii_lowercase), 26),
    (frozenset(string.ascii_uppercase), 26),
    (frozenset(string.digits), 10),
)
_OTHER_POOL = 33


def positive_days(text: str) -> int:
    """Parse a whole number of days of at least one, for an argparse option."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a whole number of days, at least 1")
    return value


def generate_secret() -> str:
    """Return a new URL-safe connect secret of :data:`SECRET_BYTES` random bytes."""
    return secrets.token_urlsafe(SECRET_BYTES)


def estimated_secret_bits(secret: str) -> float:
    """Estimate the strength of ``secret`` from its length and character classes.

    Each character is credited with the bits of the union of the classes the
    secret uses (lower case, upper case, digits, anything else). This is an
    upper bound for a human-chosen secret, so it only refuses, never proves.

    Parameters
    ----------
    secret : str
        The candidate secret.

    Returns
    -------
    float
        Estimated bits; ``0.0`` for an empty secret.
    """
    if not secret:
        return 0.0
    pool = sum(size for members, size in _POOLS if any(char in members for char in secret))
    if any(not any(char in members for members, _ in _POOLS) for char in secret):
        pool += _OTHER_POOL
    return len(secret) * math.log2(pool)


def check_operator_secret(secret: str) -> None:
    """Refuse a supplied secret that is too weak to keep in a digest file.

    A leaked token-set file exposes digests, and a weak secret can be found
    from its digest offline. A generated secret always passes.

    Parameters
    ----------
    secret : str
        The secret read from ``--secret-file``.

    Raises
    ------
    TokenSetError
        If the secret holds whitespace or control characters, uses fewer than
        :data:`MIN_DISTINCT_CHARACTERS` different characters, or is estimated
        below :data:`MIN_SECRET_BITS`.
    """
    if any(char.isspace() or not char.isprintable() for char in secret):
        raise TokenSetError("the secret must not contain whitespace or control characters")
    if len(set(secret)) < MIN_DISTINCT_CHARACTERS:
        raise TokenSetError(
            f"the secret repeats too few characters (needs {MIN_DISTINCT_CHARACTERS} distinct)"
        )
    bits = estimated_secret_bits(secret)
    if bits < MIN_SECRET_BITS:
        raise TokenSetError(
            f"the secret is estimated at {bits:.0f} bits; at least {MIN_SECRET_BITS} are "
            "required (generate one instead)"
        )


def format_instant(moment: float) -> str:
    """Return POSIX seconds as the strict UTC form the token set stores."""
    return datetime.fromtimestamp(moment, timezone.utc).strftime(_INSTANT_FORMAT)


def parse_instant(text: str) -> float:
    """Return POSIX seconds for ``YYYY-MM-DDTHH:MM:SSZ``.

    Raises
    ------
    TokenSetError
        If ``text`` is not that form.
    """
    try:
        moment = datetime.strptime(text, _INSTANT_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        raise TokenSetError(f"{text!r} is not YYYY-MM-DDTHH:MM:SSZ") from None
    return moment.timestamp()


def token_status(entry: TokenEntry, now: float) -> str:
    """Return ``"valid"``, ``"expired"`` or ``"not yet valid"`` at ``now``."""
    if entry.not_before is not None and now < entry.not_before:
        return "not yet valid"
    if entry.not_after is not None and now >= entry.not_after:
        return "expired"
    return "valid"


def _entry_document(entry: TokenEntry) -> dict[str, object]:
    """Return the stored form of one entry."""
    document: dict[str, object] = {
        "id": entry.token_id,
        "sha256": entry.digest,
        "agents": sorted(entry.agents),
    }
    if entry.not_before is not None:
        document["not_before"] = format_instant(entry.not_before)
    if entry.not_after is not None:
        document["not_after"] = format_instant(entry.not_after)
    return document


def render_token_set(entries: Iterable[TokenEntry]) -> str:
    """Return the document for ``entries`` after proving the hub accepts it.

    Raises
    ------
    TokenSetError
        If the hub's parser refuses the document or reads back other entries.
    """
    wanted = tuple(entries)
    text = (
        json.dumps({"version": 1, "tokens": [_entry_document(entry) for entry in wanted]}, indent=2)
        + "\n"
    )
    if parse_token_set(text) != wanted:
        raise TokenSetError("the new token set does not read back as written; nothing written")
    return text


def lock_path(path: Path) -> Path:
    """Return the lock file that serialises changes to the set at ``path``."""
    return state_directory(path) / "lock"


@contextmanager
def _changing(path: Path, lock_timeout: float) -> Iterator[tuple[TokenEntry, ...]]:
    """Hold the set's lock and yield the entries now on disk.

    A missing file yields an empty set. A file that does not validate is not
    changed: the operator repairs it first, so no change can hide a refusal.
    The directory must already exist, so a mistyped path creates nothing.
    The lock is waited for at most ``lock_timeout`` seconds.
    """
    if not path.parent.is_dir():
        raise TokenSetError(f"directory {path.parent} does not exist")
    deadline = time.monotonic() + lock_timeout
    while True:
        with locked(lock_path(path), blocking=False, purpose="token-set lock directory") as held:
            if held:
                yield load_token_set(path) if os.path.lexists(path) else ()
                return
        if time.monotonic() >= deadline:
            raise TokenSetError(
                f"another change to {path} has held its lock for {lock_timeout:g} s; "
                "nothing written"
            )
        time.sleep(_LOCK_POLL_SECONDS)


def _whole_seconds(moment: float | None, field: str) -> None:
    """Refuse an instant the strict stored form cannot hold."""
    if moment is not None and (not math.isfinite(moment) or moment != int(moment)):
        raise TokenSetError(f"{field} must be a whole number of seconds")


def write_token_set(path: Path, entries: Iterable[TokenEntry]) -> None:
    """Replace the set at ``path`` with ``entries`` in one rename.

    The document is validated first, written owner-only to a temporary file
    in the same directory, synced, renamed over ``path``, and the directory is
    synced. Nothing is renamed when any step fails.

    Raises
    ------
    TokenSetError
        If the document does not validate.
    OSError
        If the file cannot be written.
    """
    text = render_token_set(entries)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        apply_owner_only_file(staged)
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    sync_directory(path.parent)


def add_token(
    path: Path,
    *,
    token_id: str,
    secret: str,
    agents: Iterable[str] = (),
    not_before: float | None = None,
    not_after: float | None = None,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> TokenEntry:
    """Add a token for ``secret`` to the set at ``path``, creating the set if absent.

    Parameters
    ----------
    path : pathlib.Path
        The token-set file.
    token_id : str
        New, unique identifier.
    secret : str
        The connect secret; only its digest is stored.
    agents : iterable of str, optional
        Agent names the token admits; empty admits any name.
    not_before, not_after : float or None, optional
        Validity window in whole POSIX seconds.
    lock_timeout : float, optional
        Longest wait for another change to the same set.

    Returns
    -------
    TokenEntry
        The entry written.

    Raises
    ------
    TokenSetError
        If the id is taken, the secret is already in the set, the entry is
        invalid, an instant has a fraction of a second, the directory is
        missing, the lock stays held past ``lock_timeout``, or the file on
        disk does not validate. Nothing is written.
    SecretFileError
        If the file on disk is linked, too large or readable by others.
    """
    _whole_seconds(not_before, "not_before")
    _whole_seconds(not_after, "not_after")
    entry = TokenEntry(
        token_id, connect_token_digest(secret), frozenset(agents), not_before, not_after
    )
    with _changing(path, lock_timeout) as current:
        if any(existing.token_id == token_id for existing in current):
            raise TokenSetError(f"token {token_id} already exists in {path}")
        write_token_set(path, (*current, entry))
    return entry


def retire_token(path: Path, token_id: str, *, lock_timeout: float = LOCK_TIMEOUT_SECONDS) -> int:
    """Remove the token ``token_id`` from the set at ``path``.

    Parameters
    ----------
    path : pathlib.Path
        The token-set file.
    token_id : str
        Identifier of the token to remove.
    lock_timeout : float, optional
        Longest wait for another change to the same set.

    Returns
    -------
    int
        Tokens left in the set.

    Raises
    ------
    TokenSetError
        If the set has no such token, the lock stays held past
        ``lock_timeout``, or the file on disk does not validate. Nothing is
        written.
    SecretFileError
        If the file on disk is linked, too large or readable by others.
    """
    with _changing(path, lock_timeout) as current:
        kept = tuple(entry for entry in current if entry.token_id != token_id)
        if len(kept) == len(current):
            raise TokenSetError(f"no token {token_id} in {path}")
        write_token_set(path, kept)
    return len(kept)
