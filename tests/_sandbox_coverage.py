# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — writable coverage data directory for read-only bubblewrap sandboxes
"""Give a sandboxed child exactly the coverage data directory, and only while measuring."""

from __future__ import annotations

from pathlib import Path

import coverage


def coverage_data_bind() -> list[str]:
    """Return bubblewrap arguments that make the active coverage data directory writable.

    A measured child process writes its ``.coverage.*`` file next to the parent's data
    file. Under ``--ro-bind / /`` that write fails and the child's lines are lost, so the
    directory holding the active data file is bound writable. Without an active
    coverage session nothing is bound and the sandbox stays entirely read-only.

    Returns
    -------
    list[str]
        ``["--bind", directory, directory]`` while coverage measures this process,
        otherwise an empty list.
    """
    current = coverage.Coverage.current()
    data_file = current.get_data().data_filename() if current is not None else ""
    directory = str(Path(data_file).resolve().parent) if data_file else ""
    return ["--bind", directory, directory] if directory else []
