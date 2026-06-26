# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — two coding agents editing one repository without collisions
"""Run the packaged coding-fleet demo from a source checkout.

Use ``python examples/coding_agents_demo.py`` while developing from the
repository, or generate an installed demo workspace with
``synapse new coding-fleet``. Both paths call
:func:`synapse_channel.coding_fleet.run_coding_agents_demo`.
"""

from __future__ import annotations

import asyncio

from synapse_channel.coding_fleet import _free_port, run_coding_agents_demo


async def run_demo(port: int) -> list[str]:
    """Drive the shared coding-fleet demo against ``port`` and return narration."""
    return await run_coding_agents_demo(port)


def main() -> int:
    """Run the demo on a free port for interactive source-checkout use."""
    print("=== SYNAPSE CHANNEL — coding agents, no collisions ===")
    asyncio.run(run_demo(_free_port()))
    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
