# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identified connect tokens, validity windows and live reload
"""Token sets rotate connect tokens without a hub restart and fail safe."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import read_json, read_until_type, running_hub, send_json
from synapse_channel import cli
from synapse_channel.cli_hub_startup import HubStartupRefused
from synapse_channel.cli_hub_transport import _connect_authenticator
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.secret_files import SecretFileError
from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.core.token_set import (
    TokenSetAuthenticator,
    TokenSetError,
    connect_token_configured,
    connect_token_digest,
    parse_token_set,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes and symlinks")

_START = 1_800_000_000.0


def _entry(token_id: str, secret: str, **fields: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": token_id, "sha256": connect_token_digest(secret)}
    entry.update(fields)
    return entry


def _write(path: Path, *entries: dict[str, Any], raw: str | None = None) -> Path:
    """Replace the set atomically, as an operator would, with owner-only mode."""
    staging = path.with_name(path.name + ".new")
    staging.write_text(
        raw if raw is not None else json.dumps({"version": 1, "tokens": list(entries)}),
        encoding="utf-8",
    )
    apply_owner_only_file(staging)
    os.replace(staging, path)
    return path


class _Clock:
    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> float:
        return self.now


def test_two_tokens_rotate_without_a_restart(tmp_path: Path) -> None:
    """Both tokens admit during the overlap; retiring one refuses only it."""
    path = _write(tmp_path / "tokens.json", _entry("old", "s-old"), _entry("new", "s-new"))
    auth = TokenSetAuthenticator(path)
    assert auth.path == path
    assert auth.authenticate("s-old", "A") == (True, "Authenticated.")
    assert auth.authenticate("s-new", "A") == (True, "Authenticated.")
    _write(path, _entry("new", "s-new"))
    assert auth.authenticate("s-old", "A") == (False, "Invalid authentication token.")
    assert auth.authenticate("s-new", "A") == (True, "Authenticated.")
    assert [entry.token_id for entry in auth.entries] == ["new"]
    assert auth.stale_reason is None


def test_validity_window_is_enforced(tmp_path: Path) -> None:
    clock = _Clock()
    path = _write(
        tmp_path / "tokens.json",
        _entry("window", "s", not_before="2027-01-15T08:00:00Z", not_after="2027-01-16T08:00:00Z"),
    )
    auth = TokenSetAuthenticator(path, clock=clock)
    clock.now = 1_800_000_000.0  # 2027-01-15T08:00:00Z
    assert auth.authenticate("s", "A")[0] is True
    clock.now -= 1
    assert auth.authenticate("s", "A") == (False, "Connect token 'window' is not yet valid.")
    clock.now = 1_800_086_400.0  # exactly not_after
    assert auth.authenticate("s", "A") == (False, "Connect token 'window' has expired.")


def test_agent_binding_and_empty_token(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json", _entry("seat", "s", agents=["A/one"]))
    auth = TokenSetAuthenticator(path)
    assert auth.authenticate("s", "A/one")[0] is True
    assert auth.authenticate("s", "A/two") == (False, "Token is not authorised for agent 'A/two'.")
    assert auth.authenticate("", "A/one") == (False, "Authentication token required.")


def test_a_bad_replacement_keeps_the_last_good_set_and_reports_stale(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed or missing file is never applied; the hub keeps what it had and says so."""
    path = _write(tmp_path / "tokens.json", _entry("keep", "s"))
    auth = TokenSetAuthenticator(path)
    _write(path, raw="{not json")
    with caplog.at_level("WARNING", logger="synapse_channel.core.token_set"):
        assert auth.authenticate("s", "A")[0] is True
    assert auth.stale_reason is not None and "not valid JSON" in auth.stale_reason
    assert "token set not applied" in caplog.text and "last accepted set stays" in caplog.text
    path.unlink()
    assert auth.authenticate("s", "A")[0] is True
    assert auth.stale_reason == f"{path} is missing"
    _write(path, _entry("fresh", "t"))
    assert auth.authenticate("t", "A")[0] is True
    assert auth.authenticate("s", "A")[0] is False
    assert auth.stale_reason is None


@posix_only
def test_a_symlinked_or_shared_replacement_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json", _entry("keep", "s"))
    auth = TokenSetAuthenticator(path)
    other = _write(tmp_path / "other.json", _entry("swapped", "x"))
    path.unlink()
    path.symlink_to(other)
    assert auth.authenticate("x", "A")[0] is False
    assert auth.stale_reason is not None
    path.unlink()
    _write(path, _entry("shared", "y"))
    os.chmod(path, 0o644)
    assert auth.authenticate("y", "A")[0] is False
    assert auth.stale_reason is not None
    assert auth.authenticate("s", "A")[0] is True


@posix_only
def test_a_mode_change_alone_is_noticed_both_ways(tmp_path: Path) -> None:
    """Fixing the mode of a rejected file applies it; loosening the set in force goes stale."""
    path = _write(tmp_path / "tokens.json", _entry("a", "s-a"))
    auth = TokenSetAuthenticator(path)
    _write(path, _entry("b", "s-b"))
    os.chmod(path, 0o644)
    assert auth.authenticate("s-a", "A")[0] is True
    assert auth.stale_reason is not None
    os.chmod(path, 0o600)
    assert auth.authenticate("s-a", "A")[0] is False
    assert auth.authenticate("s-b", "A")[0] is True
    assert auth.stale_reason is None
    os.chmod(path, 0o644)
    assert auth.authenticate("s-b", "A")[0] is True
    assert auth.stale_reason is not None
    os.chmod(path, 0o600)
    assert auth.authenticate("s-b", "A")[0] is True
    assert auth.stale_reason is None


def test_position_in_the_file_does_not_change_the_result(tmp_path: Path) -> None:
    entries = [_entry(f"t{n}", f"s{n}") for n in range(5)]
    auth = TokenSetAuthenticator(_write(tmp_path / "tokens.json", *entries))
    first, last = (
        auth.authenticate_with_principal("s0", "A"),
        auth.authenticate_with_principal("s4", "A"),
    )
    assert first[:2] == last[:2] == (True, "Authenticated.")
    plain = TokenAuthenticator(["s4"]).authenticate_with_principal("s4", "A")
    assert last[2] == plain[2]


def test_the_file_holds_digests_only_and_the_principal_survives_reload(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json", _entry("a", "secret-value"))
    assert "secret-value" not in path.read_text(encoding="utf-8")
    auth = TokenSetAuthenticator(path)
    before = auth.authenticate_with_principal("secret-value", "A")[2]
    _write(path, _entry("a", "secret-value"), _entry("b", "other"))
    assert auth.authenticate_with_principal("secret-value", "A")[2] == before


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("[]", "exactly version and tokens"),
        ('{"version": 2, "tokens": []}', "version must be 1"),
        ('{"version": true, "tokens": []}', "version must be 1"),
        ('{"version": 1, "tokens": {}}', "tokens must be a list"),
        ('{"version": 1, "tokens": [1]}', "an entry must be an object"),
        ('{"version": 1, "tokens": [{"id": "a", "sha256": "0", "x": 1}]}', "unknown fields"),
        ('{"version": 1, "tokens": [{"id": "a b", "sha256": "0"}]}', "id must match"),
        ('{"version": 1, "tokens": [{"id": "a", "sha256": "AB"}]}', "64 lowercase hex"),
    ],
)
def test_malformed_documents_name_the_problem(raw: str, message: str) -> None:
    with pytest.raises(TokenSetError, match=message):
        parse_token_set(raw)


def test_entry_fields_and_duplicates_are_validated() -> None:
    digest = connect_token_digest("s")

    def doc(*entries: dict[str, Any]) -> str:
        return json.dumps({"version": 1, "tokens": list(entries)})

    with pytest.raises(TokenSetError, match="agents must be a list"):
        parse_token_set(doc({"id": "a", "sha256": digest, "agents": [""]}))
    with pytest.raises(TokenSetError, match="YYYY-MM-DDTHH:MM:SSZ"):
        parse_token_set(doc({"id": "a", "sha256": digest, "not_after": "2027-01-01"}))
    with pytest.raises(TokenSetError, match="not a real date"):
        parse_token_set(doc({"id": "a", "sha256": digest, "not_after": "2027-02-30T00:00:00Z"}))
    with pytest.raises(TokenSetError, match="later than not_before"):
        parse_token_set(
            doc(
                {
                    "id": "a",
                    "sha256": digest,
                    "not_before": "2027-01-02T00:00:00Z",
                    "not_after": "2027-01-01T00:00:00Z",
                }
            )
        )
    with pytest.raises(TokenSetError, match="ids must be unique"):
        parse_token_set(doc({"id": "a", "sha256": digest}, {"id": "a", "sha256": "0" * 64}))
    with pytest.raises(TokenSetError, match="share one secret"):
        parse_token_set(doc({"id": "a", "sha256": digest}, {"id": "b", "sha256": digest}))
    assert parse_token_set(doc()) == ()


def test_an_empty_set_admits_nobody(tmp_path: Path) -> None:
    auth = TokenSetAuthenticator(_write(tmp_path / "tokens.json"))
    assert auth.is_empty
    assert auth.authenticate("anything", "A") == (False, "Invalid authentication token.")


async def test_a_real_hub_follows_the_file_without_a_restart(tmp_path: Path) -> None:
    """A retired token is refused on the next connection; the new one is admitted."""
    path = _write(tmp_path / "tokens.json", _entry("old", "s-old"))
    hub = SynapseHub(hub_id="syn-token-set", authenticator=TokenSetAuthenticator(path))
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="A", type="heartbeat", token="s-old")
            assert (await read_until_type(websocket, "welcome"))["hub_id"] == "syn-token-set"
        _write(path, _entry("new", "s-new"))
        async with connect(uri) as websocket:
            await send_json(websocket, sender="B", type="heartbeat", token="s-old")
            denied = await read_until_type(websocket, "auth_denied")
            with pytest.raises(ConnectionClosed) as closed:
                await read_json(websocket)
        async with connect(uri) as websocket:
            await send_json(websocket, sender="C", type="heartbeat", token="s-new")
            assert (await read_until_type(websocket, "welcome"))["hub_id"] == "syn-token-set"
    assert "Invalid" in denied["payload"]
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4010


def test_startup_builds_the_token_set_or_refuses_a_bad_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path / "tokens.json", _entry("a", "s"))
    built = _connect_authenticator(argparse.Namespace(token_set=str(path), token=None))
    assert isinstance(built, TokenSetAuthenticator)
    plain = _connect_authenticator(argparse.Namespace(token_set=None, token="s"))
    assert type(plain) is TokenAuthenticator
    assert _connect_authenticator(argparse.Namespace(token_set=None, token=None)) is None
    _write(path, raw='{"version": 9, "tokens": []}')
    with pytest.raises(HubStartupRefused):
        _connect_authenticator(argparse.Namespace(token_set=str(path), token=None))
    assert "version must be 1" in capsys.readouterr().err
    with pytest.raises(SecretFileError):
        TokenSetAuthenticator(tmp_path / "absent.json")


def test_a_token_set_counts_as_configured_authentication() -> None:
    assert connect_token_configured(argparse.Namespace(token=None, token_set="tokens.json"))
    assert connect_token_configured(argparse.Namespace(token="s", token_set=None))
    assert not connect_token_configured(argparse.Namespace(token=None, token_set=None))


@pytest.mark.parametrize("source", ["--token", "--token-file", "SYNAPSE_TOKEN"])
def test_a_token_set_beside_a_single_secret_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
) -> None:
    """An unexpiring secret beside the set would bypass its lifecycle."""
    path = _write(tmp_path / "tokens.json", _entry("a", "s"))
    argv = ["hub", "--token-set", str(path)]
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    if source == "--token":
        argv += ["--token", "legacy"]
    elif source == "--token-file":
        argv += ["--token-file", str(path)]
    else:
        monkeypatch.setenv("SYNAPSE_TOKEN", "legacy")
    assert cli.main(argv) == 2
    assert "--token-set cannot be combined" in capsys.readouterr().err


@posix_only
def test_a_set_that_cannot_be_inspected_goes_stale_instead_of_raising(tmp_path: Path) -> None:
    folder = tmp_path / "sets"
    folder.mkdir()
    path = _write(folder / "tokens.json", _entry("a", "s"))
    auth = TokenSetAuthenticator(path)
    folder.chmod(0o600)
    try:
        assert auth.authenticate("s", "A") == (True, "Authenticated.")
        assert auth.stale_reason is not None
    finally:
        folder.chmod(0o700)
    auth.refresh()
    assert auth.stale_reason is None


def test_require_expiry_refuses_unending_tokens_but_applies_the_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ending = _entry("ends", "s-ends", not_after="2027-06-01T00:00:00Z")
    path = _write(tmp_path / "tokens.json", ending, _entry("forever", "s-forever"))
    auth = TokenSetAuthenticator(path, clock=lambda: _START, require_expiry=True)
    assert auth.policy == {"require_expiry": True, "max_token_days": None}
    assert auth.policy_refused() == ("forever",)
    assert auth.authenticate("s-ends", "A") == (True, "Authenticated.")
    outcome = auth.authenticate_outcome("s-forever", "A")
    assert (outcome.ok, outcome.code, outcome.token_id) == (False, "policy", "forever")
    assert (
        outcome.reason == "Connect token 'forever' has no end of validity, which this hub requires."
    )
    namespace = argparse.Namespace(token_set=str(path), token=None, require_token_expiry=True)
    assert isinstance(_connect_authenticator(namespace), TokenSetAuthenticator)
    assert "refuses these tokens, which admit nothing: forever" in capsys.readouterr().err
    _write(path, ending)
    assert auth.authenticate("s-ends", "A") == (True, "Authenticated.")
    assert auth.stale_reason is None and auth.policy_refused() == ()
    assert TokenSetAuthenticator(path).policy == {"require_expiry": False, "max_token_days": None}


def test_require_expiry_needs_a_token_set(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    assert cli.main(["hub", "--require-token-expiry"]) == 2
    assert "--require-token-expiry requires --token-set" in capsys.readouterr().err


def test_max_token_days_refuses_long_tokens_at_connection_time(tmp_path: Path) -> None:
    # _START is 2027-01-15T08:00:00Z; 30 days later is 2027-02-14T08:00:00Z.
    inside = _entry("inside", "s-in", not_after="2027-02-14T08:00:00Z")
    beyond = _entry("beyond", "s-out", not_after="2027-02-14T08:00:01Z")
    forever = _entry("forever", "s-forever")
    path = _write(tmp_path / "tokens.json", inside, beyond, forever)
    now = [_START]
    auth = TokenSetAuthenticator(path, clock=lambda: now[0], max_token_days=30)
    assert auth.policy_refused() == ("beyond", "forever")
    assert auth.authenticate("s-in", "A") == (True, "Authenticated.")
    refused = auth.authenticate_outcome("s-out", "A")
    assert (refused.code, refused.reason) == (
        "policy",
        "Connect token 'beyond' stays valid for more than 30 days, longer than this hub allows.",
    )
    assert auth.authenticate_outcome("s-forever", "A").code == "policy"
    # The bound is on remaining validity: one second later the long token fits.
    now[0] = _START + 1
    assert auth.authenticate("s-out", "A") == (True, "Authenticated.")
    assert auth.policy_refused() == ("forever",)


def test_max_token_days_needs_a_positive_count_and_a_token_set(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    assert cli.main(["hub", "--max-token-days", "7"]) == 2
    assert "--max-token-days requires --token-set" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["hub", "--max-token-days", "0"])
    assert "must be a whole number of days, at least 1" in capsys.readouterr().err
