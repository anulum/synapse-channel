# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse doctor` health checks

from __future__ import annotations

from typing import Any

from synapse_channel.client.diagnostics import (
    Diagnosis,
    check_disk_space,
    check_exposure,
    check_identity,
    check_reachable,
    check_send_identity,
    check_waiter,
    summarise,
)
from synapse_channel.ergonomics import Identity


def _identity(**overrides: Any) -> Identity:
    base: dict[str, Any] = {
        "project": "demorepo",
        "identity": "demorepo",
        "source": "flag",
        "plausible": True,
    }
    base.update(overrides)
    return Identity(**base)


# --- check_identity ----------------------------------------------------------


def test_check_identity_passes_on_explicit_plausible() -> None:
    diagnosis = check_identity(_identity(source="flag", plausible=True))
    assert diagnosis.status == "pass"
    assert "demorepo" in diagnosis.detail


def test_check_identity_warns_on_cwd_source() -> None:
    diagnosis = check_identity(_identity(source="cwd", plausible=True))
    assert diagnosis.status == "warn"
    assert diagnosis.remedy


def test_check_identity_fails_on_implausible() -> None:
    diagnosis = check_identity(_identity(project="media", plausible=False))
    assert diagnosis.status == "fail"
    assert "accidental" in diagnosis.detail


# --- check_send_identity -----------------------------------------------------


def test_check_send_identity_passes_on_bare_project() -> None:
    assert check_send_identity("demorepo", project="demorepo").status == "pass"


def test_check_send_identity_passes_on_slash_seat() -> None:
    assert check_send_identity("demorepo/claude-1", project="demorepo").status == "pass"


def test_check_send_identity_warns_on_hyphen_child() -> None:
    diagnosis = check_send_identity("demorepo-keeper", project="demorepo")
    assert diagnosis.status == "warn"
    assert "outside the project namespace" in diagnosis.detail
    assert "demorepo/<seat>" in diagnosis.remedy


def test_check_send_identity_passes_on_unrelated_name() -> None:
    diagnosis = check_send_identity("other", project="demorepo")
    assert diagnosis.status == "pass"
    assert "unrelated" in diagnosis.detail


# --- check_exposure ----------------------------------------------------------


def test_check_exposure_passes_on_loopback() -> None:
    assert check_exposure("ws://localhost:8876", None).status == "pass"


def test_check_exposure_passes_off_loopback_with_token() -> None:
    assert check_exposure("ws://10.0.0.5:8876", "tok").status == "pass"


def test_check_exposure_warns_off_loopback_without_token() -> None:
    diagnosis = check_exposure("ws://10.0.0.5:8876", None)
    assert diagnosis.status == "warn"
    assert "no token" in diagnosis.detail

    disk_ok = check_disk_space(
        "/",
        total_bytes=100 * 1024 * 1024,
        free_bytes=20 * 1024 * 1024,
        warn_used_percent=95.0,
        warn_free_mib=10,
    )
    assert disk_ok.status == "pass"
    assert "20.0 MiB free" in disk_ok.detail

    disk_warn = check_disk_space(
        "/",
        total_bytes=100 * 1024 * 1024,
        free_bytes=4 * 1024 * 1024,
        warn_used_percent=95.0,
        warn_free_mib=10,
    )
    assert disk_warn.status == "warn"
    assert "96.0% used" in disk_warn.detail
    assert "move build trees" in disk_warn.remedy

    disk_unknown = check_disk_space(
        "/",
        total_bytes=0,
        free_bytes=0,
        warn_used_percent=95.0,
        warn_free_mib=10,
    )
    assert disk_unknown.status == "warn"
    assert "could not compute filesystem pressure" in disk_unknown.detail


# --- check_reachable ---------------------------------------------------------


def test_check_reachable_passes_when_answered() -> None:
    assert check_reachable(True, "ws://h").status == "pass"


def test_check_reachable_fails_when_silent() -> None:
    diagnosis = check_reachable(False, "ws://h")
    assert diagnosis.status == "fail"
    assert "synapse hub" in diagnosis.remedy


# --- check_waiter ------------------------------------------------------------


def test_check_waiter_warns_when_unreachable() -> None:
    diagnosis = check_waiter(None, "demorepo-rx")
    assert diagnosis.status == "warn"
    assert "unreachable" in diagnosis.detail


def test_check_waiter_passes_when_present() -> None:
    assert check_waiter(["demorepo-rx", "other"], "demorepo-rx").status == "pass"


def test_check_waiter_warns_when_absent() -> None:
    diagnosis = check_waiter(["other"], "demorepo-rx")
    assert diagnosis.status == "warn"
    assert "will not wake you" in diagnosis.detail


# --- summarise ---------------------------------------------------------------


def test_summarise_all_clear() -> None:
    code, lines = summarise([Diagnosis("a", "pass", "fine")])
    assert code == 0
    assert lines[-1] == "synapse doctor: all clear"
    assert lines[0] == "[ok] a: fine"


def test_summarise_warnings_only_renders_remedy_and_does_not_fail() -> None:
    code, lines = summarise([Diagnosis("a", "warn", "hmm", "fix it")])
    assert code == 0
    assert "1 warning(s), no failures" in lines[-1]
    assert any("→ fix it" in line for line in lines)


def test_summarise_failure_sets_exit_one() -> None:
    code, lines = summarise([Diagnosis("a", "fail", "broken", "do x"), Diagnosis("b", "warn", "w")])
    assert code == 1
    assert "FAILED — 1 issue(s), 1 warning(s)" in lines[-1]
    assert "[FAIL] a: broken" in lines[0]
