# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sealed manifest and confined reads of an at-rest backup bundle
"""The sealed manifest of an at-rest backup bundle, and what restore may read and write.

A bundle is one directory holding a copy of each encrypted profile file and
``manifest.json``. The manifest
(:data:`~synapse_channel.core.at_rest_format.BACKUP_MANIFEST_SCHEMA`) names
each copy by its bare file name inside the bundle and binds it by the SHA-256
of its bytes, and a seal made with the profile key authenticates the whole
entry list. Restore therefore detects a copy replaced by another valid
envelope under the same key (an older backup, or another surface's file) and a
manifest edited to match it. It reads only regular files inside the bundle
directory, and writes only to the paths of the profile it is given. A manifest
of an earlier schema bound no copy digest, so it is refused.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.core.at_rest_format import (
    BACKUP_MANIFEST_SCHEMA,
    LEGACY_BACKUP_MANIFEST_SCHEMAS,
)
from synapse_channel.core.at_rest_keys import write_owner_only
from synapse_channel.core.secure_path import open_nofollow_leaf

if TYPE_CHECKING:
    from synapse_channel.core.at_rest import AtRestCipher, AtRestSurface

MANIFEST_NAME = "manifest.json"
"""File name of the manifest inside a backup bundle."""

_COPY_NAME = re.compile(r"[0-9]{4,}-[^/\\\x00]+\.encrypted")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_ENTRY_KEYS = frozenset({"role", "source_path", "backup_file", "sha256"})


@dataclass(frozen=True)
class BundleEntry:
    """One profile file copied into a backup bundle.

    Attributes
    ----------
    role : str
        Storage role of the profile file, for example ``relay-log``.
    source_path : str
        Profile path the copy was taken from, exactly as the profile named it.
    backup_file : str
        Bare file name of the copy inside the bundle directory.
    sha256 : str
        Lowercase hexadecimal SHA-256 of the copy's bytes.
    """

    role: str
    source_path: str
    backup_file: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        """Return the manifest record of this entry."""
        return {
            "role": self.role,
            "source_path": self.source_path,
            "backup_file": self.backup_file,
            "sha256": self.sha256,
        }


def bundle_entry(role: str, source_path: Path, copy_path: Path) -> BundleEntry:
    """Bind one copy in the bundle by the SHA-256 of the bytes it holds.

    Parameters
    ----------
    role : str
        Storage role of the profile file.
    source_path : pathlib.Path
        Profile path the copy was taken from.
    copy_path : pathlib.Path
        The copy, written inside the bundle directory.

    Returns
    -------
    BundleEntry
        The manifest entry of that copy.
    """
    digest = hashlib.sha256(copy_path.read_bytes()).hexdigest()
    return BundleEntry(role, str(source_path), copy_path.name, digest)


def _canonical_bytes(entries: Sequence[BundleEntry]) -> bytes:
    """Return the canonical bytes the seal authenticates."""
    payload = {
        "files": [entry.to_dict() for entry in entries],
        "schema_version": BACKUP_MANIFEST_SCHEMA,
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def write_bundle_manifest(
    bundle_dir: Path, entries: Sequence[BundleEntry], cipher: AtRestCipher
) -> Path:
    """Write the sealed manifest of a bundle and return its path.

    Parameters
    ----------
    bundle_dir : pathlib.Path
        The bundle directory, which already holds every copy.
    entries : Sequence[BundleEntry]
        One entry per copy, in profile order.
    cipher : AtRestCipher
        The profile key, which seals the SHA-256 of the canonical entry list.

    Returns
    -------
    pathlib.Path
        The owner-only manifest path.
    """
    seal = cipher.encrypt(hashlib.sha256(_canonical_bytes(entries)).digest())
    payload = {
        "files": [entry.to_dict() for entry in entries],
        "schema_version": BACKUP_MANIFEST_SCHEMA,
        "seal": base64.b64encode(seal).decode("ascii"),
    }
    manifest = bundle_dir / MANIFEST_NAME
    write_owner_only(
        manifest,
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n",
    )
    return manifest


def _entry(raw: object, manifest_path: Path) -> BundleEntry:
    """Return one validated manifest entry, refusing any other shape."""
    malformed = f"at-rest backup manifest contains a malformed file entry: {manifest_path}"
    if not isinstance(raw, dict) or set(raw) != _ENTRY_KEYS:
        raise ValueError(malformed)
    values = [raw[key] for key in ("role", "source_path", "backup_file", "sha256")]
    if not all(isinstance(value, str) for value in values):
        raise ValueError(malformed)
    role, source_path, backup_file, sha256 = values
    if _COPY_NAME.fullmatch(backup_file) is None or _SHA256_HEX.fullmatch(sha256) is None:
        raise ValueError(malformed)
    return BundleEntry(role, source_path, backup_file, sha256)


def _verify_seal(
    seal: object, entries: Sequence[BundleEntry], cipher: AtRestCipher, manifest_path: Path
) -> None:
    """Refuse a manifest whose seal does not authenticate its entry list."""
    from cryptography.exceptions import InvalidTag

    if not isinstance(seal, str):
        raise ValueError(f"at-rest backup manifest is not sealed: {manifest_path}")
    try:
        sealed_digest = cipher.decrypt(base64.b64decode(seal, validate=True))
    except InvalidTag as exc:
        raise ValueError(
            f"at-rest backup manifest seal does not verify with this key: {manifest_path}"
        ) from exc
    expected = hashlib.sha256(_canonical_bytes(entries)).digest()
    if not hmac.compare_digest(sealed_digest, expected):
        raise ValueError(
            f"at-rest backup manifest was changed after it was sealed: {manifest_path}"
        )


def read_bundle_manifest(manifest_path: Path, cipher: AtRestCipher) -> tuple[BundleEntry, ...]:
    """Read a bundle manifest and verify its seal with the profile key.

    Parameters
    ----------
    manifest_path : pathlib.Path
        The manifest written by backup.
    cipher : AtRestCipher
        The profile key the bundle was sealed with.

    Returns
    -------
    tuple[BundleEntry, ...]
        The sealed entries, in manifest order.

    Raises
    ------
    ValueError
        For an earlier schema, which binds no copy digest; for any other schema,
        a missing file list, a malformed entry, a missing seal, a seal made with
        another key, or an entry list changed after it was sealed.
    """
    raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = raw.get("schema_version") if isinstance(raw, dict) else None
    if schema in LEGACY_BACKUP_MANIFEST_SCHEMAS:
        raise ValueError(
            f"at-rest backup manifest {schema} binds no file digest and is refused; "
            f"create a new backup: {manifest_path}"
        )
    if schema != BACKUP_MANIFEST_SCHEMA:
        raise ValueError(f"not a Synapse at-rest backup manifest: {manifest_path}")
    files = raw.get("files")
    if not isinstance(files, list):
        raise ValueError(f"at-rest backup manifest has no file list: {manifest_path}")
    entries = tuple(_entry(item, manifest_path) for item in files)
    _verify_seal(raw.get("seal"), entries, cipher, manifest_path)
    return entries


def restore_targets(
    entries: Sequence[BundleEntry], surfaces: Iterable[AtRestSurface], manifest_path: Path
) -> tuple[Path, ...]:
    """Return the profile path each entry restores to, refusing any other path.

    Parameters
    ----------
    entries : Sequence[BundleEntry]
        The sealed manifest entries.
    surfaces : Iterable[AtRestSurface]
        The profile given to restore; only its paths may be written.
    manifest_path : pathlib.Path
        The manifest, for error messages.

    Returns
    -------
    tuple[pathlib.Path, ...]
        One profile path per entry, in entry order.

    Raises
    ------
    ValueError
        When an entry names a role and path that the profile does not hold, or
        names the same one as an earlier entry.
    """
    profile = {(surface.role, str(surface.path)): surface.path for surface in surfaces}
    targets: list[Path] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry.role, entry.source_path)
        if key not in profile:
            raise ValueError(
                f"at-rest backup manifest names {entry.role} at {entry.source_path}, "
                f"which is not in the profile given to restore: {manifest_path}"
            )
        if key in seen:
            raise ValueError(
                f"at-rest backup manifest names {entry.role} at {entry.source_path} twice: "
                f"{manifest_path}"
            )
        seen.add(key)
        targets.append(profile[key])
    return tuple(targets)


def read_bundle_copy(bundle_dir: Path, entry: BundleEntry) -> bytes:
    """Read one copy from inside the bundle and check it against its sealed digest.

    Parameters
    ----------
    bundle_dir : pathlib.Path
        The directory holding the manifest.
    entry : BundleEntry
        The sealed entry naming the copy.

    Returns
    -------
    bytes
        The copy's bytes, whose SHA-256 equals the entry's.

    Raises
    ------
    ValueError
        When the copy is missing, is a link or not a regular file, or its digest
        differs from the sealed one (a replaced or rolled-back copy).
    """
    path = bundle_dir / entry.backup_file
    try:
        descriptor = open_nofollow_leaf(path)
    except FileNotFoundError as exc:
        raise ValueError(f"at-rest backup copy is missing from the bundle: {path}") from exc
    except OSError as exc:
        raise ValueError(
            f"at-rest backup copy must be a regular file inside the bundle: {path}"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(
                f"at-rest backup copy must be a regular file inside the bundle: {path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            blob = handle.read()
    finally:
        os.close(descriptor)
    if not hmac.compare_digest(hashlib.sha256(blob).hexdigest(), entry.sha256):
        raise ValueError(
            f"at-rest backup copy does not match its recorded SHA-256 (replaced or rolled "
            f"back): {path}"
        )
    return blob
