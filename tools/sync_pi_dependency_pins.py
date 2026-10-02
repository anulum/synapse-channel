#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — verify installed Pi dependency bytes after shrinkwrap replay
"""Restore the reviewed brace-expansion pin without resolving other Pi dependencies."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

PACKAGE = "brace-expansion"
VERSION = "5.0.12"
TARBALL = "https://registry.npmjs.org/brace-expansion/-/brace-expansion-5.0.12.tgz"
RELATIVE = "node_modules/@earendil-works/pi-coding-agent/node_modules/brace-expansion"
LIMIT = 2 * 1024 * 1024


def synchronize(root: Path, archive: Path | None = None) -> None:
    """Verify the locked archive and replace only its installed package directory."""
    root = root.resolve()
    lock = json.loads((root / "integrations/pi/package-lock.json").read_text())
    entry = lock["packages"][RELATIVE]
    if entry["version"] != VERSION or entry["resolved"] != TARBALL:
        raise ValueError("Pi brace-expansion pin requires a reviewed version and registry URL")
    integrity = entry["integrity"]
    if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
        raise ValueError("Pi dependency pin is missing SHA-512 integrity")
    expected = base64.b64decode(integrity[7:], validate=True)
    if len(expected) != 64:
        raise ValueError("Pi dependency pin has malformed SHA-512 integrity")
    if archive is None:
        # The URL is a fixed HTTPS registry URL; bytes must match the reviewed lock.
        with urllib.request.urlopen(TARBALL, timeout=30) as response:  # nosec B310
            payload = response.read(LIMIT + 1)
    else:
        with archive.open("rb") as source:
            payload = source.read(LIMIT + 1)
    if len(payload) > LIMIT or hashlib.sha512(payload).digest() != expected:
        raise ValueError("Pi dependency archive does not match the locked SHA-512 digest")
    files: dict[Path, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
        for member in bundle.getmembers():
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
                continue
            relative = Path(*path.parts[1:])
            if relative == Path(".") or relative in files or member.size > LIMIT:
                raise ValueError("Pi dependency archive has a duplicate or oversized file")
            member_file = bundle.extractfile(member)
            if member_file is None:
                raise ValueError("Pi dependency archive file cannot be read")
            with member_file:
                files[relative] = member_file.read()
            if sum(map(len, files.values())) > LIMIT:
                raise ValueError("Pi dependency archive exceeds the unpacked byte limit")
    manifest = json.loads(files[Path("package.json")])
    if manifest.get("name") != PACKAGE or manifest.get("version") != VERSION:
        raise ValueError("Pi dependency archive has a different package identity")
    target = root / "integrations/pi" / RELATIVE
    if not target.is_dir() or target.is_symlink() or target.parent.resolve() != target.parent:
        raise ValueError("Pi dependency target must be an installed ordinary package directory")
    installed = {p.relative_to(target) for p in target.rglob("*") if p.is_file()}
    if installed == set(files) and all(
        not (target / name).is_symlink() and (target / name).read_bytes() == data
        for name, data in files.items()
    ):
        return
    with tempfile.TemporaryDirectory(prefix=".synapse-pin-", dir=target.parent) as directory:
        staging = Path(directory) / "replacement"
        original = Path(directory) / "original"
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


def main() -> int:
    """Check installed bytes after npm ci, refusing unreviewed archive inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--archive", type=Path, help="Use a digest-verified local registry archive")
    args = parser.parse_args()
    try:
        synchronize(args.root, args.archive)
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"Pi dependency pin refused: {error}", file=sys.stderr)
        return 1
    print(f"Pi installed dependency bytes verified: {PACKAGE} {VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
