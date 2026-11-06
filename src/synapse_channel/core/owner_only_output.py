# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — create a new owner-only output file, never replacing one
"""Write command output to a new owner-only file.

Several commands write a signed or evidential document to ``--out``. They share
one rule: the file is created new (``O_EXCL``) with mode ``0600``, its parent is
created when missing, the text is flushed and synced, and a write that fails
part-way leaves no file behind. An existing file is never replaced.
"""

from __future__ import annotations

import os
from pathlib import Path


def write_new_owner_only_text(path: str | Path, text: str) -> None:
    """Create ``path`` as a new owner-only UTF-8 file holding ``text``.

    Raises
    ------
    FileExistsError
        When ``path`` already exists; it is left untouched.
    OSError
        When the file cannot be created or written; a partial file is removed.
    """
    file = Path(path).expanduser()
    file.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        file.unlink(missing_ok=True)
        raise
