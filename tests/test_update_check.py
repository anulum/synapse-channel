# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the PyPI update-check notice
"""Tests for :mod:`synapse_channel.update_check`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from http_server_helpers import LocalHttpResponder
from hub_e2e_helpers import _free_port
from synapse_channel import update_check as uc

# --- version parsing + ordering ----------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.31.0", (0, 31, 0)),
        ("1.2.0rc1", (1, 2, 0)),
        ("1.0", (1, 0)),
        ("2.0.0.post1", (2, 0, 0, 0)),
        ("", (0,)),
    ],
)
def test_parse_version(value: str, expected: tuple[int, ...]) -> None:
    assert uc._parse_version(value) == expected


@pytest.mark.parametrize(
    ("latest", "current", "newer"),
    [
        ("0.31.0", "0.30.0", True),
        ("0.30.0", "0.30.0", False),
        ("0.29.0", "0.30.0", False),
        ("1.0.0", "0.99.0", True),
    ],
)
def test_is_newer(latest: str, current: str, newer: bool) -> None:
    assert uc._is_newer(latest, current) is newer


# --- fetching from PyPI ------------------------------------------------------


def test_fetch_latest_success() -> None:
    with LocalHttpResponder(body=json.dumps({"info": {"version": "0.31.0"}}).encode()) as server:
        assert uc._fetch_latest(url=server.url) == "0.31.0"
    assert server.requests[0].method == "GET"


def test_fetch_latest_network_error() -> None:
    assert uc._fetch_latest(url=f"http://127.0.0.1:{_free_port()}") is None


def test_fetch_latest_bad_json() -> None:
    with LocalHttpResponder(body=b"not json") as server:
        assert uc._fetch_latest(url=server.url) is None


def test_fetch_latest_missing_key() -> None:
    with LocalHttpResponder(body=b'{"info": {}}') as server:
        assert uc._fetch_latest(url=server.url) is None


def test_fetch_latest_empty_version() -> None:
    with LocalHttpResponder(body=json.dumps({"info": {"version": ""}}).encode()) as server:
        assert uc._fetch_latest(url=server.url) is None


# --- cache read/write --------------------------------------------------------


def test_cache_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "cache.json"
    uc._write_cache(path, 123.0, "0.31.0")
    assert uc._read_cache(path) == (123.0, "0.31.0")


def test_read_cache_missing(tmp_path: Path) -> None:
    assert uc._read_cache(tmp_path / "nope.json") is None


def test_read_cache_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{garbage", encoding="utf-8")
    assert uc._read_cache(path) is None


def test_write_cache_unwritable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(Path, "write_text", boom)
    uc._write_cache(tmp_path / "c.json", 1.0, "0.31.0")  # swallowed, no raise


def test_cache_path_xdg(tmp_path: Path) -> None:
    assert uc._cache_path({"XDG_CACHE_HOME": str(tmp_path)}) == (
        tmp_path / "synapse-channel" / "update-check.json"
    )


def test_cache_path_default() -> None:
    path = uc._cache_path({})
    assert path.name == "update-check.json"
    assert "synapse-channel" in str(path)


# --- cache freshness logic ---------------------------------------------------


def test_latest_known_fresh_cache_skips_fetch(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    uc._write_cache(path, 1000.0, "0.31.0")

    def fail() -> str | None:
        raise AssertionError("a fresh cache must not refetch")

    assert uc._latest_known(1000.0 + 100, path, fail) == "0.31.0"


def test_latest_known_stale_refetches(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    uc._write_cache(path, 0.0, "0.30.0")
    when = uc.CACHE_TTL_SECONDS + 1
    assert uc._latest_known(when, path, lambda: "0.31.0") == "0.31.0"
    assert uc._read_cache(path) == (when, "0.31.0")  # cache refreshed


def test_latest_known_stale_fetch_fails_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    uc._write_cache(path, 0.0, "0.30.0")
    assert uc._latest_known(uc.CACHE_TTL_SECONDS + 1, path, lambda: None) == "0.30.0"


def test_latest_known_no_cache_fetch_fails(tmp_path: Path) -> None:
    assert uc._latest_known(1.0, tmp_path / "nope.json", lambda: None) is None


# --- the public notice -------------------------------------------------------


def test_update_notice_suppressed() -> None:
    assert (
        uc.update_notice("0.30.0", env={"SYNAPSE_NO_UPDATE_CHECK": "1"}, fetch=lambda: "0.99.0")
        is None
    )


def test_update_notice_newer(tmp_path: Path) -> None:
    notice = uc.update_notice(
        "0.30.0", env={}, now=1.0, cache_path=tmp_path / "c.json", fetch=lambda: "0.31.0"
    )
    assert notice is not None
    assert "0.31.0" in notice
    assert "pipx upgrade synapse-channel" in notice
    assert uc.SUPPRESS_ENV in notice


def test_update_notice_up_to_date(tmp_path: Path) -> None:
    assert (
        uc.update_notice(
            "0.31.0", env={}, now=1.0, cache_path=tmp_path / "c.json", fetch=lambda: "0.31.0"
        )
        is None
    )


def test_update_notice_offline(tmp_path: Path) -> None:
    assert (
        uc.update_notice(
            "0.30.0", env={}, now=1.0, cache_path=tmp_path / "c.json", fetch=lambda: None
        )
        is None
    )


def test_update_notice_uses_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Exercise the default env / now / cache_path / fetch resolution without a network call.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv(uc.SUPPRESS_ENV, raising=False)
    monkeypatch.setattr(uc, "_fetch_latest", lambda: None)
    assert uc.update_notice("0.30.0") is None
