# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor reports a project's orphaned mailboxes
"""``synapse doctor --mailbox-orphans`` reads the hub store and counts the live roster."""

from __future__ import annotations

from pathlib import Path

from synapse_channel.cli import build_parser
from synapse_channel.cli_doctor import _diagnose
from synapse_channel.cli_doctor_orphans import CHECK, diagnose_orphaned_mailboxes
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

DAY = 86_400.0
NOW = 100 * DAY


def _store(tmp_path: Path) -> Path:
    db = tmp_path / "hub.db"
    with EventStore(db) as store:
        store.append(EventKind.CHAT, {"sender": "Q/x", "target": "P/gone", "payload": "a"}, ts=1.0)
        store.append(EventKind.CHAT, {"sender": "Q/x", "target": "P/gone", "payload": "b"}, ts=2.0)
        store.append(EventKind.CHAT, {"sender": "P/gone", "target": "Q/x", "payload": "c"}, ts=3.0)
    return db


def test_a_project_with_orphaned_mail_warns_and_names_the_commands(tmp_path: Path) -> None:
    """Pending mail for a quiet name of the project is a warning with a remedy."""
    db = _store(tmp_path)
    found = diagnose_orphaned_mailboxes(db, key_file=None, project="P", roster=(), now=NOW)
    assert found is not None
    assert (found.check, found.status) == (CHECK, "warn")
    assert found.detail == (
        "2 pending messages in 1 orphaned mailboxes of project P (quiet for 7 days or more)"
    )
    assert f"synapse mailbox orphans {db} --project=P --live" in str(found.remedy)
    assert "synapse mailbox handover NAME --to SUCCESSOR" in str(found.remedy)
    spaced = tmp_path / "a b"
    spaced.mkdir()
    quoted = diagnose_orphaned_mailboxes(
        _store(spaced), key_file=None, project="P", roster=(), now=NOW
    )
    assert quoted is not None and f"orphans '{spaced / 'hub.db'}' --project=P" in str(quoted.remedy)
    unread = diagnose_orphaned_mailboxes(db, key_file=None, project="P", roster=None, now=NOW)
    assert unread is not None and unread.detail.endswith("; the live roster was not read)")


def test_connected_names_other_projects_and_no_store_do_not_warn(tmp_path: Path) -> None:
    """A connected name is active; another project is not counted; no store skips."""
    db = _store(tmp_path)
    live = diagnose_orphaned_mailboxes(db, key_file=None, project="P", roster=["P/gone"], now=NOW)
    assert live is not None and live.status == "pass"
    assert live.detail == "no orphaned mailboxes in project P (quiet for 7 days or more)"
    other = diagnose_orphaned_mailboxes(db, key_file=None, project="R", roster=(), now=NOW)
    assert other is not None and other.status == "pass"
    assert diagnose_orphaned_mailboxes(None, key_file=None, project="P", roster=(), now=NOW) is None


def test_an_unreadable_store_is_a_warning_not_a_crash(tmp_path: Path) -> None:
    """A store that cannot be read is reported with its error class only."""
    found = diagnose_orphaned_mailboxes(
        tmp_path / "missing.db", key_file=None, project="P", roster=(), now=NOW
    )
    assert found is not None and found.status == "warn"
    assert found.detail.startswith("cannot read the hub store for orphaned mailboxes: ")


async def _doctor(db: Path, *, asked: bool) -> set[str] | str:
    """Run the doctor against an unreachable hub; return the check's detail or the checks."""
    _code, _lines, diagnoses = await _diagnose(
        uri="ws://127.0.0.1:1",
        project="P",
        agent_id="doctor",
        token=None,
        ready_timeout=0.1,
        env={},
        event_store_path=db,
        mailbox_orphans=asked,
    )
    by_check = {diagnosis.check: diagnosis for diagnosis in diagnoses}
    if asked:
        assert by_check[CHECK].status == "warn"
        return by_check[CHECK].detail
    return set(by_check)


async def test_doctor_runs_the_check_only_when_asked(tmp_path: Path) -> None:
    """The check joins the doctor report with --mailbox-orphans and is absent without it."""
    db = _store(tmp_path)
    assert "the live roster was not read" in str(await _doctor(db, asked=True))
    plain = await _doctor(db, asked=False)
    assert isinstance(plain, set) and CHECK not in plain
    assert build_parser().parse_args(["doctor", "--mailbox-orphans"]).mailbox_orphans is True
