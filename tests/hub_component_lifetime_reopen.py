# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — native unconfigured checkpoint refusal fixture
"""Expose the real factory refusal under the caller's Python optimisation flags.

The first argument is a test-owned journal path. The component's ValueError is
intentionally uncaught so its native process exit and diagnostic are observed.
"""

import json
import sys
from pathlib import Path

from synapse_channel.core.hub_component_lifetime import build_lifetime
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.persistence import EventStore

path = Path(sys.argv[1])
with EventStore(path) as journal:
    config = HubConfig(journal=journal, anti_rollback_checkpoint=False)
    lifetime = build_lifetime(config, None, None)
    print(json.dumps({"optimization": sys.flags.optimize}))
    lifetime.reopen_checkpoint(journal)
