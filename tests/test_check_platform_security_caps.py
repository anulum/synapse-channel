# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Linux sealed-launch preflight capability tests

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

TOOL = Path(__file__).resolve().parents[1] / "tools" / "check_platform_security_caps.py"
SPEC = importlib.util.spec_from_file_location("check_platform_security_caps", TOOL)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def _complete_module() -> SimpleNamespace:
    return SimpleNamespace(
        O_NOFOLLOW=1,
        geteuid=lambda: 1000,
        memfd_create=lambda name, flags: 3,
        MFD_ALLOW_SEALING=2,
    )


def _complete_fcntl() -> SimpleNamespace:
    return SimpleNamespace(
        **{name: index for index, name in enumerate(checker.REQUIRED_SEAL_CONSTANTS)}
    )


def test_linux_gate_accepts_complete_interpreter(tmp_path: Path) -> None:
    proc_fd = tmp_path / "proc" / "self" / "fd"
    proc_fd.mkdir(parents=True)

    assert (
        checker.missing_linux_sealed_launch_caps(
            platform="linux",
            os_module=_complete_module(),
            fcntl_module=_complete_fcntl(),
            proc_fd=proc_fd,
        )
        == ()
    )


def test_linux_gate_reports_every_missing_security_capability(tmp_path: Path) -> None:
    missing = checker.missing_linux_sealed_launch_caps(
        platform="linux",
        os_module=SimpleNamespace(),
        fcntl_module=SimpleNamespace(),
        proc_fd=tmp_path / "absent",
    )

    assert "os.memfd_create" in missing
    assert "os.MFD_ALLOW_SEALING" in missing
    assert "fcntl.F_ADD_SEALS" in missing
    assert str(tmp_path / "absent") in missing


def test_non_linux_gate_is_not_applicable(tmp_path: Path) -> None:
    assert (
        checker.missing_linux_sealed_launch_caps(
            platform="win32",
            os_module=SimpleNamespace(),
            fcntl_module=None,
            proc_fd=tmp_path / "absent",
        )
        == ()
    )
