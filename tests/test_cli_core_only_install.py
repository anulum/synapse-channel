# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the command line on a core-only install
"""``pip install synapse-channel`` installs websockets alone; the CLI must still start.

Releases 0.99.46 to 0.99.50 failed this: ``synapse --version`` and every
command that builds the full parser stopped with ``No module named
'cryptography'``, because the AEF countersigning commands imported the
signature suites, which imported ``cryptography`` at module load. The test
blocks every runtime extra named in ``pyproject.toml`` in a fresh interpreter,
so a new top-level import of an optional package fails here instead of on a
user's machine.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

import tomllib

import synapse_channel

ROOT = Path(__file__).resolve().parents[1]
_DEVELOPMENT_GROUPS = frozenset({"dev", "docs"})
"""Tool groups, not runtime extras: blocking them would also block the coverage hook."""


def _normalised(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_extra_modules() -> list[str]:
    """Return the importable top-level modules of every runtime extra's packages."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = {
        _normalised(re.split(r"[<>=!~;\[ ]", item, maxsplit=1)[0])
        for item in project["dependencies"]
    }
    extras = {
        _normalised(re.split(r"[<>=!~;\[ ]", item, maxsplit=1)[0])
        for group, items in project["optional-dependencies"].items()
        if group not in _DEVELOPMENT_GROUPS
        for item in items
    }
    optional = extras - core
    return sorted(
        module
        for module, distributions in packages_distributions().items()
        if any(_normalised(distribution) in optional for distribution in distributions)
    )


_SCRIPT = """
import json, sys
for name in json.loads(sys.argv[1]):
    sys.modules[name] = None
from synapse_channel.cli import build_parser, main
build_parser()
print("full parser built")
try:
    main(["--version"])
except SystemExit as stop:
    print("version exit", stop.code)
code = main(["aef", "pq-key", "new", "--out", sys.argv[2], "--trust-out", sys.argv[3]])
print("pq-key new exit", code)
"""


def test_the_cli_starts_and_refuses_cleanly_without_any_runtime_extra(tmp_path: Path) -> None:
    blocked = _runtime_extra_modules()
    assert "cryptography" in blocked
    key, trust = tmp_path / "pq.key", tmp_path / "pq.trust"

    completed = subprocess.run(  # noqa: S603 - this interpreter, a script the test wrote
        [sys.executable, "-c", _SCRIPT, json.dumps(blocked), str(key), str(trust)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    assert "full parser built" in completed.stdout
    assert synapse_channel.__version__ in completed.stdout
    assert "version exit 0" in completed.stdout
    assert "pq-key new exit 2" in completed.stdout
    assert "synapse-channel[encryption]" in completed.stderr
    assert not key.exists() and not trust.exists()
