# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bound wake relay entry point for local waker callers
"""Construct real wake transports only after exact owner binding checks."""

from __future__ import annotations

import socket
from pathlib import Path

from synapse_channel.agent_tmux import AgentTmuxConfig
from synapse_channel.core.event_row_mac import RowMacError, load_or_create_row_mac_key
from synapse_channel.core.persistence_receipt_view import ReceiptProjectionError
from synapse_channel.wake_adapters import WakeAdapter, WakeTarget, validate_deadline
from synapse_channel.wake_adapters.codex_queue import CodexQueueAdapter
from synapse_channel.wake_adapters.harness_waiter import (
    HarnessWaiterAdapter,
    ReceiptOf,
    ReceiptView,
    journal_receipts,
)
from synapse_channel.wake_adapters.pane_bridge import PaneBridgeAdapter
from synapse_channel.wake_ranking import order
from synapse_channel.wake_relay import RelayResult, relay
from synapse_channel.wake_relay_config import RelayBinding, load_relay_binding
from synapse_channel.waker_config import DESIRED_ARMED, load_waker_config
from synapse_channel.waker_transition import transition_state


def _pane_config(target: WakeTarget) -> AgentTmuxConfig:
    """Require an armed existing pane config and settled lifecycle control."""
    config = load_waker_config(target.seat)
    if (
        config.desired_state != DESIRED_ARMED
        or transition_state(target.seat, generation=config.generation) == "uncertain"
    ):
        raise ValueError("the pane waker is inhibited or its control is uncertain")
    return config.agent_tmux_config()


def _adapters(binding: RelayBinding) -> tuple[WakeAdapter, ...]:
    """Build only the configured adapters after validation of their required paths."""
    result: list[WakeAdapter] = []
    for name in order(binding.marks_dir, binding.target.seat, binding.adapters):
        if name == "harness_waiter":
            if binding.journal_db is None:
                raise ValueError("harness_waiter requires journal_db")
            result.append(
                HarnessWaiterAdapter(
                    _receipt_reader(binding, binding.journal_db),
                    expected_target=binding.target,
                )
            )
        elif name == "native_queue":
            if binding.codex_bin is None:
                raise ValueError("native_queue requires codex_bin")
            result.append(CodexQueueAdapter(binding.codex_bin))
        else:
            result.append(_pane_adapter(binding.target))
    return tuple(result)


def _pane_adapter(target: WakeTarget) -> PaneBridgeAdapter:
    """Capture the already bound target's existing armed pane configuration."""
    config = _pane_config(target)
    return PaneBridgeAdapter(lambda _: config)


def _receipt_reader(binding: RelayBinding, db: Path) -> ReceiptOf:
    """Read an enrolled row key without creation and fail uncertain if unavailable."""

    def receipt_of(message_seq: int) -> ReceiptView | None:
        """Verify one receipt using its existing operator-selected row key."""
        row_key = None
        if binding.row_mac_key_file is not None:
            try:
                row_key = load_or_create_row_mac_key(
                    binding.row_mac_key_file, current_max_seq=0, log_has_macs=True
                )
            except RowMacError as exc:
                raise ReceiptProjectionError("receipt row key is unavailable") from exc
        return journal_receipts(db, key_file=binding.key_file, row_mac=row_key)(message_seq)

    return receipt_of


def relay_bound_wake(
    binding_file: Path, target: WakeTarget, wake_id: int, *, deadline_seconds: float = 30.0
) -> RelayResult:
    """Relay an actual hub message using the caller's exact declared native target.

    No marks, adapter calls or receipt reads occur on a binding, host, cursor or
    deadline mismatch. Successful receipt observation remains transport evidence;
    the binding does not authenticate vendor session liveness or model consumption.

    Raises
    ------
    ValueError, OSError
        The binding, local execution host or requested cursor is invalid.
    """
    validate_deadline(deadline_seconds)
    if isinstance(wake_id, bool) or not isinstance(wake_id, int) or wake_id < 1:
        raise ValueError("wake_id must be a positive integer journal sequence")
    binding = load_relay_binding(binding_file)
    if target != binding.target:
        raise ValueError("wake target differs from its owner binding")
    if target.execution_host != socket.gethostname():
        raise ValueError("wake target is bound to another execution host")
    return relay(
        binding.marks_dir, target, wake_id, _adapters(binding), deadline_seconds=deadline_seconds
    )
