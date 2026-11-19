# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — report whether a remote Grok connector can be launched
"""Report whether the local host can launch a remote Grok connector.

The report is a result, including ``not installed``. It does not contact a hub
and does not read credential files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

NOT_INSTALLED = "not installed"
PRESENT = "present"


def classify(found_grok: bool, found_synapse: bool, http_profile: bool) -> dict[str, str]:
    """Return the journey verdict for the three local observations."""
    grok = PRESENT if found_grok else NOT_INSTALLED
    synapse = PRESENT if found_synapse else NOT_INSTALLED
    http_mcp = PRESENT if http_profile else NOT_INSTALLED
    ready = found_grok and found_synapse and http_profile
    return {
        "grok": grok,
        "http_mcp": http_mcp,
        "synapse": synapse,
        "verdict": PRESENT if ready else NOT_INSTALLED,
    }


def probe(
    *,
    which: Callable[[str], str | None] = shutil.which,
    http_ready: Callable[[str], bool] | None = None,
) -> dict[str, str]:
    """Observe ``grok`` and ``synapse`` on PATH and classify the connector."""
    grok_path = which("grok")
    synapse_path = which("synapse")
    http_profile = False
    if synapse_path is not None:
        checker = http_ready if http_ready is not None else _http_extra_installed
        http_profile = checker(synapse_path)
    return classify(grok_path is not None, synapse_path is not None, http_profile)


def _interpreter(synapse_path: str) -> str | None:
    """Return the Python named by a console script, when the text names one."""
    try:
        lines = Path(synapse_path).read_text(encoding="utf-8", errors="replace").splitlines()[:4]
    except OSError:
        return None
    for line in lines:
        if line.startswith("#!") and "bin/python" in line and "bin/sh" not in line:
            return line[2:].strip()
        for part in line.split("'"):
            if part.startswith("/") and "bin/python" in part:
                return part
    return None


def _http_extra_installed(synapse_path: str) -> bool:
    """Return whether that synapse script's interpreter can import ``mcp``."""
    interpreter = _interpreter(synapse_path)
    if interpreter is None:
        return False
    try:
        completed = subprocess.run(
            [interpreter, "-c", "import mcp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def main() -> int:
    """Print one JSON report and exit 0."""
    print(json.dumps(probe(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
