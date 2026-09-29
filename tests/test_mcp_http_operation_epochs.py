# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a retried remote MCP mutation carries the epoch it was first sent with
"""Tests for :class:`synapse_channel.mcp.http_agent.OperationEpochs`.

The end-to-end replay of a granted release through the HTTP transport is
``tests/test_mcp_http_application.py``; these tests pin the bounded memory.
"""

from __future__ import annotations

from typing import Any

from synapse_channel.mcp.http_agent import MAX_OPERATION_EPOCHS, OperationEpochs


def test_the_first_epoch_is_restored_on_a_replay_without_one() -> None:
    epochs = OperationEpochs()
    first: dict[str, Any] = {"task_id": "T", "epoch": 3}
    epochs.apply("release\0op-1", first)
    replay: dict[str, Any] = {"task_id": "T"}
    epochs.apply("release\0op-1", replay)
    assert replay == first
    unrelated: dict[str, Any] = {"task_id": "T"}
    epochs.apply("release\0op-2", unrelated)
    assert "epoch" not in unrelated


def test_the_memory_is_bounded_oldest_first() -> None:
    epochs = OperationEpochs(limit=2)
    for index in range(3):
        epochs.apply(f"k{index}", {"epoch": index})
    for key, expected in (("k0", None), ("k1", 1), ("k2", 2)):
        probe: dict[str, Any] = {}
        epochs.apply(key, probe)
        assert probe.get("epoch") == expected
    assert MAX_OPERATION_EPOCHS == 1024
    assert OperationEpochs(limit=0)._limit == 1
