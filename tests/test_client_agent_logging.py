# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — importing the agent client must not reconfigure root logging

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_importing_the_client_does_not_reconfigure_root_logging() -> None:
    """Importing the agent client must not call :func:`logging.basicConfig`.

    A library module that configures the root logger at import time is an anti-pattern: a
    single ``logging.basicConfig(level=logging.ERROR)`` clamps the whole process's root
    logger to ``ERROR`` and installs a handler, silently swallowing every ``WARNING`` an
    embedding application (or the federation multi-hub watch) emits. The client must leave
    logging configuration to the application. Import it in a fresh interpreter and assert the
    root logger keeps its default posture — no handlers installed, level still the initial
    ``WARNING`` — proving the import wrote nothing to the root logger.
    """
    src = str(Path(__file__).resolve().parents[1] / "src")
    child_env = {**os.environ, "PYTHONPATH": src}
    code = (
        "import logging\n"
        "import synapse_channel.client.agent\n"
        "root = logging.getLogger()\n"
        "assert root.handlers == [], f'import installed handlers: {root.handlers!r}'\n"
        "assert root.level == logging.WARNING, f'import changed root level to {root.level}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=child_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
