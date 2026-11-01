# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a hung test must name itself, and apt setup must fail fast

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_faulthandler_timeout_prints_the_running_node_id(tmp_path: Path) -> None:
    """A test that outlives the timeout prints its node id and a fast test does not."""
    test_file = tmp_path / "test_hang_identity_probe.py"
    test_file.write_text(
        "import time\n\n"
        "def test_fast():\n"
        "    assert True\n\n"
        "def test_hangs():\n"
        "    time.sleep(2)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "--noconftest",
            "-p",
            "tools.pytest_hang_identity",
            "-p",
            "no:cacheprovider",
            "-o",
            "faulthandler_timeout=0.4",
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    output = completed.stdout + completed.stderr
    hang_lines = [line for line in output.splitlines() if "TIMEOUT nodeid=" in line]
    assert hang_lines, output[-2500:]
    assert any(line.endswith("::test_hangs") or "::test_hangs" in line for line in hang_lines)
    assert all(not line.endswith("::test_fast") for line in hang_lines)
    assert completed.returncode == 0


def test_softhsm_setup_names_itself_and_times_out() -> None:
    """The PKCS#11 package install cannot consume the whole shard job timeout."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "name: Install SoftHSM and the software TPM" in workflow
    assert "timeout 300 bash -c" in workflow
    inner = workflow.split("timeout 300 bash -c '", 1)[1].split("'", 1)[0]
    assert inner.lstrip().startswith("set -euo pipefail")
    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert "--python 3.11 3.12 3.13 --count 4" in workflow
    assert "3.10" not in workflow
    assert "Python 3.10 journeys" not in workflow
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert 'pytest_plugins = ["tools.pytest_hang_identity"]' in conftest
