# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor check: does every hub have the token set in force
"""``synapse doctor --token-set PATH``: whether the hubs following a set applied it.

The check reads the state files hubs publish beside the set (see
:mod:`synapse_channel.token_set_hub_status`). It passes only when at least one
hub runs and every running hub has the file on disk in force. A hub that is
gone is never counted as healthy: it is reported, and so is the command that
removes its files.
"""

from __future__ import annotations

from pathlib import Path

from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.core.private_dir import PrivateDirError
from synapse_channel.token_set_hub_status import read_hub_states

CHECK = "token-set"
"""Name of the check in the doctor report."""


def diagnose_token_set(path: str | Path | None) -> list[Diagnosis]:
    """Return the token-set verdict for ``path``, or nothing when no set is named.

    Parameters
    ----------
    path : str or pathlib.Path or None
        The token-set file a hub follows (``synapse hub --token-set``).

    Returns
    -------
    list of Diagnosis
        No entry without a path; otherwise one ``pass``, ``warn`` or ``fail``.
    """
    if path is None:
        return []
    target = Path(path).expanduser()
    status_command = f"synapse token-set status {target}"
    try:
        states = read_hub_states(target)
    except (OSError, PrivateDirError) as exc:
        return [
            Diagnosis(
                CHECK,
                "fail",
                f"cannot read the hub states of {target}: {exc}",
                f"check the set's state directory, then run {status_command}",
            )
        ]
    running = [state for state in states if state.running]
    gone = [state.name for state in states if not state.running]
    unhealthy = [state for state in running if state.state != "applied"]
    if not running:
        return [
            Diagnosis(
                CHECK,
                "warn",
                f"no running hub reports {target}; its state is unknown",
                f"start the hub with --token-set {target}, or run "
                f"{status_command} --prune to drop the files of hubs that are gone",
            )
        ]
    if unhealthy:
        parts = [
            f"{state.name} {state.state}" + (f" ({state.reason_code})" if state.reason_code else "")
            for state in unhealthy
        ]
        return [
            Diagnosis(
                CHECK,
                "warn",
                "a running hub does not have the set on disk in force: " + ", ".join(parts),
                f"run {status_command}; a stale hub keeps its last accepted set "
                "until the file validates",
            )
        ]
    if gone:
        return [
            Diagnosis(
                CHECK,
                "warn",
                f"{len(running)} running hub(s) applied {target}; "
                f"files of {len(gone)} gone hub(s) remain: {', '.join(gone)}",
                f"run {status_command} --prune",
            )
        ]
    return [Diagnosis(CHECK, "pass", f"{len(running)} running hub(s) applied {target}")]
