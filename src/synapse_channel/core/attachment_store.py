# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private, bounded content-addressed attachment storage
"""Local attachment bytes and metadata; authorisation belongs to the caller.

Only opaque server-generated names reach the filesystem. The store never accepts
a path from a wire frame. One process owns a store root and its SQLite ledger.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import re
import secrets
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any

MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_SCOPE_BYTES = 256 * 1024 * 1024
MAX_CHUNK_BYTES = 32 * 1024
MAX_ACTIVE_UPLOADS = 4
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_MEDIA = re.compile(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,80}\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_PROVENANCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/#@-]{0,255}\Z")


class AttachmentError(ValueError):
    """A bounded attachment request failed without disclosing stored content."""


def _private_dir(path: Path) -> None:
    """Create or validate a directory that only its owner can traverse."""
    if path.is_symlink():
        raise AttachmentError("attachment directory is a symbolic link")
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise AttachmentError("attachment directory must be owner-only")


class AttachmentStore:
    """Atomic uploads and reference-aware retention for one private root."""

    def __init__(self, root: str | Path, *, clock: Any = time.time) -> None:
        self.root = Path(root).expanduser().absolute()
        _private_dir(self.root)
        self.staging = self.root / "staging"
        self.objects = self.root / "objects"
        _private_dir(self.staging)
        _private_dir(self.objects)
        self._clock = clock
        ledger_path = self.root / "attachments.sqlite3"
        if ledger_path.is_symlink():
            raise AttachmentError("attachment ledger is a symbolic link")
        self.db = sqlite3.connect(ledger_path, isolation_level=None)
        os.chmod(ledger_path, 0o600)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS objects (
              scope TEXT NOT NULL, digest TEXT NOT NULL, length INTEGER NOT NULL,
              media_type TEXT NOT NULL, provenance TEXT NOT NULL, expires_at REAL NOT NULL,
              filename TEXT NOT NULL, PRIMARY KEY(scope,digest)
            );
            CREATE TABLE IF NOT EXISTS uploads (
              token TEXT PRIMARY KEY, scope TEXT NOT NULL, sender TEXT NOT NULL,
              digest TEXT NOT NULL, length INTEGER NOT NULL, received INTEGER NOT NULL,
              media_type TEXT NOT NULL, provenance TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refs (
              scope TEXT NOT NULL, digest TEXT NOT NULL, ref TEXT NOT NULL,
              PRIMARY KEY(scope,digest,ref),
              FOREIGN KEY(scope,digest) REFERENCES objects(scope,digest) ON DELETE CASCADE
            );
            """
        )
        # Interrupted uploads have no authority and are never resumable after restart.
        self.db.execute("DELETE FROM uploads")
        for child in self.staging.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        live = {str(row[0]) for row in self.db.execute("SELECT filename FROM objects")}
        for child in self.objects.iterdir():
            if child.name not in live and child.is_file() and not child.is_symlink():
                child.unlink()

    @staticmethod
    def validate_scope(scope: str) -> str:
        """Accept one project identifier, never a client-supplied path."""
        if not _SCOPE.fullmatch(scope):
            raise AttachmentError("invalid attachment scope")
        return scope

    @staticmethod
    def validate_digest(digest: str) -> str:
        """Accept canonical lowercase SHA-256 spelling only."""
        if not _DIGEST.fullmatch(digest):
            raise AttachmentError("invalid attachment digest")
        return digest

    def _scope_usage(self, scope: str) -> int:
        """Count ready and reserved bytes against the scope quota."""
        ready = self.db.execute(
            "SELECT COALESCE(SUM(length),0) FROM objects WHERE scope=?", (scope,)
        ).fetchone()[0]
        pending = self.db.execute(
            "SELECT COALESCE(SUM(length),0) FROM uploads WHERE scope=?", (scope,)
        ).fetchone()[0]
        return int(ready) + int(pending)

    def begin(
        self,
        *,
        scope: str,
        sender: str,
        digest: str,
        length: int,
        media_type: str,
        provenance: str,
        expires_at: float,
    ) -> str:
        """Reserve quota and an opaque staging token for a sequential upload."""
        self.validate_scope(scope)
        self.validate_digest(digest)
        if (
            not isinstance(length, int)
            or isinstance(length, bool)
            or not 0 <= length <= MAX_ATTACHMENT_BYTES
        ):
            raise AttachmentError("invalid attachment length")
        if not _MEDIA.fullmatch(media_type):
            raise AttachmentError("invalid media type")
        if not isinstance(provenance, str) or not _PROVENANCE.fullmatch(provenance):
            raise AttachmentError("invalid provenance")
        now = float(self._clock())
        if (
            not isinstance(expires_at, (int, float))
            or isinstance(expires_at, bool)
            or not math.isfinite(expires_at)
            or not now < expires_at <= now + 365 * 86400
        ):
            raise AttachmentError("invalid expiry")
        if self.db.execute(
            "SELECT 1 FROM objects WHERE scope=? AND digest=?", (scope, digest)
        ).fetchone():
            raise AttachmentError("attachment already exists")
        if self.db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] >= MAX_ACTIVE_UPLOADS:
            raise AttachmentError("too many active uploads")
        if self._scope_usage(scope) + length > MAX_SCOPE_BYTES:
            raise AttachmentError("attachment scope quota exceeded")
        token = secrets.token_hex(24)
        fd = os.open(
            self.staging / token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        os.close(fd)
        try:
            self.db.execute(
                "INSERT INTO uploads VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    token,
                    scope,
                    sender,
                    digest,
                    length,
                    0,
                    media_type,
                    provenance,
                    float(expires_at),
                ),
            )
        except Exception:
            (self.staging / token).unlink(missing_ok=True)
            raise
        return token

    def _upload(self, token: str, sender: str) -> tuple[Any, ...]:
        """Resolve only the sending identity's active staging token."""
        if not re.fullmatch(r"[0-9a-f]{48}", token):
            raise AttachmentError("unknown upload")
        row = self.db.execute(
            "SELECT * FROM uploads WHERE token=? AND sender=?", (token, sender)
        ).fetchone()
        if row is None:
            raise AttachmentError("unknown upload")
        return tuple(row)

    def chunk(self, token: str, sender: str, offset: int, body: bytes) -> int:
        """Append one bounded chunk at the exact expected offset."""
        row = self._upload(token, sender)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset != row[5]:
            raise AttachmentError("invalid chunk offset")
        if not body or len(body) > MAX_CHUNK_BYTES or offset + len(body) > row[4]:
            raise AttachmentError("invalid chunk length")
        fd = os.open(self.staging / token, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_size != offset:
                raise AttachmentError("staging length mismatch")
            view = memoryview(body)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        self.db.execute("UPDATE uploads SET received=? WHERE token=?", (offset + len(body), token))
        return offset + len(body)

    def commit(self, token: str, sender: str) -> dict[str, Any]:
        """Verify all bytes, publish atomically, then make metadata visible."""
        row = self._upload(token, sender)
        _, scope, _, digest, length, received, media_type, provenance, expires_at = row
        if received != length:
            raise AttachmentError("upload incomplete")
        stage = self.staging / token
        sha = hashlib.sha256()
        fd = os.open(stage, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            while part := source.read(MAX_CHUNK_BYTES):
                sha.update(part)
        if sha.hexdigest() != digest or stage.stat().st_size != length:
            self.abort(token, sender)
            raise AttachmentError("attachment digest mismatch")
        filename = hashlib.sha256(f"{scope}\0{digest}".encode()).hexdigest()
        destination = self.objects / filename
        if destination.exists():
            raise AttachmentError("attachment already exists")
        os.replace(stage, destination)
        fd = os.open(self.objects, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "INSERT INTO objects VALUES(?,?,?,?,?,?,?)",
                (scope, digest, length, media_type, provenance, expires_at, filename),
            )
            self.db.execute("DELETE FROM uploads WHERE token=?", (token,))
            self.db.execute("COMMIT")
        except sqlite3.DatabaseError:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            destination.unlink(missing_ok=True)
            with contextlib.suppress(sqlite3.DatabaseError):
                self.db.execute("DELETE FROM uploads WHERE token=?", (token,))
            raise
        return self.info(scope, digest)

    def abort(self, token: str, sender: str) -> None:
        """Discard the sender's incomplete transfer and release reserved quota."""
        self._upload(token, sender)
        self.db.execute("DELETE FROM uploads WHERE token=?", (token,))
        (self.staging / token).unlink(missing_ok=True)

    def abort_sender(self, sender: str) -> None:
        """Discard every pending upload of one disconnected socket session."""
        tokens = [
            str(row[0])
            for row in self.db.execute("SELECT token FROM uploads WHERE sender=?", (sender,))
        ]
        self.db.execute("DELETE FROM uploads WHERE sender=?", (sender,))
        for token in tokens:
            (self.staging / token).unlink(missing_ok=True)

    def info(self, scope: str, digest: str) -> dict[str, Any]:
        """Return metadata after the caller has authorised the scope and digest."""
        self.validate_scope(scope)
        self.validate_digest(digest)
        row = self.db.execute(
            "SELECT length,media_type,provenance,expires_at,filename "
            "FROM objects WHERE scope=? AND digest=?",
            (scope, digest),
        ).fetchone()
        if row is None:
            raise AttachmentError("attachment unavailable")
        length, media_type, provenance, expiry, _ = row
        return {
            "scope": scope,
            "digest": digest,
            "length": length,
            "media_type": media_type,
            "provenance": provenance,
            "expires_at": expiry,
        }

    def read(self, scope: str, digest: str, offset: int) -> tuple[bytes, bool]:
        """Read at most one chunk and reject content that no longer matches metadata."""
        metadata = self.info(scope, digest)
        if metadata["expires_at"] <= float(self._clock()):
            raise AttachmentError("attachment expired")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not 0 <= offset <= metadata["length"]
        ):
            raise AttachmentError("invalid read offset")
        filename = hashlib.sha256(f"{scope}\0{digest}".encode()).hexdigest()
        fd = os.open(self.objects / filename, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_size != metadata["length"]:
                raise AttachmentError("stored attachment length mismatch")
            # Verify the complete small bounded object before disclosing any bytes.
            sha = hashlib.sha256()
            while part := os.read(fd, MAX_CHUNK_BYTES):
                sha.update(part)
            if sha.hexdigest() != digest:
                raise AttachmentError("stored attachment digest mismatch")
            os.lseek(fd, offset, os.SEEK_SET)
            chunk = os.read(fd, MAX_CHUNK_BYTES)
        finally:
            os.close(fd)
        return chunk, offset + len(chunk) >= metadata["length"]

    def reference(self, scope: str, digest: str, ref: str, *, remove: bool = False) -> None:
        """Pin or unpin an object with an explicitly named project-local reference."""
        metadata = self.info(scope, digest)
        if not remove and metadata["expires_at"] <= float(self._clock()):
            raise AttachmentError("attachment expired")
        if not _REFERENCE.fullmatch(ref):
            raise AttachmentError("invalid attachment reference")
        if remove:
            self.db.execute(
                "DELETE FROM refs WHERE scope=? AND digest=? AND ref=?", (scope, digest, ref)
            )
        else:
            self.db.execute("INSERT OR IGNORE INTO refs VALUES(?,?,?)", (scope, digest, ref))

    def gc(self, scope: str, *, dry_run: bool = True) -> list[str]:
        """Collect expired, unreferenced objects in one authorised project scope."""
        self.validate_scope(scope)
        rows = self.db.execute(
            "SELECT digest,filename FROM objects o WHERE scope=? AND expires_at<=? "
            "AND NOT EXISTS(SELECT 1 FROM refs r WHERE r.scope=o.scope AND r.digest=o.digest)",
            (scope, float(self._clock())),
        ).fetchall()
        if not dry_run:
            for digest, filename in rows:
                self.db.execute("DELETE FROM objects WHERE scope=? AND digest=?", (scope, digest))
                (self.objects / filename).unlink(missing_ok=True)
        return [str(digest) for digest, _ in rows]

    def close(self) -> None:
        """Close the ledger after the hub stops serving."""
        self.db.close()
