# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — deterministic source-distribution archive normalizer
"""Repack one locally built Python sdist with deterministic archive metadata."""

from __future__ import annotations

import argparse
import gzip
import os
import tarfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath


class SdistRepackError(ValueError):
    """The source archive cannot be safely normalized."""


def _checked_members(source: tarfile.TarFile) -> tuple[tarfile.TarInfo, ...]:
    members = source.getmembers()
    if not members:
        raise SdistRepackError("sdist archive must not be empty")
    seen: set[str] = set()
    for member in members:
        name = member.name
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise SdistRepackError(f"sdist contains an unsafe member path: {name!r}")
        if name in seen:
            raise SdistRepackError(f"sdist contains a duplicate member: {name!r}")
        if not (member.isdir() or member.isreg()):
            raise SdistRepackError(f"sdist contains a non-file member: {name!r}")
        seen.add(name)
    return tuple(sorted(members, key=lambda member: member.name))


def _normalized_info(member: tarfile.TarInfo, *, source_date_epoch: int) -> tarfile.TarInfo:
    normalized = tarfile.TarInfo(member.name)
    normalized.type = member.type
    normalized.size = member.size if member.isreg() else 0
    normalized.mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
    normalized.uid = 0
    normalized.gid = 0
    normalized.uname = ""
    normalized.gname = ""
    normalized.mtime = source_date_epoch
    normalized.pax_headers = {}
    return normalized


def repack_sdist(
    input_path: Path,
    output_path: Path,
    *,
    source_date_epoch: int,
) -> None:
    """Create one deterministic gzip/tar sdist without extracting its members."""
    if source_date_epoch < 0:
        raise SdistRepackError("source date epoch must be non-negative")
    if input_path.is_symlink() or not input_path.is_file():
        raise SdistRepackError(f"input sdist must be a regular non-symlink file: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise SdistRepackError("input and output sdist paths must differ")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with tarfile.open(input_path, mode="r:gz") as source:
            members = _checked_members(source)
            descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            created = True
            os.fchmod(descriptor, 0o644)
            with os.fdopen(descriptor, "wb") as raw_output:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=9,
                    fileobj=raw_output,
                    mtime=source_date_epoch,
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed,
                        mode="w|",
                        format=tarfile.PAX_FORMAT,
                    ) as target:
                        for member in members:
                            payload = source.extractfile(member) if member.isreg() else None
                            if member.isreg() and payload is None:
                                raise SdistRepackError(
                                    f"cannot read regular sdist member: {member.name!r}"
                                )
                            try:
                                target.addfile(
                                    _normalized_info(
                                        member,
                                        source_date_epoch=source_date_epoch,
                                    ),
                                    payload,
                                )
                            finally:
                                if payload is not None:
                                    payload.close()
    except BaseException:
        if created:
            output_path.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Normalize one sdist and return a shell status."""
    arguments = _parser().parse_args(argv)
    try:
        repack_sdist(
            arguments.input,
            arguments.output,
            source_date_epoch=arguments.source_date_epoch,
        )
    except (OSError, SdistRepackError, tarfile.TarError) as exc:
        _parser().error(str(exc))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CI as a script
    raise SystemExit(main())
