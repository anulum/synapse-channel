# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a durable log of three namespaces, unscoped work and a bare name
"""Seed one real event store whose rows each belong to a known scope.

- ``ALPHA``: tasks ``A-1`` and ``A-2`` (``A-2`` waits on ``A-1``), declared by
  ``ALPHA/lead``; a relay by ``ALPHA/ops`` on ``A-1``; a finding by ``ALPHA/x``.
- ``BETA``: tasks ``B-1`` and ``B-2`` (``B-2`` waits on ``B-1``), declared by
  ``BETA/lead``, and ``B-3`` (waits on ``B-1``), declared by ``GAMMA/planner``, so
  its declaration is in ``GAMMA`` by its actor while its wait is in ``BETA`` only;
  a relay by ``GAMMA/ops`` on ``B-1``, which is in ``GAMMA`` by its actor and in
  ``BETA`` by its task; a finding by ``BETA/y``.
- No project: tasks ``U-1`` and ``U-2`` (``U-2`` waits on ``U-1``), declared by
  the bare name ``solo``; a relay by the bare name ``ops`` on the namespace
  ``ZETA``, which is no task; a finding by ``solo``; a finding with no attested
  identity, which no scope but ``all`` holds.
"""

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.journal import EventKind, record_ledger_task, record_operator_relay
from synapse_channel.core.ledger import LedgerTask
from synapse_channel.core.persistence import EventStore

TASKS = (
    ("A-1", "ALPHA", "ALPHA/lead", ()),
    ("A-2", "ALPHA", "ALPHA/lead", ("A-1",)),
    ("B-1", "BETA", "BETA/lead", ()),
    ("B-2", "BETA", "BETA/lead", ("B-1",)),
    ("B-3", "BETA", "GAMMA/planner", ("B-1",)),
    ("U-1", "", "solo", ()),
    ("U-2", "", "solo", ("U-1",)),
)
RELAYS = (
    {"action": "release", "task_id": "A-1", "operator": "ALPHA/ops", "applied": True},
    {"action": "release", "task_id": "B-1", "operator": "GAMMA/ops", "applied": True},
    {"action": "release", "namespace": "ZETA", "operator": "ops", "applied": True},
)
FINDINGS = (("alpha finding", "ALPHA/x"), ("beta finding", "BETA/y"), ("solo finding", "solo"))


def seed_scoped_store(db: Path) -> None:
    """Write the tasks, relays and findings above into a new durable log at ``db``."""
    store = EventStore(db)
    try:
        for task_id, project, author, depends_on in TASKS:
            record_ledger_task(
                store,
                LedgerTask(
                    task_id=task_id,
                    title=task_id.lower(),
                    created_at=1.0,
                    updated_at=1.0,
                    depends_on=depends_on,
                    project=project,
                    created_by=author,
                ),
            )
        for relay in RELAYS:
            record_operator_relay(store, relay)
        for statement, by in FINDINGS:
            check = {"checked_this_session": False, "source_ref": "", "by": by, "at": 2.0}
            store.append(
                EventKind.FINDING,
                {"statement": statement, "subkind": "fact", "verified_at_source": check},
                ts=2.0,
            )
        store.append(EventKind.FINDING, {"statement": "unattested", "subkind": "fact"}, ts=3.0)
    finally:
        store.close()
