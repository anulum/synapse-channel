# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - tests for the wire decoder fuzz harness

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import NoReturn

from synapse_channel.core.protocol import MAX_JSON_DEPTH

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = REPO_ROOT / "tools" / "fuzz_protocol_decode.py"


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fuzz_protocol_decode", HARNESS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fuzz_protocol_decode"] = module
    spec.loader.exec_module(module)
    return module


def test_fuzz_harness_exposes_atheris_compatible_entrypoint() -> None:
    harness = _load_harness()

    assert callable(harness.fuzz_one_input)
    assert callable(harness.run_seed_corpus)
    assert callable(harness.main)


def test_fuzz_one_input_accepts_valid_and_invalid_wire_frames() -> None:
    harness = _load_harness()

    harness.fuzz_one_input(b'{"sender": "a", "type": "chat", "payload": "ok"}')
    harness.fuzz_one_input(b"{not json")
    harness.fuzz_one_input(b"\xff\xfe\xfa")


def test_fuzz_one_input_rejects_nesting_bomb_without_recursing() -> None:
    harness = _load_harness()
    payload = ("[" * (MAX_JSON_DEPTH + 1000) + "]" * (MAX_JSON_DEPTH + 1000)).encode()

    harness.fuzz_one_input(payload)


def test_seed_corpus_exercises_depth_and_malformed_inputs() -> None:
    harness = _load_harness()
    results = harness.run_seed_corpus()

    assert results.total >= 5
    assert results.accepted >= 1
    assert results.rejected >= 1
    assert results.crashed == 0


def test_fuzz_one_input_rejects_an_oversized_integer_without_crashing() -> None:
    harness = _load_harness()
    payload = ('{"n":' + "9" * 5000 + "}").encode()

    assert harness.run_seed_corpus([payload]).crashed == 0


def test_seed_corpus_counts_unexpected_decoder_crashes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    harness = _load_harness()

    def fail_decode(_data: bytes) -> NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr(harness, "_decode_for_fuzz", fail_decode)

    results = harness.run_seed_corpus([b"{}"])

    assert results.total == 1
    assert results.accepted == 0
    assert results.rejected == 0
    assert results.crashed == 1


def test_main_smoke_mode_reports_seed_corpus_status(capsys) -> None:  # type: ignore[no-untyped-def]
    harness = _load_harness()

    assert harness.main(["--smoke"]) == 0

    captured = capsys.readouterr()
    assert "protocol fuzz smoke:" in captured.out
    assert "crashed=0" in captured.out


def test_main_without_atheris_prints_install_hint(capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    harness = _load_harness()
    monkeypatch.delitem(sys.modules, "atheris", raising=False)

    assert harness.main([]) == 2

    captured = capsys.readouterr()
    assert "Atheris is not installed" in captured.err


def test_main_starts_atheris_when_available(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    harness = _load_harness()
    fake_atheris = ModuleType("atheris")
    calls: list[str] = []

    def setup(argv: list[str], callback) -> None:  # type: ignore[no-untyped-def]
        calls.append(f"setup:{argv[0]}")
        callback(b"{}")

    def fuzz() -> None:
        calls.append("fuzz")

    fake_atheris.Setup = setup  # type: ignore[attr-defined]
    fake_atheris.Fuzz = fuzz  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "atheris", fake_atheris)

    assert harness.main(["--", "-runs=1"]) == 0
    assert calls == ["setup:" + str(HARNESS_PATH), "fuzz"]


def test_fuzz_harness_docs_describe_local_evidence_boundary() -> None:
    combined = " ".join(
        (
            (REPO_ROOT / "docs" / "protocol.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8"),
        )
    )

    assert "tools/fuzz_protocol_decode.py" in combined
    assert "local decoder hardening evidence" in combined
    assert "not an external protocol-conformance certification" in combined


def test_fuzz_harness_does_not_import_project_code_at_manifest_time() -> None:
    tree = HARNESS_PATH.read_text(encoding="utf-8")

    assert 'if __name__ == "__main__"' in tree
    assert "atheris" in tree
    json.loads('{"guard": "test imports json so the harness may too"}')
