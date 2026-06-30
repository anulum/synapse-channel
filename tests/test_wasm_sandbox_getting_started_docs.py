# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the WASM sandbox getting-started guide stays discoverable and true

from __future__ import annotations

import argparse
import re
from pathlib import Path

from synapse_channel.cli_sandbox import add_parsers

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "wasm-sandbox-getting-started.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    return " ".join(_read(path).lower().split())


def test_guide_is_publicly_discoverable() -> None:
    """The guide must be linked from the mkdocs nav and the README."""
    nav = _read(ROOT / "mkdocs.yml")
    assert "WASM sandbox getting started: wasm-sandbox-getting-started.md" in nav
    assert "docs/wasm-sandbox-getting-started.md" in _read(ROOT / "README.md")


def test_guide_walks_the_full_operator_workflow() -> None:
    """It must cover the optional extra and every step from source to a receipt."""
    text = _collapsed(GUIDE)
    assert "pip install 'synapse-channel[wasm]'" in text
    assert "wasm32-unknown-unknown" in text
    for verb in ("synapse sandbox validate", "synapse sandbox test", "synapse sandbox run"):
        assert verb in text
    assert "--approve" in text
    assert "run receipt" in text
    assert "no ambient authority" in text


def test_guide_documents_the_test_exit_code_contract() -> None:
    """The pre-flight gate is only useful if its 0/1/2 exit codes are documented."""
    text = _collapsed(GUIDE)
    assert "exits `0`" in text
    assert "`1`" in text and "`2`" in text
    assert "not ready" in text


def test_guide_does_not_embed_a_full_content_digest() -> None:
    """A reader's build differs, so the guide must use a placeholder, not a fixed digest.

    A hardcoded 64-hex ``sha256:`` would also be misleading (it would never match the
    reader's own module) — the guide shows the command to compute it instead.
    """
    assert re.search(r"sha256:[a-f0-9]{64}", _read(GUIDE)) is None
    assert "paste-your-digest-here" in _read(GUIDE)


def _sandbox_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser


def _parse(*argv: str) -> argparse.Namespace:
    return _sandbox_parser().parse_args(["sandbox", *argv])


def test_guide_commands_parse_against_the_real_cli() -> None:
    """Every command shape the guide shows must be accepted by the live CLI parser."""
    assert _parse("validate", "greet.manifest.json").func is not None
    assert _parse("test", "greet.wasm", "--manifest", "greet.manifest.json").func is not None
    assert (
        _parse(
            "test", "greet.wasm", "--manifest", "greet.manifest.json", "--entrypoint", "main"
        ).func
        is not None
    )
    assert (
        _parse("run", "greet.wasm", "--manifest", "greet.manifest.json", "--approve").func
        is not None
    )


def test_guide_only_references_real_sandbox_verbs() -> None:
    """A verb named in the guide that the CLI does not register is drift; catch it.

    The guide must reference exactly the three shipped verbs, and each must resolve to a
    real handler on the live parser via a minimal valid invocation.
    """
    minimal = {
        "validate": ("validate", "m.json"),
        "test": ("test", "t.wasm", "--manifest", "m.json"),
        "run": ("run", "t.wasm", "--manifest", "m.json", "--approve"),
    }
    verbs_in_guide = set(re.findall(r"synapse sandbox (\w+)", _read(GUIDE)))
    assert verbs_in_guide == set(minimal)
    for verb in verbs_in_guide:
        assert _parse(*minimal[verb]).func is not None
