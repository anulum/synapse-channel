# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the portable owner-only path floor
"""Exercise portable user identity and owner-only file/directory proofs."""

from __future__ import annotations

import ctypes
import os
import stat
import subprocess
import sys
import types
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pytest

from _platform_caps import create_link_or_skip
from synapse_channel.core import secure_path_windows as win_sec
from synapse_channel.core.private_dir import PrivateDirError, ensure_private_dir
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.core.secure_path import (
    PortableUserKey,
    SecurePathError,
    WindowsAce,
    apply_owner_only_dir,
    apply_owner_only_file,
    assert_owner_only_descriptor,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
    create_owner_only_file,
    current_user_key,
    evaluate_windows_owner_only_policy,
    owner_only_floor_available,
    private_temp_user_segment,
    read_owner_only_file_bytes,
)


def test_owner_only_floor_is_available_on_posix_and_windows() -> None:
    assert owner_only_floor_available() is (os.name in ("posix", "nt"))


def test_current_user_key_is_stable_and_hashable() -> None:
    first = current_user_key()
    second = current_user_key()
    assert first == second
    assert isinstance(first, PortableUserKey)
    assert first.kind in {"posix_uid", "windows_sid"}
    assert first.value
    assert {first, second} == {first}
    suffix = private_temp_user_segment()
    assert suffix
    assert "\\" not in suffix


def test_apply_and_assert_owner_only_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "secret.bin"
    path.write_bytes(b"token-value\n")
    apply_owner_only_file(path)
    assert_owner_only_file_path(path, purpose="unit-secret")
    assert read_secret_file(path, flag="--unit-token-file") == "token-value"


def test_create_owner_only_file_creates_once_and_keeps_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite3"
    assert create_owner_only_file(path) is True
    assert path.read_bytes() == b""
    assert_owner_only_file_path(path, purpose="store")
    path.write_bytes(b"kept")
    assert create_owner_only_file(path) is False
    assert path.read_bytes() == b"kept"


def test_apply_and_assert_owner_only_dir_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "private-leaf"
    path.mkdir()
    apply_owner_only_dir(path)
    assert_owner_only_dir_path(path, purpose="unit-dir")
    assert ensure_private_dir(path, purpose="unit-dir") == path


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode-bit loosen path")
def test_assert_owner_only_file_refuses_group_readable_on_posix(tmp_path: Path) -> None:
    path = tmp_path / "loose"
    path.write_text("x\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(SecurePathError, match="accessible by other users"):
        assert_owner_only_file_path(path, purpose="loose")


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory mode-bit loosen path")
def test_assert_owner_only_dir_refuses_group_readable_on_posix(tmp_path: Path) -> None:
    directory = tmp_path / "group_readable_dir"
    directory.mkdir()
    directory.chmod(0o750)
    with pytest.raises(SecurePathError, match="accessible by other users"):
        assert_owner_only_dir_path(directory, purpose="group dir")
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(SecurePathError, match="accessible by other users"):
            assert_owner_only_descriptor(fd, path=directory, purpose="group dir", directory=True)
    finally:
        os.close(fd)


def test_read_owner_only_file_bytes_enforces_max_bytes_boundary(tmp_path: Path) -> None:
    from synapse_channel.core.secure_path import _read_bounded_file_bytes, open_nofollow_leaf

    limit = 64
    exact = tmp_path / "exact.secret"
    assert create_owner_only_file(exact) is True
    exact_payload = b"k" * limit
    exact.write_bytes(exact_payload)
    assert read_owner_only_file_bytes(exact, purpose="boundary", max_bytes=limit) == exact_payload

    over = tmp_path / "over.secret"
    assert create_owner_only_file(over) is True
    over.write_bytes(b"k" * (limit + 1))
    with pytest.raises(SecurePathError, match=f"exceeds the {limit}-byte secret-file limit"):
        read_owner_only_file_bytes(over, purpose="boundary", max_bytes=limit)

    # Also verify _read_bounded_file_bytes enforces max_bytes if a file grows after fstat.
    fd = open_nofollow_leaf(over)
    try:
        with pytest.raises(SecurePathError, match=f"exceeds the {limit}-byte secret-file limit"):
            _read_bounded_file_bytes(fd, size=limit, max_bytes=limit, path=over, purpose="boundary")
    finally:
        os.close(fd)


def test_read_owner_only_file_bytes_reads_multi_chunk_payload(tmp_path: Path) -> None:
    # Larger than the 64 KiB read chunk so single-chunk truncation fails.
    payload = bytes((index * 31) % 251 for index in range(200_000))
    target = tmp_path / "large.secret"
    assert create_owner_only_file(target) is True
    target.write_bytes(payload)
    assert read_owner_only_file_bytes(target, purpose="large") == payload
    assert read_owner_only_file_bytes(target, purpose="large", max_bytes=len(payload)) == payload
    assert (
        read_owner_only_file_bytes(target, purpose="large", max_bytes=len(payload) + 4096)
        == payload
    )
    with pytest.raises(SecurePathError, match=f"exceeds the {len(payload) - 1}-byte"):
        read_owner_only_file_bytes(target, purpose="large", max_bytes=len(payload) - 1)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership monkeypatch")
def test_assert_owner_only_file_refuses_foreign_owner_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "owned"
    path.write_text("x\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(os, "geteuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(SecurePathError, match="not owned by the effective user"):
        assert_owner_only_file_path(path, purpose="foreign")


@pytest.mark.skipif(os.name != "posix", reason="symlink leaf semantics")
def test_assert_owner_only_file_refuses_symlink_leaf(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text("x\n", encoding="utf-8")
    real.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(SecurePathError, match="symlink"):
        assert_owner_only_file_path(link, purpose="link")


def test_private_dir_and_secret_file_integration(tmp_path: Path) -> None:
    """End-to-end: private dir holds an owner-only secret loadable by the floor."""
    directory = ensure_private_dir(tmp_path / "runtime", purpose="integration runtime")
    secret = directory / "token"
    secret.write_text("  integration-secret\n", encoding="utf-8")
    apply_owner_only_file(secret)
    assert read_secret_file(secret, flag="--integration-token-file") == "integration-secret"


def test_secret_file_error_types_remain_stable_on_missing(tmp_path: Path) -> None:
    with pytest.raises(SecretFileError, match="--missing-token-file"):
        read_secret_file(tmp_path / "absent", flag="--missing-token-file")


def test_private_dir_error_types_remain_stable_on_file_clobber(tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("file", encoding="utf-8")
    with pytest.raises(PrivateDirError):
        ensure_private_dir(target, purpose="clobber")


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode after ensure")
def test_private_dir_mode_is_700_on_posix(tmp_path: Path) -> None:
    target = ensure_private_dir(tmp_path / "mode-leaf", purpose="mode")
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_evaluate_windows_owner_only_policy_accepts_owner_system_and_deny_everyone() -> None:
    path = Path("C:/users/me/secret")
    current = "S-1-5-21-100"
    evaluate_windows_owner_only_policy(
        path=path,
        purpose="policy",
        owner_sid=current,
        current_sid=current,
        dacl_present=True,
        aces=(
            WindowsAce(ace_type=0, mask=0x001F01FF, sid=current),
            WindowsAce(ace_type=0, mask=0x001F01FF, sid="S-1-5-18"),
            WindowsAce(ace_type=0, mask=0x001F01FF, sid="S-1-5-32-544"),
            # Access-denied ACE for Everyone restricts rather than grants and is accepted.
            WindowsAce(ace_type=1, mask=0x001F01FF, sid="S-1-1-0"),
        ),
    )
    evaluate_windows_owner_only_policy(
        path=path,
        purpose="policy",
        owner_sid="S-1-5-32-544",
        current_sid=current,
        dacl_present=True,
        aces=(
            WindowsAce(ace_type=0, mask=0x001F01FF, sid=current),
            WindowsAce(ace_type=0, mask=0x001F01FF, sid="S-1-5-32-544"),
            WindowsAce(ace_type=0, mask=0x001F01FF, sid="S-1-3-4"),
        ),
    )
    evaluate_windows_owner_only_policy(
        path=path,
        purpose="policy",
        owner_sid="S-1-5-32-544",
        current_sid=current,
        dacl_present=True,
        aces=(WindowsAce(ace_type=0, mask=0x001F01FF, sid="S-1-5-32-544"),),
    )


def test_evaluate_windows_owner_only_policy_refuses_everyone_null_dacl_and_unknown_aces() -> None:
    path = Path("C:/users/me/secret")
    current = "S-1-5-21-100"
    with pytest.raises(SecurePathError, match="NULL DACL"):
        evaluate_windows_owner_only_policy(
            path=path,
            purpose="policy",
            owner_sid=current,
            current_sid=current,
            dacl_present=False,
            aces=(),
        )
    with pytest.raises(SecurePathError, match="not owned by the effective user"):
        evaluate_windows_owner_only_policy(
            path=path,
            purpose="policy",
            owner_sid="S-1-5-21-other",
            current_sid=current,
            dacl_present=True,
            aces=(),
        )
    with pytest.raises(SecurePathError, match="accessible by other principals"):
        evaluate_windows_owner_only_policy(
            path=path,
            purpose="policy",
            owner_sid=current,
            current_sid=current,
            dacl_present=True,
            aces=(WindowsAce(ace_type=0, mask=0x00120089, sid="S-1-1-0"),),
        )
    # Unknown ACE types fail closed: object allow (0x5), callback allow (0x9), audit (0x2).
    for unknown_type in (0x5, 0x9, 0x2):
        with pytest.raises(SecurePathError, match=f"unsupported ACE type {unknown_type}"):
            evaluate_windows_owner_only_policy(
                path=path,
                purpose="policy",
                owner_sid=current,
                current_sid=current,
                dacl_present=True,
                aces=(
                    WindowsAce(ace_type=0, mask=0x001F01FF, sid=current),
                    WindowsAce(ace_type=unknown_type, mask=0x00120089, sid="S-1-1-0"),
                ),
            )
    # An allow ACE with no interesting bits is not a grant, so the next ACE is read.
    evaluate_windows_owner_only_policy(
        path=path,
        purpose="policy",
        owner_sid=current,
        current_sid=current,
        dacl_present=True,
        aces=(
            WindowsAce(ace_type=0, mask=0, sid="S-1-1-0"),
            WindowsAce(ace_type=0, mask=0x001F01FF, sid=current),
        ),
    )


def test_owner_birth_sddl_matches_fleet_template_byte_for_byte() -> None:
    """Pin Core's birth SDDL to the fleet tier's birth template until unified creation."""
    sid = "S-1-5-21-3623811015-3361044348-30300820-1013"
    assert win_sec.owner_birth_sddl(sid, directory=False) == (
        f"O:{sid}D:P(A;;FA;;;{sid})(A;;FA;;;SY)"
    )
    assert win_sec.owner_birth_sddl(sid, directory=True) == (
        f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)"
    )
    with pytest.raises(SecurePathError, match="invalid Windows owner SID"):
        win_sec.owner_birth_sddl("not-a-sid")
    with pytest.raises(SecurePathError, match="invalid Windows owner SID"):
        win_sec.owner_birth_sddl("")


def test_dispatch_branches_when_forced_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force Windows dispatch on a POSIX host so call wiring is covered."""
    import synapse_channel.core.private_dir as private_dir
    import synapse_channel.core.secure_path as module

    path = tmp_path / "token"
    path.write_bytes(b"win-secret\n")
    current = "S-1-5-21-force"

    monkeypatch.setattr(module, "_WINDOWS", True)
    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(win_sec, "windows_current_user_sid", lambda: current)
    monkeypatch.setattr(win_sec, "windows_apply_owner_only", lambda p, directory=False: None)
    monkeypatch.setattr(
        win_sec,
        "windows_assert_owner_only_path",
        lambda p, purpose, directory=False, require_single_link=False: None,
    )
    monkeypatch.setattr(
        win_sec,
        "windows_assert_owner_only_descriptor",
        lambda fd, path, purpose, directory=False, require_single_link=False: None,
    )
    monkeypatch.setattr(win_sec, "windows_create_owner_only_file", lambda p: True)
    monkeypatch.setattr(module, "owner_only_floor_available", lambda: True)

    key = module.current_user_key()
    assert key.kind == "windows_sid"
    assert key.path_suffix() == "1-5-21-force"
    assert module.create_owner_only_file(path) is True
    module.apply_owner_only_file(path)
    module.apply_owner_only_dir(tmp_path)
    module.assert_owner_only_file_path(path, purpose="forced", require_single_link=True)
    module.assert_owner_only_dir_path(tmp_path, purpose="forced-dir")
    module.assert_owner_only_descriptor(0, path=path, purpose="forced-fd", require_single_link=True)

    monkeypatch.setattr(private_dir, "_WINDOWS", True)
    monkeypatch.setattr(private_dir, "_POSIX", False)
    monkeypatch.setattr(private_dir, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(private_dir, "apply_owner_only_dir", lambda p: None)
    monkeypatch.setattr(private_dir, "assert_owner_only_dir_path", lambda p, purpose: None)
    leaf = tmp_path / "win-leaf"
    assert private_dir.ensure_private_dir(leaf, purpose="win leaf") == leaf
    nested = tmp_path / "a" / "b" / "c"
    assert private_dir.ensure_private_dir(nested, parents=True, purpose="win nested") == nested


def test_secret_files_windows_opens_first_and_validates_same_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows secret_files opens the leaf first and proves custody on that exact fd."""
    import synapse_channel.core.secret_files as secret_files

    path = tmp_path / "t"
    path.write_text("secret-value\n", encoding="utf-8")
    monkeypatch.setattr(secret_files, "_WINDOWS", True)
    monkeypatch.setattr(secret_files, "owner_only_floor_available", lambda: True)

    events: list[tuple[str, int]] = []
    real_open = secret_files.open_nofollow_descriptor

    def tracked_open(p: Path, *, directory: bool = False) -> int:
        fd = real_open(p, directory=directory)
        events.append(("open", fd))
        return fd

    def tracked_assert(
        descriptor: int,
        *,
        path: str | Path,
        purpose: str,
        directory: bool = False,
        require_single_link: bool = False,
    ) -> None:
        del path, purpose, directory, require_single_link
        events.append(("assert_fd", descriptor))

    monkeypatch.setattr(secret_files, "open_nofollow_descriptor", tracked_open)
    monkeypatch.setattr(secret_files, "assert_owner_only_descriptor", tracked_assert)

    assert secret_files.read_secret_file(path, flag="--t") == "secret-value"
    assert len(events) == 3
    assert events[0][0] == "open"
    assert events[1] == ("assert_fd", events[0][1])
    assert events[2] == ("assert_fd", events[0][1])


def test_secret_files_windows_error_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.secret_files as secret_files
    from synapse_channel.core.secure_path import SecurePathError as SPE

    path = tmp_path / "t"
    path.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(secret_files, "_WINDOWS", True)
    monkeypatch.setattr(secret_files, "owner_only_floor_available", lambda: True)

    def raise_for(purpose_fragment: str) -> None:
        def _raise(
            _descriptor: int,
            *,
            path: str | Path,
            purpose: str,
            directory: bool = False,
            require_single_link: bool = False,
        ) -> None:
            del path, directory, require_single_link
            raise SPE(f"{purpose}: {purpose_fragment}")

        monkeypatch.setattr(secret_files, "assert_owner_only_descriptor", _raise)

    raise_for("not owned by the effective user")
    with pytest.raises(SecretFileError, match="effective hub service user"):
        secret_files.read_secret_file(path, flag="--t")

    raise_for("accessible by other principals (ACE for S-1-1-0)")
    with pytest.raises(SecretFileError, match="accessible by other users"):
        secret_files.read_secret_file(path, flag="--t")

    raise_for("has unsupported ACE type 5; refused")
    with pytest.raises(SecretFileError, match="accessible by other users"):
        secret_files.read_secret_file(path, flag="--t")

    raise_for("has 2 hard links")
    with pytest.raises(SecretFileError, match="hard links"):
        secret_files.read_secret_file(path, flag="--t")

    raise_for("is a symlink; refused")
    with pytest.raises(SecretFileError, match="symlink refused"):
        secret_files.read_secret_file(path, flag="--t")

    raise_for("is not a regular file")
    with pytest.raises(SecretFileError, match="not a regular secret file"):
        secret_files.read_secret_file(path, flag="--t")

    raise_for("validation is unavailable on this platform")
    with pytest.raises(SecretFileError, match="unavailable"):
        secret_files.read_secret_file(path, flag="--t")

    raise_for("unexpected security failure")
    with pytest.raises(SecretFileError, match="unexpected security failure"):
        secret_files.read_secret_file(path, flag="--t")


def test_private_dir_windows_error_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import synapse_channel.core.private_dir as private_dir
    from synapse_channel.core.secure_path import SecurePathError as SPE

    monkeypatch.setattr(private_dir, "_WINDOWS", True)
    monkeypatch.setattr(private_dir, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(private_dir, "apply_owner_only_dir", lambda p: None)

    target = tmp_path / "leaf"

    def set_assert(message: str) -> None:
        monkeypatch.setattr(
            private_dir,
            "assert_owner_only_dir_path",
            lambda p, purpose: (_ for _ in ()).throw(SPE(message)),
        )

    set_assert("symlink refused")
    with pytest.raises(PrivateDirError, match="symlink"):
        private_dir.ensure_private_dir(target, purpose="p")

    target.mkdir(exist_ok=True)
    set_assert("is not a directory")
    with pytest.raises(PrivateDirError, match="not a directory"):
        private_dir.ensure_private_dir(target, purpose="p")

    set_assert("not owned by the effective user")
    with pytest.raises(PrivateDirError, match="not owned by the effective user"):
        private_dir.ensure_private_dir(target, purpose="p")

    set_assert("accessible by other principals")
    with pytest.raises(PrivateDirError, match="accessible by other users"):
        private_dir.ensure_private_dir(target, purpose="p")

    set_assert("unavailable on this platform")
    with pytest.raises(PrivateDirError, match="unavailable"):
        private_dir.ensure_private_dir(target, purpose="p")

    set_assert("generic win failure")
    with pytest.raises(PrivateDirError, match="generic win failure"):
        private_dir.ensure_private_dir(target, purpose="p")


def test_floor_unavailable_and_unsupported_platform_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.secure_path as module

    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(module, "_WINDOWS", False)
    assert module.owner_only_floor_available() is False
    with pytest.raises(SecurePathError, match="portable user identity is unavailable"):
        module.current_user_key()
    with pytest.raises(SecurePathError, match="cannot apply owner-only mode"):
        module.apply_owner_only_file(tmp_path / "x")
    with pytest.raises(SecurePathError, match="cannot apply owner-only directory"):
        module.apply_owner_only_dir(tmp_path)
    with pytest.raises(SecurePathError, match="unavailable"):
        module.assert_owner_only_descriptor(0, path=tmp_path / "x", purpose="p")
    with pytest.raises(SecurePathError, match="unavailable"):
        module.assert_owner_only_file_path(tmp_path / "x", purpose="p")
    with pytest.raises(SecurePathError, match="unavailable"):
        module.assert_owner_only_dir_path(tmp_path, purpose="p")
    with pytest.raises(OSError, match="unavailable"):
        module.open_nofollow_leaf(tmp_path / "x")

    class IncompletePosix:
        name = "posix"

    monkeypatch.setattr(module, "os", IncompletePosix())
    monkeypatch.setattr(module, "_POSIX", True)
    monkeypatch.setattr(module, "_WINDOWS", False)
    assert module.owner_only_floor_available() is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX assert helpers")
def test_posix_assert_helpers_cover_error_paths(tmp_path: Path) -> None:
    import synapse_channel.core.secure_path as module

    missing = tmp_path / "gone"
    with pytest.raises(SecurePathError, match="cannot stat"):
        module.assert_owner_only_file_path(missing, purpose="p")
    with pytest.raises(SecurePathError, match="cannot stat"):
        module.assert_owner_only_dir_path(missing, purpose="p")
    with pytest.raises(SecurePathError, match="cannot stat"):
        module.assert_owner_only_descriptor(-1, path=missing, purpose="p")

    file_path = tmp_path / "f"
    file_path.write_text("x", encoding="utf-8")
    file_path.chmod(0o600)
    with pytest.raises(SecurePathError, match="not a directory"):
        module.assert_owner_only_dir_path(file_path, purpose="p")
    dir_path = tmp_path / "d"
    dir_path.mkdir(mode=0o755)
    dir_path.chmod(0o755)
    with pytest.raises(SecurePathError, match="accessible by other users"):
        module.assert_owner_only_dir_path(dir_path, purpose="p")

    tight = tmp_path / "tight"
    tight.mkdir(mode=0o700)
    tight.chmod(0o700)
    module.assert_owner_only_dir_path(tight, purpose="p")

    # Descriptor-level POSIX checks: file + directory + wrong kind + hard links.
    file_fd = module.open_nofollow_leaf(file_path)
    try:
        module.assert_owner_only_descriptor(file_fd, path=file_path, purpose="p")
        with pytest.raises(SecurePathError, match="not a directory"):
            module.assert_owner_only_descriptor(
                file_fd, path=file_path, purpose="p", directory=True
            )
    finally:
        os.close(file_fd)

    tight_fd = module.open_nofollow_leaf(tight, directory=True)
    try:
        module.assert_owner_only_descriptor(tight_fd, path=tight, purpose="p", directory=True)
        with pytest.raises(SecurePathError, match="not a regular file"):
            module.assert_owner_only_descriptor(tight_fd, path=tight, purpose="p", directory=False)
    finally:
        os.close(tight_fd)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(os, "geteuid", lambda: tight.stat().st_uid + 1)
    try:
        with pytest.raises(SecurePathError, match="not owned by the effective user"):
            module.assert_owner_only_dir_path(tight, purpose="p")
    finally:
        monkey.undo()

    link_dir = tmp_path / "dlink"
    link_dir.symlink_to(tight, target_is_directory=True)
    with pytest.raises(SecurePathError, match="symlink"):
        module.assert_owner_only_dir_path(link_dir, purpose="p")

    only = tmp_path / "only"
    only.write_text("x", encoding="utf-8")
    only.chmod(0o600)
    info = only.stat()
    module.assert_posix_owner_only_file_info(info, path=only, purpose="p")
    hard = tmp_path / "hard"
    os.link(only, hard)
    with pytest.raises(SecurePathError, match="hard links"):
        module.assert_posix_owner_only_file_info(
            hard.stat(), path=hard, purpose="p", require_single_link=True
        )
    hard_fd = module.open_nofollow_leaf(hard)
    try:
        with pytest.raises(SecurePathError, match="hard links"):
            module.assert_owner_only_descriptor(
                hard_fd, path=hard, purpose="p", require_single_link=True
            )
    finally:
        os.close(hard_fd)

    dir_info = dir_path.stat()
    with pytest.raises(SecurePathError, match="not a regular file"):
        module.assert_posix_owner_only_file_info(dir_info, path=dir_path, purpose="p")

    class OsShim:
        name = "posix"

        def getuid(self) -> int:
            return 42

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(module, "os", OsShim())
        monkey.setattr(module, "_POSIX", True)
        monkey.setattr(module, "_WINDOWS", False)
        key = module.current_user_key()
        assert key.value == "42"
    finally:
        monkey.undo()

    fd = module.open_nofollow_leaf(only)
    os.close(fd)
    fd = module.open_nofollow_leaf(dir_path, directory=True)
    os.close(fd)


def test_windows_secret_files_and_private_dir_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.private_dir as private_dir
    import synapse_channel.core.secret_files as secret_files
    from synapse_channel.core.secure_path import SecurePathError as SPE

    path = tmp_path / "leaf"
    path.write_text("data\n", encoding="utf-8")

    # secret_files Windows: open failure, size limit, drift, re-assert failure, utf-8
    monkeypatch.setattr(secret_files, "_WINDOWS", True)
    monkeypatch.setattr(secret_files, "_POSIX", False)
    monkeypatch.setattr(secret_files, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(secret_files, "assert_owner_only_descriptor", lambda *a, **k: None)

    def boom_open(*_a: object, **_k: object) -> int:
        raise OSError("open refused")

    monkeypatch.setattr(secret_files, "open_nofollow_descriptor", boom_open)
    with pytest.raises(SecretFileError, match="cannot securely open"):
        secret_files.read_secret_file(path, flag="--t")

    monkeypatch.undo()
    monkeypatch.setattr(secret_files, "_WINDOWS", True)
    monkeypatch.setattr(secret_files, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(secret_files, "assert_owner_only_descriptor", lambda *a, **k: None)

    with pytest.raises(SecretFileError, match="byte secret-file limit"):
        secret_files._read_owner_only_text_windows(
            path, flag="--t", limit=1, require_single_link=False
        )

    real_fstat = os.fstat

    def as_dir(fd: int) -> os.stat_result:
        info = real_fstat(fd)
        return os.stat_result(
            (
                stat.S_IFDIR | 0o700,
                info.st_ino,
                info.st_dev,
                info.st_nlink,
                info.st_uid,
                info.st_gid,
                info.st_size,
                info.st_atime,
                info.st_mtime,
                info.st_ctime,
            )
        )

    monkeypatch.setattr(os, "fstat", as_dir)
    with pytest.raises(SecretFileError, match="not a regular secret file"):
        secret_files._read_owner_only_text_windows(
            path, flag="--t", limit=1000, require_single_link=False
        )
    monkeypatch.setattr(os, "fstat", real_fstat)

    real_read = os.read

    def small_then_read(fd: int) -> os.stat_result:
        info = real_fstat(fd)
        return os.stat_result(
            (
                info.st_mode,
                info.st_ino,
                info.st_dev,
                info.st_nlink,
                info.st_uid,
                info.st_gid,
                1,
                info.st_atime,
                info.st_mtime,
                info.st_ctime,
            )
        )

    monkeypatch.setattr(os, "fstat", small_then_read)
    monkeypatch.setattr(os, "read", lambda _fd, _n: b"abcdef")
    with pytest.raises(SecretFileError, match="byte secret-file limit"):
        secret_files._read_owner_only_text_windows(
            path, flag="--t", limit=3, require_single_link=False
        )
    monkeypatch.setattr(os, "fstat", real_fstat)
    monkeypatch.setattr(os, "read", real_read)

    path.write_text("ok\n", encoding="utf-8")
    calls = {"n": 0}

    def drift(fd: int) -> os.stat_result:
        calls["n"] += 1
        info = real_fstat(fd)
        if calls["n"] == 2:
            return os.stat_result(
                (
                    info.st_mode,
                    info.st_ino,
                    info.st_dev,
                    info.st_nlink,
                    info.st_uid,
                    info.st_gid,
                    info.st_size + 1,
                    info.st_atime,
                    info.st_mtime + 1,
                    info.st_ctime,
                )
            )
        return info

    monkeypatch.setattr(os, "fstat", drift)
    with pytest.raises(SecretFileError, match="changed while its policy was being read"):
        secret_files._read_owner_only_text_windows(
            path, flag="--t", limit=1000, require_single_link=False
        )
    monkeypatch.setattr(os, "fstat", real_fstat)

    state = {"n": 0}

    def assert_then_fail(
        _fd: int,
        *,
        path: str | Path,
        purpose: str,
        directory: bool = False,
        require_single_link: bool = False,
    ) -> None:
        del path, purpose, directory, require_single_link
        state["n"] += 1
        if state["n"] > 1:
            raise SPE("ACL loosened")

    monkeypatch.setattr(secret_files, "assert_owner_only_descriptor", assert_then_fail)
    with pytest.raises(SecretFileError, match="changed while its policy was being read"):
        secret_files._read_owner_only_text_windows(
            path, flag="--t", limit=1000, require_single_link=False
        )

    bad = tmp_path / "bad"
    bad.write_bytes(b"\xff\xff")
    monkeypatch.setattr(secret_files, "assert_owner_only_descriptor", lambda *a, **k: None)
    with pytest.raises(SecretFileError, match="not valid UTF-8"):
        secret_files._read_owner_only_text_windows(
            bad, flag="--t", limit=1000, require_single_link=False
        )

    def fail_read(_fd: int, _n: int) -> bytes:
        raise OSError("read boom")

    monkeypatch.setattr(os, "read", fail_read)
    with pytest.raises(SecretFileError, match="cannot securely read"):
        secret_files._read_owner_only_text_windows(
            path, flag="--t", limit=1000, require_single_link=False
        )
    monkeypatch.undo()

    # private_dir Windows create failures
    monkeypatch.setattr(private_dir, "_WINDOWS", True)
    monkeypatch.setattr(private_dir, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(private_dir, "apply_owner_only_dir", lambda p: None)
    monkeypatch.setattr(private_dir, "assert_owner_only_dir_path", lambda p, purpose: None)

    def refuse_mkdir(self: Path, *a: object, **k: object) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", refuse_mkdir)
    with pytest.raises(PrivateDirError, match="cannot create parents"):
        private_dir.ensure_private_dir(tmp_path / "p" / "c", parents=True, purpose="p")
    with pytest.raises(PrivateDirError, match="cannot create"):
        private_dir.ensure_private_dir(tmp_path / "solo", purpose="p")

    monkeypatch.undo()
    monkeypatch.setattr(private_dir, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(private_dir, "_WINDOWS", False)
    monkeypatch.setattr(private_dir, "_POSIX", False)
    with pytest.raises(PrivateDirError, match="unavailable"):
        private_dir.ensure_private_dir(tmp_path / "x", purpose="p")

    monkeypatch.setattr(secret_files, "_POSIX", True)
    monkeypatch.setattr(secret_files, "_WINDOWS", False)

    class OsPosixNoNofollow:
        name = "posix"

    monkeypatch.setattr(secret_files, "os", OsPosixNoNofollow())
    with pytest.raises(SecretFileError, match="unavailable"):
        secret_files.read_regular_file_bytes(path, label="public")


def test_windows_floor_is_available_when_the_host_is_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import synapse_channel.core.secure_path as module

    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(module, "_WINDOWS", True)
    assert module.owner_only_floor_available() is True


# ---------------------------------------------------------------------------
# Scripted Win32 ctypes harness (exercises core.secure_path_windows on POSIX)
# ---------------------------------------------------------------------------


class _Win32State:
    """Shared stand-in for one ``WinDLL`` scenario. The security policy stays real."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.token_reads = 0
        self.converts = 0
        self.last_error = 5
        self.acl: ctypes.Array[ctypes.c_char] | None = None
        self.ace_offsets: list[int] = []
        self.sddl_inputs: list[str] = []
        self.create_calls: list[tuple[Any, ...]] = []
        self.get_info_calls: list[tuple[Any, ...]] = []
        self.set_info_calls: list[tuple[Any, ...]] = []
        self.freed_ptrs: list[int] = []
        self.closed_handles: list[int] = []
        self.dll_loads: list[tuple[str, bool]] = []
        self.loaded_dlls: dict[str, Any] = {}


def _ptr_obj(arg: Any) -> Any:
    """Return the underlying ctypes object inside a ``ctypes.byref(...)`` pointer."""
    return arg._obj


def _write_token_user(buffer: Any) -> None:
    """Plant a ``_TokenUser`` whose SID pointer the string converter reads."""
    user = win_sec._TokenUser()
    user.User.Sid = 0x11
    user.User.Attributes = 0
    ctypes.memmove(ctypes.addressof(buffer), ctypes.addressof(user), ctypes.sizeof(user))


def _plant_acl(
    entries: tuple[tuple[int, int, int], ...],
    *,
    acl_size_override: int | None = None,
) -> tuple[ctypes.Array[ctypes.c_char], list[int]]:
    """Build a Win32 ACL buffer with ``(ace_type, mask, ace_size)`` entries."""
    header_size = ctypes.sizeof(win_sec._AclHeader)
    total = max(64, header_size + sum(max(24, sz) for _, _, sz in entries) + 16)
    raw = ctypes.create_string_buffer(total)
    acl = win_sec._AclHeader.from_buffer(raw)
    acl.AclRevision = 2
    acl.AceCount = len(entries)
    acl.AclSize = total if acl_size_override is None else acl_size_override
    offsets: list[int] = []
    cursor = header_size
    for ace_type, mask_val, ace_size in entries:
        offsets.append(cursor)
        header = win_sec._AceHeader.from_buffer(raw, cursor)
        header.AceType = ace_type
        header.AceFlags = 0
        header.AceSize = ace_size
        mask = ctypes.c_uint32.from_buffer(raw, cursor + 4)
        mask.value = mask_val
        cursor += max(24, ace_size)
    return raw, offsets


def _write_file_info(
    buffer_ref: Any,
    *,
    attributes: int,
    nlinks: int,
) -> None:
    info = _ptr_obj(buffer_ref)
    info.dwFileAttributes = attributes
    info.nNumberOfLinks = nlinks


def _handle_create_file(state: _Win32State, args: tuple[Any, ...]) -> int:
    state.create_calls.append(args)
    if state.scenario == "create_missing_2":
        state.last_error = win_sec.ERROR_FILE_NOT_FOUND
        return int(ctypes.c_void_p(-1).value or -1)
    if state.scenario == "create_missing_3":
        state.last_error = win_sec.ERROR_PATH_NOT_FOUND
        return int(ctypes.c_void_p(-1).value or -1)
    if state.scenario == "create_exists_80":
        state.last_error = win_sec.ERROR_FILE_EXISTS
        return int(ctypes.c_void_p(-1).value or -1)
    if state.scenario == "create_exists_183":
        state.last_error = win_sec.ERROR_ALREADY_EXISTS
        return int(ctypes.c_void_p(-1).value or -1)
    if state.scenario == "create_denied":
        state.last_error = 5
        return int(ctypes.c_void_p(-1).value or -1)
    return 0x420


def _handle_file_info(state: _Win32State, args: tuple[Any, ...]) -> int:
    if state.scenario == "file_info_fail":
        state.last_error = 6
        return 0
    attrs = win_sec.FILE_ATTRIBUTE_NORMAL
    nlinks = 1
    if state.scenario == "reparse_leaf":
        attrs |= win_sec.FILE_ATTRIBUTE_REPARSE_POINT
    elif state.scenario in {"dir_ok", "is_dir"}:
        attrs = win_sec.FILE_ATTRIBUTE_DIRECTORY
    elif state.scenario == "two_links":
        nlinks = 2
    _write_file_info(args[1], attributes=attrs, nlinks=nlinks)
    return 1


def _handle_get_security_info(state: _Win32State, args: tuple[Any, ...]) -> int:
    state.get_info_calls.append(args)
    if state.scenario == "security_status":
        return 5
    _ptr_obj(args[7]).value = 0x7001
    if state.scenario == "missing_owner_sid":
        _ptr_obj(args[3]).value = 0
        return 0
    _ptr_obj(args[3]).value = 0x1001
    if state.scenario == "null_dacl":
        _ptr_obj(args[5]).value = 0
        return 0
    if state.scenario == "acl_size_small":
        state.acl, state.ace_offsets = _plant_acl(((0, 0x001F01FF, 24),), acl_size_override=4)
    elif state.scenario == "ace_size_small":
        state.acl, state.ace_offsets = _plant_acl(((0, 0x001F01FF, 12),))
    elif state.scenario == "ace_overruns_end":
        state.acl, state.ace_offsets = _plant_acl(((0, 0x001F01FF, 200),), acl_size_override=64)
    elif state.scenario == "unknown_ace_5":
        state.acl, state.ace_offsets = _plant_acl(((0, 0x001F01FF, 24), (5, 0x00120089, 24)))
    elif state.scenario == "unknown_ace_9":
        state.acl, state.ace_offsets = _plant_acl(((9, 0x00120089, 24),))
    elif state.scenario == "unknown_ace_2":
        state.acl, state.ace_offsets = _plant_acl(((2, 0x00120089, 24),))
    elif state.scenario == "deny_everyone_ok":
        state.acl, state.ace_offsets = _plant_acl(((0, 0x001F01FF, 24), (1, 0x001F01FF, 24)))
    else:
        state.acl, state.ace_offsets = _plant_acl(((0, 0x001F01FF, 24),))
    _ptr_obj(args[5]).value = ctypes.addressof(state.acl)
    return 0


def _handle_convert_sid(state: _Win32State, args: tuple[Any, ...]) -> int:
    state.converts += 1
    out = _ptr_obj(args[1])
    if state.scenario == "convert_fail":
        return 0
    if state.scenario == "owner_convert_fail" and state.converts == 1:
        return 0
    if state.scenario == "ace_convert_fail" and state.converts >= 2:
        return 0
    if state.scenario == "empty_sid":
        out.value = None
        return 1
    if state.scenario == "apply_readback_everyone" and state.converts >= 3:
        out.value = "S-1-1-0"
        return 1
    if state.scenario == "everyone_allow" and state.converts == 2:
        out.value = "S-1-1-0"
        return 1
    if state.scenario == "deny_everyone_ok" and state.converts == 3:
        out.value = "S-1-1-0"
        return 1
    out.value = "S-1-5-21-9"
    return 1


def _dispatch_win32(state: _Win32State, name: str, args: tuple[Any, ...]) -> int:
    """Return scripted Win32 codes driving every branch while keeping policy real."""
    if name == "GetCurrentProcess":
        return 0x10
    if name == "OpenProcessToken":
        if state.scenario == "open_fail":
            state.last_error = 5
            return 0
        _ptr_obj(args[2]).value = 0x20
        return 1
    if name == "GetTokenInformation":
        state.token_reads += 1
        if state.scenario == "size_zero":
            state.last_error = 122
            return 1
        if args[2] is None:
            _ptr_obj(args[4]).value = 32
            return 0
        if state.scenario == "read_fail":
            state.last_error = 87
            return 0
        _write_token_user(args[2])
        return 1
    if name == "ConvertSidToStringSidW":
        return _handle_convert_sid(state, args)
    if name == "ConvertStringSecurityDescriptorToSecurityDescriptorW":
        state.sddl_inputs.append(str(args[0]))
        if state.scenario == "sddl_fail":
            state.last_error = 1336
            return 0
        _ptr_obj(args[2]).value = 0x8001
        return 1
    if name == "GetSecurityDescriptorOwner":
        if state.scenario == "sd_owner_fail":
            state.last_error = 1338
            return 0
        _ptr_obj(args[1]).value = 0x8101
        return 1
    if name == "GetSecurityDescriptorDacl":
        if state.scenario == "sd_dacl_fail":
            state.last_error = 1338
            return 0
        _ptr_obj(args[1]).value = 1
        _ptr_obj(args[2]).value = 0x8201
        return 1
    if name == "CreateFileW":
        return _handle_create_file(state, args)
    if name == "GetFileInformationByHandle":
        return _handle_file_info(state, args)
    if name == "GetSecurityInfo":
        return _handle_get_security_info(state, args)
    if name == "SetSecurityInfo":
        state.set_info_calls.append(args)
        if state.scenario == "invalid_owner":
            return win_sec.ERROR_INVALID_OWNER
        if state.scenario == "set_security_fail":
            return 5
        return 0
    if name == "GetAce":
        if state.scenario == "get_ace_fail":
            state.last_error = 87
            return 0
        assert state.acl is not None
        if state.scenario == "ace_ptr_oob":
            _ptr_obj(args[2]).value = ctypes.addressof(state.acl) + 512
            return 1
        idx = int(args[1])
        _ptr_obj(args[2]).value = ctypes.addressof(state.acl) + state.ace_offsets[idx]
        return 1
    if name == "LocalFree":
        ptr = args[0]
        raw_val = getattr(ptr, "value", ptr)
        if isinstance(raw_val, int):
            state.freed_ptrs.append(raw_val)
        return 0
    if name == "CloseHandle":
        handle = args[0]
        raw_val = getattr(handle, "value", handle)
        if isinstance(raw_val, int):
            state.closed_handles.append(raw_val)
        return 1
    raise AssertionError(f"unexpected Win32 call: {name}")


def _install_win32(monkeypatch: pytest.MonkeyPatch, scenario: str) -> _Win32State:
    """Point ``ctypes.WinDLL`` at scripted advapi32/kernel32 and forbid subprocesses."""
    state = _Win32State(scenario)

    class Call:
        def __init__(self, entry: str) -> None:
            self.entry = entry
            self.argtypes: Any = None
            self.restype: Any = None

        def __call__(self, *call_args: Any) -> int:
            return _dispatch_win32(state, self.entry, call_args)

    class DLL:
        def __init__(self, library: str, use_last_error: bool = False) -> None:
            state.dll_loads.append((library, use_last_error))
            self._library = library
            self._calls: dict[str, Call] = {}
            state.loaded_dlls[library] = self

        def __getattr__(self, entry: str) -> Call:
            if entry.startswith("_"):
                raise AttributeError(entry)
            if entry not in self._calls:
                self._calls[entry] = Call(entry)
            return self._calls[entry]

    def no_subprocess(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("secure_path must never spawn a subprocess on Windows")

    monkeypatch.setattr(ctypes, "WinDLL", DLL, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state.last_error, raising=False)
    monkeypatch.setattr(subprocess, "run", no_subprocess)
    return state


def _install_fake_msvcrt(
    monkeypatch: pytest.MonkeyPatch,
    *,
    osfhandle: int = 0x550,
    error: OSError | None = None,
) -> None:
    fake: Any = types.ModuleType("msvcrt")

    def get_osfhandle(_fd: int) -> int:
        if error is not None:
            raise error
        return osfhandle

    fake.get_osfhandle = get_osfhandle
    monkeypatch.setitem(sys.modules, "msvcrt", fake)


def _force_windows_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    import synapse_channel.core.secure_path as module

    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(module, "_WINDOWS", True)


def test_ctypes_prototypes_and_use_last_error_are_declared_for_every_win32_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every Win32 binding uses a fresh ``WinDLL(..., use_last_error=True)`` and HANDLE types."""
    state = _install_win32(monkeypatch, "token_ok")
    kernel32 = win_sec._load_kernel32()
    advapi32 = win_sec._load_advapi32()

    assert ("kernel32", True) in state.dll_loads
    assert ("advapi32", True) in state.dll_loads

    expected_kernel = {
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "CreateFileW": (
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ],
            wintypes.HANDLE,
        ),
        "GetFileInformationByHandle": ([wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
        "LocalFree": ([ctypes.c_void_p], ctypes.c_void_p),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (argtypes, restype) in expected_kernel.items():
        fn = getattr(kernel32, name)
        assert fn.argtypes == argtypes, name
        assert fn.restype is restype, name

    expected_advapi = {
        "OpenProcessToken": (
            [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)],
            wintypes.BOOL,
        ),
        "GetTokenInformation": (
            [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ],
            wintypes.BOOL,
        ),
        "ConvertSidToStringSidW": (
            [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)],
            wintypes.BOOL,
        ),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.ULONG),
            ],
            wintypes.BOOL,
        ),
        "GetSecurityDescriptorOwner": (
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
        ),
        "GetSecurityDescriptorDacl": (
            [
                ctypes.c_void_p,
                ctypes.POINTER(wintypes.BOOL),
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.BOOL),
            ],
            wintypes.BOOL,
        ),
        "GetSecurityInfo": (
            [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p),
            ],
            wintypes.DWORD,
        ),
        "SetSecurityInfo": (
            [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ],
            wintypes.DWORD,
        ),
        "GetAce": (
            [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)],
            wintypes.BOOL,
        ),
    }
    for name, (argtypes, restype) in expected_advapi.items():
        fn = getattr(advapi32, name)
        assert fn.argtypes == argtypes, name
        assert fn.restype is restype, name


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("open_fail", "OpenProcessToken"),
        ("size_zero", "GetTokenInformation"),
        ("read_fail", "GetTokenInformation"),
        ("convert_fail", "ConvertSidToStringSidW"),
        ("empty_sid", "SID is empty"),
    ],
    ids=("open_fail", "size_zero", "read_fail", "convert_fail", "empty_sid"),
)
def test_native_sid_failure_fails_closed_without_subprocess(
    scenario: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every native token step fails closed; ``whoami`` is never invoked."""
    _force_windows_floor(monkeypatch)
    state = _install_win32(monkeypatch, scenario)
    with pytest.raises(SecurePathError, match=message):
        current_user_key()
    if scenario != "open_fail":
        assert 0x20 in state.closed_handles


def test_current_user_key_reads_the_process_token_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_windows_floor(monkeypatch)
    state = _install_win32(monkeypatch, "token_ok")
    identified = current_user_key()
    assert identified.kind == "windows_sid"
    assert identified.value == "S-1-5-21-9"
    assert 0x20 in state.closed_handles


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("create_missing_2", "does not exist"),
        ("create_missing_3", "does not exist"),
        ("create_denied", "CreateFileW"),
        ("file_info_fail", "GetFileInformationByHandle"),
        ("reparse_leaf", "symlink"),
        ("is_dir", "not a regular file"),
        ("security_status", "GetSecurityInfo"),
        ("missing_owner_sid", "missing owner SID"),
        ("owner_convert_fail", "cannot convert owner SID"),
        ("ace_convert_fail", "cannot convert ACE SID"),
        ("null_dacl", "NULL DACL"),
        ("acl_size_small", "invalid DACL header"),
        ("get_ace_fail", "GetAce failed"),
        ("ace_ptr_oob", "invalid DACL ACE pointer"),
        ("ace_size_small", "invalid DACL ACE size"),
        ("ace_overruns_end", "invalid DACL ACE size"),
        ("unknown_ace_5", "unsupported ACE type 5"),
        ("unknown_ace_9", "unsupported ACE type 9"),
        ("unknown_ace_2", "unsupported ACE type 2"),
    ],
)
def test_windows_owner_proof_reports_each_held_handle_failure(
    scenario: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"sekret")
    _force_windows_floor(monkeypatch)
    state = _install_win32(monkeypatch, scenario)
    with pytest.raises(SecurePathError, match=message):
        assert_owner_only_file_path(secret, purpose="secret")
    if not scenario.startswith("create_"):
        assert 0x420 in state.closed_handles
    if scenario not in {
        "create_missing_2",
        "create_missing_3",
        "create_denied",
        "file_info_fail",
        "reparse_leaf",
        "is_dir",
        "security_status",
    }:
        assert 0x7001 in state.freed_ptrs


@pytest.mark.parametrize("scenario", ["token_ok", "deny_everyone_ok"])
def test_windows_owner_proof_accepts_owner_only_and_deny_everyone_aces(
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"sekret")
    _force_windows_floor(monkeypatch)
    state = _install_win32(monkeypatch, scenario)
    assert_owner_only_file_path(secret, purpose="secret")
    assert 0x420 in state.closed_handles
    assert 0x7001 in state.freed_ptrs
    # CreateFileW opened with FILE_FLAG_OPEN_REPARSE_POINT and no GetNamedSecurityInfoW was called.
    assert state.create_calls[0][5] & win_sec.FILE_FLAG_OPEN_REPARSE_POINT


def test_windows_owner_proof_refuses_everyone_allow_and_hard_links_on_held_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"sekret")
    _force_windows_floor(monkeypatch)
    _install_win32(monkeypatch, "everyone_allow")
    with pytest.raises(SecurePathError, match="other principals"):
        assert_owner_only_file_path(secret, purpose="secret")

    _install_win32(monkeypatch, "two_links")
    with pytest.raises(SecurePathError, match="hard links"):
        assert_owner_only_file_path(secret, purpose="secret", require_single_link=True)

    _install_win32(monkeypatch, "token_ok")
    with pytest.raises(SecurePathError, match="not a directory"):
        assert_owner_only_dir_path(secret, purpose="dir")

    state_dir = _install_win32(monkeypatch, "dir_ok")
    assert_owner_only_dir_path(tmp_path, purpose="dir")
    assert state_dir.create_calls[0][5] == (
        win_sec.FILE_FLAG_OPEN_REPARSE_POINT | win_sec.FILE_FLAG_BACKUP_SEMANTICS
    )


def test_windows_descriptor_validation_and_read_owner_only_file_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``assert_owner_only_descriptor`` and ``read_owner_only_file_bytes`` validate the held fd."""
    import synapse_channel.core.secure_path as module

    secret = tmp_path / "key.bin"
    secret.write_bytes(b"abcd")
    _force_windows_floor(monkeypatch)
    _install_win32(monkeypatch, "token_ok")
    _install_fake_msvcrt(monkeypatch, osfhandle=0x550)

    fd = os.open(secret, os.O_RDONLY)
    try:
        assert_owner_only_descriptor(fd, path=secret, purpose="key", require_single_link=True)
    finally:
        os.close(fd)

    # read_owner_only_file_bytes routes through assert_owner_only_descriptor on the open leaf.
    monkeypatch.setattr(
        module, "open_nofollow_leaf", lambda p, directory=False: os.open(p, os.O_RDONLY)
    )
    assert read_owner_only_file_bytes(secret, purpose="key", expected_size=4) == b"abcd"

    # Hard-link refusal on held descriptor.
    _install_win32(monkeypatch, "two_links")
    fd = os.open(secret, os.O_RDONLY)
    try:
        with pytest.raises(SecurePathError, match="hard links"):
            assert_owner_only_descriptor(fd, path=secret, purpose="key", require_single_link=True)
    finally:
        os.close(fd)

    # msvcrt handle resolution errors fail closed.
    _install_win32(monkeypatch, "token_ok")
    _install_fake_msvcrt(monkeypatch, error=OSError("bad fd"))
    with pytest.raises(SecurePathError, match="cannot resolve Windows handle"):
        assert_owner_only_descriptor(0, path=secret, purpose="key")

    _install_fake_msvcrt(monkeypatch, osfhandle=int(ctypes.c_void_p(-1).value or -1))
    with pytest.raises(SecurePathError, match="invalid Windows handle"):
        assert_owner_only_descriptor(0, path=secret, purpose="key")


def test_windows_create_owner_only_file_uses_birth_security_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "new.secret"
    _force_windows_floor(monkeypatch)
    state = _install_win32(monkeypatch, "token_ok")

    assert create_owner_only_file(target) is True
    assert state.sddl_inputs == ["O:S-1-5-21-9D:P(A;;FA;;;S-1-5-21-9)(A;;FA;;;SY)"]
    assert state.create_calls[0][4] == win_sec.CREATE_NEW
    assert state.create_calls[0][5] & win_sec.FILE_FLAG_OPEN_REPARSE_POINT
    assert state.create_calls[0][3] is not None
    assert 0x8001 in state.freed_ptrs
    assert 0x420 in state.closed_handles

    for exists_scenario in ("create_exists_80", "create_exists_183"):
        exists_state = _install_win32(monkeypatch, exists_scenario)
        assert create_owner_only_file(target) is False
        assert 0x8001 in exists_state.freed_ptrs

    _install_win32(monkeypatch, "create_denied")
    with pytest.raises(SecurePathError, match="CreateFileW"):
        create_owner_only_file(target)

    _install_win32(monkeypatch, "sddl_fail")
    with pytest.raises(SecurePathError, match="ConvertStringSecurityDescriptor"):
        create_owner_only_file(target)


def test_windows_apply_owner_only_uses_set_security_info_and_verifies_same_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "sec"
    target.write_text("x", encoding="utf-8")
    _force_windows_floor(monkeypatch)

    # File apply succeeds, passes PROTECTED_DACL, and verifies back through the same handle.
    state = _install_win32(monkeypatch, "token_ok")
    apply_owner_only_file(target)
    assert state.sddl_inputs == ["O:S-1-5-21-9D:P(A;;FA;;;S-1-5-21-9)(A;;FA;;;SY)"]
    assert len(state.set_info_calls) == 1
    expected_flags = (
        win_sec.OWNER_SECURITY_INFORMATION
        | win_sec.DACL_SECURITY_INFORMATION
        | win_sec.PROTECTED_DACL_SECURITY_INFORMATION
    )
    assert state.set_info_calls[0][2] == expected_flags
    assert len(state.get_info_calls) == 1
    assert state.get_info_calls[0][0].value == state.set_info_calls[0][0].value == 0x420
    assert 0x8001 in state.freed_ptrs
    assert 0x7001 in state.freed_ptrs
    assert 0x420 in state.closed_handles

    # Directory apply uses OICI birth SDDL and backup semantics.
    dir_state = _install_win32(monkeypatch, "dir_ok")
    apply_owner_only_dir(tmp_path)
    assert dir_state.sddl_inputs == ["O:S-1-5-21-9D:P(A;OICI;FA;;;S-1-5-21-9)(A;OICI;FA;;;SY)"]
    assert dir_state.create_calls[0][5] == (
        win_sec.FILE_FLAG_OPEN_REPARSE_POINT | win_sec.FILE_FLAG_BACKUP_SEMANTICS
    )

    # Reparse point is refused BEFORE SetSecurityInfo is ever called.
    reparse_state = _install_win32(monkeypatch, "reparse_leaf")
    with pytest.raises(SecurePathError, match="symlink"):
        apply_owner_only_file(target)
    assert reparse_state.set_info_calls == []

    # ERROR_INVALID_OWNER fails closed with zero subprocess fallback.
    _install_win32(monkeypatch, "invalid_owner")
    with pytest.raises(SecurePathError, match="ERROR_INVALID_OWNER"):
        apply_owner_only_file(target)

    # Generic SetSecurityInfo error fails closed.
    _install_win32(monkeypatch, "set_security_fail")
    with pytest.raises(SecurePathError, match="SetSecurityInfo"):
        apply_owner_only_file(target)

    # Descriptor extraction failures fail closed.
    _install_win32(monkeypatch, "sd_owner_fail")
    with pytest.raises(SecurePathError, match="GetSecurityDescriptorOwner"):
        apply_owner_only_file(target)

    _install_win32(monkeypatch, "sd_dacl_fail")
    with pytest.raises(SecurePathError, match="GetSecurityDescriptorDacl"):
        apply_owner_only_file(target)

    # Verify-after-apply read-back mismatch raises SecurePathError.
    mismatch_state = _install_win32(monkeypatch, "apply_readback_everyone")
    with pytest.raises(SecurePathError, match="other principals"):
        apply_owner_only_file(target)
    assert len(mismatch_state.set_info_calls) == 1
    assert len(mismatch_state.get_info_calls) == 1


# ---------------------------------------------------------------------------
# Real Windows NT custody proofs (run on Windows hosts and hosted Windows CI)
# ---------------------------------------------------------------------------


def _win32_descriptor_dacl_protected(sd: ctypes.c_void_p) -> bool:
    """Return whether ``SE_DACL_PROTECTED`` (0x1000) is set on ``sd``."""
    advapi32 = win_sec._load_advapi32()
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    assert advapi32.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision))
    return bool(control.value & 0x1000)


def _win32_read_dacl_protected(handle: wintypes.HANDLE) -> bool:
    """Return whether ``SE_DACL_PROTECTED`` (0x1000) is set on ``handle``."""
    advapi32 = win_sec._load_advapi32()
    kernel32 = win_sec._load_kernel32()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    status = int(
        advapi32.GetSecurityInfo(
            handle,
            win_sec.SE_FILE_OBJECT,
            win_sec.OWNER_SECURITY_INFORMATION | win_sec.DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(sd),
        )
    )
    assert status == 0
    try:
        return _win32_descriptor_dacl_protected(sd)
    finally:
        if sd.value:
            kernel32.LocalFree(sd)


def _win32_grant_everyone_read(target: Path, sid: str, *, directory: bool = False) -> None:
    """Set a DACL granting Everyone read access via native Win32.

    For directories, sets a protected inheritable (``OICI``) DACL so child files
    created without ``D:P`` inherit the Everyone ACE from ``target``.
    """
    advapi32 = win_sec._load_advapi32()
    kernel32 = win_sec._load_kernel32()
    if directory:
        sddl = f"D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FR;;;WD)"
        info_flags = win_sec.DACL_SECURITY_INFORMATION | win_sec.PROTECTED_DACL_SECURITY_INFORMATION
    else:
        sddl = f"D:(A;;FA;;;{sid})(A;;FA;;;SY)(A;;FR;;;WD)"
        info_flags = win_sec.DACL_SECURITY_INFORMATION | 0x20000000
    sd = ctypes.c_void_p()
    assert advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, win_sec.SDDL_REVISION_1, ctypes.byref(sd), None
    )
    handle = win_sec._open_nofollow_handle(
        kernel32,
        target,
        desired_access=win_sec.WRITE_DAC | win_sec.READ_CONTROL | win_sec.FILE_READ_ATTRIBUTES,
        directory=directory,
        purpose="win32 test",
    )
    try:
        dacl_present = wintypes.BOOL()
        dacl_ptr = ctypes.c_void_p()
        dacl_defaulted = wintypes.BOOL()
        assert advapi32.GetSecurityDescriptorDacl(
            sd, ctypes.byref(dacl_present), ctypes.byref(dacl_ptr), ctypes.byref(dacl_defaulted)
        )
        status = int(
            advapi32.SetSecurityInfo(
                handle, win_sec.SE_FILE_OBJECT, info_flags, None, None, dacl_ptr, None
            )
        )
        assert status == 0
    finally:
        kernel32.CloseHandle(handle)
        kernel32.LocalFree(sd)


def _win32_set_null_dacl(target: Path) -> None:
    """Replace ``target``'s DACL with a real protected NULL DACL via native Win32."""
    advapi32 = win_sec._load_advapi32()
    kernel32 = win_sec._load_kernel32()
    handle = win_sec._open_nofollow_handle(
        kernel32,
        target,
        desired_access=win_sec.WRITE_DAC | win_sec.READ_CONTROL | win_sec.FILE_READ_ATTRIBUTES,
        directory=False,
        purpose="win32 null dacl test",
    )
    try:
        flags = win_sec.DACL_SECURITY_INFORMATION | win_sec.PROTECTED_DACL_SECURITY_INFORMATION
        status = int(
            advapi32.SetSecurityInfo(handle, win_sec.SE_FILE_OBJECT, flags, None, None, None, None)
        )
        assert status == 0
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows NT birth DACL read-back proof")
def test_win32_create_owner_only_file_sets_birth_protected_dacl(tmp_path: Path) -> None:
    sid = current_user_key().value
    with win_sec.owner_birth_security_descriptor(sid, directory=False) as birth_sd:
        assert _win32_descriptor_dacl_protected(birth_sd) is True

    broad_parent = tmp_path / "broad_parent"
    broad_parent.mkdir()
    _win32_grant_everyone_read(broad_parent, sid, directory=True)

    # Prove a plain child inherits Everyone from broad_parent when D:P is absent.
    inherited = broad_parent / "inherited.txt"
    inherited.write_bytes(b"plain")
    with pytest.raises(SecurePathError, match="other principals"):
        assert_owner_only_file_path(inherited, purpose="inherited")

    target = broad_parent / "birth.secret"
    assert create_owner_only_file(target) is True
    assert create_owner_only_file(target) is False
    assert_owner_only_file_path(target, purpose="birth")

    # Re-applying inheritable Everyone on the parent must not propagate into birth.secret.
    _win32_grant_everyone_read(broad_parent, sid, directory=True)
    assert_owner_only_file_path(target, purpose="birth after parent re-grant")
    kernel32 = win_sec._load_kernel32()
    handle = win_sec._open_nofollow_handle(
        kernel32,
        target,
        desired_access=win_sec.READ_CONTROL | win_sec.FILE_READ_ATTRIBUTES,
        directory=False,
        purpose="birth proof",
    )
    try:
        assert _win32_read_dacl_protected(handle) is True
        owner_sid, dacl_present, aces = win_sec.windows_read_owner_and_aces_handle(
            handle, path=target, purpose="birth proof"
        )
    finally:
        kernel32.CloseHandle(handle)
    assert owner_sid == sid
    assert dacl_present is True
    assert all(ace.sid != "S-1-1-0" for ace in aces)
    assert {(ace.ace_type, ace.mask, ace.sid) for ace in aces} == {
        (0, 0x001F01FF, sid),
        (0, 0x001F01FF, "S-1-5-18"),
    }


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows NT NULL DACL refusal proof")
def test_win32_null_dacl_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "null_dacl.secret"
    assert create_owner_only_file(target) is True
    target.write_bytes(b"secret-payload")
    assert_owner_only_file_path(target, purpose="before null dacl")
    _win32_set_null_dacl(target)
    with pytest.raises(SecurePathError, match="NULL DACL"):
        assert_owner_only_file_path(target, purpose="null dacl")
    with pytest.raises(SecurePathError, match="NULL DACL"):
        read_owner_only_file_bytes(target, purpose="null dacl")


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="real Windows NT directory-as-file refusal proof",
)
def test_win32_directory_is_refused_as_regular_file(tmp_path: Path) -> None:
    import msvcrt

    msvcrt_mod: Any = msvcrt
    private_dir = tmp_path / "private_dir"
    private_dir.mkdir()
    apply_owner_only_dir(private_dir)
    assert_owner_only_dir_path(private_dir, purpose="private dir")

    # Path-level file helpers refuse a real directory (CreateFileW without
    # FILE_FLAG_BACKUP_SEMANTICS fails closed with ERROR_ACCESS_DENIED before
    # _check_handle_file_info is reached).
    with pytest.raises(SecurePathError, match="cannot open|not a regular file"):
        assert_owner_only_file_path(private_dir, purpose="dir as file")
    with pytest.raises(SecurePathError, match="cannot open|not a regular file"):
        read_owner_only_file_bytes(private_dir, purpose="dir as file")

    # Pass a real NT directory descriptor through assert_owner_only_descriptor
    # with directory=False so _check_handle_file_info's regular-file guard is
    # directly exercised on a real owner-only directory handle.
    kernel32 = win_sec._load_kernel32()
    handle = win_sec._open_nofollow_handle(
        kernel32,
        private_dir,
        desired_access=win_sec.READ_CONTROL | win_sec.FILE_READ_ATTRIBUTES,
        directory=True,
        purpose="dir descriptor proof",
    )
    raw_handle = int(handle.value or 0)
    assert raw_handle != 0
    descriptor_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        dir_fd = int(msvcrt_mod.open_osfhandle(raw_handle, descriptor_flags))
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    try:
        with pytest.raises(SecurePathError, match="not a regular file"):
            assert_owner_only_descriptor(
                dir_fd, path=private_dir, purpose="dir as file", directory=False
            )
    finally:
        os.close(dir_fd)

    # Mirror guard: a real owner-only file is refused when directory=True.
    private_file = tmp_path / "private_file.secret"
    assert create_owner_only_file(private_file) is True
    with pytest.raises(SecurePathError, match="not a directory"):
        assert_owner_only_dir_path(private_file, purpose="file as dir")


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows NT apply over Everyone ACE proof")
def test_win32_apply_owner_only_file_strips_inherited_everyone_ace(tmp_path: Path) -> None:
    target = tmp_path / "broad.secret"
    target.write_bytes(b"payload")
    sid = current_user_key().value
    _win32_grant_everyone_read(target, sid)
    with pytest.raises(SecurePathError, match="other principals"):
        assert_owner_only_file_path(target, purpose="broad")

    apply_owner_only_file(target)
    assert_owner_only_file_path(target, purpose="hardened")
    kernel32 = win_sec._load_kernel32()
    handle = win_sec._open_nofollow_handle(
        kernel32,
        target,
        desired_access=win_sec.READ_CONTROL | win_sec.FILE_READ_ATTRIBUTES,
        directory=False,
        purpose="hardened proof",
    )
    try:
        assert _win32_read_dacl_protected(handle) is True
        _, dacl_present, aces = win_sec.windows_read_owner_and_aces_handle(
            handle, path=target, purpose="hardened proof"
        )
    finally:
        kernel32.CloseHandle(handle)
    assert dacl_present is True
    assert all(ace.sid != "S-1-1-0" for ace in aces)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="real Windows NT read_owner_only_file_bytes accept and refuse proof",
)
def test_win32_read_owner_only_file_bytes_reads_private_and_refuses_everyone(
    tmp_path: Path,
) -> None:
    target = tmp_path / "read.secret"
    assert create_owner_only_file(target) is True
    target.write_bytes(b"secret-payload")
    assert read_owner_only_file_bytes(target, purpose="secret") == b"secret-payload"

    _win32_grant_everyone_read(target, current_user_key().value)
    with pytest.raises(SecurePathError, match="other principals"):
        read_owner_only_file_bytes(target, purpose="secret")


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows NT reparse-point refusal proof")
def test_win32_reparse_point_leaf_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "real.secret"
    assert create_owner_only_file(target) is True
    target.write_bytes(b"data")
    link = tmp_path / "link.secret"
    create_link_or_skip(link, target)
    with pytest.raises(SecurePathError, match="symlink"):
        assert_owner_only_file_path(link, purpose="link")
    with pytest.raises(SecurePathError, match="cannot open|symlink"):
        read_owner_only_file_bytes(link, purpose="link")


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows NT hard-link refusal proof")
def test_win32_assert_owner_only_descriptor_refuses_hard_link(tmp_path: Path) -> None:
    from synapse_channel.core.secure_path import open_nofollow_leaf

    orig = tmp_path / "orig.secret"
    assert create_owner_only_file(orig) is True
    orig.write_bytes(b"policy")
    alias = tmp_path / "alias.secret"
    os.link(orig, alias)
    fd = open_nofollow_leaf(alias)
    try:
        assert_owner_only_descriptor(fd, path=alias, purpose="policy", require_single_link=False)
        with pytest.raises(SecurePathError, match="hard links"):
            assert_owner_only_descriptor(fd, path=alias, purpose="policy", require_single_link=True)
    finally:
        os.close(fd)
