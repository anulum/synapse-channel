# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict staged Git path extraction
"""Read and validate the repository paths currently staged in Git's index."""

from __future__ import annotations

import re

from synapse_channel.core.scoping import MAX_PATH_LENGTH, normalize_path
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner

_SINGLE_PATH_STATUSES = frozenset({"A", "M", "D", "T", "U", "X", "B"})
_COPY_OR_RENAME = re.compile(r"[CR](?:100|0[0-9]{2}|[0-9]{1,2})")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _normalise_staged_path(raw: str) -> str:
    """Return one unambiguous repository-relative path or fail closed."""
    if not raw or raw != raw.strip() or len(raw) > MAX_PATH_LENGTH:
        raise GitError("git returned an invalid staged path")
    if raw.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE.match(raw):
        raise GitError("git returned an absolute staged path")
    if any(ord(char) < 32 and char not in "\t\n" for char in raw):
        raise GitError("git returned a staged path with unsupported control characters")
    slash_path = raw.replace("\\", "/")
    if any(segment == ".." for segment in slash_path.split("/")):
        raise GitError("git returned a parent-escaping staged path")
    normalised = normalize_path(raw)
    if not normalised:
        raise GitError("git returned a staged path that resolves to the worktree root")
    return normalised


def parse_staged_name_status(raw: str) -> tuple[str, ...]:
    """Parse NUL-delimited ``git diff --name-status`` output.

    Copy and rename records contribute both their source and destination. The
    parser rejects unknown statuses and truncated records rather than guessing.
    """
    if not raw:
        return ()
    if not raw.endswith("\0"):
        raise GitError("git returned truncated staged-path output")
    fields = raw.split("\0")[:-1]
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        path_count = 1 if status in _SINGLE_PATH_STATUSES else 0
        if _COPY_OR_RENAME.fullmatch(status):
            path_count = 2
        if path_count == 0:
            raise GitError("git returned an unknown staged-path status")
        if index + path_count > len(fields):
            raise GitError("git returned a truncated staged-path record")
        paths.extend(_normalise_staged_path(path) for path in fields[index : index + path_count])
        index += path_count
    return tuple(dict.fromkeys(paths))


def read_staged_paths(*, runner: GitRunner = _default_git_runner) -> tuple[str, ...]:
    """Return all paths represented by the current staged index diff."""
    output = runner(["diff", "--cached", "--name-status", "-z", "--find-renames", "--find-copies"])
    return parse_staged_name_status(output)
