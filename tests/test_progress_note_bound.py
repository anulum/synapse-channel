# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one bound on a progress note's text, held by the hub and every writer
"""A progress note over ``MAX_PROGRESS_NOTE_BYTES`` is refused, never trimmed, everywhere."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.cli import main
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.approvals import STATE_APPROVED, STATE_REJECTED, format_approval_note
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.ledger import MAX_PROGRESS_NOTE_BYTES, progress_note_refusal
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.dashboard_approval_decide import MAX_APPROVAL_REASON_BYTES

_REFUSAL = f"progress note text exceeds {MAX_PROGRESS_NOTE_BYTES} UTF-8 bytes"
_CLOSED_HUB = "ws://127.0.0.1:1"


def test_the_bound_counts_utf8_bytes_and_refuses_one_byte_over() -> None:
    assert progress_note_refusal("r" * MAX_PROGRESS_NOTE_BYTES) is None
    assert progress_note_refusal("é" * (MAX_PROGRESS_NOTE_BYTES // 2)) is None
    assert progress_note_refusal("r" * (MAX_PROGRESS_NOTE_BYTES + 1)) == _REFUSAL
    assert progress_note_refusal("é" * (MAX_PROGRESS_NOTE_BYTES // 2) + "r") == _REFUSAL


def test_the_longest_approval_the_dashboard_can_send_fits_the_hub_bound() -> None:
    subject = "sha256:" + "f" * 64
    for state in (STATE_APPROVED, STATE_REJECTED):
        note = format_approval_note(
            subject=subject, state=state, reason="r" * MAX_APPROVAL_REASON_BYTES
        )
        assert progress_note_refusal(note) is None


async def test_the_hub_keeps_a_note_at_the_bound_and_refuses_one_byte_over(
    tmp_path: Path,
) -> None:
    at_bound, over = "a" * MAX_PROGRESS_NOTE_BYTES, "b" * (MAX_PROGRESS_NOTE_BYTES + 1)
    # The frame must carry the note at the bound; the note bound, not the frame, refuses.
    frame = 2 * MAX_PROGRESS_NOTE_BYTES
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal, max_msg_bytes=frame)) as (hub, uri):
            agent = await connect_agent("P/writer", uri)
            try:
                for text in (at_bound, over):
                    await agent.agent.send_message(
                        MessageType.LEDGER_PROGRESS,
                        target="System",
                        payload=text,
                        task_id="T-1",
                        kind="note",
                    )
                posted = await agent.recorder.wait_for(
                    lambda data: data.get("type") == MessageType.LEDGER_PROGRESS_POSTED
                )
                refused = await agent.recorder.wait_for(
                    lambda data: (
                        data.get("type") == MessageType.ERROR and data.get("payload") == _REFUSAL
                    )
                )
            finally:
                await close_agents(agent)
            notes = [note.text for note in hub.blackboard.progress]
        kept = [row.payload for row in journal.iter_events() if row.kind == "ledger_progress"]

    assert posted["note"]["text"] == at_bound
    assert refused["target"] == "P/writer"
    assert notes == [at_bound]
    assert len(kept) == 1


def test_the_sdk_refuses_an_over_long_note_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[object] = []

    async def record(*args: object, **kwargs: object) -> None:
        sent.append((args, kwargs))

    agent = SynapseAgent("P/writer", uri=_CLOSED_HUB)
    monkeypatch.setattr(agent, "send_message", record)
    with pytest.raises(ValueError, match=f"^{_REFUSAL}$"):
        asyncio.run(agent.post_progress("T-1", "r" * (MAX_PROGRESS_NOTE_BYTES + 1)))
    asyncio.run(agent.post_progress("T-1", "r" * MAX_PROGRESS_NOTE_BYTES))
    assert len(sent) == 1


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(
            [
                "approval",
                "decide",
                "--subject",
                "sha256:" + "a" * 64,
                "--approve",
                "--reason",
                "r" * (MAX_PROGRESS_NOTE_BYTES + 1),
            ],
            id="approval-decide",
        ),
        pytest.param(
            ["task", "progress", "T-1", "r" * (MAX_PROGRESS_NOTE_BYTES + 1)],
            id="task-progress",
        ),
        pytest.param(
            ["verdict", "evidence", "--sha256", "c" * 64, "--namespace", "P", "--members-file"],
            id="verdict-evidence",
        ),
    ],
)
def test_each_command_refuses_an_over_long_note_before_connecting(
    argv: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    common = ["--uri", _CLOSED_HUB, "--name", "P/writer"]
    if argv[0] == "verdict":
        # 17,000 member digests make a manifest over the bound, within its own member limit.
        members = tmp_path / "members.txt"
        members.write_text("\n".join(f"{n:064x}" for n in range(17_000)), encoding="utf-8")
        common += ["--task", "T-1"]
        argv = [*argv, str(members)]
    status = main([*argv[:2], *common, *argv[2:]])

    assert status == 2
    assert capsys.readouterr().err.strip() == _REFUSAL
