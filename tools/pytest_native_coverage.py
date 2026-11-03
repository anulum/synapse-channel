# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — per-case native subprocess coverage routing
"""Give every native writer test a fresh, independently verifiable coverage directory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

NATIVE_WRITERS = (
    "tests/fixtures/protected_write_cgroup_controls_linux.py::"
    "test_private_namespace_refuses_cross_filesystem_control",
    "tests/fixtures/protected_write_kernel_capture_races_linux.py::"
    "test_kernel_capture_refuses_actual_private_mount_table_changes",
    "tests/fixtures/protected_write_kernel_boundary_linux.py::"
    "test_capture_refuses_inconsistent_private_proc_mounts",
    "tests/fixtures/protected_write_mount_attributes_linux.py::"
    "test_actual_private_mount_observation_and_root_stage_refusals",
    "tests/fixtures/protected_write_mounts_linux.py::test_real_private_namespace_mount_policy",
)
PROBE_TEST_PREFIX = "tests/test_protected_runtime_probe"
_ROOT = pytest.StashKey[Path]()


def pytest_configure(config: pytest.Config) -> None:
    """Retain the absolute native output root before any test can change its environment."""
    raw = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR")
    if raw is None:
        raise pytest.UsageError("native coverage routing requires SYNAPSE_TEST_COVERAGE_DIR")
    root = Path(raw)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise pytest.UsageError("native coverage routing requires an ordinary absolute directory")
    root.with_name(root.name + "-probe").mkdir(mode=0o700)
    config.stash[_ROOT] = root


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Route a selected writer to a fresh node-bound directory before fixture setup."""
    root = item.config.stash[_ROOT]
    native = item.nodeid.split("[", 1)[0] in NATIVE_WRITERS
    probe = item.nodeid.startswith(PROBE_TEST_PREFIX)
    if native or probe:
        if probe:
            root = root.with_name(root.name + "-probe")
        root = root / hashlib.sha256(item.nodeid.encode("utf-8")).hexdigest()
        root.mkdir(mode=0o700)
    os.environ["SYNAPSE_TEST_COVERAGE_DIR"] = str(root)
