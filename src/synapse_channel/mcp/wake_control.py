# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local operator control of provisioned wake deliveries
"""Inspect and recover private notification state without acquiring a native seat."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal, TypedDict

from synapse_channel.core.secure_path import assert_owner_only_file_path
from synapse_channel.mcp.http_config import HttpConfigError, load_http_auth_config
from synapse_channel.mcp.wake_admission import WakeAdmission
from synapse_channel.mcp.wake_database import MAX_WAKE_DATABASE_BYTES
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import WakeLedgerError, wake_scope_key
from synapse_channel.mcp.wake_receipts import ControlOutcome, WakeInspection, WakeReceipt


class WakeStatus(WakeInspection):
    """Describe one provisioned notification profile without signing material."""

    subject: str
    project: str
    seat: str
    hub_id: str


class WakeControlResult(TypedDict):
    """Return an exact operator outcome and the retained current receipt metadata."""

    outcome: ControlOutcome
    delivery_id: str
    failed_attempt: int
    deliveries: list[WakeReceipt]


class WakeControl:
    """Use owner-only provisioned files as local operator authority.

    Parameters
    ----------
    auth_file : pathlib.Path
        Existing private HTTP MCP grants. No remote MCP tool exposes this API.
    subject, project : str
        Exact provisioned notification profile, never an arbitrary database path.

    Notes
    -----
    Operations open only an existing authority-bound ledger beside the policy.
    They never connect as the daemon's native seat, read a signing key, create
    a replacement ledger or acknowledge native work. The running broker observes
    a committed redrive on its next cycle without a second hub connection.
    """

    def __init__(self, auth_file: Path, subject: str, project: str) -> None:
        self.auth_file = auth_file.absolute()
        self.subject = subject
        self.project = project

    def open(self) -> WakeLedger:
        """Open existing private state under its current grant and stored hub.

        Returns
        -------
        WakeLedger
            Existing authority-bound ledger, never a replacement empty file.
            Subsequent storage access can migrate its retained schema atomically.

        Raises
        ------
        WakeLedgerError
            If the profile is not authorised or existing state is unavailable,
            foreign, larger than four MiB, or cannot prove owner-only custody.
        """
        try:
            config = load_http_auth_config(self.auth_file)
        except HttpConfigError:
            raise WakeLedgerError("wake control state unavailable") from None
        admission = WakeAdmission(self.auth_file, self.subject, self.project, config)
        initial_key = wake_scope_key(
            self.subject, self.project, admission.grant.seat, admission.binding
        )
        root = self.auth_file.parent / ".synapse-mcp-wake"
        path = root / (initial_key + ".sqlite3")
        try:
            assert_owner_only_file_path(path, purpose="MCP wake control", require_single_link=True)
            if path.stat().st_size > MAX_WAKE_DATABASE_BYTES:
                raise ValueError("wake control file budget exceeded")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                rows = db.execute("SELECT scope,hub FROM meta LIMIT 2").fetchall()
            if len(rows) != 1 or rows[0][0] != initial_key:
                raise ValueError("wake control binding changed")
            scope = admission.scope(rows[0][1])
        except (OSError, ValueError, sqlite3.Error):
            raise WakeLedgerError("wake control state unavailable") from None
        return WakeLedger(root, scope)

    def status(self) -> WakeStatus:
        """Inspect local transport metadata without disclosing delivery content.

        Returns
        -------
        WakeStatus
            Fixed subject/project/seat/hub binding, plus one transactional view
            of counters and at most 64 retained transport/operator receipts.
            No endpoint URL, signing key, body or native processing ACK is returned.
        """
        ledger = self.open()
        return {
            "subject": self.subject,
            "project": self.project,
            "seat": ledger.scope.seat,
            "hub_id": ledger.scope.hub_id,
            **ledger.inspect(),
        }

    def act(
        self,
        action: Literal["redrive", "retire"],
        delivery_id: str,
        failed_attempt: int,
        now: int,
    ) -> WakeControlResult:
        """Apply an operator decision to an exact retained failure.

        Parameters
        ----------
        action : {"redrive", "retire"}
            Queue a fresh four-attempt cycle or permit explicit receipt retirement.
        delivery_id : str
            Exact notification ID from a retained operator receipt.
        failed_attempt : int
            Observed lifetime attempt fence, independent of the retry-cycle budget.
        now : int
            UTC epoch seconds for the operator action and the next due time.

        Returns
        -------
        WakeControlResult
            Applied, repeated, changed, unknown or busy outcome and the subsequent
            retained receipt view. A running broker can advance that view after
            the control transaction commits. Neither action consumes native work.
        """
        ledger = self.open()
        outcome = ledger.control_delivery(action, delivery_id, failed_attempt, now)
        return {
            "outcome": outcome,
            "delivery_id": delivery_id,
            "failed_attempt": failed_attempt,
            "deliveries": ledger.receipts(),
        }
