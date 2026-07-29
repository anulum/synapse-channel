# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — subprocess crash matrix for atomic operation commit points

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.core.persistence import EventStore

_FAMILIES = (
    "claim",
    "task_update",
    "release",
    "handoff",
    "checkpoint",
    "guard_denial",
    "resource",
    "ledger_task",
    "ledger_task_update",
    "ledger_progress",
    "operator_relay",
)

_CHILD = r"""
import os
import signal
from synapse_channel.core.persistence import EventStore

store = EventStore(os.environ["ATOMIC_DB"])
stage = os.environ["ATOMIC_STAGE"]

def kill(observed):
    if observed == stage:
        os.kill(os.getpid(), signal.SIGKILL)

store.commit_operation(
    operation_key="seat\0" + os.environ["ATOMIC_KIND"] + "\0kill-1",
    request_digest="a" * 64,
    response={"type": os.environ["ATOMIC_KIND"] + "_result", "fixed": True},
    events=((os.environ["ATOMIC_KIND"], {"family": os.environ["ATOMIC_KIND"]}),),
    intent={"family": os.environ["ATOMIC_KIND"]},
    stage_hook=kill,
)
"""


@pytest.mark.parametrize("family", _FAMILIES)
@pytest.mark.parametrize(
    ("stage", "committed"),
    [
        ("after_legacy_event_insert", False),
        ("after_operation_insert", False),
        ("after_operation_outbox_insert", False),
        ("before_commit", False),
        ("after_commit", True),
    ],
)
def test_real_process_death_is_all_or_nothing(
    tmp_path: Path,
    family: str,
    stage: str,
    committed: bool,
) -> None:
    db = tmp_path / f"{family}-{stage}.db"
    env = {
        **os.environ,
        "ATOMIC_DB": str(db),
        "ATOMIC_KIND": family,
        "ATOMIC_STAGE": stage,
        "PYTHONPATH": str(Path.cwd() / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-c", _CHILD],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode < 0

    store = EventStore(db)
    key = f"seat\x00{family}\x00kill-1"
    if not committed:
        assert store.read_all() == []
        assert store.read_operations() == ()
        assert store.pending_operation_outbox_count() == 0
    else:
        operation = store.get_operation(key)
        assert operation is not None
        assert len(store.read_all()) == 2
        assert store.pending_operation_outbox_count() == 1
        replayed = store.commit_operation(
            operation_key=key,
            request_digest="a" * 64,
            response={"type": "different"},
            events=((family, {"family": "duplicate"}),),
            intent={"family": family},
        )
        conflict = store.commit_operation(
            operation_key=key,
            request_digest="b" * 64,
            response={"type": "different"},
            events=((family, {"family": "changed"}),),
            intent={"family": family},
        )
        assert replayed.outcome == "replayed"
        assert conflict.outcome == "conflict"
        assert len(store.read_all()) == 2
    store.close()
