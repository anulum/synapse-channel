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
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli_federation import (
    SECONDS_PER_DAY,
    _cmd_import,
    _cmd_list,
    _cmd_rotate,
    add_parsers,
)
from synapse_channel.core.federation import FederationPeer
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


def _imported_store(tmp_path: Path, *, bundle: dict[str, object] | None = None) -> str:
    """Import one peering at epoch 0 and return the store path."""
    store = str(tmp_path / "store.json")
    imp = _args(
        "import",
        _write_bundle(tmp_path, bundle if bundle is not None else _BUNDLE),
        "--confirmed-by",
        "ops",
        "--store",
        store,
    )
    assert _cmd_import(imp, clock=lambda: 0.0) == 0
    return store


def test_list_shows_each_peerings_age(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _imported_store(tmp_path)
    capsys.readouterr()

    args = _args("list", "--store", store)
    assert _cmd_list(args, clock=lambda: 5 * SECONDS_PER_DAY) == 0

    out = capsys.readouterr().out
    assert "imported 5 day(s) ago" in out
    assert "[stale" not in out


def test_list_flags_stale_peerings_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _imported_store(tmp_path)
    capsys.readouterr()

    args = _args("list", "--store", store, "--max-age", "3")
    assert _cmd_list(args, clock=lambda: 5 * SECONDS_PER_DAY) == 1

    captured = capsys.readouterr()
    assert "acme [active] [stale: imported 5 days ago > 3]" in captured.out
    assert "1 peering(s) exceed --max-age 3 days" in captured.err
    assert "re-run the exchange ceremony" in captured.err


def test_list_within_max_age_is_not_flagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _imported_store(tmp_path)
    capsys.readouterr()

    args = _args("list", "--store", store, "--max-age", "30")
    assert _cmd_list(args, clock=lambda: 5 * SECONDS_PER_DAY) == 0

    captured = capsys.readouterr()
    assert "[stale" not in captured.out
    assert captured.err == ""


def test_list_expired_peering_shows_expired_and_is_not_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # an expired peering is already refused by the gate; staleness only
    # grades peerings that still grant
    expiring = dict(_BUNDLE, expires_at=2 * SECONDS_PER_DAY)
    store = _imported_store(tmp_path, bundle=expiring)
    capsys.readouterr()

    args = _args("list", "--store", store, "--max-age", "3")
    assert _cmd_list(args, clock=lambda: 5 * SECONDS_PER_DAY) == 0

    captured = capsys.readouterr()
    assert "acme [expired]" in captured.out
    assert "expires=1970-01-03T00:00:00Z (expired 3.0d ago)" in captured.out
    assert "[stale" not in captured.out
    assert captured.err == ""


def test_list_revoked_peering_shows_revoked_and_is_not_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _imported_store(tmp_path)
    rev = _args("revoke", "acme", "--store", store)
    assert rev.func(rev) == 0
    capsys.readouterr()

    args = _args("list", "--store", store, "--max-age", "3")
    assert _cmd_list(args, clock=lambda: 5 * SECONDS_PER_DAY) == 0

    captured = capsys.readouterr()
    assert "acme [revoked]" in captured.out
    assert "[stale" not in captured.out
    assert captured.err == ""


def test_list_shows_expiry_and_rotation_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlap = dict(
        _BUNDLE,
        expires_at=7 * SECONDS_PER_DAY,
        signing_key_ids=["key-1", "key-2"],
        certificate_pins=["sha256:aa", "sha256:bb"],
    )
    store = _imported_store(tmp_path, bundle=overlap)
    capsys.readouterr()

    args = _args("list", "--store", store)
    assert _cmd_list(args, clock=lambda: 5 * SECONDS_PER_DAY) == 0

    out = capsys.readouterr().out
    assert "keys=2 pins=2 scope=1" in out
    assert "expires=1970-01-08T00:00:00Z (in 2.0d)" in out
    assert "rotation=overlap" in out


def test_list_rejects_a_non_positive_max_age(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("list", "--store", str(tmp_path / "s"), "--max-age", "0")
    assert _cmd_list(args) == 2
    assert "--max-age must be a positive number of days" in capsys.readouterr().err


def test_import_rejects_a_non_positive_max_age(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(
        "import",
        str(tmp_path / "never-read.json"),
        "--confirmed-by",
        "ops",
        "--store",
        str(tmp_path / "s"),
        "--max-age",
        "-1",
    )
    assert _cmd_import(args) == 2
    assert "--max-age must be a positive number of days" in capsys.readouterr().err


def test_import_warns_when_the_bundle_never_expires(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = str(tmp_path / "store.json")
    args = _args(
        "import",
        _write_bundle(tmp_path, _BUNDLE),
        "--confirmed-by",
        "ops",
        "--store",
        store,
        "--max-age",
        "30",
    )

    assert _cmd_import(args, clock=lambda: 0.0) == 0

    captured = capsys.readouterr()
    assert "warning: bundle never expires" in captured.err
    assert "imported peering with domain 'acme'" in captured.out
    assert "acme" in load_store(store)


def test_import_warns_when_expiry_overruns_max_age(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    distant = dict(_BUNDLE, expires_at=100 * SECONDS_PER_DAY)
    args = _args(
        "import",
        _write_bundle(tmp_path, distant),
        "--confirmed-by",
        "ops",
        "--store",
        str(tmp_path / "store.json"),
        "--max-age",
        "30",
    )

    assert _cmd_import(args, clock=lambda: 0.0) == 0
    assert "warning: bundle expiry is 100 days out, beyond --max-age 30" in capsys.readouterr().err


def test_import_expiry_within_max_age_is_quiet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    near = dict(_BUNDLE, expires_at=10 * SECONDS_PER_DAY)
    args = _args(
        "import",
        _write_bundle(tmp_path, near),
        "--confirmed-by",
        "ops",
        "--store",
        str(tmp_path / "store.json"),
        "--max-age",
        "30",
    )

    assert _cmd_import(args, clock=lambda: 0.0) == 0
    assert capsys.readouterr().err == ""


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


# --- the exchange ceremony: offer and fetch ------------------------------------------------


def _material() -> dict[str, object]:
    """The bundle material a domain publishes for peering."""
    return dict(_BUNDLE)


def test_offer_prints_the_fingerprint_block_and_serving_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel.core.federation_wire import bundle_fingerprint, decode_federation_offer

    bundle = _write_bundle(tmp_path, _material(), "$(touch injected).json")
    args = _args("offer", bundle)
    assert args.func(args) == 0
    out = capsys.readouterr().out
    peer = decode_federation_offer(_material())
    assert "domain:             acme" in out
    assert f"bundle fingerprint: {bundle_fingerprint(peer)}" in out
    assert f"synapse hub --federation-offer='{bundle}'" in out
    assert "out-of-band" in out


def test_offer_reports_an_unreadable_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("offer", str(tmp_path / "absent.json"))
    assert args.func(args) == 2
    assert "could not read bundle file" in capsys.readouterr().err


def test_offer_reports_non_json_material(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "offer.json"
    path.write_text("{not json", encoding="utf-8")
    args = _args("offer", str(path))
    assert args.func(args) == 2
    assert "invalid federation bundle" in capsys.readouterr().err


def test_offer_reports_a_malformed_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _write_bundle(tmp_path, {"namespaces": ["acme/shared"]})
    args = _args("offer", bundle)
    assert args.func(args) == 2
    assert "invalid federation bundle" in capsys.readouterr().err


def _stub_fetcher(
    captured: dict[str, object] | None = None, *, error: str | None = None
) -> Callable[..., Coroutine[Any, Any, FederationPeer]]:
    """Return an async fetcher stub yielding the material bundle, or raising."""
    from synapse_channel.core.federation_fetch import FederationFetchError
    from synapse_channel.core.federation_wire import decode_federation_offer

    async def fetch(uri: str, **kwargs: object) -> FederationPeer:
        if captured is not None:
            captured.update({"uri": uri, **kwargs})
        if error is not None:
            raise FederationFetchError(error)
        return decode_federation_offer(_material())

    return fetch


def test_fetch_writes_the_bundle_and_prints_the_same_fingerprints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel.cli_federation import _cmd_fetch
    from synapse_channel.core.federation_store import peer_from_dict
    from synapse_channel.core.federation_wire import bundle_fingerprint, decode_federation_offer

    out_file = tmp_path / "$(touch injected)" / "peer.json"
    captured: dict[str, object] = {}
    uri = "ws://peer:8876/$(touch injected)"
    args = _args("fetch", uri, "--out", str(out_file))
    assert _cmd_fetch(args, fetcher=_stub_fetcher(captured)) == 0
    peer = decode_federation_offer(_material())
    assert peer_from_dict(json.loads(out_file.read_text(encoding="utf-8"))) == peer
    out = capsys.readouterr().out
    from synapse_channel.terminal_text import shell_command_arg

    assert f"bundle fingerprint: {bundle_fingerprint(peer)}" in out
    assert "NOT imported" in out
    assert "synapse federation import --confirmed-by='<operator>'" in out
    assert f"--source='{uri}'" in out
    # Copyable import argv uses shell_command_arg (Path.as_posix on Windows).
    assert f"-- {shell_command_arg(out_file)}" in out
    assert captured["uri"] == uri
    assert captured["local_id"] == "federation-fetch"
    assert captured["token"] is None
    assert captured["timeout"] == 10.0


def test_fetch_passes_token_local_id_and_timeout(tmp_path: Path) -> None:
    from synapse_channel.cli_federation import _cmd_fetch

    captured: dict[str, object] = {}
    args = _args(
        "fetch",
        "ws://peer:8876",
        "--out",
        str(tmp_path / "peer.json"),
        "--token",
        "secret",
        "--local-id",
        "ops-a",
        "--timeout",
        "3.5",
    )
    assert _cmd_fetch(args, fetcher=_stub_fetcher(captured)) == 0
    assert captured["token"] == "secret"
    assert captured["local_id"] == "ops-a"
    assert captured["timeout"] == 3.5


def test_fetch_passes_a_pinned_connector(tmp_path: Path) -> None:
    from synapse_channel.cli_federation import _cmd_fetch

    captured: dict[str, object] = {}
    args = _args(
        "fetch",
        "wss://peer:8876",
        "--out",
        str(tmp_path / "peer.json"),
        "--pin",
        "sha256:" + "a" * 64,
    )
    assert _cmd_fetch(args, fetcher=_stub_fetcher(captured)) == 0
    assert callable(captured["connector"])


def test_fetch_refuses_to_overwrite_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel.cli_federation import _cmd_fetch

    out_file = tmp_path / "peer.json"
    out_file.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}
    args = _args("fetch", "ws://peer:8876", "--out", str(out_file))
    assert _cmd_fetch(args, fetcher=_stub_fetcher(captured)) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
    assert captured == {}  # the network is never touched
    assert out_file.read_text(encoding="utf-8") == "{}"


def test_fetch_replaces_an_existing_file_with_force(tmp_path: Path) -> None:
    from synapse_channel.cli_federation import _cmd_fetch
    from synapse_channel.core.federation_store import peer_from_dict
    from synapse_channel.core.federation_wire import decode_federation_offer

    out_file = tmp_path / "peer.json"
    out_file.write_text("{}", encoding="utf-8")
    args = _args("fetch", "ws://peer:8876", "--out", str(out_file), "--force")
    assert _cmd_fetch(args, fetcher=_stub_fetcher()) == 0
    fetched = peer_from_dict(json.loads(out_file.read_text(encoding="utf-8")))
    assert fetched == decode_federation_offer(_material())


def test_fetch_reports_a_failed_fetch_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel.cli_federation import _cmd_fetch

    out_file = tmp_path / "peer.json"
    args = _args("fetch", "ws://peer:8876", "--out", str(out_file))
    assert _cmd_fetch(args, fetcher=_stub_fetcher(error="refused")) == 2
    assert "could not fetch the federation offer: refused" in capsys.readouterr().err
    assert not out_file.exists()


def test_offer_and_fetch_print_the_identical_fingerprint_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both ceremony halves render the same lines, so operators compare like for like."""
    from synapse_channel.cli_federation import _cmd_fetch
    from synapse_channel.core.federation_wire import render_offer_fingerprints

    bundle = _write_bundle(tmp_path, _material())
    offer_args = _args("offer", bundle)
    assert offer_args.func(offer_args) == 0
    offer_out = capsys.readouterr().out
    fetch_args = _args("fetch", "ws://peer:8876", "--out", str(tmp_path / "fetched.json"))
    assert _cmd_fetch(fetch_args, fetcher=_stub_fetcher()) == 0
    fetch_out = capsys.readouterr().out
    from synapse_channel.core.federation_wire import decode_federation_offer

    block = render_offer_fingerprints(decode_federation_offer(_material()))
    assert block in offer_out
    assert block in fetch_out


def _own_bundle(tmp_path: Path, **over: object) -> str:
    data: dict[str, object] = dict(_BUNDLE)
    data.update(over)
    return _write_bundle(tmp_path, data, name="own.json")


def test_rotate_rewrites_the_bundle_with_fresh_expiry_and_a_kept_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _own_bundle(tmp_path)
    args = _args("rotate", bundle, "--lifetime-days", "90", "--add-signing-key", "key-2")
    assert _cmd_rotate(args, clock=lambda: 1000.0) == 0
    out = capsys.readouterr().out
    assert "rotated bundle:" in out and "+ key-2" in out
    written = json.loads(Path(bundle).read_text(encoding="utf-8"))
    assert set(written["signing_key_ids"]) == {"key-1", "key-2"}
    assert written["expires_at"] == 1000.0 + 90 * SECONDS_PER_DAY
    # The prior bundle is kept for the grace window.
    prior = json.loads(Path(bundle + ".prev").read_text(encoding="utf-8"))
    assert set(prior["signing_key_ids"]) == {"key-1"}


def test_rotate_refuses_retiring_an_absent_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _own_bundle(tmp_path)
    args = _args("rotate", bundle, "--retire-signing-key", "key-9")
    assert _cmd_rotate(args, clock=lambda: 1000.0) == 2
    assert "does not hold" in capsys.readouterr().err


def test_rotate_refuses_a_non_positive_lifetime(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _own_bundle(tmp_path)
    args = _args("rotate", bundle, "--lifetime-days", "0")
    assert _cmd_rotate(args) == 2
    assert "positive number of days" in capsys.readouterr().err


def test_rotate_reports_an_unreadable_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("rotate", str(tmp_path / "nope.json"))
    assert _cmd_rotate(args) == 2
    assert "could not read bundle" in capsys.readouterr().err


def test_rotate_reports_an_invalid_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    args = _args("rotate", str(bad))
    assert _cmd_rotate(args) == 2
    assert "invalid federation bundle" in capsys.readouterr().err


def test_rotate_warns_when_no_signing_key_remains(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _own_bundle(tmp_path)
    args = _args("rotate", bundle, "--retire-signing-key", "key-1")
    assert _cmd_rotate(args, clock=lambda: 1000.0) == 0
    assert "no signing keys" in capsys.readouterr().err


def test_rotate_warns_when_no_certificate_pin_remains(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _own_bundle(tmp_path)
    args = _args("rotate", bundle, "--retire-pin", "sha256:aa")
    assert _cmd_rotate(args, clock=lambda: 1000.0) == 0
    assert "no certificate pins" in capsys.readouterr().err


def test_rotate_writes_the_backup_to_a_custom_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _own_bundle(tmp_path)
    backup = tmp_path / "prior.json"
    args = _args("rotate", bundle, "--backup", str(backup))
    assert _cmd_rotate(args, clock=lambda: 1000.0) == 0
    assert set(json.loads(backup.read_text(encoding="utf-8"))["signing_key_ids"]) == {"key-1"}


def test_rotate_reports_a_write_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = _own_bundle(tmp_path)
    args = _args("rotate", bundle, "--backup", str(tmp_path / "missing" / "b.json"))
    assert _cmd_rotate(args, clock=lambda: 1000.0) == 2
    assert "could not write the rotated bundle" in capsys.readouterr().err
