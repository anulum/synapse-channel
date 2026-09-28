# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private attachment filesystem, retention and crash tests
"""Exercise private attachment storage, integrity and recovery behavior."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.attachment_store import AttachmentError, AttachmentStore

SENDER = "proj/alice"
OTHER = "other/bob"


def test_corruption_interruption_quota_and_path_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staging is invisible; restart drops it, and hostile names never become paths."""
    import synapse_channel.core.attachment_store as module

    root = tmp_path / "private"
    store = AttachmentStore(root)
    digest = hashlib.sha256(b"safe").hexdigest()
    expiry = time.time() + 60
    token = store.begin(
        scope="proj",
        sender=SENDER,
        digest=digest,
        length=4,
        media_type="text/plain",
        provenance="claim:C12",
        expires_at=expiry,
    )
    store.chunk(token, SENDER, 0, b"bad!")
    with pytest.raises(AttachmentError, match="digest mismatch"):
        store.commit(token, SENDER)
    with pytest.raises(AttachmentError, match="invalid attachment scope"):
        store.begin(
            scope="../proj",
            sender=SENDER,
            digest=digest,
            length=4,
            media_type="text/plain",
            provenance="claim:C12",
            expires_at=expiry,
        )
    monkeypatch.setattr(module, "MAX_SCOPE_BYTES", 3)
    with pytest.raises(AttachmentError, match="quota"):
        store.begin(
            scope="proj",
            sender=SENDER,
            digest=digest,
            length=4,
            media_type="text/plain",
            provenance="claim:C12",
            expires_at=expiry,
        )
    monkeypatch.setattr(module, "MAX_SCOPE_BYTES", 256 * 1024 * 1024)
    token = store.begin(
        scope="proj",
        sender=SENDER,
        digest=digest,
        length=4,
        media_type="text/plain",
        provenance="claim:C12",
        expires_at=expiry,
    )
    store.chunk(token, SENDER, 0, b"sa")
    store.close()
    reopened = AttachmentStore(root)
    try:
        with pytest.raises(AttachmentError, match="unknown upload"):
            reopened.commit(token, SENDER)
        assert list(reopened.staging.iterdir()) == []
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("digest", "../objects/secret"),
        ("media_type", "text/html\nX-Evil: true"),
        ("provenance", "<img src=x>"),
        ("provenance", "claim:\u202eC12"),
        ("length", -1),
        ("length", True),
        ("expires_at", float("nan")),
    ],
)
def test_hostile_metadata_is_rejected(tmp_path: Path, field: str, value: Any) -> None:
    """Untrusted metadata cannot become a path, preview markup, or quota bypass."""
    store = AttachmentStore(tmp_path / "private")
    metadata: dict[str, Any] = {
        "scope": "proj",
        "sender": SENDER,
        "digest": hashlib.sha256(b"x").hexdigest(),
        "length": 1,
        "media_type": "text/plain",
        "provenance": "claim:C12",
        "expires_at": time.time() + 60,
    }
    metadata[field] = value
    try:
        with pytest.raises(AttachmentError):
            store.begin(**metadata)
        assert list(store.staging.iterdir()) == []
    finally:
        store.close()


def test_committed_file_corruption_is_never_read(tmp_path: Path) -> None:
    """A file modified after commit is refused before returning a byte."""
    store = AttachmentStore(tmp_path / "private")
    digest = hashlib.sha256(b"safe").hexdigest()
    try:
        upload_id = store.begin(
            scope="proj",
            sender=SENDER,
            digest=digest,
            length=4,
            media_type="text/plain",
            provenance="claim:C12",
            expires_at=time.time() + 60,
        )
        store.chunk(upload_id, SENDER, 0, b"safe")
        store.commit(upload_id, SENDER)
        path = store.objects / hashlib.sha256(f"proj\0{digest}".encode()).hexdigest()
        path.write_bytes(b"evil")
        with pytest.raises(AttachmentError, match="stored attachment digest mismatch"):
            store.read("proj", digest, 0)
    finally:
        store.close()


def test_sequential_chunks_and_reference_retention_across_restart(tmp_path: Path) -> None:
    """Only exact sequential chunks commit; referenced bytes survive restart and expiry."""
    root = tmp_path / "private"
    body = b"two chunks"
    digest = hashlib.sha256(body).hexdigest()
    store = AttachmentStore(root)
    upload_id = store.begin(
        scope="proj",
        sender=SENDER,
        digest=digest,
        length=len(body),
        media_type="application/octet-stream",
        provenance="commit:abc123",
        expires_at=time.time() + 60,
    )
    try:
        with pytest.raises(AttachmentError, match="incomplete"):
            store.commit(upload_id, SENDER)
        with pytest.raises(AttachmentError, match="offset"):
            store.chunk(upload_id, SENDER, 1, body[:3])
        assert store.chunk(upload_id, SENDER, 0, body[:3]) == 3
        with pytest.raises(AttachmentError, match="length"):
            store.chunk(upload_id, SENDER, 3, b"x" * 32769)
        assert store.chunk(upload_id, SENDER, 3, body[3:]) == len(body)
        store.commit(upload_id, SENDER)
        store.reference("proj", digest, "commit:abc123")
        with pytest.raises(AttachmentError, match="already exists"):
            store.begin(
                scope="proj",
                sender=SENDER,
                digest=digest,
                length=len(body),
                media_type="application/octet-stream",
                provenance="commit:abc123",
                expires_at=time.time() + 60,
            )
    finally:
        store.close()
    reopened = AttachmentStore(root)
    try:
        assert reopened.read("proj", digest, 0) == (body, True)
        with pytest.raises(AttachmentError, match="offset"):
            reopened.read("proj", digest, len(body) + 1)
        with pytest.raises(AttachmentError, match="reference"):
            reopened.reference("proj", digest, "../escape")
        reopened.db.execute("UPDATE objects SET expires_at=?", (time.time() - 1,))
        assert reopened.gc("proj", dry_run=False) == []
        with pytest.raises(AttachmentError, match="expired"):
            reopened.read("proj", digest, 0)
        with pytest.raises(AttachmentError, match="expired"):
            reopened.reference("proj", digest, "late:reference")
        reopened.reference("proj", digest, "commit:abc123", remove=True)
        assert reopened.gc("proj") == [digest]
        assert reopened.info("proj", digest)["digest"] == digest
        assert reopened.gc("proj", dry_run=False) == [digest]
        with pytest.raises(AttachmentError, match="unavailable"):
            reopened.info("proj", digest)
    finally:
        reopened.close()


def test_active_upload_limit_and_abort(tmp_path: Path) -> None:
    """Pending reservations count toward the global transfer ceiling until aborted."""
    store = AttachmentStore(tmp_path / "private")
    tokens: list[str] = []
    try:
        for number in range(4):
            digest = hashlib.sha256(str(number).encode()).hexdigest()
            tokens.append(
                store.begin(
                    scope="proj",
                    sender=SENDER,
                    digest=digest,
                    length=1,
                    media_type="text/plain",
                    provenance="claim:C12",
                    expires_at=time.time() + 60,
                )
            )
        with pytest.raises(AttachmentError, match="too many active"):
            store.begin(
                scope="proj",
                sender=SENDER,
                digest=hashlib.sha256(b"fifth").hexdigest(),
                length=1,
                media_type="text/plain",
                provenance="claim:C12",
                expires_at=time.time() + 60,
            )
        with pytest.raises(AttachmentError, match="unknown upload"):
            store.abort(tokens[0], OTHER)
        store.abort(tokens[0], SENDER)
        assert not (store.staging / tokens[0]).exists()
    finally:
        store.close()


def test_private_storage_rejects_symlinks_and_recovers_orphans(tmp_path: Path) -> None:
    """A symlink cannot redirect the store, and crash-orphan bytes are removed."""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AttachmentError, match="symbolic link"):
        AttachmentStore(link)
    root = tmp_path / "private"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(AttachmentError, match="owner-only"):
        AttachmentStore(root)
    root.chmod(0o700)
    (root / "attachments.sqlite3").symlink_to(tmp_path / "outside.db")
    with pytest.raises(AttachmentError, match="ledger is a symbolic link"):
        AttachmentStore(root)
    (root / "attachments.sqlite3").unlink()
    store = AttachmentStore(root)
    store.close()
    orphan = root / "objects" / ("a" * 64)
    orphan.write_bytes(b"orphan")
    staging_link = root / "staging" / "unsafe"
    staging_link.symlink_to(outside, target_is_directory=True)
    reopened = AttachmentStore(root)
    try:
        assert not orphan.exists()
        assert staging_link.is_symlink()
    finally:
        reopened.close()
        staging_link.unlink()


def test_staging_and_published_length_corruption_are_refused(tmp_path: Path) -> None:
    """Tampering at either phase fails before a corrupted object is served."""
    store = AttachmentStore(tmp_path / "private")
    digest = hashlib.sha256(b"safe").hexdigest()
    try:
        with pytest.raises(AttachmentError, match="unknown upload"):
            store.abort("../unsafe", SENDER)
        upload_id = store.begin(
            scope="proj",
            sender=SENDER,
            digest=digest,
            length=4,
            media_type="text/plain",
            provenance="claim:C12",
            expires_at=time.time() + 60,
        )
        (store.staging / upload_id).write_bytes(b"unexpected")
        with pytest.raises(AttachmentError, match="staging length mismatch"):
            store.chunk(upload_id, SENDER, 0, b"safe")
        (store.staging / upload_id).write_bytes(b"")
        store.chunk(upload_id, SENDER, 0, b"safe")
        filename = hashlib.sha256(f"proj\0{digest}".encode()).hexdigest()
        destination = store.objects / filename
        destination.write_bytes(b"preexisting")
        with pytest.raises(AttachmentError, match="already exists"):
            store.commit(upload_id, SENDER)
        destination.unlink()
        store.commit(upload_id, SENDER)
        destination.write_bytes(b"length changed")
        with pytest.raises(AttachmentError, match="stored attachment length mismatch"):
            store.read("proj", digest, 0)
    finally:
        store.close()


def test_reservation_database_failure_removes_staging_file(tmp_path: Path) -> None:
    """A failed durable reservation leaves no untracked upload bytes behind."""
    store = AttachmentStore(tmp_path / "private")
    try:
        store.db.execute(
            "CREATE TRIGGER reject_upload BEFORE INSERT ON uploads "
            "BEGIN SELECT RAISE(ABORT,'reservation refused'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="reservation refused"):
            store.begin(
                scope="proj",
                sender=SENDER,
                digest=hashlib.sha256(b"safe").hexdigest(),
                length=4,
                media_type="text/plain",
                provenance="claim:C12",
                expires_at=time.time() + 60,
            )
        assert list(store.staging.iterdir()) == []
    finally:
        store.close()


def test_commit_database_failure_removes_unpublished_object(tmp_path: Path) -> None:
    """A failed metadata commit leaves neither visible bytes nor reserved quota."""
    store = AttachmentStore(tmp_path / "private")
    digest = hashlib.sha256(b"safe").hexdigest()
    try:
        upload_id = store.begin(
            scope="proj",
            sender=SENDER,
            digest=digest,
            length=4,
            media_type="text/plain",
            provenance="claim:C12",
            expires_at=time.time() + 60,
        )
        store.chunk(upload_id, SENDER, 0, b"safe")
        store.db.execute(
            "CREATE TRIGGER reject_object BEFORE INSERT ON objects "
            "BEGIN SELECT RAISE(ABORT,'metadata refused'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="metadata refused"):
            store.commit(upload_id, SENDER)
        with pytest.raises(AttachmentError, match="unavailable"):
            store.info("proj", digest)
        assert list(store.objects.iterdir()) == []
        assert store.db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 0
    finally:
        store.close()
