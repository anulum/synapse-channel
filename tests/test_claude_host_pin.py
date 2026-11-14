# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the plugin's tested host is one version everywhere it is named
"""The installer, the integration catalogue, the compatibility matrix, the CI host package and
the CI step that checks it all name the same tested host, and the catalogue's rollback is the
previous admitted one."""

from __future__ import annotations

import json
import re
from pathlib import Path

from synapse_channel.claude_plugin_install import TESTED_HOST_VERSION
from synapse_channel.integration_catalog import host_capability

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "@anthropic-ai/claude-code"


def _json(path: str) -> dict[str, object]:
    loaded = json.loads((ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_every_place_that_names_the_tested_host_names_the_same_version() -> None:
    manifest = _json("integrations/claude-code/package.json")
    lock = _json("integrations/claude-code/package-lock.json")
    matrix = _json("integrations/vendor-watch/compatibility.json")
    packages = lock["packages"]
    assert isinstance(packages, dict)
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    checked = re.findall(
        r"claude-bin/claude\" --version\)\" = '([0-9.]+) \(Claude Code\)'", workflow
    )

    assert manifest["dependencies"] == {PACKAGE: TESTED_HOST_VERSION}
    assert packages[""]["dependencies"] == {PACKAGE: TESTED_HOST_VERSION}
    assert packages[f"node_modules/{PACKAGE}"]["version"] == TESTED_HOST_VERSION
    surfaces = matrix["surfaces"]
    assert isinstance(surfaces, dict)
    assert surfaces["claude-code"]["verified_version"] == TESTED_HOST_VERSION
    assert checked == [TESTED_HOST_VERSION]
    assert host_capability("claude-code").verified_version == TESTED_HOST_VERSION


def test_the_rollback_is_the_previously_admitted_host() -> None:
    capability = host_capability("claude-code")

    assert (TESTED_HOST_VERSION, capability.rollback_version) == ("2.1.311", "2.1.284")
