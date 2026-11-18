# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — observe a component's own state through the catalogue read row that observes it
"""Observe a component's own state through its catalogue read row.

A row that changes a component's own state (a setting, a plugin, a policy)
names ``observed_by``, the read row of the same component that observes the
changed object. The hub path declares such a change with the effect
``component_state`` and the digest of that read's projection after the change;
another identity verifies it by running the read and comparing digests. Both
sides use this module, so they observe the same way:

* :func:`plan_observer` plans the read row and checks it may observe
  (:meth:`~synapse_channel.operations.catalogue.Catalogue.observer`'s rules);
* :func:`observe` runs it once, under the caller's own ``operation-read``
  grant and in the run journal, and returns the digest of its projection
  (:mod:`~synapse_channel.operations.projection`);
* :func:`expected_digest` digests the projection an operator expects after the
  change, which must name exactly the read row's paths;
* :func:`verify_component` is the ``component_read`` verifier.

The read writes nothing, retries nothing and repairs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from synapse_channel.core.aef_canonical import AefCanonicalizationError
from synapse_channel.core.result_verification_record import ResultVerification
from synapse_channel.operations.catalogue import Catalogue, observer_refusal
from synapse_channel.operations.projection import ProjectionError, project, projection_digest
from synapse_channel.operations.row import OperationRow
from synapse_channel.operations.runner import (
    OperationRunRefused,
    OperationTarget,
    RunPlan,
    RunProfile,
    execute,
    plan,
)
from synapse_channel.operations.runner_journal import RunJournalError

COMPONENT_VERIFIER = "component_read"
"""The verifier name of a component's own state, as the hub's operation records know it."""

_EXPECTED_LIMIT = 1 << 20
"""Largest expected-projection file read, in bytes."""


def plan_read(catalogue: Catalogue, target: OperationTarget, profile: RunProfile) -> RunPlan:
    """Plan the read row ``target`` names and check that it may observe a change.

    Raises
    ------
    OperationRunRefused
        The row is not admitted, or reads more than its object, prints no
        projection Core applies, or discloses session content.
    """
    read = plan(catalogue, target, {}, profile=profile)
    reason = observer_refusal(read.row, read.fingerprint)
    if reason:
        raise OperationRunRefused(f"{read.row.operation} cannot observe a change: it {reason}")
    return read


def plan_observer(catalogue: Catalogue, row: OperationRow, profile: RunProfile) -> RunPlan:
    """Plan the read row that observes ``row``'s change: the one ``row.observed_by`` names.

    Raises
    ------
    OperationRunRefused
        ``row`` names no observer, or :func:`plan_read` refuses it.
    """
    if not row.observed_by:
        raise OperationRunRefused(f"{row.operation} names no observing read row")
    target = OperationTarget(
        row.observed_by,
        row.host,
        row.vendor,
        row.component,
        row.component_version,
        row.os,
        row.transport,
    )
    return plan_read(catalogue, target, profile)


def observe(
    read: RunPlan,
    *,
    operation_id: str,
    subject: str,
    grants_path: Path,
    journal: Path,
    timeout: float,
) -> str:
    """Run the observing read once and return the digest of its projection.

    Raises
    ------
    OperationRunRefused
        The read was refused before it ran (no grant, a replaced program, an
        id already used).
    ProjectionError
        The read did not finish, or its output gives no projection.
    """
    report = execute(
        read,
        operation_id=operation_id,
        subject=subject,
        grants_path=grants_path,
        journal=journal,
        timeout=timeout,
    )
    if report.state != "confirmed_done":
        raise ProjectionError(f"the read {read.row.operation} ended {report.state}")
    projection = project(
        report.output, read.row.projection_paths, truncated=report.output_truncated
    )
    return projection_digest(projection)


def expected_digest(path: Path, read: OperationRow) -> str:
    """Return the digest of the projection expected after the change, read from ``path``.

    The file is a JSON object whose keys are exactly the read row's projection
    paths and whose values are what each must select after the change.

    Raises
    ------
    OperationRunRefused
        The file is too large, not one JSON object of those keys, or holds a
        value outside Core's canonical JSON.
    """
    data = path.read_bytes()
    if len(data) > _EXPECTED_LIMIT:
        raise OperationRunRefused("the expected projection is larger than 1 MiB")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationRunRefused("the expected projection is not one JSON object") from exc
    if not isinstance(document, dict) or set(document) != set(read.projection_paths):
        raise OperationRunRefused(
            f"the expected projection names exactly the paths of {read.operation}: "
            + ", ".join(read.projection_paths)
        )
    try:
        return projection_digest(document)
    except AefCanonicalizationError as exc:
        raise OperationRunRefused(
            f"the expected projection is outside canonical JSON: {exc}"
        ) from exc


def verify_component(
    read: RunPlan,
    expected_sha256: str,
    *,
    operation_id: str,
    communication: str,
    read_id: str,
    subject: str,
    grants_path: Path,
    journal: Path,
    timeout: float,
) -> ResultVerification:
    """Run the observing read and compare its projection's digest with ``expected_sha256``.

    The evidence names the read operation, the executable's fingerprint and,
    when the read projected, ``observed_sha256``; never the projected value,
    which may name a setting.

    Returns
    -------
    ResultVerification
        ``verified`` when the digests are equal, ``not_verified`` when they
        differ, ``not_checked`` when the read was refused (``read_refused``) or
        gave no projection (``read_not_projected``).
    """
    evidence = {"read_operation": read.row.operation, "row_fingerprint": read.fingerprint}

    def record(effect: str, result: str, observation: str) -> ResultVerification:
        return ResultVerification(
            operation_id, COMPONENT_VERIFIER, communication, effect, result, observation, evidence
        )

    try:
        digest = observe(
            read,
            operation_id=read_id,
            subject=subject,
            grants_path=grants_path,
            journal=journal,
            timeout=timeout,
        )
    except (OperationRunRefused, RunJournalError):
        return record("unknown", "not_checked", "read_refused")
    except ProjectionError:
        return record("unknown", "not_checked", "read_not_projected")
    evidence["observed_sha256"] = digest
    if digest == expected_sha256:
        return record("observed", "verified", "projection_matches")
    return record("observed", "not_verified", "projection_differs")
