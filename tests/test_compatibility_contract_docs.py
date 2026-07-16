# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public compatibility-contract documentation regressions
"""Freeze the pre-1.0 public compatibility contract across documentation."""

from __future__ import annotations

import re
from pathlib import Path

from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[1]
PRE_ONE_CONTRACT = (
    "Current `0.x` releases do not promise backward compatibility across minor releases."
)
CANONICAL_DOCS = (
    "README.md",
    "CONTRIBUTING.md",
    "docs/api-stability.md",
    "docs/changelog.md",
    "docs/migration-1.0.md",
    "docs/public-surface.md",
    "docs/index.md",
)
CONTRACT_ENTRY_LINKS = {
    "README.md": "docs/api-stability.md",
    "CONTRIBUTING.md": "docs/api-stability.md",
    "docs/changelog.md": "api-stability.md",
    "docs/index.md": "api-stability.md",
    "docs/migration-1.0.md": "api-stability.md",
    "docs/public-surface.md": "api-stability.md",
}
TRANSLATED_CONTRACTS = {
    "docs/readme/README.de.md": (
        "`0.x`-Releases versprechen keine Rückwärtskompatibilität über Minor-Releases"
    ),
    "docs/readme/README.es.md": (
        "releases `0.x` actuales no prometen retrocompatibilidad entre releases menores"
    ),
    "docs/readme/README.fr.md": (
        "releases `0.x` actuelles ne promettent pas de rétrocompatibilité entre releases"
    ),
    "docs/readme/README.ja.md": (
        "現在の `0.x` リリースでは、マイナーリリースをまたぐ後方互換性を保証しません"
    ),
    "docs/readme/README.ko.md": (
        "현재 `0.x` 릴리스는 마이너 릴리스 간 하위 호환성을 보장하지 않습니다"
    ),
    "docs/readme/README.pt-BR.md": (
        "releases `0.x` atuais não prometem retrocompatibilidade entre releases menores"
    ),
    "docs/readme/README.sk.md": (
        "Aktuálne `0.x` releasy nesľubujú spätnú kompatibilitu medzi minor releasmi"
    ),
    "docs/readme/README.zh-CN.md": "当前 `0.x` 发行不承诺跨次版本的向后兼容",
}
LEGACY_TRANSLATED_FRAGMENTS = (
    "innerhalb einer Major-Version",
    "retrocompatibles dentro de una versión mayor",
    "rétrocompatibles au sein d'une version majeure",
    "メジャーバージョン内で後方互換",
    "메이저 버전 안에서",
    "retrocompatíveis dentro de uma versão maior",
    "spätne kompatibilné v rámci major verzie",
    "在主版本内保持向后",
)


def _read(relative_path: str) -> str:
    """Return one repository document as UTF-8 text."""
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _normalized(relative_path: str) -> str:
    """Return one document with Markdown line wrapping collapsed."""
    return " ".join(_read(relative_path).split())


def test_every_canonical_document_states_the_same_pre_one_contract() -> None:
    """Require the exact pre-1.0 promise in every canonical public document."""
    for relative_path in CANONICAL_DOCS:
        assert PRE_ONE_CONTRACT in _normalized(relative_path), relative_path


def test_canonical_documents_remove_the_old_major_version_promise() -> None:
    """Reject the wording that incorrectly made all 0.x releases compatible."""
    combined = "\n".join(_normalized(relative_path).casefold() for relative_path in CANONICAL_DOCS)
    assert "backwards-compatible within a major version" not in combined
    assert "backward-compatible within a major version" not in combined


def test_translated_readmes_state_the_localized_contract_and_link_canonically() -> None:
    """Keep every translated release section aligned with the English contract."""
    actual_readmes = {
        path.relative_to(ROOT).as_posix() for path in (ROOT / "docs" / "readme").glob("README.*.md")
    }
    assert actual_readmes == set(TRANSLATED_CONTRACTS)

    for relative_path, required_fragment in TRANSLATED_CONTRACTS.items():
        text = _normalized(relative_path)
        assert required_fragment in text, relative_path
        assert "../api-stability.md" in text, relative_path
        assert "`WIRE_PROTOCOL_VERSION`" in text, relative_path
        assert "`1.0.0`" in text, relative_path


def test_translated_readmes_remove_every_legacy_major_version_promise() -> None:
    """Reject all localized variants of the superseded compatibility promise."""
    combined = "\n".join(_normalized(relative_path) for relative_path in TRANSLATED_CONTRACTS)
    for fragment in LEGACY_TRANSLATED_FRAGMENTS:
        assert fragment not in combined, fragment


def test_canonical_policy_distinguishes_package_and_wire_versioning() -> None:
    """Cross-check the written policy against the live wire-version constant."""
    stability = _read("docs/api-stability.md")
    contributing = _read("CONTRIBUTING.md")
    migration = _read("docs/migration-1.0.md")

    assert f"currently `{WIRE_PROTOCOL_VERSION}`" in stability
    assert "wire-incompatible change" in stability
    assert "package major release" in stability
    assert "`WIRE_PROTOCOL_VERSION`" in contributing
    assert "`WIRE_PROTOCOL_VERSION`" in migration


def test_public_entry_points_link_to_the_canonical_contract() -> None:
    """Require all public release entry points to cross-link the policy."""
    for relative_path, contract_link in CONTRACT_ENTRY_LINKS.items():
        assert contract_link in _read(relative_path), relative_path

    assert "migration-1.0.md" in _read("docs/api-stability.md")


def test_current_package_line_remains_pre_one_until_contract_migration() -> None:
    """Force the 1.0 release cut to deliberately replace this pre-1.0 contract."""
    pyproject = _read("pyproject.toml")
    pattern = r'^version = "(?P<version>[0-9]+(?:\.[0-9]+){2})"$'
    match = re.search(pattern, pyproject, re.MULTILINE)

    assert match is not None
    assert match.group("version").startswith("0.")
