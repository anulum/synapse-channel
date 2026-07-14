# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral apply_patch target extraction
"""Extract mutation targets from the shared ``apply_patch`` control grammar."""

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.errors import SynapseError

_FILE_PREFIXES = ("*** Add File: ", "*** Update File: ", "*** Delete File: ")
_MOVE_PREFIXES = ("*** Move to: ", "*** Move from: ")


class ApplyPatchPathError(SynapseError, ValueError):
    """An ``apply_patch`` document is malformed or has no mutation target."""

    code = "apply_patch_path"


def parse_apply_patch_paths(command: str) -> tuple[Path, ...]:
    """Return every unique source and destination path in ``command``.

    The parser accepts only the control lines understood by the repository's
    ``apply_patch`` tool.  Unknown control lines fail closed so a future grammar
    extension cannot silently bypass a provider claim guard.
    """
    lines = command.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ApplyPatchPathError("apply_patch input needs exact begin and end markers.")

    paths: list[Path] = []
    for line in lines[1:-1]:
        prefix = next(
            (item for item in (*_FILE_PREFIXES, *_MOVE_PREFIXES) if line.startswith(item)),
            None,
        )
        if prefix is not None:
            raw_path = line.removeprefix(prefix)
            if not raw_path.strip() or raw_path != raw_path.strip() or "\0" in raw_path:
                raise ApplyPatchPathError("apply_patch contains an invalid file path.")
            paths.append(Path(raw_path))
        elif line.startswith("*** ") and line != "*** End of File":
            raise ApplyPatchPathError("apply_patch contains an unsupported control line.")
    unique = tuple(dict.fromkeys(paths))
    if not unique:
        raise ApplyPatchPathError("apply_patch contains no file mutation.")
    return unique
