# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the authorisation intent of a planned catalogue run
"""Build, from a planned catalogue run, the intent a hub declaration carries.

A run that goes through the hub is declared first, with an authorisation intent
that an approval then names by digest. The intent is built from the plan, never
from the caller's words, so any change to what would run changes the digest:

* ``tool`` is the row's vendor, component and version, and
  ``tool_descriptor_sha256`` the executable's fingerprint taken at planning;
* ``arguments_sha256`` is the SHA-256 of the canonical JSON of the row's
  identity and the rendered command line (without the executable's local path,
  which the fingerprint already stands for);
* ``rule_generation`` is the row's binding digest, the catalogue rule the plan
  was admitted under;
* the effect is one file: its path and the content it must hold afterwards form
  the declaration's effect target, and the content it holds now (or
  ``absent``) is the intent's pre-state;
* or, for a row that names ``observed_by``, the effect is the component's own
  state (``component_state``): its target is the component's identity, the
  observing read row and the digest of that read's projection after the run,
  and the digest of the projection now is the intent's pre-state
  (:mod:`~synapse_channel.operations.component_state`).

Only rows whose effects are ``read``, ``change`` or ``destroy``, with at least
one that changes something, have such an intent. ``execute`` is refused because
its effect is not one file; ``extend`` and ``approve`` because an approval on the
hub is not a named person's consent. The runner does not confine its component,
so the required assurance is ``cooperative`` and no egress origin is allowed.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.aef_canonical import AefCanonicalizationError, canonical_json
from synapse_channel.core.authorisation_intent import (
    INTENT_SCHEMA,
    AuthorisationIntentError,
    parse_intent_statement,
)
from synapse_channel.operations.row import OperationRow
from synapse_channel.operations.runner import OperationRunRefused, RunPlan

HUB_PATH_EFFECTS = frozenset({"read", "change", "destroy"})
"""Effects a row may have and still go through the hub; the rest are refused there."""

_CHANGING = frozenset({"change", "destroy"})
_HEX64 = re.compile(r"[0-9a-f]{64}")
_MAX_PATH = 512


@dataclass(frozen=True, slots=True)
class HubIntent:
    """The parts of a hub declaration a planned run supplies.

    Attributes
    ----------
    statement : dict[str, Any]
        The validated intent statement, for ``synapse operation declare --intent``.
    effect_target : dict[str, str]
        The effect's target: for a file, its absolute ``path`` and the ``sha256``
        it must hold after the run; for a component's own state, as
        :func:`component_target` builds it.
    effect : str
        ``file`` or ``component_state``.
    """

    statement: dict[str, Any]
    effect_target: dict[str, str]
    effect: str = "file"


def _refused_effects(run: RunPlan) -> None:
    """Refuse a row whose effects are not one file's, naming why."""
    effects = set(run.row.effects)
    outside = sorted(effects - HUB_PATH_EFFECTS)
    if outside:
        raise OperationRunRefused(
            f"{run.row.operation} cannot go through the hub: its effects {outside} are not one "
            "file's change, and extend or approve need a named person's consent"
        )
    if not effects & _CHANGING:
        raise OperationRunRefused(f"{run.row.operation} changes nothing for the hub to govern")


def _file_target(path: str, after_sha256: str) -> dict[str, str]:
    """Return the file effect's target, or refuse a path or digest of the wrong form."""
    if not os.path.isabs(path) or not 0 < len(path) <= _MAX_PATH or not path.isprintable():
        raise OperationRunRefused(
            f"the hub path needs an absolute file path of 1 to {_MAX_PATH} characters"
        )
    if _HEX64.fullmatch(after_sha256) is None:
        raise OperationRunRefused("the hub path needs the file's expected SHA-256 after the run")
    return {"path": path, "sha256": after_sha256}


def _arguments_digest(run: RunPlan) -> str:
    """Return the SHA-256 of the row's identity and the rendered command line."""
    row = run.row
    identity = {
        "operation": row.operation,
        "host": row.host,
        "os": row.os,
        "transport": row.transport,
        "variant": row.variant,
        "argv": list(run.argv[1:]),
    }
    try:
        return hashlib.sha256(canonical_json(identity)).hexdigest()
    except AefCanonicalizationError as exc:
        raise OperationRunRefused("the run's command line cannot be written canonically") from exc


def component_target(row: OperationRow, expected_sha256: str) -> dict[str, str]:
    """Return a component's own state as a declaration names it: identity, observer, digest."""
    return {
        "host": row.host,
        "vendor": row.vendor,
        "component": row.component,
        "component_version": row.component_version,
        "os": row.os,
        "transport": row.transport,
        "observed_by": row.observed_by,
        "expected_sha256": expected_sha256,
    }


def _statement(run: RunPlan, *, before: str, deadline: int, effect: str) -> dict[str, Any]:
    """Return the validated intent statement of ``run`` for ``effect``."""
    row = run.row
    statement: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "pre_state": before,
        "recovery_class": "one_way" if "destroy" in row.effects else "reversible",
        "deadline": deadline,
        "budget": "not_applicable",
        "egress": [],
        "tool": {"name": f"{row.vendor}/{row.component}", "version": row.component_version},
        "tool_descriptor_sha256": run.fingerprint.removeprefix("sha256:"),
        "arguments_sha256": _arguments_digest(run),
        "rule_generation": row.binding_digest,
        "required_assurance": "cooperative",
    }
    try:
        parsed = parse_intent_statement(statement, effect)
    except AuthorisationIntentError as exc:
        raise OperationRunRefused(str(exc)) from exc
    return dict(parsed.document)


def component_hub_intent(
    run: RunPlan, *, before: str, expected_sha256: str, deadline: int
) -> HubIntent:
    """Return the intent and the ``component_state`` target of ``run`` for a hub declaration.

    Parameters
    ----------
    run : RunPlan
        The planned change of a row that names ``observed_by``.
    before : str
        The digest of the observing read's projection now.
    expected_sha256 : str
        The digest of the projection the read must give after the run.
    deadline : int
        The intent's deadline, in whole UNIX seconds.

    Raises
    ------
    OperationRunRefused
        For a row whose effects the hub path refuses, or a statement the intent
        schema refuses; the message says which.
    """
    _refused_effects(run)
    target = component_target(run.row, expected_sha256)
    statement = _statement(run, before=before, deadline=deadline, effect="component_state")
    return HubIntent(statement, target, "component_state")


def hub_intent(
    run: RunPlan, *, path: str, before: str, after_sha256: str, deadline: int
) -> HubIntent:
    """Return the intent statement and the file target of ``run`` for a hub declaration.

    Parameters
    ----------
    run : RunPlan
        The planned run; nothing is executed or read from disk here.
    path : str
        The absolute path of the one file the run changes.
    before : str
        The file's content SHA-256 now, or ``absent``.
    after_sha256 : str
        The content SHA-256 the file must hold after the run.
    deadline : int
        The intent's deadline, in whole UNIX seconds.

    Raises
    ------
    OperationRunRefused
        For a row whose effects are not one file's, a malformed path or digest,
        or a statement the intent schema refuses; the message says which.
    """
    _refused_effects(run)
    target = _file_target(path, after_sha256)
    return HubIntent(_statement(run, before=before, deadline=deadline, effect="file"), target)
