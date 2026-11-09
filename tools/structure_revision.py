# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — source-bound measurement of a Git revision
"""Measure committed Python blobs without a checkout or executing their contents."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tools.structure_measures import MEASURES, Figures, measure_source


def _git(root: Path, *arguments: str, data: bytes | None = None) -> bytes:
    """Read one bounded Git operation, failing on unavailable history."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            input=data,
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("committed structure measurement is unavailable") from error
    return result.stdout


def parse_blob_batch(raw: bytes, objects: list[str]) -> list[bytes]:
    """Decode a Git batch, binding every payload to its requested object.

    Parameters
    ----------
    raw:
        Complete stdout bytes from ``git cat-file --batch``.
    objects:
        Exact requested blob object IDs, in request order.

    Returns
    -------
    list[bytes]
        Complete payloads in the original order, without headers or separators.

    Raises
    ------
    ValueError:
        On incomplete, reordered, mistyped or additional data.
    """
    offset = 0
    contents: list[bytes] = []
    for expected in objects:
        boundary = raw.find(b"\n", offset)
        if boundary < 0:
            raise ValueError("incomplete committed blob header")
        header = raw[offset:boundary].split()
        if len(header) != 3 or header[:2] != [expected.encode(), b"blob"]:
            raise ValueError("committed blob identity or type differs")
        size = int(header[2])
        start = boundary + 1
        end = start + size
        if size < 0 or end >= len(raw) or raw[end : end + 1] != b"\n":
            raise ValueError("incomplete committed blob payload")
        contents.append(raw[start:end])
        offset = end + 1
    if offset != len(raw):
        raise ValueError("unexpected data after committed blobs")
    return contents


def measure_revision(
    repository: Path,
    revision: str,
    roots: tuple[str, ...],
    *,
    allowed_private: frozenset[str] = frozenset(),
) -> Figures:
    """Measure the Python sources actually stored in a Git commit.

    Parameters
    ----------
    repository:
        Existing repository; neither its index nor working tree is modified.
    revision:
        Exact commit object selected by the caller's verified baseline.
    roots:
        The baseline's measured roots, not a candidate-supplied reduced scope.
    allowed_private:
        Baseline spellings exempted from private-member access.

    Returns
    -------
    dict[str, dict[str, int]]
        The same measurements as a working-tree scan, bound to committed blobs.

    Raises
    ------
    ValueError:
        When Git or its object stream cannot be read completely.
    SyntaxError, UnicodeDecodeError:
        When committed Python cannot be parsed.
    """
    tree = _git(repository, "ls-tree", "-r", "-z", revision, "--", *roots)
    paths: list[str] = []
    objects: list[str] = []
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        _, kind, object_id = metadata.split()
        path = name.decode("utf-8")
        if kind == b"blob" and path.endswith(".py"):
            paths.append(path)
            objects.append(object_id.decode("ascii"))
    figures: Figures = {measure: {} for measure in MEASURES}
    raw = (
        _git(repository, "cat-file", "--batch", data=("\n".join(objects) + "\n").encode())
        if objects
        else b""
    )
    for path, content in zip(paths, parse_blob_batch(raw, objects), strict=True):
        package = next(Path(r).name for r in reversed(roots) if Path(path).is_relative_to(r))
        measured = measure_source(
            content.decode("utf-8"),
            module_key=path,
            package=package,
            allowed_private=allowed_private,
        )
        for measure in MEASURES:
            figures[measure].update(measured[measure])
    return figures
