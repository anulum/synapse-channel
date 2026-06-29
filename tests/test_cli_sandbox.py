# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse sandbox` CLI regressions

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from synapse_channel.cli_sandbox import _cmd_run, add_parsers
from synapse_channel.core.sandbox_receipt import EXIT_OK, RunReceipt, digest_bytes

_WASM = b"\x00asm\x01\x00\x00\x00fake-module-bytes"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser


def _args(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(["sandbox", *argv])


def _tool(tmp_path: Path) -> Path:
    path = tmp_path / "tool.wasm"
    path.write_bytes(_WASM)
    return path


def _manifest_file(tmp_path: Path, **overrides: object) -> Path:
    data: dict[str, object] = {
        "tool_id": "calc",
        "content_digest": digest_bytes(_WASM),
        "resources": {"memory_bytes": 1 << 20, "fuel": 100_000, "wall_clock_ms": 2_000},
    }
    data.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _receipt(**fields: object) -> RunReceipt:
    base: RunReceipt = {
        "tool_id": "calc",
        "content_digest": digest_bytes(_WASM),
        "inputs_digest": digest_bytes(b""),
        "granted_capabilities": ["resource:mem=1048576,fuel=100000,wall=2000ms"],
        "exit": EXIT_OK,
        "output_digest": digest_bytes(b"7"),
        "fuel_used": 2,
        "reason": "",
    }
    base.update(fields)  # type: ignore[typeddict-item]
    return base


def _run(args: argparse.Namespace, **received: object) -> int:
    """Invoke ``_cmd_run`` with a fake runner that records what it was handed."""

    def _runner(manifest: object, wasm: bytes, inputs: bytes, *, entrypoint: str) -> RunReceipt:
        received.update(wasm=wasm, inputs=inputs, entrypoint=entrypoint)
        return _receipt()

    return _cmd_run(args, runner=_runner)


def test_validate_reports_a_valid_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("validate", str(_manifest_file(tmp_path)))
    assert args.func(args) == 0
    assert "manifest for 'calc' is valid" in capsys.readouterr().out

    json_args = _args("validate", str(_manifest_file(tmp_path)), "--json")
    assert json_args.func(json_args) == 0
    assert json.loads(capsys.readouterr().out)["tool_id"] == "calc"


def test_validate_rejects_a_broken_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = _args("validate", str(tmp_path / "nope.json"))
    assert missing.func(missing) == 2
    assert "could not read manifest" in capsys.readouterr().err

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    bad_args = _args("validate", str(bad))
    assert bad_args.func(bad_args) == 2
    assert "not valid JSON" in capsys.readouterr().err

    wrong_digest = _manifest_file(tmp_path, content_digest="md5:nope")
    digest_args = _args("validate", str(wrong_digest))
    assert digest_args.func(digest_args) == 2
    assert "sha256:" in capsys.readouterr().err


def test_run_requires_explicit_approval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _args("run", str(_tool(tmp_path)), "--manifest", str(_manifest_file(tmp_path)))
    assert _run(args) == 2
    assert "re-run with --approve" in capsys.readouterr().err


def test_run_refuses_a_module_that_does_not_match_its_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest_file(tmp_path, content_digest="sha256:" + "0" * 64)
    args = _args("run", str(_tool(tmp_path)), "--manifest", str(manifest), "--approve")
    assert _run(args) == 2
    err = capsys.readouterr().err
    assert "refused: digest_mismatch" in err


def test_run_executes_and_prints_a_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(
        "run", str(_tool(tmp_path)), "--manifest", str(_manifest_file(tmp_path)), "--approve"
    )
    assert _run(args) == 0
    out = capsys.readouterr().out
    assert "ran 'calc' — exit ok" in out and "granted:" in out

    json_args = _args(
        "run",
        str(_tool(tmp_path)),
        "--manifest",
        str(_manifest_file(tmp_path)),
        "--approve",
        "--json",
    )
    assert _run(json_args) == 0
    assert json.loads(capsys.readouterr().out)["exit"] == EXIT_OK


def test_run_passes_an_input_file_to_the_tool(tmp_path: Path) -> None:
    input_file = tmp_path / "in.bin"
    input_file.write_bytes(b"payload")
    args = _args(
        "run",
        str(_tool(tmp_path)),
        "--manifest",
        str(_manifest_file(tmp_path)),
        "--input",
        str(input_file),
        "--approve",
    )
    captured: dict[str, object] = {}

    def _runner(manifest: object, wasm: bytes, inputs: bytes, *, entrypoint: str) -> RunReceipt:
        captured["inputs"] = inputs
        return _receipt()

    assert _cmd_run(args, runner=_runner) == 0
    assert captured["inputs"] == b"payload"


def test_run_reports_missing_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing_tool = _args(
        "run", str(tmp_path / "nope.wasm"), "--manifest", str(_manifest_file(tmp_path)), "--approve"
    )
    assert _run(missing_tool) == 2
    assert "could not read tool module" in capsys.readouterr().err

    bad_manifest = _args(
        "run", str(_tool(tmp_path)), "--manifest", str(tmp_path / "no.json"), "--approve"
    )
    assert _run(bad_manifest) == 2
    assert "could not read manifest" in capsys.readouterr().err

    missing_input = _args(
        "run",
        str(_tool(tmp_path)),
        "--manifest",
        str(_manifest_file(tmp_path)),
        "--input",
        str(tmp_path / "no.bin"),
        "--approve",
    )
    assert _run(missing_input) == 2
    assert "could not read input file" in capsys.readouterr().err


def test_run_reports_a_missing_wasm_extra(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def _no_runtime(*_args: object, **_kwargs: object) -> RunReceipt:
        raise RuntimeError(
            "the WASM sandbox needs the optional extra: pip install 'synapse-channel[wasm]'"
        )

    args = _args(
        "run", str(_tool(tmp_path)), "--manifest", str(_manifest_file(tmp_path)), "--approve"
    )
    assert _cmd_run(args, runner=_no_runtime) == 2
    assert "synapse-channel[wasm]" in capsys.readouterr().err


def test_run_prints_a_failure_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _args(
        "run", str(_tool(tmp_path)), "--manifest", str(_manifest_file(tmp_path)), "--approve"
    )

    def _runner(manifest: object, wasm: bytes, inputs: bytes, *, entrypoint: str) -> RunReceipt:
        return _receipt(exit="error", reason="entrypoint 'run' is not exported")

    assert _cmd_run(args, runner=_runner) == 0
    out = capsys.readouterr().out
    assert "exit error" in out and "reason: entrypoint 'run' is not exported" in out
