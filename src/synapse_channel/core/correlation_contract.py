# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the frozen correlation contract, version 1
"""Name, schema and exact attribute set of correlation contract v1.

A correlated projection places five kinds of recorded fact into the trace of
the task they belong to: a model call, a policy decision, an operation, its
outcome and its verification. This module freezes what each such span may
carry. Any change here (a key added, removed, renamed or retyped, a family
added, the schema URL moved) is a new contract version beside this one,
never an edit of v1.

The three ``gen_ai.*`` names are the OpenTelemetry GenAI attributes as the
semantic-conventions release 1.41.1 defines them. From 1.42.0 the GenAI
conventions moved to their own repository, which had not released when this
contract was frozen, so 1.41.1 is the newest release that defines the names.
Upstream marks them ``development``; their stability for readers of this
contract comes from the freeze, not from upstream.

Never part of any span: prompts, completions, messages, tool arguments and
results, operation targets, outcome and verification observations, and
verification evidence digests. The hub journal keeps those fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

CORRELATION_CONTRACT = "synapse.correlation.v1"
"""Name of this contract, stamped on every correlated projection."""

CORRELATION_SCHEMA_URL = "https://opentelemetry.io/schemas/1.41.1"
"""OpenTelemetry schema the contract's ``gen_ai.*`` names belong to."""

CONTRACT_RESOURCE_ATTRIBUTE = "synapse.correlation.contract"
"""Resource attribute that names the contract on an exported correlated projection."""

FAMILY_MODEL_CALL = "model_call"
FAMILY_POLICY_DECISION = "policy_decision"
FAMILY_OPERATION = "operation"
FAMILY_OUTCOME = "outcome"
FAMILY_VERIFICATION = "verification"

TYPE_STRING = "string"
TYPE_INT = "int"
TYPE_DOUBLE = "double"

GENAI_REQUEST_MODEL = "gen_ai.request.model"
GENAI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GENAI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"


@dataclass(frozen=True)
class ContractKey:
    """One attribute a span family may carry.

    Attributes
    ----------
    key : str
        The attribute name.
    kind : str
        ``string``, ``int`` or ``double``.
    required : bool
        Whether every span of the family carries it.
    """

    key: str
    kind: str
    required: bool


def _keys(*rows: tuple[str, str, bool]) -> tuple[ContractKey, ...]:
    """Return the common keys followed by the family's own rows."""
    common = (
        ContractKey("service.name", TYPE_STRING, True),
        ContractKey("synapse.kind", TYPE_STRING, True),
        ContractKey("synapse.seq", TYPE_STRING, True),
        ContractKey("synapse.task_id", TYPE_STRING, True),
    )
    return common + tuple(ContractKey(key, kind, required) for key, kind, required in rows)


CORRELATION_CONTRACT_V1: Mapping[str, tuple[ContractKey, ...]] = MappingProxyType(
    {
        FAMILY_MODEL_CALL: _keys(
            (GENAI_REQUEST_MODEL, TYPE_STRING, True),
            (GENAI_USAGE_INPUT_TOKENS, TYPE_INT, True),
            (GENAI_USAGE_OUTPUT_TOKENS, TYPE_INT, True),
            ("synapse.usage.calls", TYPE_INT, True),
            ("synapse.usage.cost_usd", TYPE_DOUBLE, False),
            ("synapse.agent", TYPE_STRING, False),
        ),
        FAMILY_POLICY_DECISION: _keys(
            ("synapse.policy.decision", TYPE_STRING, True),
            ("synapse.policy.reason", TYPE_STRING, False),
            ("synapse.claimant", TYPE_STRING, False),
        ),
        FAMILY_OPERATION: _keys(
            ("synapse.operation.id", TYPE_STRING, True),
            ("synapse.operation.effect", TYPE_STRING, True),
            ("synapse.operation.snapshot_epoch", TYPE_INT, True),
            ("synapse.operation.budget_remaining", TYPE_INT, False),
            ("synapse.operation.sender", TYPE_STRING, True),
        ),
        FAMILY_OUTCOME: _keys(
            ("synapse.operation.id", TYPE_STRING, True),
            ("synapse.outcome.state", TYPE_STRING, True),
            ("synapse.outcome.boundary", TYPE_STRING, True),
            ("synapse.outcome.external_effect", TYPE_STRING, True),
            ("synapse.operation.sender", TYPE_STRING, True),
        ),
        FAMILY_VERIFICATION: _keys(
            ("synapse.operation.id", TYPE_STRING, True),
            ("synapse.verification.verifier", TYPE_STRING, True),
            ("synapse.verification.communication", TYPE_STRING, True),
            ("synapse.verification.effect", TYPE_STRING, True),
            ("synapse.verification.result", TYPE_STRING, True),
            ("synapse.operation.sender", TYPE_STRING, True),
        ),
    }
)
"""Every key each span family of contract v1 may carry, with its type."""


def _kind_of(value: object) -> str | None:
    """Return the contract type of ``value``, or ``None`` for any other value."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return TYPE_STRING
    if isinstance(value, int):
        return TYPE_INT
    if isinstance(value, float):
        return TYPE_DOUBLE
    return None


def contract_mismatches(family: str, attributes: Mapping[str, object]) -> tuple[str, ...]:
    """Return how ``attributes`` depart from contract v1 for ``family``.

    Parameters
    ----------
    family : str
        One of the five family names.
    attributes : Mapping[str, object]
        A span's attributes.

    Returns
    -------
    tuple[str, ...]
        One sentence per departure, in key order; empty when the span conforms.
    """
    rows = CORRELATION_CONTRACT_V1.get(family)
    if rows is None:
        return (f"{family} is not a family of {CORRELATION_CONTRACT}",)
    allowed = {row.key: row for row in rows}
    problems: list[str] = []
    for key in sorted(set(attributes) | set(allowed)):
        row = allowed.get(key)
        if row is None:
            problems.append(f"{key} is not part of the {family} family")
        elif key not in attributes:
            if row.required:
                problems.append(f"{key} is required for the {family} family")
        elif _kind_of(attributes[key]) != row.kind:
            problems.append(f"{key} must be {row.kind}")
    return tuple(problems)
