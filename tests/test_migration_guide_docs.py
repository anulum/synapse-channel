# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — migration guide documentation regressions

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_migration_guide_documents_guarded_upgrade_surfaces() -> None:
    guide = (ROOT / "docs" / "migration-1.0.md").read_text(encoding="utf-8")

    assert "synapse_channel.__all__" in guide
    assert "WIRE_PROTOCOL_VERSION" in guide
    assert "tests/test_wire_surface_freeze.py" in guide
    assert "tools/capability_manifest.py --check" in guide
    assert "synapse doctor" in guide


def test_migration_guide_is_linked_from_rendered_docs() -> None:
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    api_stability = (ROOT / "docs" / "api-stability.md").read_text(encoding="utf-8")

    assert "Migration to 1.0: migration-1.0.md" in mkdocs
    assert "migration-1.0.md" in index
    assert "migration-1.0.md" in api_stability
