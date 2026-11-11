# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - mypy contract checks in a child interpreter
"""Run mypy for a typing contract test without changing the test process.

``mypy.api.run`` executes the checker inside the calling interpreter. Its build
raises that interpreter's garbage collector thresholds to 200000/30/30
(``mypy/build.py``) and never restores them, and it leaves millions of objects
behind. In the pytest process both outlive the check: one later young-generation
collection took 3.76 s on a server pinned to four CPUs, and hub tests on the
hosted Python 3.11 runner missed their welcome deadlines. A child interpreter
keeps that state out of the suite.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

MYPY_TIMEOUT_SECONDS = 600.0
"""Bound for one cold strict check, below the suite's 900 s hang report."""


def run_mypy(arguments: Sequence[str]) -> tuple[str, str, int]:
    """Run mypy with ``arguments`` in a child of this interpreter.

    Parameters
    ----------
    arguments : Sequence[str]
        The command line ``mypy.api.run`` would have received.

    Returns
    -------
    tuple[str, str, int]
        Standard output, standard error and the exit status, as returned by
        ``mypy.api.run``.

    Raises
    ------
    subprocess.TimeoutExpired
        If the check outlives ``MYPY_TIMEOUT_SECONDS``; the child is killed.
    """
    result = subprocess.run(
        [sys.executable, "-m", "mypy", *arguments],
        capture_output=True,
        text=True,
        timeout=MYPY_TIMEOUT_SECONDS,
        check=False,
    )
    return result.stdout, result.stderr, result.returncode
