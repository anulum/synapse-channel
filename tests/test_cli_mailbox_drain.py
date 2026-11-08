# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse mailbox drain` plans, then hands over or closes a project's orphans
"""A drain prints its plan by default and applies it, mailbox by mailbox, only with --execute."""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel import cli_mailbox_drain as drain
from synapse_channel.cli import build_parser
from synapse_channel.cli_mailbox_handover import (
    EXIT_APPLIED,
    EXIT_MORE_REMAINS,
    EXIT_NO_VERDICT,
    EXIT_REFUSED,
    HandoverConnection,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_orphans import MailboxStanding
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

DAY = 86_400.0
LATER = 30 * DAY


def _store(db: Path) -> None:
    with EventStore(db) as store:
        for target, text in (("P/gone", "a"), ("P/gone", "b"), ("P/left", "c"), ("Q/x", "d")):
            store.append(
                EventKind.CHAT, {"sender": "X/z", "target": target, "payload": text}, ts=1.0
            )


def _args(db: Path, *extra: str) -> Any:
    return build_parser().parse_args(
        ["mailbox", "drain", str(db), "--project", "P", "--reason", "sessions ended", *extra]
    )


def _run(args: Any, **kwargs: Any) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = drain._cmd_drain(args, clock=lambda: LATER, write=lines.append, **kwargs)
    return code, lines


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_parser_requires_a_project_a_reason_an_actor_and_one_action(tmp_path: Path) -> None:
    parser = build_parser()
    args = _args(tmp_path / "hub.db", "--name", "P/boss", "--close")
    assert args.func is drain._cmd_drain
    assert (args.close, args.to, args.execute) == (True, None, False)
    for missing in (
        ["mailbox", "drain", "db", "--project", "P", "--reason", "r", "--close"],
        ["mailbox", "drain", "db", "--name", "P/boss", "--reason", "r", "--close"],
        ["mailbox", "drain", "db", "--project", "P", "--name", "P/boss", "--close"],
        ["mailbox", "drain", "db", "--project", "P", "--name", "P/boss", "--reason", "r"],
        [
            "mailbox",
            "drain",
            "db",
            "--project",
            "P",
            "--name",
            "N",
            "--reason",
            "r",
            "--close",
            "--to",
            "P/new",
        ],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(missing)


def test_without_execute_the_plan_is_printed_and_nothing_changes(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _store(db)
    code, lines = _run(_args(db, "--name", "P/boss", "--close"))
    assert code == EXIT_APPLIED
    assert lines == [
        "      2  P/gone",
        "      1  P/left",
        "would close 3 pending messages in 2 orphaned mailboxes of project P; "
        "run again with --execute to apply",
    ]
    code, lines = _run(_args(db, "--name", "P/boss", "--to", "P/left", "--json"))
    document = json.loads(lines[0])
    assert code == EXIT_APPLIED
    assert (document["action"], document["successor"], document["execute"]) == (
        "handover",
        "P/left",
        False,
    )
    assert document["mailboxes"] == [{"identity": "P/gone", "pending": 2}]
    with EventStore(db) as store:
        assert store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,)) == []


def test_nothing_to_drain_and_an_unreadable_store_are_reported(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _store(db)
    code, lines = _run(
        _args(db, "--name", "P/boss", "--close", "--active", "P/gone", "--active", "P/left")
    )
    assert (code, lines) == (EXIT_APPLIED, ["no orphaned mailboxes in project P"])
    code, lines = _run(_args(tmp_path / "missing" / "hub.db", "--name", "P/boss", "--close"))
    assert (code, lines) == (EXIT_REFUSED, [])


async def test_execute_drains_each_orphan_as_its_custodian_through_the_hub(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    _store(db)
    store = EventStore(db)
    try:
        hub = SynapseHub(journal=store, mailbox_custodians={"P": "P/boss"})
        async with running_hub(hub) as (_hub, uri):
            args = _args(db, "--name", "P/boss", "--close", "--execute", "--uri", uri, "--json")
            code, lines = await asyncio.to_thread(_run, args)
        marks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
    finally:
        store.close()
    document = json.loads(lines[0])
    assert code == EXIT_APPLIED
    assert [(item["identity"], item["status"]) for item in document["mailboxes"]] == [
        ("P/gone", "drained"),
        ("P/left", "drained"),
    ]
    assert {mark.payload["identity"]: mark.payload["source"] for mark in marks} == {
        "P/gone": "closed",
        "P/left": "closed",
    }


async def test_a_refused_mailbox_is_reported_and_the_drain_goes_on(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _store(db)
    store = EventStore(db)
    try:
        hub = SynapseHub(journal=store, mailbox_custodians={"P/left": "P/boss"})
        async with running_hub(hub) as (_hub, uri):
            args = _args(db, "--name", "P/boss", "--to", "P/new", "--execute", "--uri", uri)
            code, lines = await asyncio.to_thread(_run, args)
    finally:
        store.close()
    assert code == EXIT_REFUSED
    assert lines[0].startswith("refused (not_authorised)  P/gone  ")
    assert lines[1].startswith("drained  P/left  handed 1 message(s) from P/left to P/new")
    assert lines[2] == "drained 1 of 2 orphaned mailboxes (hand over to P/new)"


def test_an_unreachable_hub_gives_no_verdict(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _store(db)
    uri = f"ws://127.0.0.1:{_free_port()}"
    args = _args(
        db, "--name", "P/boss", "--close", "--execute", "--uri", uri, "--ready-timeout", "0.3"
    )
    code, _lines = _run(args)
    assert code == EXIT_NO_VERDICT


class _Connection(HandoverConnection):
    """A stand-in connection that answers every request with one canned frame."""

    def __init__(self, answer: dict[str, Any] | None) -> None:  # no hub behind it
        self.answer = answer
        self.requests: list[dict[str, Any]] = []

    async def request(self, fields: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
        del timeout
        self.requests.append(fields)
        return self.answer


def _standing(identity: str = "P/gone") -> MailboxStanding:
    return MailboxStanding(identity, "P", 5, 0, 1.0, 1.0)


async def test_a_mailbox_that_keeps_more_mail_stops_at_the_request_limit() -> None:
    connection = _Connection(
        {
            "type": MessageType.MAILBOX_HANDOVER_RESULT,
            "status": "applied",
            "remaining": True,
            "through_seq": 9,
            "forwarded": 2,
            "payload": "handed 2",
        }
    )
    outcome = await drain.drain_mailbox(
        connection,
        _standing(),
        successor="P/new",
        reason="r",
        timeout=1.0,
        max_requests=3,
    )
    assert (outcome.status, outcome.requests, outcome.forwarded, outcome.through_seq) == (
        "remaining",
        3,
        6,
        9,
    )
    assert connection.requests[0] == {"from": "P/gone", "reason": "r", "to": "P/new"}


async def test_no_verdict_and_a_hub_error_end_one_mailbox() -> None:
    silent = await drain.drain_mailbox(
        _Connection(None),
        _standing(),
        successor=None,
        reason="r",
        timeout=0.1,
    )
    assert (silent.status, silent.requests) == ("no_verdict", 1)
    error = await drain.drain_mailbox(
        _Connection({"type": MessageType.ERROR, "payload": "denied"}),
        _standing(),
        successor=None,
        reason="r",
        timeout=0.1,
    )
    assert (error.status, error.reason, error.detail) == ("refused", "hub_error", "denied")


def test_the_exit_code_is_the_most_serious_outcome() -> None:
    def outcome(status: str) -> drain.DrainOutcome:
        return drain.DrainOutcome("P/x", 1, status)

    assert drain._exit_code([]) == EXIT_APPLIED
    assert drain._exit_code([outcome("drained"), outcome("remaining")]) == EXIT_MORE_REMAINS
    assert drain._exit_code([outcome("remaining"), outcome("refused")]) == EXIT_REFUSED
    assert drain._exit_code([outcome("no_verdict"), outcome("refused")]) == EXIT_NO_VERDICT


async def test_a_silent_mailbox_ends_the_whole_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def silent(_connection: Any, standing: MailboxStanding, **_kwargs: Any) -> Any:
        calls.append(standing.identity)
        return drain.DrainOutcome(standing.identity, standing.pending, "no_verdict")

    monkeypatch.setattr(drain, "drain_mailbox", silent)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            outcomes = await drain.drain_mailboxes(
                [_standing("P/a"), _standing("P/b")],
                uri=uri,
                actor="P/boss",
                token=None,
                ready_timeout=3.0,
                successor=None,
                reason="r",
                timeout=1.0,
            )
    finally:
        store.close()
    assert calls == ["P/a"]
    assert outcomes is not None and [item.status for item in outcomes] == ["no_verdict"]


async def test_a_verdict_without_a_sequence_leaves_the_covered_sequence_unknown() -> None:
    answer = {"type": MessageType.MAILBOX_HANDOVER_RESULT, "status": "already_applied"}
    outcome = await drain.drain_mailbox(
        _Connection(answer), _standing(), successor=None, reason="r", timeout=1.0
    )
    assert (outcome.status, outcome.through_seq, outcome.forwarded) == ("drained", None, 0)
