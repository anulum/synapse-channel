# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox pending WHO/status/doctor surface tests

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel import cli_doctor
from synapse_channel.cli_doctor_mailbox import (
    DoctorRoster,
    diagnose_mailbox_pending,
    doctor_roster_from_snapshot,
)
from synapse_channel.cli_query_rendering import _render_who, _render_who_me
from synapse_channel.cli_status import _tally, render_status_line, status_to_json
from synapse_channel.core.protocol import MessageType


def test_who_renders_positive_none_and_unavailable_pending_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _render_who(
        ["PROJ/A", "OTHER/B"],
        project="PROJ",
        mailbox_pending={"PROJ/A": 2, "OTHER/B": 4},
        show_mailbox_pending=True,
    )
    positive = capsys.readouterr().out
    assert "2 undelivered messages pending for PROJ/A" in positive
    assert "OTHER/B" not in positive

    _render_who([], mailbox_pending={}, show_mailbox_pending=True)
    assert "Mailbox pending: none" in capsys.readouterr().out

    _render_who([], mailbox_pending=None, show_mailbox_pending=True)
    assert "Mailbox pending: unavailable" in capsys.readouterr().out


def test_who_me_always_names_the_requested_identity_pending_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _render_who_me(
        ["PROJ/A-rx"],
        name="PROJ/A",
        mailbox_pending={"PROJ/A": 1},
        show_mailbox_pending=True,
    )

    out = capsys.readouterr().out
    assert "1 undelivered message pending for PROJ/A" in out
    assert "waiter: online" in out


def test_status_tallies_renders_and_serialises_the_requested_mailbox() -> None:
    seen: dict[str, dict[str, Any]] = {
        MessageType.WHO_SNAPSHOT: {
            "online_agents": ["PROJ/A", "PROJ/A-rx", "PROJ/A-status"],
            "mailbox_pending": {"PROJ/A": 3},
        },
        MessageType.STATE_SNAPSHOT: {"snapshot": {"active_claims": []}},
    }

    status = _tally(seen, probe="PROJ/A-status", identity="PROJ/A")

    assert status.mailbox_pending_available is True
    assert status.mailbox_pending == 3
    assert "3 undelivered messages pending for PROJ/A" in render_status_line(status)
    payload = status_to_json(status)
    assert payload["mailbox_identity"] == "PROJ/A"
    assert payload["mailbox_pending"] == 3
    assert payload["mailbox_pending_available"] is True


def test_status_does_not_fabricate_zero_for_missing_projection() -> None:
    status = _tally({}, probe="PROJ/A-status", identity="PROJ/A")

    assert status.mailbox_pending_available is False
    assert "mailbox pending unavailable for PROJ/A" in render_status_line(status, plain=True)


def test_doctor_snapshot_parser_and_diagnosis_preserve_unavailability() -> None:
    parsed = doctor_roster_from_snapshot(
        {"online_agents": ["PROJ/A-rx"], "mailbox_pending": {"PROJ/A": 2}}
    )
    malformed = doctor_roster_from_snapshot({"online_agents": "bad", "mailbox_pending": "bad"})

    assert parsed == DoctorRoster(("PROJ/A-rx",), {"PROJ/A": 2})
    assert malformed == DoctorRoster((), None)
    assert diagnose_mailbox_pending(parsed.mailbox_pending, identity="PROJ/A").status == "warn"
    unavailable = diagnose_mailbox_pending(None, identity="PROJ/A")
    assert unavailable.status == "warn"
    assert "unavailable" in unavailable.detail
    assert diagnose_mailbox_pending({}, identity="PROJ/A").status == "pass"


def test_doctor_diagnosis_renders_copyable_commands_safely() -> None:
    identity = "--help$(touch injected)\x1b]0;fake\x07"

    diagnosis = diagnose_mailbox_pending({identity: 1}, identity=identity)

    assert diagnosis.detail == (
        r"1 undelivered message pending for --help$(touch injected)\x1b]0;fake\x07"
    )
    assert diagnosis.remedy is not None
    assert "--name='--help$(touch injected)\\x1b]0;fake\\x07-rx'" in diagnosis.remedy
    assert "--for='--help$(touch injected)\\x1b]0;fake\\x07'" in diagnosis.remedy
    assert "--as='--help$(touch injected)\\x1b]0;fake\\x07'" in diagnosis.remedy


async def test_doctor_diagnose_includes_hub_mailbox_pending_finding() -> None:
    async def roster_probe(**_kwargs: Any) -> DoctorRoster:
        return DoctorRoster(("demorepo-rx",), {"demorepo": 2})

    code, lines, diagnoses = await cli_doctor._diagnose(
        uri="ws://localhost:8876",
        project="demorepo",
        agent_id=None,
        token=None,
        roster_probe=roster_probe,
        feed_tail_reader=lambda _env: [],
        cursor_names_reader=lambda _env: [],
    )

    mailbox = next(diagnosis for diagnosis in diagnoses if diagnosis.check == "mailbox-pending")
    assert code == 0
    assert mailbox.status == "warn"
    assert any("2 undelivered messages pending for demorepo" in line for line in lines)
