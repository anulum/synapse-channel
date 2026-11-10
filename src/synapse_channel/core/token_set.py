# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — connect tokens with identifiers, validity and live reload
"""Connect tokens that carry an identifier, a validity window and agent names.

A token set is an owner-only JSON file. Each entry stores the SHA-256 digest of
a secret, never the secret, so a copied file does not hand out a usable token.
Several entries may be valid at once, which lets an operator rotate: add the
new token, move the clients over, then retire the old one. The hub re-reads the
file when it changes, so a rotation needs no restart. A file that fails
validation is not applied: the hub keeps the last set it accepted and reports
itself stale until a valid file appears.

The digest of a low-entropy secret can be guessed offline from a leaked file.
Secrets for a token set should be generated, not chosen.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.core.auth import _QUOTA_PRINCIPAL_DOMAIN, TokenAuthenticator
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.core.utc_instants import parse_utc_instant

TOKEN_DIGEST_DOMAIN = b"synapse-channel:connect-token:v1\x00"
"""Domain prefix hashed before a secret, separate from the quota principal."""

TOKEN_SET_LIMIT = 262_144
"""Largest accepted token-set file, in bytes."""

SET_FILE_FLAG = "--token-set"
_LOG = logging.getLogger(__name__)
_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ENTRY_KEYS = frozenset({"id", "sha256", "agents", "not_before", "not_after"})


class TokenSetError(SynapseError, ValueError):
    """Raised when a token-set document is not a valid version-1 set.

    The message names the offending entry by its identifier or position,
    never a digest or a secret.
    """

    code = "token_set_invalid"


@dataclass(frozen=True)
class TokenEntry:
    """One accepted connect token, identified without its secret.

    Parameters
    ----------
    token_id : str
        Operator-chosen identifier, used in audit records and refusals.
    digest : str
        Lowercase hex SHA-256 of :data:`TOKEN_DIGEST_DOMAIN` plus the secret.
    agents : frozenset of str
        Agent names the token admits; empty admits any name.
    not_before, not_after : float or None
        Validity window in POSIX seconds; ``None`` leaves that side open.
    """

    token_id: str
    digest: str
    agents: frozenset[str]
    not_before: float | None
    not_after: float | None


@dataclass(frozen=True)
class TokenOutcome:
    """The verdict on one presented connect token.

    Parameters
    ----------
    ok : bool
        Whether the connection is admitted.
    reason : str
        Human-readable reason; names a token identifier, never a secret.
    principal : str or None
        Stable quota principal on success.
    token_id : str or None
        Identifier of the matched entry, or ``None`` when nothing matched.
    code : str
        Stable reason code: ``admitted``, ``missing``, ``unknown``,
        ``not_yet_valid``, ``expired``, ``agent_not_bound`` or ``policy``.
    entry : TokenEntry or None, optional
        The admitting entry, set only on success.
    """

    ok: bool
    reason: str
    principal: str | None
    token_id: str | None
    code: str
    entry: TokenEntry | None = None


class TokenSetObserver(Protocol):
    """Receives each change in which token set is in force."""

    def set_applied(self, path: Path, token_ids: tuple[str, ...]) -> None:
        """Report that the file at ``path`` is now in force."""

    def set_rejected(self, path: Path, reason: str) -> None:
        """Report that the file at ``path`` was not applied and why."""


def connect_token_digest(secret: str) -> str:
    """Return the token-set digest of ``secret``.

    Parameters
    ----------
    secret : str
        The connect token as clients present it.

    Returns
    -------
    str
        Lowercase hex SHA-256 over the domain prefix and the UTF-8 secret.
    """
    return hashlib.sha256(TOKEN_DIGEST_DOMAIN + secret.encode("utf-8")).hexdigest()


def _instant(value: object, *, field: str, label: str) -> float | None:
    """Parse an optional strict RFC 3339 UTC instant ending in ``Z``."""
    if value is None:
        return None
    try:
        return parse_utc_instant(value)
    except ValueError as exc:
        raise TokenSetError(f"token {label}: {field} {exc}") from exc


def _entry(raw: object, position: int) -> TokenEntry:
    """Validate one raw entry; ``position`` names it until its id is known."""
    if not isinstance(raw, dict):
        raise TokenSetError(f"token #{position}: an entry must be an object")
    unknown = set(raw) - _ENTRY_KEYS
    if unknown:
        raise TokenSetError(f"token #{position}: unknown fields {sorted(unknown)}")
    token_id = raw.get("id")
    if not isinstance(token_id, str) or not _ID.fullmatch(token_id):
        raise TokenSetError(f"token #{position}: id must match [A-Za-z0-9._-]{{1,64}}")
    digest = raw.get("sha256")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise TokenSetError(f"token {token_id}: sha256 must be 64 lowercase hex digits")
    agents = raw.get("agents", [])
    if not isinstance(agents, list) or not all(isinstance(name, str) and name for name in agents):
        raise TokenSetError(f"token {token_id}: agents must be a list of names")
    not_before = _instant(raw.get("not_before"), field="not_before", label=token_id)
    not_after = _instant(raw.get("not_after"), field="not_after", label=token_id)
    if not_before is not None and not_after is not None and not_after <= not_before:
        raise TokenSetError(f"token {token_id}: not_after must be later than not_before")
    return TokenEntry(token_id, digest, frozenset(agents), not_before, not_after)


def parse_token_set(text: str) -> tuple[TokenEntry, ...]:
    """Validate a version-1 token-set document.

    Parameters
    ----------
    text : str
        The JSON document.

    Returns
    -------
    tuple of TokenEntry
        The entries in file order. An empty tuple admits no connection.

    Raises
    ------
    TokenSetError
        On malformed JSON, an unknown version, a bad entry, or a duplicate
        identifier or digest.
    """
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TokenSetError(f"token set is not valid JSON (line {exc.lineno})") from None
    if not isinstance(document, dict) or set(document) != {"version", "tokens"}:
        raise TokenSetError("token set must be an object with exactly version and tokens")
    if document["version"] != 1 or isinstance(document["version"], bool):
        raise TokenSetError("token set version must be 1")
    raw_entries = document["tokens"]
    if not isinstance(raw_entries, list):
        raise TokenSetError("tokens must be a list")
    entries = tuple(_entry(raw, position) for position, raw in enumerate(raw_entries))
    if len({entry.token_id for entry in entries}) != len(entries):
        raise TokenSetError("token ids must be unique")
    if len({entry.digest for entry in entries}) != len(entries):
        raise TokenSetError("two entries share one secret")
    return entries


def load_token_set(path: Path) -> tuple[TokenEntry, ...]:
    """Read and validate an owner-only token-set file.

    Parameters
    ----------
    path : pathlib.Path
        The token-set file. It must be a single-link regular file readable by
        its owner only; symbolic links are refused.

    Returns
    -------
    tuple of TokenEntry
        The validated entries.

    Raises
    ------
    SecretFileError
        If the file is missing, linked, too large or readable by others.
    TokenSetError
        If the content is not a valid token set.
    """
    text = read_secret_file(
        path, flag=SET_FILE_FLAG, require_single_link=True, limit=TOKEN_SET_LIMIT
    )
    return parse_token_set(text)


def state_directory(path: Path) -> Path:
    """Return the owner-only directory beside a set that holds its lock and hub states.

    Parameters
    ----------
    path : pathlib.Path
        The token-set file.

    Returns
    -------
    pathlib.Path
        ``.<name>.lockdir`` in the set's directory.
    """
    return path.parent / f".{path.name}.lockdir"


def _signature(path: Path) -> tuple[int, ...] | None:
    """Return the identity and access state of ``path`` without following a link.

    Mode, owner, link count and change time are included with the content
    fields, so a ``chmod``, ``chown`` or hard link alone also triggers a reload.
    A path that cannot be inspected gets a signature of its own error, so the
    reload that follows reports why instead of the check raising.
    """
    try:
        status = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return (-1, exc.errno or 0)
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_mode,
        status.st_uid,
        status.st_nlink,
        status.st_ctime_ns,
    )


def _status_digest(signature: tuple[int, ...] | None) -> str:
    """Return the SHA-256 hex of a file status, without any file content."""
    return hashlib.sha256(repr(signature).encode("ascii")).hexdigest()


def status_digest(path: Path) -> str:
    """Return the digest of the status of ``path`` as a token-set hub sees it now.

    Two digests are equal when the hub would see no change, so a hub whose
    published digest differs from this one has not yet looked at the file.

    Parameters
    ----------
    path : pathlib.Path
        The token-set file.

    Returns
    -------
    str
        SHA-256 hex over the file status; never over its content.
    """
    return _status_digest(_signature(path))


class TokenSetAuthenticator(TokenAuthenticator):
    """Authenticate against a token-set file that may change while the hub runs.

    Parameters
    ----------
    path : str or pathlib.Path
        The token-set file. It is loaded at construction; an invalid or missing
        file at that point is an error.
    clock : Callable[[], float], optional
        Source of the current POSIX time for validity windows.
    expiry_closes_sessions : bool, optional
        Ask the hub to close a session when the token that admitted it
        reaches ``not_after``. By default expiry refuses new connections only.
    require_expiry : bool, optional
        Admit no connection with a token that has no ``not_after``.
    max_token_days : int or None, optional
        Admit no connection with a token whose ``not_after`` is more than this
        many days away at the moment of the connection, or that has none.

    Raises
    ------
    SecretFileError, TokenSetError
        If the initial file cannot be accepted.

    Notes
    -----
    Before each authentication the file's device, inode, size, modification
    time, mode, owner, link count and change time are compared with the last
    read. A difference triggers a reload. A reload that fails keeps the
    previous set and records why in :attr:`stale_reason`. The check, the read
    and the swap run without an ``await``, so no other coroutine can
    authenticate against a half-loaded set.

    The token policy (``require_expiry``, ``max_token_days``) acts on single
    tokens when they are presented, not on the file. A set that holds a token
    the policy refuses is still applied, so a retire written into the same
    file always takes effect; the refused token admits nothing, with the code
    ``policy``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        expiry_closes_sessions: bool = False,
        require_expiry: bool = False,
        max_token_days: int | None = None,
    ) -> None:
        super().__init__(())
        self._path = Path(path).expanduser()
        self._clock = clock
        self._expiry_closes_sessions = expiry_closes_sessions
        self._require_expiry = require_expiry
        self._max_token_days = max_token_days
        self._signature = _signature(self._path)
        self._entries = load_token_set(self._path)
        self._stale_reason: str | None = None
        self._stale_code: str | None = None
        self._observers: list[TokenSetObserver] = []
        self._outcome_observers: list[Callable[[TokenOutcome], None]] = []

    @property
    def policy(self) -> dict[str, object]:
        """The token policy this hub applies, as published to operators."""
        return {"require_expiry": self._require_expiry, "max_token_days": self._max_token_days}

    def policy_refusal(self, entry: TokenEntry, now: float) -> str | None:
        """Return why this hub's token policy refuses ``entry`` at ``now``, or ``None``."""
        if self._require_expiry and entry.not_after is None:
            return (
                f"Connect token '{entry.token_id}' has no end of validity, which this hub requires."
            )
        days = self._max_token_days
        if days is not None and (entry.not_after is None or entry.not_after - now > days * 86_400):
            return (
                f"Connect token '{entry.token_id}' stays valid for more than {days} days, "
                "longer than this hub allows."
            )
        return None

    def policy_refused(self) -> tuple[str, ...]:
        """Return the identifiers in force that the token policy refuses now."""
        now = self._clock()
        return tuple(
            entry.token_id for entry in self._entries if self.policy_refusal(entry, now) is not None
        )

    def observe(self, observer: TokenSetObserver) -> None:
        """Send every later change to ``observer``, starting with the set in force now.

        A set that is stale at this point is reported as applied and then as
        rejected, so the observer starts from the same state as the hub.
        Observers are called in the order they were added.
        """
        self._observers.append(observer)
        observer.set_applied(self._path, self._token_ids())
        if self._stale_reason is not None:
            observer.set_rejected(self._path, self._stale_reason)

    def observe_outcomes(self, observer: Callable[[TokenOutcome], None]) -> None:
        """Send the outcome of every later authentication to ``observer``.

        Observers are called in the order they were added, after the verdict is
        reached and before it is returned. A failing observer is logged; the
        verdict is not changed by it.
        """
        self._outcome_observers.append(observer)

    def _token_ids(self) -> tuple[str, ...]:
        """Return the identifiers of the set in force, in file order."""
        return tuple(entry.token_id for entry in self._entries)

    @property
    def path(self) -> Path:
        """The token-set file this authenticator follows."""
        return self._path

    @property
    def entries(self) -> tuple[TokenEntry, ...]:
        """The set currently in force (the last accepted file)."""
        return self._entries

    @property
    def stale_reason(self) -> str | None:
        """Why the file on disk is not in force, or ``None`` when it is."""
        return self._stale_reason

    @property
    def stale_code(self) -> str | None:
        """Why the file is not in force as a code (``missing``, ``unsafe``, ``invalid``)."""
        return self._stale_code

    @property
    def signature_digest(self) -> str:
        """SHA-256 hex of the file status last seen, without any file content."""
        return _status_digest(self._signature)

    @property
    def is_empty(self) -> bool:
        """Whether the set in force admits no connection."""
        return not self._entries

    @property
    def expiry_closes_sessions(self) -> bool:
        """Whether a session ends when the token that admitted it expires."""
        return self._expiry_closes_sessions

    def now(self) -> float:
        """Return the current time on the clock that judges validity windows."""
        return self._clock()

    def refresh(self) -> None:
        """Reload the file if it changed; keep the current set if it fails.

        A failing observer is logged and the others are still told; the
        change has already happened, so it is not undone.
        """
        current = _signature(self._path)
        if current == self._signature:
            return
        self._signature = current
        if current is None:
            self._mark_stale(f"{self._path} is missing", "missing")
            return
        try:
            self._entries = load_token_set(self._path)
        except SecretFileError as exc:
            self._mark_stale(str(exc), "unsafe")
            return
        except TokenSetError as exc:
            self._mark_stale(str(exc), "invalid")
            return
        self._stale_reason = None
        self._stale_code = None
        for observer in self._observers:
            try:
                observer.set_applied(self._path, self._token_ids())
            except Exception:
                _LOG.exception("a token-set observer failed on an applied set")

    def _mark_stale(self, reason: str, code: str) -> None:
        """Record and log that the file on disk is not in force."""
        self._stale_reason = reason
        self._stale_code = code
        _LOG.warning("token set not applied (%s); the last accepted set stays in force", reason)
        for observer in self._observers:
            try:
                observer.set_rejected(self._path, reason)
            except Exception:
                _LOG.exception("a token-set observer failed on a rejected set")

    def authenticate_with_principal(self, token: str, agent: str) -> tuple[bool, str, str | None]:
        """Authenticate against the set in force after picking up any change.

        Returns
        -------
        tuple[bool, str, str or None]
            Verdict, reason and the quota principal on success. The reason
            names the token identifier, never its secret.
        """
        outcome = self.authenticate_outcome(token, agent)
        return outcome.ok, outcome.reason, outcome.principal

    def authenticate_outcome(self, token: str, agent: str) -> TokenOutcome:
        """Authenticate and name the matched entry and a stable reason code.

        Every entry is compared, in constant time, before the match is used,
        so the position of a token in the file does not shape the refusal.

        Parameters
        ----------
        token : str
            The presented secret.
        agent : str
            The agent name the connection claims.

        Returns
        -------
        TokenOutcome
            The verdict with the matched identifier, if any.
        """
        outcome = self._judge(token, agent)
        for observer in self._outcome_observers:
            try:
                observer(outcome)
            except Exception:
                _LOG.exception("a token-set outcome observer failed")
        return outcome

    def _judge(self, token: str, agent: str) -> TokenOutcome:
        """Reach the verdict on ``token`` for ``agent`` against the set in force."""
        self.refresh()
        candidate = str(token)
        if not candidate:
            return TokenOutcome(False, "Authentication token required.", None, None, "missing")
        digest = connect_token_digest(candidate)
        matched: TokenEntry | None = None
        for entry in self._entries:
            if hmac.compare_digest(entry.digest, digest) and matched is None:
                matched = entry
        if matched is None:
            return TokenOutcome(False, "Invalid authentication token.", None, None, "unknown")
        token_id = matched.token_id
        now = self._clock()
        if matched.not_before is not None and now < matched.not_before:
            reason = f"Connect token '{token_id}' is not yet valid."
            return TokenOutcome(False, reason, None, token_id, "not_yet_valid")
        if matched.not_after is not None and now >= matched.not_after:
            reason = f"Connect token '{token_id}' has expired."
            return TokenOutcome(False, reason, None, token_id, "expired")
        if matched.agents and agent not in matched.agents:
            reason = f"Token is not authorised for agent '{agent}'."
            return TokenOutcome(False, reason, None, token_id, "agent_not_bound")
        refusal = self.policy_refusal(matched, now)
        if refusal is not None:
            return TokenOutcome(False, refusal, None, token_id, "policy")
        principal = hashlib.sha256(_QUOTA_PRINCIPAL_DOMAIN + candidate.encode("utf-8"))
        return TokenOutcome(
            True,
            "Authenticated.",
            f"auth-token:{principal.hexdigest()}",
            token_id,
            "admitted",
            matched,
        )


def connect_token_configured(args: Any) -> bool:
    """Return whether hub arguments configure connect authentication.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed hub arguments; ``token`` is the resolved shared secret and
        ``token_set`` the optional token-set path.

    Returns
    -------
    bool
        ``True`` when either a shared secret or a token set is configured.
    """
    return bool(getattr(args, "token", None) or getattr(args, "token_set", None))


def metrics_token_configured(args: Any) -> bool:
    """Return whether hub arguments guard the metrics surface with a token.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed hub arguments; ``metrics_token`` is the resolved fixed metrics
        token and ``metrics_token_set`` the optional metrics token-set path.

    Returns
    -------
    bool
        ``True`` when either a fixed metrics token or a metrics token set is
        configured.
    """
    return bool(getattr(args, "metrics_token", None) or getattr(args, "metrics_token_set", None))
