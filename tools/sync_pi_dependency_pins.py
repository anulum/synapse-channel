#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — verify installed Pi dependency bytes after shrinkwrap replay
"""Restore reviewed Pi dependency bytes after the vendor shrinkwrap is replayed."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

LIMIT = 2 * 1024 * 1024
MAX_MEMBERS = 1024


@dataclass(frozen=True)
class DependencyPin:
    """Identify a reviewed registry artifact and its bounded installed contents.

    Parameters
    ----------
    name : str
        Reviewed npm package name, selected from the source-controlled pins.
    version : str
        Exact reviewed package version.
    unpacked_limit : int
        Maximum aggregate regular-file bytes, separate from tar metadata.
    """

    name: str
    version: str
    unpacked_limit: int

    @property
    def tarball(self) -> str:
        """Return the fixed registry HTTPS URL for this exact release."""
        return f"https://registry.npmjs.org/{self.name}/-/{self.name}-{self.version}.tgz"

    @property
    def relative(self) -> str:
        """Return the admitted vendor's nested dependency location."""
        return f"node_modules/@earendil-works/pi-coding-agent/node_modules/{self.name}"


PINS = {
    "brace-expansion": DependencyPin("brace-expansion", "5.0.12", LIMIT),
    "minimatch": DependencyPin("minimatch", "11.0.3", LIMIT),
    "undici": DependencyPin("undici", "8.12.3", 4 * 1024 * 1024),
}


def _member_path(member: tarfile.TarInfo) -> Path | None:
    """Refuse escaping or special archive entries before interpreting their bytes."""
    path = PurePosixPath(member.name)
    if path.is_absolute() or not path.parts or path.parts[0] != "package":
        raise ValueError("Pi dependency archive has an unexpected package path")
    if (
        ".." in path.parts
        or "\\" in member.name
        or ":" in member.name
        or not (member.isfile() or member.isdir())
    ):
        raise ValueError("Pi dependency archive contains a link or unsafe path")
    if member.isdir():
        return None
    return Path(*path.parts[1:])


def _archive_files(payload: bytes, pin: DependencyPin) -> dict[Path, bytes]:
    """Read an authenticated package within member-count and total unpacked bounds."""
    expanded_limit = pin.unpacked_limit + MAX_MEMBERS * 1024
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
        expanded = compressed.read(expanded_limit + 1)
    if len(expanded) > expanded_limit:
        raise ValueError("Pi dependency archive exceeds the expanded archive limit")
    files: dict[Path, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as bundle:
        for index, member in enumerate(bundle, 1):
            if index > MAX_MEMBERS:
                raise ValueError("Pi dependency archive exceeds the member limit")
            relative = _member_path(member)
            if relative is None:
                continue
            if relative == Path(".") or relative in files or member.size > pin.unpacked_limit:
                raise ValueError("Pi dependency archive has a duplicate or oversized file")
            member_file = bundle.extractfile(member)
            if member_file is None:
                raise ValueError("Pi dependency archive file cannot be read")
            with member_file:
                files[relative] = member_file.read(pin.unpacked_limit + 1)
            total += len(files[relative])
            if total > pin.unpacked_limit:
                raise ValueError("Pi dependency archive exceeds the unpacked byte limit")
    manifest = json.loads(files[Path("package.json")])
    if (
        not isinstance(manifest, dict)
        or manifest.get("name") != pin.name
        or manifest.get("version") != pin.version
    ):
        raise ValueError("Pi dependency archive has a different package identity")
    return files


def _replace_directory(target: Path, files: dict[Path, bytes]) -> None:
    """Replace one package, retaining its backup if a concurrent change blocks rollback."""
    directory = Path(tempfile.mkdtemp(prefix=".synapse-pin-", dir=target.parent))
    staging = directory / "replacement"
    original = directory / "original"
    replaced = False
    try:
        staging.mkdir()
        for name, data in files.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        os.rename(target, original)
        try:
            os.rename(staging, target)
        except BaseException:
            os.rename(original, target)
            raise
        replaced = True
    finally:
        if replaced or not original.exists():
            shutil.rmtree(directory)


def _installed_files_match(target: Path, files: dict[Path, bytes]) -> bool:
    """Require exact ordinary installed files without accepting hidden links or devices."""
    entries = list(target.rglob("*"))
    ordinary = all(not path.is_symlink() and (path.is_file() or path.is_dir()) for path in entries)
    installed = {path.relative_to(target) for path in entries if path.is_file()}
    return (
        ordinary
        and installed == set(files)
        and all((target / name).read_bytes() == data for name, data in files.items())
    )


def synchronize(
    root: Path, archive: Path | None = None, *, dependency: str = "brace-expansion"
) -> None:
    """Verify one reviewed dependency and replace only its installed directory.

    The Python entry defaults to the original brace-expansion contract. The
    operator CLI verifies every reviewed dependency unless one is selected.
    Verify while Pi and other installers are stopped. Publication uses two
    renames, so the target can be temporarily absent. A blocked rollback
    retains the original beneath its ``.synapse-pin-`` recovery directory.

    Parameters
    ----------
    root : Path
        Repository root containing the Pi lock and installed dependency tree.
    archive : Path or None, optional
        Local registry archive verified against the lock, or a registry fetch.
    dependency : str, optional
        Reviewed dependency name; defaults to the original brace-expansion pin.

    Raises
    ------
    ValueError
        The reviewed identity, digest, archive bounds or installed path is invalid.
    KeyError
        The dependency or its required lock entry is absent.
    OSError
        Reading or replacement fails. A blocked rollback preserves its backup.
    """
    pin = PINS[dependency]
    root = root.resolve()
    lock = json.loads((root / "integrations/pi/package-lock.json").read_text())
    entry = lock["packages"][pin.relative]
    if entry["version"] != pin.version or entry["resolved"] != pin.tarball:
        raise ValueError(f"Pi {pin.name} pin requires a reviewed version and registry URL")
    integrity = entry["integrity"]
    if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
        raise ValueError("Pi dependency pin is missing SHA-512 integrity")
    expected = base64.b64decode(integrity[7:], validate=True)
    if len(expected) != 64:
        raise ValueError("Pi dependency pin has malformed SHA-512 integrity")
    if archive is None:
        # The URL is a fixed HTTPS registry URL; bytes must match the reviewed lock.
        with urllib.request.urlopen(pin.tarball, timeout=30) as response:  # nosec B310
            payload = response.read(LIMIT + 1)
    else:
        with archive.open("rb") as source:
            payload = source.read(LIMIT + 1)
    if len(payload) > LIMIT or hashlib.sha512(payload).digest() != expected:
        raise ValueError("Pi dependency archive does not match the locked SHA-512 digest")
    files = _archive_files(payload, pin)
    target = root / "integrations/pi" / pin.relative
    if not target.is_dir() or target.is_symlink() or target.parent.resolve() != target.parent:
        raise ValueError("Pi dependency target must be an installed ordinary package directory")
    if _installed_files_match(target, files):
        return
    _replace_directory(target, files)


def main() -> int:
    """Check installed bytes after npm ci, refusing unreviewed archive inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--archive", type=Path, help="Use a digest-verified local registry archive")
    parser.add_argument("--dependency", choices=["all", *PINS], default="all")
    args = parser.parse_args()
    if args.archive is not None and args.dependency == "all":
        parser.error("--archive requires one explicit --dependency")
    selected = list(PINS) if args.dependency == "all" else [args.dependency]
    try:
        for dependency in selected:
            synchronize(args.root, args.archive, dependency=dependency)
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"Pi dependency pin refused: {error}", file=sys.stderr)
        return 1
    verified = ", ".join(f"{PINS[name].name} {PINS[name].version}" for name in selected)
    print(f"Pi installed dependency bytes verified: {verified}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
