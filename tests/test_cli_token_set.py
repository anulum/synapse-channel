# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — token-set management CLI tests
"""Exercise ``synapse token-set`` against real files and the hub's authenticator."""

from __future__ import annotations

import json
import math
import os
import stat
import threading
from pathlib import Path

import pytest

from synapse_channel.advisory_lock import locked
from synapse_channel.cli import build_parser, main
from synapse_channel.cli_token_set import (
    DEFAULT_VALID_DAYS,
    _cmd_list,
    _cmd_new,
    _write_secret_out,
)
from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.core.token_set import (
    TokenEntry,
    TokenSetAuthenticator,
    TokenSetError,
    connect_token_digest,
    load_token_set,
)
from synapse_channel.token_set_admin import (
    add_token,
    check_operator_secret,
    estimated_secret_bits,
    generate_secret,
    lock_path,
    parse_instant,
    render_token_set,
    retire_token,
    write_token_set,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
NOW = 1_800_000_000  # 2027-01-15T08:00:00Z


def _new(path: Path, *extra: str, now: int = NOW) -> int:
    """Run ``token-set new``; a generated secret is printed unless the test names a file."""
    if not {"--secret-file", "--secret-out"} & set(extra):
        extra = (*extra, "--print-secret")
    args = build_parser().parse_args(["token-set", "new", str(path), *extra])
    return _cmd_new(args, clock=lambda: now)


def _secret_file(tmp_path: Path, secret: str) -> Path:
    path = tmp_path / "operator.secret"
    path.write_text(secret + "\n")
    apply_owner_only_file(path)
    return path


def test_new_prints_the_secret_once_and_stores_only_its_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    assert _new(path, "--id", "ops-1") == 0
    captured = capsys.readouterr()
    secret = captured.out.strip()
    assert len(secret) == 43 and "\n" not in secret
    assert "added token ops-1" in captured.err and secret not in captured.err
    assert captured.err.rstrip().endswith("; valid now")
    text = path.read_text()
    assert secret not in text
    (entry,) = load_token_set(path)
    assert entry.digest == connect_token_digest(secret)
    assert entry.not_before is None
    assert entry.not_after == NOW + DEFAULT_VALID_DAYS * 86_400
    authenticator = TokenSetAuthenticator(path, clock=lambda: NOW)
    assert authenticator.authenticate_with_principal(secret, "A/any")[0] is True


def test_a_generated_secret_needs_a_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--secret-out`` or ``--print-secret`` nothing is generated, written or printed."""
    path = tmp_path / "tokens.json"
    args = build_parser().parse_args(["token-set", "new", str(path), "--id", "ops-1"])
    assert _cmd_new(args, clock=lambda: NOW) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--secret-out FILE" in captured.err and "--print-secret" in captured.err
    assert not path.exists()
    existing = tmp_path / "existing.json"
    assert _new(existing, "--id", "kept") == 0
    capsys.readouterr()
    before = existing.read_bytes()
    args = build_parser().parse_args(["token-set", "new", str(existing), "--id", "second"])
    assert _cmd_new(args, clock=lambda: NOW) == 2
    assert capsys.readouterr().out == ""
    assert existing.read_bytes() == before


@pytest.mark.parametrize("other", [["--secret-file", "operator.secret"], ["--secret-out", "x"]])
def test_print_secret_excludes_the_other_secret_options(
    tmp_path: Path, other: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    command = ["token-set", "new", str(tmp_path / "t.json"), "--id", "a", "--print-secret"]
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([*command, *other])
    assert exit_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_rotation_through_the_cli_reaches_a_running_authenticator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    assert _new(path, "--id", "old") == 0
    old = capsys.readouterr().out.strip()
    authenticator = TokenSetAuthenticator(path, clock=lambda: NOW)
    assert _new(path, "--id", "new", "--agents", "A/one, A/two") == 0
    new = capsys.readouterr().out.strip()
    assert authenticator.authenticate_with_principal(new, "A/two")[0] is True
    assert authenticator.authenticate_with_principal(old, "A/x")[0] is True
    assert main(["token-set", "retire", str(path), "--id", "old"]) == 0
    assert "1 token(s) remain" in capsys.readouterr().out
    assert authenticator.authenticate_with_principal(old, "A/x") == (
        False,
        "Invalid authentication token.",
        None,
    )
    assert authenticator.authenticate_with_principal(new, "A/one")[0] is True
    assert authenticator.authenticate_with_principal(new, "A/x")[0] is False
    assert authenticator.stale_reason is None


def test_an_invalid_result_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    assert _new(path, "--id", "ops-1") == 0
    capsys.readouterr()
    before = path.read_bytes()
    assert _new(path, "--id", "bad id!") == 1
    assert _new(path, "--id", "ops-2", "--agents", "A/one,") == 1
    assert _new(path, "--id", "ops-1") == 1
    assert (
        _new(
            path,
            "--id",
            "late",
            "--not-before",
            "2027-02-01T00:00:00Z",
            "--not-after",
            "2027-01-01T00:00:00Z",
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "id must match" in err
    assert "agents must be a list of names" in err
    assert "already exists" in err
    assert "not_after must be later than not_before" in err
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == [
        ".tokens.json.lockdir",
        "tokens.json",
    ]


def test_retire_refuses_a_corrupted_file_and_an_unknown_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    assert _new(path, "--id", "ops-1") == 0
    capsys.readouterr()
    assert main(["token-set", "retire", str(path), "--id", "missing"]) == 1
    assert "no token missing" in capsys.readouterr().err
    path.write_text('{"version": 1, "tokens": [')
    corrupted = path.read_bytes()
    assert main(["token-set", "retire", str(path), "--id", "ops-1"]) == 1
    assert "not valid JSON" in capsys.readouterr().err
    assert path.read_bytes() == corrupted


def test_retiring_the_last_token_leaves_a_valid_empty_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    assert _new(path, "--id", "only", "--no-expiry") == 0
    secret = capsys.readouterr().out.strip()
    assert main(["token-set", "retire", str(path), "--id", "only"]) == 0
    assert "admits no connection" in capsys.readouterr().err
    assert load_token_set(path) == ()
    authenticator = TokenSetAuthenticator(path)
    assert authenticator.is_empty
    assert authenticator.authenticate_with_principal(secret, "A/x")[0] is False


def test_validity_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "tokens.json"
    assert _new(path, "--id", "open", "--no-expiry") == 0
    assert _new(path, "--id", "days", "--days", "7", "--not-before", "2027-03-01T00:00:00Z") == 0
    assert _new(path, "--id", "until", "--not-after", "2027-06-30T12:00:00Z") == 0
    assert _new(path, "--id", "badtime", "--not-after", "2027-06-30") == 1
    assert "is not YYYY-MM-DDTHH:MM:SSZ" in capsys.readouterr().err
    entries = {entry.token_id: entry for entry in load_token_set(path)}
    assert set(entries) == {"open", "days", "until"}
    assert entries["open"].not_after is None
    start = parse_instant("2027-03-01T00:00:00Z")
    assert (entries["days"].not_before, entries["days"].not_after) == (start, start + 7 * 86_400)
    assert entries["until"].not_after == parse_instant("2027-06-30T12:00:00Z")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["token-set", "new", str(path), "--id", "x", "--days", "0"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["token-set", "new", str(path), "--id", "x", "--days", "3", "--no-expiry"]
        )


def test_operator_secrets_are_checked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "tokens.json"
    for weak, reason in [
        ("short-Secret-1", "estimated at"),
        ("a" * 64, "too few characters"),
        ("abcdefghijklmnop qrstuvwxyz", "whitespace"),
    ]:
        assert _new(path, "--id", "op", "--secret-file", str(_secret_file(tmp_path, weak))) == 1
        assert reason in capsys.readouterr().err
    assert not path.exists()
    strong = generate_secret()
    assert _new(path, "--id", "op", "--secret-file", str(_secret_file(tmp_path, strong))) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and strong not in captured.err
    (entry,) = load_token_set(path)
    assert entry.digest == connect_token_digest(strong)
    assert estimated_secret_bits("") == 0.0
    assert estimated_secret_bits("abcABC123") == pytest.approx(9 * math.log2(62))
    assert estimated_secret_bits("abc-") == pytest.approx(4 * math.log2(59))
    assert estimated_secret_bits(strong) >= 256
    check_operator_secret("Kq7#mZ2!pX9$wR4&vT6*nY8")
    with pytest.raises(TokenSetError, match="control"):
        check_operator_secret("abcdefghijklmnopqrstuvwxyz\x00ABCDEFGHIJ")


@posix_only
def test_secret_out_is_owner_only_and_never_overwritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    out = tmp_path / "ops.secret"
    assert _new(path, "--id", "ops-1", "--secret-out", str(out)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    secret = out.read_text().strip()
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_token_set(path)[0].digest == connect_token_digest(secret)
    before = path.read_bytes()
    assert _new(path, "--id", "ops-2", "--secret-out", str(out)) == 1
    assert out.read_text().strip() == secret
    assert path.read_bytes() == before
    fresh = tmp_path / "fresh.secret"
    assert _new(path, "--id", "ops-1", "--secret-out", str(fresh)) == 1
    assert not fresh.exists()


def test_list_never_shows_secrets_or_digests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    assert _new(path, "--id", "live", "--agents", "A/one") == 0
    assert _new(path, "--id", "gone", "--not-after", "2026-01-01T00:00:00Z") == 0
    assert "; expired now" in capsys.readouterr().err
    assert _new(path, "--id", "soon", "--not-before", "2028-01-01T00:00:00Z") == 0
    secrets = capsys.readouterr().out.split()
    digests = [entry.digest for entry in load_token_set(path)]
    listing = build_parser().parse_args(["token-set", "list", str(path)])
    assert _cmd_list(listing, clock=lambda: NOW) == 0
    text = capsys.readouterr().out
    assert "live  valid" in text and "for A/one" in text
    assert "gone  expired" in text and "soon  not yet valid" in text
    as_json = build_parser().parse_args(["token-set", "list", str(path), "--json"])
    assert _cmd_list(as_json, clock=lambda: NOW) == 0
    document = capsys.readouterr().out
    rows = {row["id"]: row for row in json.loads(document)["tokens"]}
    assert rows["live"]["agents"] == ["A/one"] and rows["live"]["not_before"] is None
    assert rows["gone"]["status"] == "expired"
    assert rows["soon"]["not_before"] == "2028-01-01T00:00:00Z"
    for hidden in [*secrets, *digests]:
        assert hidden not in text and hidden not in document


def test_list_reports_an_empty_missing_or_invalid_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tokens.json"
    assert main(["token-set", "list", str(path)]) == 1
    assert "synapse token-set list:" in capsys.readouterr().err
    assert _new(path, "--id", "only") == 0
    assert main(["token-set", "retire", str(path), "--id", "only"]) == 0
    capsys.readouterr()
    assert main(["token-set", "list", str(path)]) == 0
    assert "no tokens; the set admits no connection" in capsys.readouterr().out


def test_a_missing_directory_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "typo" / "tokens.json"
    with pytest.raises(TokenSetError, match="does not exist"):
        add_token(path, token_id="x", secret=generate_secret())
    assert not (tmp_path / "typo").exists()


def test_concurrent_changes_lose_nothing(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    failures: list[BaseException] = []

    def add(number: int) -> None:
        try:
            add_token(path, token_id=f"seat-{number}", secret=generate_secret())
        except BaseException as exc:  # collected for the assertion below
            failures.append(exc)

    workers = [threading.Thread(target=add, args=(number,)) for number in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert failures == []
    assert {entry.token_id for entry in load_token_set(path)} == {f"seat-{n}" for n in range(8)}
    assert retire_token(path, "seat-3") == 7


@posix_only
def test_the_lock_directory_is_private_and_the_parent_is_untouched(tmp_path: Path) -> None:
    folder = tmp_path / "shared"
    folder.mkdir()
    folder.chmod(0o755)
    path = folder / "tokens.json"
    add_token(path, token_id="x", secret=generate_secret())
    assert stat.S_IMODE(folder.stat().st_mode) == 0o755
    assert stat.S_IMODE(lock_path(path).parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@posix_only
def test_a_loose_or_linked_set_is_not_changed(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    add_token(path, token_id="x", secret=generate_secret())
    path.chmod(0o644)
    with pytest.raises(ValueError, match="--token-set"):
        retire_token(path, "x")
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="--token-set"):
        add_token(link, token_id="y", secret=generate_secret())
    assert link.is_symlink()
    assert [entry.token_id for entry in load_token_set(path)] == ["x"]


def test_render_refuses_entries_that_do_not_read_back() -> None:
    # A fractional instant cannot be stored; the read-back check catches it.
    entry = TokenEntry("x", "0" * 64, frozenset(), 1.5, None)
    with pytest.raises(TokenSetError, match="does not read back"):
        render_token_set([entry])


def test_a_failed_rename_leaves_no_staged_file(tmp_path: Path) -> None:
    occupied = tmp_path / "tokens.json"
    occupied.mkdir()
    with pytest.raises(OSError):
        write_token_set(occupied, [])
    assert [item.name for item in tmp_path.iterdir()] == ["tokens.json"]
    assert occupied.is_dir()


def test_a_failed_secret_out_write_leaves_no_partial_file(tmp_path: Path) -> None:
    # A lone surrogate cannot be encoded, so the write fails after the file
    # was created; the helper must remove what it created.
    out = tmp_path / "ops.secret"
    with pytest.raises(UnicodeEncodeError):
        _write_secret_out(out, "\ud800")
    assert not out.exists()
    out.write_text("kept\n")
    with pytest.raises(FileExistsError):
        _write_secret_out(out, generate_secret())
    assert out.read_text() == "kept\n"


def test_fractional_instants_are_refused_plainly(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    with pytest.raises(TokenSetError, match="not_before must be a whole number of seconds"):
        add_token(path, token_id="x", secret=generate_secret(), not_before=NOW + 0.5)
    with pytest.raises(TokenSetError, match="not_after must be a whole number of seconds"):
        add_token(path, token_id="x", secret=generate_secret(), not_after=NOW + 0.5)
    with pytest.raises(TokenSetError, match="not_after must be a whole number of seconds"):
        add_token(path, token_id="x", secret=generate_secret(), not_after=float("inf"))
    assert not path.exists()


def test_a_held_lock_is_waited_for_only_so_long(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    add_token(path, token_id="x", secret=generate_secret())
    before = path.read_bytes()
    with locked(lock_path(path), blocking=True, purpose="test"):
        with pytest.raises(TokenSetError, match="held its lock for 0.2 s"):
            add_token(path, token_id="y", secret=generate_secret(), lock_timeout=0.2)
        with pytest.raises(TokenSetError, match="held its lock"):
            retire_token(path, "x", lock_timeout=0.1)
    assert path.read_bytes() == before
    assert retire_token(path, "x") == 0
