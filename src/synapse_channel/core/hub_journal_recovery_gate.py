# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed live gate for incomplete journal recovery
"""Refuse hub mutations while replay has quarantined durable event rows."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from synapse_channel.core.acl_enforcement import GATED_MUTATIONS
from synapse_channel.core.event_row_recovery import CorruptEventRow
from synapse_channel.core.protocol import MessageType

SendJson = Callable[[Any, dict[str, Any]], Awaitable[None]]
SystemMessage = Callable[..., dict[str, Any]]


class HubJournalRecoveryGate:
    """Deny state-changing frames when durable replay is incomplete.

    Parameters
    ----------
    corrupt_rows : Sequence[CorruptEventRow]
        Quarantined rows found during startup replay.
    send_json : SendJson
        Hub callback that sends one private response to the current socket.
    system : SystemMessage
        Hub callback that builds a stamped system envelope.
    """

    def __init__(
        self,
        corrupt_rows: Sequence[CorruptEventRow],
        *,
        send_json: SendJson,
        system: SystemMessage,
    ) -> None:
        self.corrupt_rows = tuple(corrupt_rows)
        self._send_json = send_json
        self._system = system

    async def refuse_mutation(self, sender: str, msg_type: str, websocket: Any) -> bool:
        """Return whether a mutating frame was privately refused.

        Read/query/heartbeat frames remain available so operators can inspect a
        degraded hub. Every type in the central ACL mutation registry is denied,
        including chat because it broadcasts and appends durable state.

        Parameters
        ----------
        sender, msg_type : str
            Resolved sender and normalized inbound frame type.
        websocket : Any
            Socket that receives the private denial.

        Returns
        -------
        bool
            ``True`` after a denial was sent; ``False`` when routing may continue.
        """
        if not self.corrupt_rows or msg_type not in GATED_MUTATIONS:
            return False
        await self._send_json(
            websocket,
            self._system(
                "Mutation refused: durable journal recovery is required. "
                "Inspect degraded health and archive/remove settled corrupt rows "
                "with the explicit `synapse compact --drop-corrupt` workflow.",
                msg_type=MessageType.ERROR,
                target=sender,
                journal_recovery_required=True,
                corrupt_rows=len(self.corrupt_rows),
                first_corrupt_seq=self.corrupt_rows[0].seq,
            ),
        )
        return True
