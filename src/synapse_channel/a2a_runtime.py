# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the A2A correlation runtime
"""Build the frame correlator and the execution that share its lock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synapse_channel.a2a_correlation import A2AFrameCorrelator
from synapse_channel.a2a_execution import A2AExecution, PublishTask, WriteStatus
from synapse_channel.a2a_store import A2ATaskStore


@dataclass(frozen=True, slots=True)
class A2ARuntime:
    """The correlator and the execution bound to that correlator's lock."""

    correlator: A2AFrameCorrelator
    execution: A2AExecution


def build_a2a_runtime(
    *,
    agent: Any,
    store: A2ATaskStore,
    run: Any,
    write_status: WriteStatus,
    publish: PublishTask,
) -> A2ARuntime:
    """Build both halves of one correlation lock and return them together."""
    correlator = A2AFrameCorrelator(
        agent=agent,
        store=store,
        run=run,
        write_status=write_status,
    )
    execution = A2AExecution(
        store,
        correlator.forward,
        write_status,
        publish,
        correlator.lock,
    )
    return A2ARuntime(correlator=correlator, execution=execution)
