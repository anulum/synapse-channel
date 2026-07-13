# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — version-independent fail-closed path resolution
"""Resolve paths with a missing tail while rejecting invalid symlinks."""

from __future__ import annotations

import stat
from pathlib import Path


class PathResolutionError(OSError):
    """A path cannot be resolved without accepting an invalid component."""


def resolve_weakly_fail_closed(path: Path) -> Path:
    """Resolve existing components and preserve a genuinely missing tail.

    Parameters
    ----------
    path : pathlib.Path
        Absolute or current-directory-relative path to canonicalise.

    Returns
    -------
    pathlib.Path
        An absolute path with every existing symlink resolved.

    Raises
    ------
    PathResolutionError
        If an existing component is unreadable, broken, cyclic, or otherwise
        cannot be resolved strictly.
    """
    candidate = path if path.is_absolute() else Path.cwd() / path
    resolved = Path(candidate.anchor)
    missing: list[str] = []

    for component in candidate.parts[1:]:
        if component == "..":
            if missing:
                missing.pop()
            else:
                resolved = resolved.parent
            continue
        if missing:
            missing.append(component)
            continue

        prefix = resolved / component
        try:
            metadata = prefix.lstat()
        except FileNotFoundError:
            missing.append(component)
            continue
        except OSError as exc:
            raise PathResolutionError("Path contains an unreadable component.") from exc

        if stat.S_ISLNK(metadata.st_mode):
            try:
                resolved = prefix.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise PathResolutionError("Path contains an invalid symbolic link.") from exc
        else:
            resolved = prefix

    return resolved.joinpath(*missing)
