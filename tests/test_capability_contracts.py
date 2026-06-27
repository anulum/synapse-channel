# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for formal capability contract normalization

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.capability_contracts import (
    CapabilityContract,
    normalize_contracts,
)


def test_capability_contract_as_dict_is_manifest_stable() -> None:
    contract = CapabilityContract(
        task_class="chat",
        input_schema={"type": "object", "required": ["prompt"]},
        output_schema={"type": "object", "required": ["answer"]},
        preconditions=("token budget declared",),
        postconditions=("answer cites evidence",),
    )

    assert contract.as_dict() == {
        "task_class": "chat",
        "input_schema": {"type": "object", "required": ["prompt"]},
        "output_schema": {"type": "object", "required": ["answer"]},
        "preconditions": ["token budget declared"],
        "postconditions": ["answer cites evidence"],
    }


def test_normalize_contracts_accepts_list_and_cleans_fields() -> None:
    contracts = normalize_contracts(
        [
            {
                "task_class": " chat ",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "string"},
                "preconditions": [" ready ", "", "ready"],
                "postconditions": (" done ", "done"),
            },
            {"task_class": "chat", "input_schema": {"ignored": True}},
        ]
    )

    assert contracts == (
        CapabilityContract(
            task_class="chat",
            input_schema={"type": "object"},
            output_schema={"type": "string"},
            preconditions=("ready",),
            postconditions=("done",),
        ),
    )


def test_normalize_contracts_accepts_task_class_mapping() -> None:
    contracts = normalize_contracts(
        {
            "rule": {
                "input_schema": {"type": "object"},
                "output_schema": {"type": "boolean"},
                "preconditions": ["deterministic"],
            }
        }
    )

    assert contracts == (
        CapabilityContract(
            task_class="rule",
            input_schema={"type": "object"},
            output_schema={"type": "boolean"},
            preconditions=("deterministic",),
        ),
    )


def test_normalize_contracts_accepts_aliases_and_existing_contracts() -> None:
    existing = CapabilityContract(
        task_class=" chat ",
        input_schema={"type": "object", "nested": {"ok": True}},
        preconditions=(" ready ",),
    )
    contracts = normalize_contracts(
        [
            existing,
            {
                "task": "reason",
                "inputs": {"type": "string"},
                "outputs": {"type": "object"},
            },
        ]
    )

    assert contracts == (
        CapabilityContract(
            task_class="chat",
            input_schema={"type": "object", "nested": {"ok": True}},
            preconditions=("ready",),
        ),
        CapabilityContract(
            task_class="reason",
            input_schema={"type": "string"},
            output_schema={"type": "object"},
        ),
    )


def test_normalize_contracts_handles_empty_and_scalar_inputs() -> None:
    direct = CapabilityContract(task_class=" direct ")
    assert normalize_contracts(direct) == (CapabilityContract(task_class="direct"),)
    assert normalize_contracts(None) == ()
    assert normalize_contracts("bad") == ()
    assert normalize_contracts({"bad": "not-a-contract"}) == ()
    assert normalize_contracts({"": {}}) == ()
    assert normalize_contracts({"task_class": ""}) == ()
    assert normalize_contracts({"task_class": "chat", "preconditions": 7}) == (
        CapabilityContract(task_class="chat"),
    )


def test_normalize_contracts_drops_malformed_entries() -> None:
    contracts = normalize_contracts(
        [
            "bad",
            {"task_class": "   ", "input_schema": {"type": "object"}},
            {"task_class": "chat", "input_schema": "not-a-schema"},
            {"task_class": "reason", "postconditions": "single check"},
        ]
    )

    assert contracts == (
        CapabilityContract(
            task_class="chat",
            output_schema={},
        ),
        CapabilityContract(
            task_class="reason",
            postconditions=("single check",),
        ),
    )


def test_capability_contract_docs_are_wired() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    protocol = Path("docs/protocol.md").read_text(encoding="utf-8")
    cli = Path("docs/cli.md").read_text(encoding="utf-8")
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")

    assert "capability contracts" in readme
    assert "`contracts`" in protocol
    assert "input_schema" in protocol
    assert "synapse manifest" in cli and "contracts" in cli
    assert "capability contracts" in changelog
