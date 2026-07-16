# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse deliberate` conclude/verify CLI regressions (AOT-D1)

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli_deliberate import _cmd_conclude, _cmd_verify, _write_new, add_parsers
from synapse_channel.core.receipt_signing import generate_receipt_signing_key

_SPEC: dict[str, Any] = {
    "deliberation_id": "land-7ed25d6-2026-07-16",
    "pattern": "land_council",
    "project": "SYNAPSE-CHANNEL",
    "thesis": "land the AOT foundation",
    "resolution": "PASS",
    "actions": ["non-force FF"],
    "gate_checks": [{"gate": "G7_seal", "status": "sealed", "evidence": "receipt:x"}],
    "license_tag": "internal-ops",
    "retention_class": "standard",
}


def _spec_file(tmp_path: Path, **overrides: Any) -> Path:
    spec = dict(_SPEC)
    spec.update(overrides)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _conclude_ns(*, spec: str, out: str, receipt_key: str = "") -> argparse.Namespace:
    return argparse.Namespace(spec=spec, out=out, receipt_key=receipt_key)


def _receipt_key(tmp_path: Path) -> tuple[Path, Path]:
    private = tmp_path / "receipt-key"
    generate_receipt_signing_key(private)
    return private, tmp_path / "receipt-key.pub"


class TestConclude:
    def test_unsealed_conclude_writes_canonical_package(self, tmp_path: Path) -> None:
        out = tmp_path / "pkg.json"
        rc = _cmd_conclude(_conclude_ns(spec=str(_spec_file(tmp_path)), out=str(out)))
        assert rc == 0
        document = json.loads(out.read_text(encoding="utf-8"))
        assert document["aot_version"] == "aot.export.v0"
        assert "verification" not in document

    def test_sealed_conclude_writes_a_verifiable_receipt(self, tmp_path: Path) -> None:
        private, _pub = _receipt_key(tmp_path)
        out = tmp_path / "sealed.json"
        rc = _cmd_conclude(
            _conclude_ns(spec=str(_spec_file(tmp_path)), out=str(out), receipt_key=str(private))
        )
        assert rc == 0
        document = json.loads(out.read_text(encoding="utf-8"))
        assert "merkle_signature" in document["verification"]

    def test_owner_only_permissions_on_output(self, tmp_path: Path) -> None:
        out = tmp_path / "pkg.json"
        _cmd_conclude(_conclude_ns(spec=str(_spec_file(tmp_path)), out=str(out)))
        assert out.stat().st_mode & 0o777 == 0o600

    def test_missing_spec_file_returns_2(self, tmp_path: Path) -> None:
        rc = _cmd_conclude(_conclude_ns(spec=str(tmp_path / "nope.json"), out=str(tmp_path / "o")))
        assert rc == 2

    def test_invalid_json_spec_returns_2(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        rc = _cmd_conclude(_conclude_ns(spec=str(bad), out=str(tmp_path / "o")))
        assert rc == 2

    def test_non_object_spec_returns_2(self, tmp_path: Path) -> None:
        arr = tmp_path / "arr.json"
        arr.write_text("[1,2,3]", encoding="utf-8")
        rc = _cmd_conclude(_conclude_ns(spec=str(arr), out=str(tmp_path / "o")))
        assert rc == 2

    def test_malformed_spec_returns_2(self, tmp_path: Path) -> None:
        spec = _spec_file(tmp_path, pattern="gossip")
        rc = _cmd_conclude(_conclude_ns(spec=str(spec), out=str(tmp_path / "o")))
        assert rc == 2

    def test_existing_output_is_never_replaced(self, tmp_path: Path) -> None:
        out = tmp_path / "pkg.json"
        out.write_text("existing", encoding="utf-8")
        rc = _cmd_conclude(_conclude_ns(spec=str(_spec_file(tmp_path)), out=str(out)))
        assert rc == 2
        assert out.read_text(encoding="utf-8") == "existing"

    def test_bad_receipt_key_returns_2(self, tmp_path: Path) -> None:
        rc = _cmd_conclude(
            _conclude_ns(
                spec=str(_spec_file(tmp_path)),
                out=str(tmp_path / "o"),
                receipt_key=str(tmp_path / "missing-key"),
            )
        )
        assert rc == 2


class TestVerify:
    def _seal_to(self, tmp_path: Path) -> tuple[Path, Path]:
        private, pub = _receipt_key(tmp_path)
        out = tmp_path / "sealed.json"
        _cmd_conclude(
            _conclude_ns(spec=str(_spec_file(tmp_path)), out=str(out), receipt_key=str(private))
        )
        return out, pub

    def test_sealed_package_verifies(self, tmp_path: Path) -> None:
        sealed, pub = self._seal_to(tmp_path)
        rc = _cmd_verify(argparse.Namespace(sealed=str(sealed), trust=str(pub)))
        assert rc == 0

    def test_tampered_package_fails(self, tmp_path: Path) -> None:
        sealed, pub = self._seal_to(tmp_path)
        document = json.loads(sealed.read_text(encoding="utf-8"))
        document["package"]["result"]["resolution"] = "BLOCK"
        tampered = tmp_path / "tampered.json"
        tampered.write_text(json.dumps(document), encoding="utf-8")
        rc = _cmd_verify(argparse.Namespace(sealed=str(tampered), trust=str(pub)))
        assert rc == 1

    def test_missing_sealed_file_returns_2(self, tmp_path: Path) -> None:
        _private, pub = _receipt_key(tmp_path)
        rc = _cmd_verify(argparse.Namespace(sealed=str(tmp_path / "none.json"), trust=str(pub)))
        assert rc == 2

    def test_invalid_json_sealed_returns_2(self, tmp_path: Path) -> None:
        _private, pub = _receipt_key(tmp_path)
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        rc = _cmd_verify(argparse.Namespace(sealed=str(bad), trust=str(pub)))
        assert rc == 2

    def test_non_object_sealed_returns_2(self, tmp_path: Path) -> None:
        _private, pub = _receipt_key(tmp_path)
        arr = tmp_path / "arr.json"
        arr.write_text("[]", encoding="utf-8")
        rc = _cmd_verify(argparse.Namespace(sealed=str(arr), trust=str(pub)))
        assert rc == 2

    def test_bad_trust_key_returns_2(self, tmp_path: Path) -> None:
        sealed, _pub = self._seal_to(tmp_path)
        rc = _cmd_verify(
            argparse.Namespace(sealed=str(sealed), trust=str(tmp_path / "missing.pub"))
        )
        assert rc == 2


class TestWriteNew:
    def test_partial_file_is_removed_when_the_write_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "partial.json"

        def _boom(_fd: int) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr("os.fsync", _boom)
        with pytest.raises(OSError, match="simulated disk failure"):
            _write_new(out, "content")
        assert not out.exists()


class TestParsers:
    def test_add_parsers_wires_conclude_and_verify(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        add_parsers(sub)
        conclude = parser.parse_args(["deliberate", "conclude", "--from", "s", "--out", "o"])
        assert conclude.func is _cmd_conclude
        verify = parser.parse_args(["deliberate", "verify", "sealed.json", "--trust", "k.pub"])
        assert verify.func is _cmd_verify
