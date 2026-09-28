# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — policy contract for the whole-tree mypy hook
"""Keep local and remote mypy hook execution whole-tree and environment-aligned."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".pre-commit-config.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "pre-commit.yml"
DEV_LOCK = ROOT / ".github" / "requirements" / "requirements-dev.txt"
PKCS11_BOOTSTRAP = ROOT / ".github" / "requirements" / "requirements-pkcs11-bootstrap.txt"


def test_mypy_hook_cannot_narrow_to_staged_filenames() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    block = text.split("- id: mypy-whole-tree", 1)[1].split("\n      - id:", 1)[0]

    assert "entry: python tools/run_mypy_hook.py" in block
    assert "language: system" in block
    assert "stages: [pre-commit]" in block
    assert "pass_filenames: false" in block
    assert "pyproject\\.toml" in block
    for surface in ("src", "tests", "benchmarks", "tools", "examples"):
        assert surface in block


def test_precommit_ci_installs_the_whole_tree_type_environment() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--require-hashes -r .github/requirements/requirements-dev.txt" in text
    assert "python -m pip install -e . --no-deps" in text
    assert "python -m pre_commit run --all-files --show-diff-on-failure" in text
    assert "requirements-tools.txt" not in text


def test_precommit_pkcs11_bootstrap_matches_the_universal_lock() -> None:
    """Keep the direct CI artifact aligned with the authoritative dev lock."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    dev_lock = DEV_LOCK.read_text(encoding="utf-8")
    bootstrap = PKCS11_BOOTSTRAP.read_text(encoding="utf-8")
    dev_pin = re.search(r"(?m)^python-pkcs11==(?P<version>[^ ]+) \\$", dev_lock)
    wheel_pin = re.search(
        r"python_pkcs11-(?P<version>[0-9.]+)-cp312-cp312-manylinux[^ ]+\.whl",
        bootstrap,
    )
    wheel_hash = re.search(r"--hash=sha256:(?P<digest>[0-9a-f]{64})", bootstrap)
    artifact_match = re.search(r"python-pkcs11 @ (?P<url>https://\S+)", bootstrap)

    assert dev_pin is not None
    assert wheel_pin is not None
    assert wheel_hash is not None
    assert artifact_match is not None
    assert wheel_pin["version"] == dev_pin["version"]
    assert wheel_hash["digest"] in dev_lock
    artifact = urlsplit(artifact_match["url"])
    assert artifact.scheme == "https"
    assert artifact.hostname == "files.pythonhosted.org"
    assert artifact.path.endswith(wheel_pin.group(0))
    assert not any((artifact.username, artifact.password, artifact.port))
    assert not artifact.query
    assert not artifact.fragment
    assert "--require-hashes --no-deps" in workflow
    assert "requirements-pkcs11-bootstrap.txt" in workflow
