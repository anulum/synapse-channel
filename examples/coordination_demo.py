# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — runnable end-to-end coordination demo
"""Run the installed coordination demo from a source checkout.

Use ``python examples/coordination_demo.py`` while developing from the repository,
or ``synapse demo`` after installing the package. Both entry points call
:func:`synapse_channel.demo.run_coordination_demo`, so the checkout example and
installed first-run path stay behaviorally identical.
"""

from __future__ import annotations

import asyncio

from synapse_channel.demo import _free_port, run_coordination_demo
from synapse_channel.demo_scenario import GoldenDemoResult


async def run_demo(port: int) -> GoldenDemoResult:
    """Drive the shared golden demo against ``port`` and return its evidence."""
    return await run_coordination_demo(port)


def main() -> int:
    """Run the demo on a free port for interactive source-checkout use."""
    print("=== SYNAPSE CHANNEL — coordination demo ===")
    asyncio.run(run_demo(_free_port()))
    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
