# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse doctor` health checks

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.client.diagnostics import (
    Diagnosis,
    check_deaf_agents,
    check_disk_space,
    check_exposure,
    check_identity,
    check_multi_seat_posture,
    check_reachable,
    check_send_identity,
    check_sqlcipher_event_store,
    check_unread_addressees,
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
    assert "--for=demorepo" in diagnosis.remedy


def test_check_waiter_hints_exact_terminal_identity_when_absent() -> None:
    diagnosis = check_waiter(["other"], "user/terminal-14753-rx")
    assert diagnosis.status == "warn"
    assert "--name=user/terminal-14753-rx" in diagnosis.remedy
    assert "--for=user/terminal-14753 --directed-only" in diagnosis.remedy


def test_check_waiter_neutralises_hostile_identity_in_command() -> None:
    waiter = "--help$(touch injected)\x1b]0;fake\x07-rx"

    diagnosis = check_waiter([], waiter)

    assert "\x1b" not in diagnosis.detail
    assert "\x07" not in diagnosis.detail
    assert "--name='--help$(touch injected)\\x1b]0;fake\\x07-rx'" in diagnosis.remedy
    assert "--for='--help$(touch injected)\\x1b]0;fake\\x07'" in diagnosis.remedy


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


# --- directed-message blackhole visibility -------------------------------------------


def _chat(to: str) -> str:
    return json.dumps({"v": 1, "ty": "chat", "s": "A", "to": to, "p": "hello"})


class TestUnreadAddressees:
    def test_quiet_feed_passes(self) -> None:
        verdict = check_unread_addressees(feed_lines=[], cursor_names=[], roster=[])
        assert verdict.status == "pass"

    def test_directed_traffic_nobody_reads_warns_with_the_remedy(self) -> None:
        lines = [_chat("ACME/coordinator"), _chat("ACME/coordinator"), _chat("OTHER")]

        verdict = check_unread_addressees(feed_lines=lines, cursor_names=[], roster=[])

        assert verdict.status == "warn"
        assert "ACME/coordinator (2 msg)" in verdict.detail
        assert "OTHER (1 msg)" in verdict.detail
        assert "syn inbox --as=ACME/coordinator" in verdict.remedy

    def test_project_cursor_covers_its_sub_addresses(self) -> None:
        # relay --project matches 'project/...', so a drained project inbox
        # already reads the role name
        verdict = check_unread_addressees(
            feed_lines=[_chat("ACME/coordinator")], cursor_names=["ACME"], roster=[]
        )
        assert verdict.status == "pass"

    def test_aliased_cursor_covers_the_exact_name(self) -> None:
        verdict = check_unread_addressees(
            feed_lines=[_chat("ACME/coordinator")],
            cursor_names=["ACME__coordinator"],
            roster=[],
        )
        assert verdict.status == "pass"

    def test_live_name_or_waiter_counts_as_read(self) -> None:
        by_name = check_unread_addressees(
            feed_lines=[_chat("ACME/coordinator")],
            cursor_names=[],
            roster=["ACME/coordinator"],
        )
        by_waiter = check_unread_addressees(
            feed_lines=[_chat("ACME/coordinator")],
            cursor_names=[],
            roster=["ACME/coordinator-rx"],
        )
        assert by_name.status == "pass"
        assert by_waiter.status == "pass"

    def test_broadcasts_globs_and_noise_are_ignored(self) -> None:
        lines = [
            _chat("all"),
            _chat("ACME/*"),
            "",
            "{not json",
            json.dumps({"ty": "presence_update", "to": "ACME/x"}),
        ]

        verdict = check_unread_addressees(feed_lines=lines, cursor_names=[], roster=None)

        assert verdict.status == "pass"

    def test_listing_is_bounded_with_a_count_of_the_rest(self) -> None:
        lines = [_chat(f"P{index}/role") for index in range(5)]

        verdict = check_unread_addressees(feed_lines=lines, cursor_names=[], roster=[])

        assert verdict.status == "warn"
        assert "and 2 more" in verdict.detail


# --- check_multi_seat_posture -------------------------------------------------


def test_multi_seat_skips_single_seat_roster() -> None:
    diagnosis = check_multi_seat_posture(
        roster=["solo", "solo-rx"],
        token=None,
    )
    assert diagnosis.status == "pass"
    assert "single-seat" in diagnosis.detail
    assert "--multi-seat" in diagnosis.remedy


def test_multi_seat_warns_when_hub_unreachable() -> None:
    diagnosis = check_multi_seat_posture(roster=None, token="t")
    assert diagnosis.status == "warn"
    assert "unreachable" in diagnosis.detail


def test_multi_seat_detects_multiple_agents_without_materials() -> None:
    diagnosis = check_multi_seat_posture(
        roster=["a/one", "a/two", "a/one-rx"],
        token=None,
    )
    assert diagnosis.status == "warn"
    assert "multi-seat" in diagnosis.detail
    assert "no connect token" in diagnosis.detail
    assert "identity trust" in diagnosis.detail
    assert "role-grants" in diagnosis.detail
    assert "flood rate limiters not confirmed" in diagnosis.detail
    assert "--team-secure" in diagnosis.remedy
    assert "--secure" in diagnosis.remedy or "--rate" in diagnosis.remedy


def test_multi_seat_force_single_agent_with_materials(tmp_path: Path) -> None:
    trust = tmp_path / "trust.json"
    roles = tmp_path / "roles.json"
    trust.write_text("{}", encoding="utf-8")
    roles.write_text("{}", encoding="utf-8")
    diagnosis = check_multi_seat_posture(
        roster=["solo"],
        token="secret",
        identity_trust=trust,
        role_grants=roles,
        force=True,
        rate_limit_enabled=True,
    )
    assert diagnosis.status == "pass"
    assert "token + trust + role-grants + rate limits present" in diagnosis.detail
    assert "--team-secure" in diagnosis.remedy


def test_multi_seat_token_but_missing_files_is_warn() -> None:
    diagnosis = check_multi_seat_posture(
        roster=["a/x", "a/y"],
        token="t",
        identity_trust="/no/such/trust.json",
        role_grants="/no/such/roles.json",
        rate_limit_enabled=True,
    )
    assert diagnosis.status == "warn"
    assert "identity trust bundle missing" in diagnosis.detail
    assert "role-grants store missing" in diagnosis.detail
    assert "no connect token" not in diagnosis.detail


def test_multi_seat_two_waiters_counts_as_multi() -> None:
    diagnosis = check_multi_seat_posture(
        roster=["agent", "agent-rx", "other-rx"],
        token=None,
    )
    assert diagnosis.status == "warn"
    assert "multi-seat" in diagnosis.detail


def test_multi_seat_warns_when_rate_limiters_disabled(tmp_path: Path) -> None:
    trust = tmp_path / "trust.json"
    roles = tmp_path / "roles.json"
    trust.write_text("{}", encoding="utf-8")
    roles.write_text("{}", encoding="utf-8")
    diagnosis = check_multi_seat_posture(
        roster=["a/one", "a/two"],
        token="secret",
        identity_trust=trust,
        role_grants=roles,
        rate_limit_enabled=False,
    )
    assert diagnosis.status == "warn"
    assert "flood rate limiters disabled" in diagnosis.detail
    assert "--secure" in diagnosis.remedy or "--rate" in diagnosis.remedy


def test_multi_seat_warns_when_rate_limiters_unobserved(tmp_path: Path) -> None:
    trust = tmp_path / "trust.json"
    roles = tmp_path / "roles.json"
    trust.write_text("{}", encoding="utf-8")
    roles.write_text("{}", encoding="utf-8")
    diagnosis = check_multi_seat_posture(
        roster=["a/one", "a/two"],
        token="secret",
        identity_trust=trust,
        role_grants=roles,
    )
    assert diagnosis.status == "warn"
    assert "flood rate limiters not confirmed" in diagnosis.detail
    assert "--secure" in diagnosis.remedy or "--rate" in diagnosis.remedy


# --- check_deaf_agents -------------------------------------------------------


def test_deaf_agents_warns_when_hub_unreachable() -> None:
    diagnosis = check_deaf_agents(None)
    assert diagnosis.status == "warn"
    assert "unreachable" in diagnosis.detail


def test_deaf_agents_passes_when_every_agent_has_rx() -> None:
    diagnosis = check_deaf_agents(["proj/a", "proj/a-rx", "proj/b", "proj/b-rx"])
    assert diagnosis.status == "pass"
    assert "every live agent" in diagnosis.detail


def test_deaf_agents_warns_on_arm_without_waiter() -> None:
    diagnosis = check_deaf_agents(["FLUCTARA/codex-arm", "SCPN/CORE", "SCPN/CORE-rx"])
    assert diagnosis.status == "warn"
    assert "FLUCTARA/codex-arm" in diagnosis.detail
    assert "SCPN/CORE" not in diagnosis.detail
    assert "synapse wait --name=FLUCTARA/codex-arm-rx" in diagnosis.remedy


def test_deaf_agents_lists_bound_and_counts_rest() -> None:
    roster = [f"p/a{i}" for i in range(5)]
    diagnosis = check_deaf_agents(roster)
    assert diagnosis.status == "warn"
    assert "and 2 more" in diagnosis.detail


# --- check_sqlcipher_event_store ---------------------------------------------


def test_sqlcipher_check_passes_when_key_not_configured() -> None:
    diagnosis = check_sqlcipher_event_store("~/synapse/hub.db", None)
    assert diagnosis.status == "pass"
    assert diagnosis.check == "sqlcipher-store"


def test_sqlcipher_check_fails_when_key_file_missing(tmp_path: Path) -> None:
    diagnosis = check_sqlcipher_event_store(tmp_path / "hub.db", tmp_path / "absent.key")
    assert diagnosis.status == "fail"
    assert "missing" in diagnosis.detail


def test_sqlcipher_check_opens_real_encrypted_store(tmp_path: Path) -> None:
    pytest.importorskip("sqlcipher3")
    from synapse_channel.core.at_rest import generate_key_file
    from synapse_channel.core.persistence import EventStore

    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    store.append("chat", {"n": 1})
    store.close()
    diagnosis = check_sqlcipher_event_store(db, key)
    assert diagnosis.status == "pass"
    assert "max_seq=1" in diagnosis.detail
