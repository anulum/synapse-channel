# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse federation` CLI regressions

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from synapse_channel.cli_federation import _cmd_import, add_parsers
from synapse_channel.core.federation_store import load_store

_BUNDLE = {
    "domain_id": "acme",
    "namespaces": ["acme/shared"],
    "certificate_pins": ["sha256:aa"],
    "signing_key_ids": ["key-1"],
    "scope_grants": [{"verb": "read_board", "namespace": "acme/shared"}],
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser


def _args(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(["federation", *argv])


def _write_bundle(tmp_path: Path, data: object, name: str = "acme.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_import_records_a_peering_with_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = str(tmp_path / "store.json")
    args = _args(
        "import",
        _write_bundle(tmp_path, _BUNDLE),
        "--confirmed-by",
        "ops@local",
        "--source",
        "signed-bundle",
        "--store",
        store,
    )
    assert _cmd_import(args, clock=lambda: 100.0) == 0
    assert "imported peering with domain 'acme'" in capsys.readouterr().out
    records = load_store(store)
    assert records["acme"].provenance.confirmed_by == "ops@local"
    assert records["acme"].provenance.source == "signed-bundle"
    assert records["acme"].provenance.imported_at == 100.0


def test_import_defaults_source_to_filename_and_reports_update(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = str(tmp_path / "store.json")
    bundle = _write_bundle(tmp_path, _BUNDLE)
    first = _args("import", bundle, "--confirmed-by", "a", "--store", store)
    assert _cmd_import(first, clock=lambda: 1.0) == 0
    capsys.readouterr()
    # re-importing the same domain reports an update, source defaults to the file name
    again = _args("import", bundle, "--confirmed-by", "b", "--store", store)
    assert _cmd_import(again, clock=lambda: 2.0) == 0
    assert "updated peering with domain 'acme'" in capsys.readouterr().out
    assert load_store(store)["acme"].provenance.source == "acme.json"


def test_import_reports_a_missing_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(
        "import", str(tmp_path / "nope.json"), "--confirmed-by", "a", "--store", str(tmp_path / "s")
    )
    assert _cmd_import(args) == 2
    assert "could not read bundle file" in capsys.readouterr().err


def test_import_reports_invalid_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    args = _args("import", str(bad_json), "--confirmed-by", "a", "--store", str(tmp_path / "s"))
    assert _cmd_import(args) == 2
    assert "invalid federation bundle" in capsys.readouterr().err

    no_domain = _write_bundle(tmp_path, {"namespaces": ["x"]}, name="no_domain.json")
    args2 = _args("import", no_domain, "--confirmed-by", "a", "--store", str(tmp_path / "s"))
    assert _cmd_import(args2) == 2
    assert "invalid federation bundle" in capsys.readouterr().err


def test_list_empty_and_populated(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = str(tmp_path / "store.json")
    empty = _args("list", "--store", store)
    assert empty.func(empty) == 0
    assert "no peer domains imported" in capsys.readouterr().out

    imp = _args(
        "import", _write_bundle(tmp_path, _BUNDLE), "--confirmed-by", "ops", "--store", store
    )
    _cmd_import(imp, clock=lambda: 1.0)
    capsys.readouterr()
    populated = _args("list", "--store", store)
    assert populated.func(populated) == 0
    out = capsys.readouterr().out
    assert "acme [active]" in out and "confirmed by ops" in out


def test_revoke_marks_revoked_and_keeps_the_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = str(tmp_path / "store.json")
    imp = _args(
        "import", _write_bundle(tmp_path, _BUNDLE), "--confirmed-by", "ops", "--store", store
    )
    _cmd_import(imp, clock=lambda: 1.0)
    capsys.readouterr()
    rev = _args("revoke", "acme", "--store", store)
    assert rev.func(rev) == 0
    assert "revoked peering with domain 'acme'" in capsys.readouterr().out
    record = load_store(store)["acme"]
    assert record.peer.revoked is True
    assert record.provenance.confirmed_by == "ops"  # audit record kept


def test_revoke_unknown_domain(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rev = _args("revoke", "ghost", "--store", str(tmp_path / "store.json"))
    assert rev.func(rev) == 2
    assert "no peering with domain 'ghost'" in capsys.readouterr().err


def test_list_and_revoke_report_a_corrupt_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.json"
    store.write_text("{not json", encoding="utf-8")
    lst = _args("list", "--store", str(store))
    assert lst.func(lst) == 2
    assert "not valid JSON" in capsys.readouterr().err
    rev = _args("revoke", "acme", "--store", str(store))
    assert rev.func(rev) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_store_path_default_expands_home() -> None:
    args = _args("list")
    assert args.store == "~/.synapse/federation.json"
