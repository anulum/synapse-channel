# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor check for a project's orphaned mailboxes
"""Warn when the doctor's project has directed mail waiting for names that went quiet.

The check runs only when ``synapse doctor`` is given the hub's store
(``--db-path``). It reads the journal read-only, counts a name as active when
the live roster shows it connected, and applies the same rule as
``synapse mailbox orphans``: pending directed mail and no journalled activity
for :data:`~synapse_channel.cli_mailbox.DEFAULT_DORMANT_DAYS` days.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from pathlib import Path

from synapse_channel.cli_mailbox import DEFAULT_DORMANT_DAYS, read_mailbox_standings
from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.core.event_row_mac import RowMacError
from synapse_channel.core.mailbox_orphans import orphaned_mailboxes
from synapse_channel.terminal_text import shell_command_arg, shell_long_option, terminal_text

CHECK = "mailbox-orphans"
"""Name of the doctor check."""


def diagnose_orphaned_mailboxes(
    event_store_path: str | Path | None,
    *,
    key_file: str | Path | None,
    project: str,
    roster: Sequence[str] | None,
    now: float,
    dormant_after_days: float = DEFAULT_DORMANT_DAYS,
) -> Diagnosis | None:
    """Report the project's orphaned mailboxes in the hub store, if one was named.

    Parameters
    ----------
    event_store_path : str or pathlib.Path or None
        The hub's event store; ``None`` skips the check.
    key_file : str or pathlib.Path or None
        Owner-only SQLCipher key of an encrypted store.
    project : str
        The doctor identity's project; only its names are counted.
    roster : Sequence[str] or None
        Names the hub shows connected now; ``None`` when the hub was not reached.
    now : float
        Current time, seconds since the epoch.
    dormant_after_days : float, optional
        Days without activity before a name with pending mail counts.

    Returns
    -------
    Diagnosis or None
        ``pass`` without orphans, ``warn`` with them or when the store cannot be
        read, and ``None`` when no store was named.
    """
    if event_store_path is None:
        return None
    store = Path(event_store_path)
    try:
        standings = read_mailbox_standings(store, key_file=key_file)
    except (ValueError, OSError, RuntimeError, RowMacError) as exc:
        return Diagnosis(
            check=CHECK,
            status="warn",
            detail=f"cannot read the hub store for orphaned mailboxes: {type(exc).__name__}",
            remedy="name the hub's own store with --db-path (and --db-key-file if encrypted)",
        )
    orphans = orphaned_mailboxes(
        standings,
        now=now,
        dormant_after_days=dormant_after_days,
        active=tuple(roster or ()),
        project=project,
    )
    quiet = f"quiet for {dormant_after_days:g} days or more"
    shown_project = terminal_text(project)
    if not orphans:
        return Diagnosis(
            check=CHECK,
            status="pass",
            detail=f"no orphaned mailboxes in project {shown_project} ({quiet})",
        )
    pending = sum(item.pending for item in orphans)
    roster_note = "" if roster is not None else "; the live roster was not read"
    db_text = shell_command_arg(str(store))
    return Diagnosis(
        check=CHECK,
        status="warn",
        detail=(
            f"{pending} pending messages in {len(orphans)} orphaned mailboxes "
            f"of project {shown_project} ({quiet}{roster_note})"
        ),
        remedy=(
            f"list them with synapse mailbox orphans {db_text} "
            f"{shell_long_option('--project', project)} --live; as the name, its custodian "
            "or an operator, hand each over with synapse mailbox handover NAME --to SUCCESSOR "
            "--reason TEXT or close it with synapse mailbox close NAME --reason TEXT"
        ),
    )


def add_orphan_doctor_argument(parser: argparse.ArgumentParser) -> None:
    """Register ``--mailbox-orphans`` on the ``doctor`` parser.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The ``doctor`` subparser.
    """
    parser.add_argument(
        "--mailbox-orphans",
        action="store_true",
        help=(
            "Read the hub store at --db-path and warn when this project has directed "
            "mail waiting for names quiet for 7 days or more (live roster names count "
            "as active)."
        ),
    )


def orphan_diagnoses(
    asked: bool,
    event_store_path: str | Path | None,
    *,
    key_file: str | Path | None,
    project: str,
    roster: Sequence[str] | None,
) -> list[Diagnosis]:
    """Return the orphan check's verdict when ``doctor --mailbox-orphans`` asked for it.

    Parameters
    ----------
    asked : bool
        Whether ``--mailbox-orphans`` was given.
    event_store_path : str or pathlib.Path or None
        The hub store from ``--db-path``.
    key_file : str or pathlib.Path or None
        Owner-only SQLCipher key of an encrypted store.
    project : str
        The doctor identity's project.
    roster : Sequence[str] or None
        Names the hub shows connected now.

    Returns
    -------
    list[Diagnosis]
        The single verdict, or nothing when the check was not asked for or no
        store was named.
    """
    if not asked:
        return []
    found = diagnose_orphaned_mailboxes(
        event_store_path, key_file=key_file, project=project, roster=roster, now=time.time()
    )
    return [] if found is None else [found]
