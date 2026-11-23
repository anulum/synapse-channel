# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a first mailbox arm does not replay old group traffic

"""The first mailbox wait of an identity with no cursor file.

A real hub already holds a broadcast, mail for someone else, and mail for the
project. ``syn-wait --mailbox --max-wakes 1`` is that wait: mailbox replay, one
wake. Old group traffic must not end it. A message that names the identity
exactly, sent before the arm, still must.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli
from synapse_channel.cli_messaging_wait import UNREADABLE_CURSOR_EXIT_CODE, _note_anchor, _wait
from synapse_channel.core.handlers.mailbox_replay import send_frames
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mailbox_cursor import read_waiter_cursor

SEAT = "PROJ/fresh"


async def _seed(uri: str, payload: str, target: str) -> None:
    """Journal one directed or broadcast chat."""
    sender = await connect_agent("peer", uri)
    try:
        await sender.agent.chat(payload, target=target)
    finally:
        await close_agents(sender)


async def test_syn_wait_mailbox_max_wakes_one_ignores_old_personal_mail(
    tmp_path: Path,
    capsys: Any,
) -> None:
    """A missing cursor does not wake on mail already addressed to the identity."""
    cursor = tmp_path / "cursor"
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            await _seed(uri, "old broadcast", "all")
            await _seed(uri, "old personal", SEAT)
            code = await _wait(
                uri=uri,
                name=f"{SEAT}-rx",
                for_name=SEAT,
                timeout=0.6,
                directed_only=True,
                mailbox=True,
                mailbox_cursor_path=cursor,
            )
    finally:
        store.close()

    out = capsys.readouterr().out
    assert code == 2
    assert "old personal" not in out
    assert "old broadcast" not in out
    assert "mailbox cursor starts at" in out
    assert f"1 earlier message addressed to {SEAT}" in out
    assert "syn-wait --since 0 replays them" in out
    assert load_saved(cursor) > 0


async def test_since_zero_replays_mail_sent_before_the_first_arm(
    tmp_path: Path,
    capsys: Any,
) -> None:
    """``--since 0`` is how the seat reads mail that arrived before the first arm."""
    cursor = tmp_path / "cursor"
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            await _seed(uri, "onboarding", SEAT)
            code = await _wait(
                uri=uri,
                name=f"{SEAT}-rx",
                for_name=SEAT,
                timeout=5.0,
                directed_only=True,
                mailbox=True,
                mailbox_cursor_path=cursor,
                mailbox_since=0,
            )
    finally:
        store.close()

    out = capsys.readouterr().out
    assert code == 0
    assert "onboarding" in out


async def test_a_project_glob_is_not_earlier_mail_for_this_name(
    tmp_path: Path,
    capsys: Any,
) -> None:
    """A project glob is not mail addressed to this name, and it does not wake."""
    cursor = tmp_path / "cursor"
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            await _seed(uri, "project fanout", "PROJ/*")
            code = await _wait(
                uri=uri,
                name=f"{SEAT}-rx",
                for_name=SEAT,
                timeout=0.6,
                directed_only=True,
                mailbox=True,
                mailbox_cursor_path=cursor,
            )
    finally:
        store.close()
    out = capsys.readouterr().out
    assert code == 2
    assert "project fanout" not in out
    assert f"no earlier mail addressed to {SEAT}" in out


async def test_an_unreadable_cursor_stops_before_connect(tmp_path: Path, capsys: Any) -> None:
    """A broken cursor file does not become a replay from sequence 0."""
    cursor = tmp_path / "cursor"
    cursor.mkdir()
    code = await _wait(
        uri="ws://127.0.0.1:9",
        name=f"{SEAT}-rx",
        for_name=SEAT,
        timeout=5.0,
        mailbox=True,
        mailbox_cursor_path=cursor,
    )

    assert code == UNREADABLE_CURSOR_EXIT_CODE
    out = capsys.readouterr().out
    assert "mailbox cursor is unreadable" in out
    assert "pass --since" in out


def test_arm_since_accepts_zero_and_refuses_a_negative() -> None:
    """``synapse arm --since`` is the history override ``syn-wait`` forwards."""
    args = cli.build_parser().parse_args(["arm", "--since", "0", "--mailbox"])
    assert args.since == 0
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["arm", "--since", "-1"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["arm", "--since", "nope"])


def test_a_negative_waiter_cursor_is_unreadable(tmp_path: Path) -> None:
    """A negative file is not a resume point and not a missing file."""
    path = tmp_path / "cursor"
    path.write_text("-3", encoding="utf-8")
    state = read_waiter_cursor(path)
    assert state.unreadable is True
    assert state.seq is None


async def test_a_journal_less_hub_anchors_at_zero(tmp_path: Path, capsys: Any) -> None:
    """Without a journal the anchor is zero and the wait does not replay."""
    cursor = tmp_path / "cursor"
    async with running_hub(SynapseHub()) as (_hub, uri):
        code = await _wait(
            uri=uri,
            name=f"{SEAT}-rx",
            for_name=SEAT,
            timeout=0.6,
            directed_only=True,
            mailbox=True,
            mailbox_cursor_path=cursor,
        )
    out = capsys.readouterr().out
    assert code == 2
    assert "mailbox cursor starts at 0" in out
    assert f"no earlier mail addressed to {SEAT}" in out


async def test_replay_frames_fall_back_to_one_send_at_a_time() -> None:
    """A hub without a batch send still delivers each replay frame."""
    sent: list[dict[str, Any]] = []

    class _Hub:
        _broadcaster = object()

        async def send_json(self, _websocket: Any, frame: dict[str, Any]) -> None:
            sent.append(frame)

    await send_frames(cast(Any, _Hub()), None, [{"seq": 4}])
    assert sent == [{"seq": 4}]


def test_an_anchor_is_said_once_and_never_moves_the_cursor_backwards(capsys: Any) -> None:
    """A second anchor does not repeat the line, and an older one does not rewind."""
    surfaced = [3]
    announced = [False]
    frame = {"mailbox_anchor": 1, "mailbox_pending_before": 1}
    _note_anchor(frame, surfaced, announced, cursor_absent=True, for_name=SEAT)
    _note_anchor(
        {"mailbox_anchor": 8, "mailbox_pending_before": 2},
        surfaced,
        announced,
        cursor_absent=True,
        for_name=SEAT,
    )
    _note_anchor(
        {"mailbox_anchor": 9, "mailbox_pending_before": 1},
        surfaced,
        announced,
        cursor_absent=False,
        for_name=SEAT,
    )
    out = capsys.readouterr().out
    assert out.count(f"1 earlier message addressed to {SEAT}") == 1
    assert "mailbox cursor starts at 8" not in out
    assert surfaced[0] == 9


def test_the_anchor_line_names_two_messages_and_refuses_a_bad_count(capsys: Any) -> None:
    """Two earlier messages use the plural. A boolean or a missing count is none."""
    surfaced = [0]
    announced = [False]
    _note_anchor(
        {"mailbox_anchor": 4, "mailbox_pending_before": 2},
        surfaced,
        announced,
        cursor_absent=True,
        for_name=SEAT,
    )
    announced[0] = False
    _note_anchor(
        {"mailbox_anchor": 5, "mailbox_pending_before": True},
        surfaced,
        announced,
        cursor_absent=True,
        for_name=SEAT,
    )
    announced[0] = False
    _note_anchor(
        {"mailbox_anchor": 6, "mailbox_pending_before": -1},
        surfaced,
        announced,
        cursor_absent=True,
        for_name=SEAT,
    )
    announced[0] = False
    _note_anchor(
        {"mailbox_anchor": 7},
        surfaced,
        announced,
        cursor_absent=True,
        for_name=SEAT,
    )
    out = capsys.readouterr().out
    assert f"2 earlier messages addressed to {SEAT}" in out
    assert out.count(f"no earlier mail addressed to {SEAT}") == 3


def load_saved(path: Path) -> int:
    """Return the integer a waiter persisted."""
    return int(path.read_text(encoding="utf-8"))
