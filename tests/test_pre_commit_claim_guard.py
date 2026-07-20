# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — staged claim gate pre-commit contract

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".pre-commit-config.yaml"


def _hook_block(hook_id: str) -> str:
    text = CONFIG.read_text(encoding="utf-8")
    start = text.index(f"      - id: {hook_id}\n")
    end = text.find("      - id: ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_staged_claim_hook_is_always_run_and_reads_git_itself() -> None:
    block = _hook_block("staged-claim-coverage")
    assert "entry: python tools/run_staged_claim_hook.py" in block
    assert ".venv/bin/python" not in block
    assert "language: system" in block
    assert "stages: [pre-commit]" in block
    assert "always_run: true" in block
    assert "pass_filenames: false" in block
    assert "files:" not in block
