# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local wake storage controls
"""Expose local owner-only storage status, capacity and fenced generation archival."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from synapse_channel.core.secure_path import read_owner_only_file_bytes
from synapse_channel.mcp.http_config import HttpAuthConfig, load_http_auth_config
from synapse_channel.mcp.wake_admission import WakeAdmission
from synapse_channel.mcp.wake_limits import WAKE_DATABASE_APPLICATION_ID, RootLimits
from synapse_channel.mcp.wake_pointer import MAX_WAKE_INTEGER, WakeLedgerError, wake_scope_key
from synapse_channel.mcp.wake_root import RootInventory, locked_wake_root, scan_wake_root
from synapse_channel.mcp.wake_root_archive import (
    ArchiveSnapshot,
    archive_destination,
    archive_location_digest,
    archive_wake_files,
    prepare_archive_directory,
    snapshot_wake_generation,
    verify_wake_archive,
)
from synapse_channel.mcp.wake_root_catalogue import RootEntry, root_catalogue


def _policy_digest(config: HttpAuthConfig) -> str:
    """Fence semantic operator authority without exposing its URLs, paths or key material."""
    value = config.model_dump(mode="json")
    value["revoked_token_ids"] = sorted(config.revoked_token_ids)
    for subject, authority in config.subjects.items():
        for project, grant in authority.projects.items():
            value["subjects"][subject]["projects"][project]["tools"] = sorted(grant.tools)
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _current_generations(path: Path, config: HttpAuthConfig) -> frozenset[str]:
    """Keep every enabled provisioned wake generation out of operator archival."""
    keys = set()
    for subject, authority in config.subjects.items():
        if not authority.enabled:
            continue
        for project, grant in authority.projects.items():
            if grant.wake is not None:
                admission = WakeAdmission(path, subject, project, config)
                keys.add(wake_scope_key(subject, project, grant.seat, admission.binding))
    return frozenset(keys)


def _native_summary(path: Path) -> dict[str, int] | None:
    """Read only fixed counters; corrupt or hot-journal state remains explicitly unknown."""
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute("PRAGMA application_id").fetchone()[0] != WAKE_DATABASE_APPLICATION_ID:
                return None
            rows = db.execute("SELECT pending FROM meta LIMIT 2").fetchall()
            if len(rows) != 1 or type(rows[0][0]) is not int or not 0 <= rows[0][0] <= 7:
                return None
            if db.execute("PRAGMA user_version").fetchone()[0] not in (1, 2, 3, 4):
                return None
            counts = dict(
                db.execute("SELECT state,count(*) FROM deliveries GROUP BY state LIMIT 6")
            )
            if (
                not counts.keys() <= {"inflight", "retry", "accepted", "dead-letter", "retired"}
                or sum(counts.values()) > 64
            ):
                return None
            return {
                "pending_sources": rows[0][0],
                "active_deliveries": counts.get("inflight", 0) + counts.get("retry", 0),
                "unresolved_failures": counts.get("dead-letter", 0),
            }
    except sqlite3.Error:
        return None


def _read_manifest(directory: Path, key: str, fingerprint: str) -> ArchiveSnapshot:
    """Validate a retired snapshot from its durable external manifest."""
    raw = read_owner_only_file_bytes(
        directory / "manifest.json",
        purpose="MCP wake archive manifest",
        max_bytes=8192,
    )
    snapshot = ArchiveSnapshot.from_json(raw.decode("ascii"))
    if snapshot.key != key or snapshot.fingerprint != fingerprint:
        raise WakeLedgerError("wake archive manifest binding changed")
    return snapshot


class WakeStorageControl:
    """Administer only the state root beside an existing owner-private HTTP policy.

    No remote MCP agent tool exposes these controls. The caller supplies local
    owner authority through that private file, never a native seat or Bot token.
    Raw archival preserves pending and uncertain transport state; it neither
    consumes native work nor retracts an HTTP request already in flight.
    """

    def __init__(self, auth_file: Path) -> None:
        """Fix policy and state-root locations outside later operator arguments."""
        self.auth_file = auth_file.absolute()
        self.root = self.auth_file.parent / ".synapse-mcp-wake"

    @contextmanager
    def _operator(
        self, *, create: bool = False
    ) -> Iterator[tuple[HttpAuthConfig, str, RootInventory]]:
        """Reload private owner authority under the same portable root lock."""
        try:
            load_http_auth_config(self.auth_file)
            with locked_wake_root(self.root, create=create) as inventory:
                config = load_http_auth_config(self.auth_file)
                with root_catalogue(self.root) as catalogue:
                    catalogue.bind_policy(self.auth_file.resolve().name)
                yield config, _policy_digest(config), inventory
        except (OSError, ValueError, sqlite3.Error):
            raise WakeLedgerError("wake storage control unavailable") from None

    def status(self) -> dict[str, object]:
        """Inspect bounded physical usage and reservations without work bodies or native I/O."""
        with self._operator() as (_, policy, inventory):
            with root_catalogue(self.root) as catalogue:
                limits = catalogue.limits()
                keys = inventory.keys | catalogue.live_keys()
                entries = []
                for key in sorted(keys):
                    entry = catalogue.get(key)
                    entries.append(
                        {
                            "key": key,
                            "phase": entry.phase if entry is not None else "legacy",
                            "main_bytes": inventory.sizes.get(key + ".sqlite3", 0),
                            "journal_bytes": inventory.sizes.get(key + ".sqlite3-journal", 0),
                        }
                    )
                result: dict[str, object] = {
                    "policy_digest": policy,
                    "revision": catalogue.revision(),
                    "max_ledgers": limits.max_ledgers,
                    "max_bytes": limits.max_bytes,
                    "logical_bytes": inventory.logical_bytes,
                    "files": len(inventory.sizes),
                    "occupied_generations": len(keys),
                    "phases": catalogue.phases(),
                    "entries": entries,
                }
            current = scan_wake_root(self.root)
            result["logical_bytes"] = current.logical_bytes
            result["files"] = len(current.sizes)
            return result

    def configure(
        self, max_ledgers: int, max_bytes: int, expected_revision: int
    ) -> dict[str, object]:
        """Apply validated root limits with an exact observed revision."""
        limits = RootLimits(max_ledgers, max_bytes)
        with self._operator(create=True) as (_, policy, inventory):
            with root_catalogue(self.root) as catalogue:
                catalogue.configure(limits, expected_revision, inventory)
                return {
                    "outcome": "configured",
                    "revision": catalogue.revision(),
                    "policy_digest": policy,
                    "max_ledgers": max_ledgers,
                    "max_bytes": max_bytes,
                }

    def inspect(self, key: str) -> dict[str, object]:
        """Return an exact raw snapshot fence and nullable transport counters for review."""
        with self._operator() as (_, policy, inventory):
            with root_catalogue(self.root) as catalogue:
                entry = catalogue.get(key)
                revision = catalogue.revision()
            if entry is not None and entry.phase == "retired":
                return {
                    "key": key,
                    "phase": "retired",
                    "fingerprint": entry.fingerprint,
                    "archive_directory": entry.archive_path,
                    "policy_digest": policy,
                    "revision": revision,
                }
            snapshot = (
                ArchiveSnapshot.from_json(entry.manifest)
                if entry is not None and entry.phase == "archiving"
                else snapshot_wake_generation(self.root, key)
            )
            return {
                "key": key,
                "phase": entry.phase if entry is not None else "legacy",
                "fingerprint": snapshot.fingerprint,
                "files": [item.encode() for item in snapshot.files],
                "transport": _native_summary(self.root / (key + ".sqlite3")),
                "policy_digest": policy,
                "revision": revision,
            }

    def archive(
        self, key: str, fingerprint: str, policy_digest: str, archive_parent: Path, now: int
    ) -> dict[str, object]:
        """Preserve an exact inactive generation before releasing its active-root capacity."""
        if type(now) is not int or not 0 <= now <= MAX_WAKE_INTEGER:
            raise WakeLedgerError("invalid wake archive timestamp")
        directory = archive_destination(self.root, archive_parent, key, fingerprint).resolve()
        with self._operator() as (config, policy, inventory):
            if policy_digest != policy:
                raise WakeLedgerError("wake archive policy changed")
            if key in _current_generations(self.auth_file, config):
                raise WakeLedgerError("current wake generation cannot be archived")
            with root_catalogue(self.root) as catalogue:
                entry = catalogue.get(key)
            if entry is not None and entry.phase == "retired":
                return self._repeat_archive(entry, directory, fingerprint)
            snapshot = (
                ArchiveSnapshot.from_json(entry.manifest)
                if entry is not None and entry.phase == "archiving"
                else snapshot_wake_generation(self.root, key)
            )
            if snapshot.fingerprint != fingerprint or snapshot.key != key:
                raise WakeLedgerError("wake archive snapshot changed")
            prepare_archive_directory(archive_parent.absolute())
            intent = RootEntry(
                key,
                "archiving",
                fingerprint,
                str(directory),
                snapshot.encode(),
                archive_location_digest(directory),
            )
            with root_catalogue(self.root) as catalogue:
                catalogue.begin_archive(intent)
            archive_wake_files(self.root, directory, snapshot)
            with root_catalogue(self.root) as catalogue:
                catalogue.finish_archive(key, fingerprint, intent.location_digest, now)
            return {
                "outcome": "archived",
                "key": key,
                "fingerprint": fingerprint,
                "archive_directory": str(directory),
            }

    def _repeat_archive(
        self, entry: RootEntry, directory: Path, fingerprint: str
    ) -> dict[str, object]:
        """Confirm only the same durable archive and refuse a conflicting repeat destination."""
        if (
            entry.fingerprint != fingerprint
            or entry.archive_path != str(directory)
            or entry.location_digest != archive_location_digest(directory)
        ):
            raise WakeLedgerError("wake archive receipt changed")
        if any(
            (self.root / (entry.key + suffix)).exists()
            for suffix in (".sqlite3", ".sqlite3-journal")
        ):
            raise WakeLedgerError("retired wake generation reappeared")
        snapshot = _read_manifest(directory, entry.key, fingerprint)
        verify_wake_archive(directory, snapshot)
        return {
            "outcome": "already_archived",
            "key": entry.key,
            "fingerprint": fingerprint,
            "archive_directory": str(directory),
        }

    def cancel(self, key: str, fingerprint: str, policy_digest: str) -> dict[str, object]:
        """Cancel only before any original byte/file is removed; keep external copies intact."""
        with self._operator() as (_, policy, inventory):
            if policy != policy_digest:
                raise WakeLedgerError("wake archive policy changed")
            with root_catalogue(self.root) as catalogue:
                entry = catalogue.get(key)
                if entry is None or entry.phase != "archiving":
                    raise WakeLedgerError("wake generation is not archiving")
                expected = ArchiveSnapshot.from_json(entry.manifest)
                current = snapshot_wake_generation(self.root, key)
                if current != expected or fingerprint != expected.fingerprint:
                    raise WakeLedgerError("wake archive source changed")
                catalogue.cancel_archive(key, fingerprint)
                return {"outcome": "cancelled", "key": key, "fingerprint": fingerprint}
