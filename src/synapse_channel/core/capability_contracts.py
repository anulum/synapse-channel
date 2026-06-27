# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — formal capability contract records
"""Declarative input/output contracts attached to capability cards.

Capability contracts are discovery metadata, not executable trust. They give a
router or peer a stable, schema-backed description of what one task class
accepts and returns, plus optional natural-language preconditions and
postconditions that humans or higher-level policy engines can inspect. The hub
normalises contract records before storing them so every manifest exposes one
canonical shape.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

JsonSchema = dict[str, Any]
"""JSON-object shape used for stored input and output schemas."""


@dataclass(frozen=True)
class CapabilityContract:
    """Declarative contract for one advertised task class.

    Parameters
    ----------
    task_class : str
        Routing class the contract describes, for example ``chat`` or ``rule``.
    input_schema : dict[str, Any], optional
        JSON Schema style mapping describing accepted input.
    output_schema : dict[str, Any], optional
        JSON Schema style mapping describing produced output.
    preconditions : tuple[str, ...], optional
        Declarative checks that should hold before the task is invoked.
    postconditions : tuple[str, ...], optional
        Declarative checks that should hold after the task completes.
    """

    task_class: str
    input_schema: JsonSchema = field(default_factory=dict)
    output_schema: JsonSchema = field(default_factory=dict)
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical manifest representation of the contract."""
        return {
            "task_class": self.task_class,
            "input_schema": copy.deepcopy(self.input_schema),
            "output_schema": copy.deepcopy(self.output_schema),
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
        }


def _clean_checks(values: object) -> tuple[str, ...]:
    """Return stripped, de-duplicated condition strings from ``values``."""
    if isinstance(values, str):
        source: Iterable[object] = (values,)
    elif isinstance(values, Iterable):
        source = values
    else:
        return ()
    seen: dict[str, None] = {}
    for raw in source:
        text = str(raw).strip()
        if text:
            seen.setdefault(text, None)
    return tuple(seen)


def _schema(value: object) -> JsonSchema:
    """Return a detached schema mapping, or an empty mapping for bad input."""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def _contract_from_mapping(raw: Mapping[str, Any]) -> CapabilityContract | None:
    """Build one contract from a raw mapping, returning ``None`` when unnamed."""
    task_class = str(raw.get("task_class") or raw.get("task") or "").strip()
    if not task_class:
        return None
    return CapabilityContract(
        task_class=task_class,
        input_schema=_schema(raw.get("input_schema", raw.get("inputs"))),
        output_schema=_schema(raw.get("output_schema", raw.get("outputs"))),
        preconditions=_clean_checks(raw.get("preconditions", ())),
        postconditions=_clean_checks(raw.get("postconditions", ())),
    )


def _mapping_contracts(raw: Mapping[str, Any]) -> tuple[CapabilityContract, ...]:
    """Normalise either one contract mapping or a task-class keyed mapping."""
    if "task_class" in raw or "task" in raw:
        contract = _contract_from_mapping(raw)
        return () if contract is None else (contract,)

    contracts: list[CapabilityContract] = []
    for task_class, details in raw.items():
        if not isinstance(details, Mapping):
            continue
        merged: dict[str, Any] = dict(details)
        merged.setdefault("task_class", str(task_class))
        contract = _contract_from_mapping(merged)
        if contract is not None:
            contracts.append(contract)
    return tuple(contracts)


def normalize_contracts(raw_contracts: object) -> tuple[CapabilityContract, ...]:
    """Return canonical, de-duplicated capability contracts.

    Parameters
    ----------
    raw_contracts : object
        Either a list/tuple of contract mappings, a single contract mapping, a
        task-class keyed mapping, existing :class:`CapabilityContract` objects,
        or ``None``. Malformed entries are ignored so a bad advertisement cannot
        crash the hub.

    Returns
    -------
    tuple[CapabilityContract, ...]
        Contracts keyed by first-seen task class, preserving input order.
    """
    if raw_contracts is None:
        return ()
    candidates: tuple[CapabilityContract, ...]
    if isinstance(raw_contracts, CapabilityContract):
        candidates = (raw_contracts,)
    elif isinstance(raw_contracts, Mapping):
        candidates = _mapping_contracts(raw_contracts)
    elif isinstance(raw_contracts, Iterable) and not isinstance(raw_contracts, str):
        collected: list[CapabilityContract] = []
        for item in raw_contracts:
            if isinstance(item, CapabilityContract):
                collected.append(item)
            elif isinstance(item, Mapping):
                collected.extend(_mapping_contracts(item))
        candidates = tuple(collected)
    else:
        return ()

    by_task: dict[str, CapabilityContract] = {}
    for contract in candidates:
        task_class = contract.task_class.strip()
        if not task_class or task_class in by_task:
            continue
        by_task[task_class] = CapabilityContract(
            task_class=task_class,
            input_schema=_schema(contract.input_schema),
            output_schema=_schema(contract.output_schema),
            preconditions=_clean_checks(contract.preconditions),
            postconditions=_clean_checks(contract.postconditions),
        )
    return tuple(by_task.values())
