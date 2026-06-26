# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI supervisor command
"""Supervisor process command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel.cli_processes_runtime import _run
from synapse_channel.client.supervisor import SupervisorWorker


def _cmd_supervisor(
    args: argparse.Namespace,
    *,
    runner: Callable[[Coroutine[Any, Any, None]], None] = _run,
) -> int:
    """Run an LLM-free supervisor that re-offers stalled tasks until interrupted."""
    supervisor = SupervisorWorker(
        name=args.name,
        uri=args.uri,
        idle_seconds=args.idle_seconds,
        interval=args.interval,
        token=args.token,
        ready_timeout=args.ready_timeout,
    )
    try:
        runner(supervisor.run())
    except KeyboardInterrupt:
        print(f"\n[{args.name}] supervisor stopped by user.")
    return 0
