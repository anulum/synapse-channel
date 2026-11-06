# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared step bookkeeping and the journal check of an interop journey
"""Run journey steps, stop at the first failure, then judge by the journal.

A journey is a fixed list of client steps followed by journal steps. The client
steps record only their route, outcome code and key shape. The journal steps
are positive assertions read from the hub's own store once the hub has
stopped. A journey passes only when every step passes.

Two controls show that a journey can fail:

* ``surface-disabled`` makes the hub or server refuse the client, so the
  journey must fail on a client step;
* ``journal-oracle`` runs the client steps unchanged but judges them against an
  unrelated, untouched store, so the journey must fail on the first journal
  step and nowhere else.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from synapse_channel.core.persistence import EventStore
from tools.interop_oracle import JournalOracle, OracleFailure
from tools.interop_record import JourneyRecord, StepRecord, journal_digest, key_shape

CONTROLS = ("none", "surface-disabled", "journal-oracle")
"""The journey modes: a real run and its two negative controls."""


class StepFailure(AssertionError):
    """A client step did not give the expected result; ``step`` names it."""

    def __init__(self, step: str, outcome: str) -> None:
        """Keep the step name and its short outcome code."""
        super().__init__(f"{step}: {outcome}")
        self.step = step
        self.outcome = outcome


@dataclass
class Steps:
    """The steps of one journey so far."""

    records: list[StepRecord] = field(default_factory=list)

    def passed(
        self,
        name: str,
        route: str,
        *,
        request: object = None,
        response: object = None,
    ) -> None:
        """Record a step that gave the expected result."""
        self.records.append(StepRecord(name, route, "ok", key_shape(request), key_shape(response)))

    def failed(self, name: str, route: str, outcome: str) -> StepFailure:
        """Record a failed step and return the failure to raise."""
        self.records.append(StepRecord(name, route, outcome))
        return StepFailure(name, outcome)


def read_client_report(stdout: str, returncode: int, steps: Steps) -> dict[str, object] | None:
    """Copy a separate client's reported steps into ``steps`` and return its report.

    A client that runs in its own environment prints one JSON document with its
    ``steps`` and ``failed_step``. Output that is not such a document records a
    failed ``client-run`` step and returns ``None``.
    """
    try:
        report = json.loads(stdout)
        reported = [
            StepRecord(
                str(step["name"]),
                str(step["route"]),
                str(step["outcome"]),
                [str(key) for key in step["request_keys"]],
                [str(key) for key in step["response_keys"]],
            )
            for step in report["steps"]
        ]
        str(report["failed_step"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        steps.records.append(StepRecord("client-run", "client process", f"exit-{returncode}"))
        return None
    steps.records.extend(reported)
    return dict(report)


def judge(
    steps: Steps,
    db: Path,
    *,
    control: str,
    unrelated: Path,
    assertions: Callable[[JournalOracle], None],
) -> tuple[str, str]:
    """Run the journal assertions and return ``(failed_step, journal_sha256)``.

    In the ``journal-oracle`` control the assertions read ``unrelated``, a new
    empty store, instead of the hub's.
    """
    if control == "journal-oracle":
        EventStore(unrelated).close()
        db = unrelated
    oracle = JournalOracle.read(db)
    try:
        assertions(oracle)
    except OracleFailure as failure:
        steps.records.append(StepRecord(failure.step, "journal", "missing"))
        return failure.step, journal_digest(oracle.asserted)
    steps.records.append(StepRecord("journal-checked", "journal", "ok"))
    return "", journal_digest(oracle.asserted)


def journey_record(
    *,
    client: str,
    version: str,
    integrity: str,
    transport: str,
    journey: str,
    steps: Steps,
    failed_step: str,
    journal_sha256: str,
) -> JourneyRecord:
    """Return the record of one finished journey."""
    return JourneyRecord(
        client=client,
        version=version,
        integrity=integrity,
        transport=transport,
        journey=journey,
        steps=list(steps.records),
        journal_sha256=journal_sha256,
        failed_step=failed_step,
    )


def expectation_met(record: JourneyRecord, control: str, journal_step: str) -> bool:
    """Return whether a journey ended the way its mode requires.

    A real run must pass. ``surface-disabled`` must fail on a client step.
    ``journal-oracle`` must fail on exactly ``journal_step``, the first journal
    assertion.
    """
    if control == "none":
        return record.failed_step == ""
    if control == "surface-disabled":
        return record.failed_step != "" and not record.failed_step.startswith("journal-")
    return record.failed_step == journal_step
