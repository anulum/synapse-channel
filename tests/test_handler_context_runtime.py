# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — handler context stays a typing-only contract at runtime
"""Importing the handler context module adds no runtime class and no hub import."""

from __future__ import annotations

import json
import subprocess
import sys

PROBE = """
import json, sys
before = set(sys.modules)
import synapse_channel.core.handler_context as module
loaded = sorted(name for name in set(sys.modules) - before if name.startswith("synapse_channel"))
public = sorted(name for name in vars(module) if not name.startswith("_"))
print(json.dumps({"loaded": loaded, "public": public}))
"""


def test_runtime_import_exposes_no_contract_class_and_loads_no_hub_module() -> None:
    """The Protocol exists only for the type checker; runtime sees two names only."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=30, check=True
    )
    observed = json.loads(result.stdout)
    assert observed["public"] == ["TYPE_CHECKING", "annotations"]
    # Only the module and its two parent packages load; no hub, state or journal module.
    assert observed["loaded"] == [
        "synapse_channel",
        "synapse_channel.core",
        "synapse_channel.core.handler_context",
    ]
