# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI compatibility surface
"""Compatibility exports for long-running process CLI commands."""

from __future__ import annotations

from synapse_channel.cli_processes_hub import _cmd_hub
from synapse_channel.cli_processes_parsers import _add_logging_args, add_parsers
from synapse_channel.cli_processes_runtime import _run
from synapse_channel.cli_processes_supervisor import _cmd_supervisor
from synapse_channel.cli_processes_team import _cmd_team
from synapse_channel.cli_processes_worker import _LOCAL_HOSTS, _cmd_worker, _egress_warning

__all__ = [
    "_LOCAL_HOSTS",
    "_add_logging_args",
    "_cmd_hub",
    "_cmd_supervisor",
    "_cmd_team",
    "_cmd_worker",
    "_egress_warning",
    "_run",
    "add_parsers",
]
