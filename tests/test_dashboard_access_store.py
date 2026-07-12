# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard access store parser and permission tests
"""Exercise strict JSON, owner-only files, token bounds, and uniqueness."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from synapse_channel.dashboard_access_store import (
    MAX_ACCESS_FILE_BYTES,
    MAX_PRINCIPALS,
    MAX_TOKEN_BYTES,
    load_dashboard_access_policy,
)


def _private(path: Path, contents: bytes | str) -> Path:
    data = contents.encode("utf-8") if isinstance(contents, str) else contents
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def _entry(
    principal_id: str,
    role: str,
    token_file: str,
    operator_name: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": principal_id,
        "role": role,
        "token_file": token_file,
    }
    if operator_name is not None:
        result["operator_name"] = operator_name
    return result


def _policy(path: Path, principals: list[object], **changes: object) -> Path:
    payload: dict[str, object] = {"version": 1, "principals": principals}
    payload.update(changes)
    return _private(path, json.dumps(payload))


def test_loads_viewer_operator_admin_with_relative_private_token_files(tmp_path: Path) -> None:
    _private(tmp_path / "viewer.token", "v" * 32 + "\n")
    _private(tmp_path / "operator.token", "o" * 40 + "\r\n")
    _private(tmp_path / "admin.token", "a" * 48)
    path = _policy(
        tmp_path / "access.json",
        [
            _entry("review", "viewer", "viewer.token"),
            _entry("ops-a", "operator", "operator.token", "operator:studio/ops-a"),
            _entry("owner", "admin", "admin.token", "operator:studio/owner"),
        ],
    )

    policy = load_dashboard_access_policy(path, operator_armed=True)

    assert policy.reads_gated is True
    assert [item.principal.role for item in policy.credentials] == [
        "viewer",
        "operator",
        "admin",
    ]
    assert policy.resolve_credential(f"Bearer {'v' * 32}").capabilities.read is True  # type: ignore[union-attr]
    assert policy.resolve_credential(f"Bearer {'o' * 40}").operator_name == (  # type: ignore[union-attr]
        "operator:studio/ops-a"
    )
    assert "o" * 40 not in repr(policy)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not json", "strict UTF-8 JSON"),
        (b"\xff", "strict UTF-8 JSON"),
        (b'{"version":1,"version":1,"principals":[]}', "strict UTF-8 JSON"),
        (b'{"version":NaN,"principals":[]}', "strict UTF-8 JSON"),
        (b'{"version":true,"principals":[]}', "version must be 1"),
        (b'{"version":2,"principals":[]}', "version must be 1"),
        (b'{"version":1,"principals":[],"extra":1}', "exactly version"),
        (b"[]", "exactly version"),
    ],
)
def test_rejects_malformed_top_level_json(tmp_path: Path, raw: bytes, message: str) -> None:
    path = _private(tmp_path / "access.json", raw)
    with pytest.raises(ValueError, match=message):
        load_dashboard_access_policy(path, operator_armed=False)


def test_principal_count_is_bounded(tmp_path: Path) -> None:
    for count in (0, MAX_PRINCIPALS + 1):
        path = _policy(tmp_path / f"access-{count}.json", [{}] * count)
        with pytest.raises(ValueError, match="1..64"):
            load_dashboard_access_policy(path, operator_armed=False)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("bad", "must be an object"),
        (_entry("review", "root", "token"), "unknown role"),
        (_entry(" bad", "viewer", "token"), "invalid id"),
        ({"id": "review", "role": "viewer"}, "unknown or missing"),
        ({**_entry("review", "viewer", "token"), "extra": 1}, "unknown or missing"),
        (
            _entry("review", "viewer", "token", "operator:studio/review"),
            "unknown or missing",
        ),
        (_entry("ops", "operator", "token", "operator:studio/ops"), "require --operator"),
    ],
)
def test_rejects_invalid_principal_shapes(
    tmp_path: Path,
    entry: object,
    message: str,
) -> None:
    _private(tmp_path / "token", "t" * 32)
    path = _policy(tmp_path / "access.json", [entry])
    with pytest.raises(ValueError, match=message):
        load_dashboard_access_policy(path, operator_armed=False)


def test_rejects_invalid_operator_relay_while_armed(tmp_path: Path) -> None:
    _private(tmp_path / "token", "t" * 32)
    path = _policy(
        tmp_path / "access.json",
        [_entry("ops", "operator", "token", "bad relay!")],
    )
    with pytest.raises(ValueError, match="invalid relay"):
        load_dashboard_access_policy(path, operator_armed=True)


def test_rejects_invalid_token_file_field(tmp_path: Path) -> None:
    path = _policy(
        tmp_path / "access.json",
        [{"id": "review", "role": "viewer", "token_file": 7}],
    )
    with pytest.raises(ValueError, match="invalid token file"):
        load_dashboard_access_policy(path, operator_armed=False)


@pytest.mark.parametrize("duplicate", ["id", "token", "relay"])
def test_rejects_duplicate_security_bindings(tmp_path: Path, duplicate: str) -> None:
    _private(tmp_path / "one", "1" * 32)
    _private(tmp_path / "two", ("1" if duplicate == "token" else "2") * 32)
    first = _entry("one", "operator", "one", "operator:studio/one")
    second = _entry(
        "one" if duplicate == "id" else "two",
        "operator",
        "two",
        "operator:studio/one" if duplicate == "relay" else "operator:studio/two",
    )
    path = _policy(tmp_path / "access.json", [first, second])
    label = duplicate if duplicate != "token" else "bearer token"
    with pytest.raises(ValueError, match=f"{label}.*unique"):
        load_dashboard_access_policy(path, operator_armed=True)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"x" * 31, "byte length"),
        (b"x" * (MAX_TOKEN_BYTES + 1), "byte length"),
        (b"\xff" * 32, "valid UTF-8"),
        (b"x" * 31 + b" ", "whitespace"),
        (b"x" * 31 + b"\x7f", "control"),
        (b"x" * 32 + b"\n\n", "whitespace"),
    ],
)
def test_rejects_invalid_token_contents(tmp_path: Path, contents: bytes, message: str) -> None:
    _private(tmp_path / "token", contents)
    path = _policy(tmp_path / "access.json", [_entry("review", "viewer", "token")])
    with pytest.raises(ValueError, match=message):
        load_dashboard_access_policy(path, operator_armed=False)


def test_rejects_missing_nonprivate_and_symlink_files(tmp_path: Path) -> None:
    access = _policy(tmp_path / "access.json", [_entry("review", "viewer", "missing")])
    with pytest.raises(ValueError, match="token file is unavailable"):
        load_dashboard_access_policy(access, operator_armed=False)

    token = _private(tmp_path / "token", "x" * 32)
    token.chmod(0o640)
    with pytest.raises(ValueError, match="owner-readable and private"):
        load_dashboard_access_policy(
            _policy(tmp_path / "access-private.json", [_entry("review", "viewer", "token")]),
            operator_armed=False,
        )
    token.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(token)
    with pytest.raises(ValueError, match="non-symlink"):
        load_dashboard_access_policy(
            _policy(tmp_path / "access-link.json", [_entry("review", "viewer", "link")]),
            operator_armed=False,
        )


def test_rejects_bad_access_file_surface_and_size(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="access file is unavailable"):
        load_dashboard_access_policy(missing, operator_armed=False)
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_dashboard_access_policy(directory, operator_armed=False)
    target = _private(tmp_path / "target", "{}")
    link = tmp_path / "access-link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular non-symlink"):
        load_dashboard_access_policy(link, operator_armed=False)
    oversized = _private(tmp_path / "oversized.json", b"x" * (MAX_ACCESS_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        load_dashboard_access_policy(oversized, operator_armed=False)
    private = _private(tmp_path / "public.json", "{}")
    private.chmod(0o604)
    with pytest.raises(ValueError, match="owner-readable and private"):
        load_dashboard_access_policy(private, operator_armed=False)


def test_access_file_owner_check_is_posix_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _private(tmp_path / "token", "x" * 32)
    path = _policy(tmp_path / "access.json", [_entry("review", "viewer", str(token))])
    if os.name != "posix":
        pytest.skip("owner id check applies on POSIX")
    monkeypatch.setattr(os, "geteuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(ValueError, match="owner-readable and private"):
        load_dashboard_access_policy(path, operator_armed=False)


def test_absolute_token_path_and_open_race_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _private(tmp_path / "token", "x" * 32)
    path = _policy(tmp_path / "access.json", [_entry("review", "viewer", str(token.resolve()))])
    assert load_dashboard_access_policy(path, operator_armed=False).credentials

    real_open = os.open
    monkeypatch.setattr(os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
    with pytest.raises(ValueError, match="access file is unavailable"):
        load_dashboard_access_policy(path, operator_armed=False)
    monkeypatch.setattr(os, "open", real_open)

    real_fstat = os.fstat
    monkeypatch.setattr(
        os,
        "fstat",
        lambda descriptor: os.stat_result(
            tuple(real_fstat(descriptor))[:1]
            + (real_fstat(descriptor).st_ino + 1,)
            + tuple(real_fstat(descriptor))[2:]
        ),
    )
    with pytest.raises(ValueError, match="changed while opening"):
        load_dashboard_access_policy(path, operator_armed=False)
