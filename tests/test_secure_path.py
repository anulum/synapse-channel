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
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.core.private_dir import PrivateDirError, ensure_private_dir
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.core.secure_path import (
    PortableUserKey,
    SecurePathError,
    apply_owner_only_dir,
    apply_owner_only_file,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
    current_user_key,
    owner_only_floor_available,
    private_temp_user_segment,
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


def test_evaluate_windows_owner_only_policy_accepts_owner_and_system() -> None:
    from synapse_channel.core.secure_path import (
        WindowsAce,
        evaluate_windows_owner_only_policy,
    )

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
            # Access-denied ACEs are ignored by the allow-list policy.
            WindowsAce(ace_type=1, mask=0x001F01FF, sid="S-1-1-0"),
        ),
    )
    # Administrators-owned with an explicit current-user ACE (common under
    # admin-group tokens on Windows CI). OWNER RIGHTS (S-1-3-4) is virtual.
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
    # Administrators-only DACL (no current-user ACE) is accepted when the owner
    # is Administrators — common under admin-group tokens on Windows CI.
    evaluate_windows_owner_only_policy(
        path=path,
        purpose="policy",
        owner_sid="S-1-5-32-544",
        current_sid=current,
        dacl_present=True,
        aces=(WindowsAce(ace_type=0, mask=0x001F01FF, sid="S-1-5-32-544"),),
    )


def test_evaluate_windows_owner_only_policy_refuses_everyone_and_null_dacl() -> None:
    from synapse_channel.core.secure_path import (
        WindowsAce,
        evaluate_windows_owner_only_policy,
    )

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


def test_windows_path_kind_guards(tmp_path: Path) -> None:
    from synapse_channel.core.secure_path import _windows_path_kind_guards

    missing = tmp_path / "missing"
    with pytest.raises(SecurePathError, match="does not exist"):
        _windows_path_kind_guards(missing, purpose="g", directory=False)

    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(SecurePathError, match="not a directory"):
        _windows_path_kind_guards(file_path, purpose="g", directory=True)

    dir_path = tmp_path / "dir"
    dir_path.mkdir()
    with pytest.raises(SecurePathError, match="not a regular file"):
        _windows_path_kind_guards(dir_path, purpose="g", directory=False)

    link = tmp_path / "link"
    linked: Path | None
    try:
        link.symlink_to(file_path)
    except OSError:
        linked = None
    else:
        linked = link
    if linked is not None and linked.is_symlink():
        with pytest.raises(SecurePathError, match="symlink"):
            _windows_path_kind_guards(linked, purpose="g", directory=False)
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(SecurePathError, match="not a regular file"):
            _windows_path_kind_guards(fifo, purpose="g", directory=False)


def test_windows_apply_owner_only_invokes_icacls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.secure_path as module

    path = tmp_path / "sec"
    path.write_text("x", encoding="utf-8")
    calls: list[list[str]] = []

    class Result:
        def __init__(self, code: int = 0, err: str = "") -> None:
            self.returncode = code
            self.stderr = err
            self.stdout = ""

    def fake_run(argv: list[str], **_kwargs: object) -> Result:
        calls.append(list(argv))
        return Result(0)

    import subprocess as subprocess_mod

    monkeypatch.setattr(module, "_windows_current_user_sid", lambda: "S-1-5-21-9")
    monkeypatch.setattr(subprocess_mod, "run", fake_run)
    module._windows_apply_owner_only(path, directory=False)
    assert calls[0][:2] == ["takeown", "/f"]
    assert calls[1][:2] == ["icacls", str(path)]
    assert "/inheritance:r" in calls[1]
    assert any(a.startswith("*S-1-5-21-9:") for a in calls[2])

    def takeown_already_owned(argv: list[str], **_kwargs: object) -> Result:
        if argv and argv[0] == "takeown":
            return Result(1, "already owned")
        return Result(0)

    monkeypatch.setattr(subprocess_mod, "run", takeown_already_owned)
    module._windows_apply_owner_only(path, directory=False)

    # Directory rights use object-inherit flags.
    monkeypatch.setattr(subprocess_mod, "run", fake_run)
    calls.clear()
    module._windows_apply_owner_only(path, directory=True)
    assert "(OI)(CI)(F)" in calls[2][3]

    def fail_strip(argv: list[str], **_kwargs: object) -> Result:
        if "/inheritance:r" in argv:
            return Result(1, "strip failed")
        return Result(0)

    monkeypatch.setattr(subprocess_mod, "run", fail_strip)
    with pytest.raises(SecurePathError, match="strip inherited ACL"):
        module._windows_apply_owner_only(path, directory=False)

    def fail_grant(argv: list[str], **_kwargs: object) -> Result:
        if "/grant:r" in argv:
            return Result(2, "grant failed")
        return Result(0)

    monkeypatch.setattr(subprocess_mod, "run", fail_grant)
    with pytest.raises(SecurePathError, match="grant owner-only ACL"):
        module._windows_apply_owner_only(path, directory=False)

    def missing_tool(argv: list[str], **_kwargs: object) -> Result:
        raise FileNotFoundError("icacls")

    monkeypatch.setattr(subprocess_mod, "run", missing_tool)
    with pytest.raises(SecurePathError, match="icacls/takeown is required"):
        module._windows_apply_owner_only(path, directory=False)


def test_windows_assert_owner_only_uses_policy_and_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.secure_path as module
    from synapse_channel.core.secure_path import WindowsAce

    path = tmp_path / "owned"
    path.write_text("secret", encoding="utf-8")
    current = "S-1-5-21-1"
    monkeypatch.setattr(module, "_windows_current_user_sid", lambda: current)
    monkeypatch.setattr(
        module,
        "_windows_read_owner_and_aces",
        lambda _path, purpose: (
            current,
            True,
            (WindowsAce(ace_type=0, mask=0x001F01FF, sid=current),),
        ),
    )
    module._windows_assert_owner_only_path(path, purpose="w", directory=False)

    monkeypatch.setattr(
        module,
        "_windows_read_owner_and_aces",
        lambda _path, purpose: (current, True, (WindowsAce(0, 0x120089, "S-1-1-0"),)),
    )
    with pytest.raises(SecurePathError, match="accessible by other principals"):
        module._windows_assert_owner_only_path(path, purpose="w", directory=False)


def test_dispatch_branches_when_forced_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force the Windows dispatch on a POSIX host so call wiring is covered."""
    import synapse_channel.core.private_dir as private_dir
    import synapse_channel.core.secret_files as secret_files
    import synapse_channel.core.secure_path as module

    path = tmp_path / "token"
    # Binary write so Windows does not inject CRLF into the expected payload.
    path.write_bytes(b"win-secret\n")
    current = "S-1-5-21-force"

    monkeypatch.setattr(module, "_WINDOWS", True)
    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(module, "_windows_current_user_sid", lambda: current)
    monkeypatch.setattr(module, "_windows_apply_owner_only", lambda p, directory=False: None)
    monkeypatch.setattr(
        module,
        "_windows_assert_owner_only_path",
        lambda p, purpose, directory=False: None,
    )
    monkeypatch.setattr(module, "owner_only_floor_available", lambda: True)

    key = module.current_user_key()
    assert key.kind == "windows_sid"
    assert key.path_suffix() == "1-5-21-force"
    module.apply_owner_only_file(path)
    module.apply_owner_only_dir(tmp_path)
    module.assert_owner_only_file_path(path, purpose="forced")
    module.assert_owner_only_dir_path(tmp_path, purpose="forced-dir")

    # secret_files Windows branch
    monkeypatch.setattr(secret_files, "_WINDOWS", True)
    monkeypatch.setattr(secret_files, "_POSIX", False)
    monkeypatch.setattr(secret_files, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(
        secret_files,
        "assert_owner_only_file_path",
        lambda p, purpose, require_single_link=False: None,
    )
    assert secret_files.read_secret_file(path, flag="--forced-token") == "win-secret"
    assert secret_files.read_regular_file_bytes(path, label="public") == b"win-secret\n"

    # private_dir Windows branch
    monkeypatch.setattr(private_dir, "_WINDOWS", True)
    monkeypatch.setattr(private_dir, "_POSIX", False)
    monkeypatch.setattr(private_dir, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(private_dir, "apply_owner_only_dir", lambda p: None)
    monkeypatch.setattr(private_dir, "assert_owner_only_dir_path", lambda p, purpose: None)
    leaf = tmp_path / "win-leaf"
    assert private_dir.ensure_private_dir(leaf, purpose="win leaf") == leaf
    nested = tmp_path / "a" / "b" / "c"
    assert private_dir.ensure_private_dir(nested, parents=True, purpose="win nested") == nested


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
        def _raise(p: Path, *, purpose: str, require_single_link: bool = False) -> None:
            raise SPE(f"{purpose}: {purpose_fragment}")

        monkeypatch.setattr(secret_files, "assert_owner_only_file_path", _raise)

    raise_for("not owned by the effective user")
    with pytest.raises(SecretFileError, match="effective hub service user"):
        secret_files.read_secret_file(path, flag="--t")

    raise_for("accessible by other principals (ACE for S-1-1-0)")
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
        module.assert_owner_only_file_path(tmp_path / "x", purpose="p")
    with pytest.raises(SecurePathError, match="unavailable"):
        module.assert_owner_only_dir_path(tmp_path, purpose="p")
    with pytest.raises(OSError, match="unavailable"):
        module.open_nofollow_leaf(tmp_path / "x")

    # POSIX host missing geteuid/O_NOFOLLOW falls through to False.
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

    # Hard-link single-link policy on POSIX info helper.
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
    # Non-regular descriptor presentation.
    dir_info = dir_path.stat()
    with pytest.raises(SecurePathError, match="not a regular file"):
        module.assert_posix_owner_only_file_info(dir_info, path=dir_path, purpose="p")

    # Foreign owner for directory via monkeypatch is covered elsewhere; cover
    # geteuid fallback path on current_user_key when geteuid is absent.
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


def test_windows_open_and_single_link_and_private_dir_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.private_dir as private_dir
    import synapse_channel.core.secret_files as secret_files
    import synapse_channel.core.secure_path as module
    from synapse_channel.core.secure_path import SecurePathError as SPE

    path = tmp_path / "leaf"
    path.write_text("data\n", encoding="utf-8")

    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(module, "_WINDOWS", True)
    monkeypatch.setattr(module, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(module, "_windows_assert_owner_only_path", lambda *a, **k: None)

    # require_single_link success and failure on Windows path.
    module.assert_owner_only_file_path(path, purpose="p", require_single_link=True)

    class Stat:
        st_nlink = 2

    monkeypatch.setattr(Path, "stat", lambda self: Stat())
    with pytest.raises(SecurePathError, match="hard links"):
        module.assert_owner_only_file_path(path, purpose="p", require_single_link=True)
    monkeypatch.undo()

    # Re-apply windows force after undo of Path.stat
    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(module, "_WINDOWS", True)
    monkeypatch.setattr(module, "owner_only_floor_available", lambda: True)

    # open_nofollow_leaf Windows branch
    fd = module.open_nofollow_leaf(path)
    os.close(fd)
    # directory branch: on real Windows, opening a directory as a file raises
    # PermissionError; the POSIX host with forced _WINDOWS still exercises the
    # flag wiring via a successful open.
    d = tmp_path / "d"
    d.mkdir()
    if os.name == "nt":
        with pytest.raises(OSError):
            module.open_nofollow_leaf(d, directory=True)
    else:
        fd = module.open_nofollow_leaf(d, directory=True)
        os.close(fd)

    # symlink refuse on windows open
    if os.name == "posix":
        link = tmp_path / "sl"
        link.symlink_to(path)
        with pytest.raises(OSError, match="symlink refused"):
            module.open_nofollow_leaf(link)

    # secret_files Windows: open failure, size limit, drift, re-assert failure, utf-8
    monkeypatch.setattr(secret_files, "_WINDOWS", True)
    monkeypatch.setattr(secret_files, "_POSIX", False)
    monkeypatch.setattr(secret_files, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(
        secret_files,
        "assert_owner_only_file_path",
        lambda *a, **k: None,
    )

    def boom_open(*_a: object, **_k: object) -> int:
        raise OSError("open refused")

    monkeypatch.setattr(secret_files, "open_nofollow_descriptor", boom_open)
    with pytest.raises(SecretFileError, match="cannot securely open"):
        secret_files.read_secret_file(path, flag="--t")

    monkeypatch.undo()
    monkeypatch.setattr(secret_files, "_WINDOWS", True)
    monkeypatch.setattr(secret_files, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(secret_files, "assert_owner_only_file_path", lambda *a, **k: None)

    # oversize via limit=1 (st_size path)
    with pytest.raises(SecretFileError, match="byte secret-file limit"):
        secret_files._read_owner_only_text_windows(
            path, flag="--t", limit=1, require_single_link=False
        )

    # non-regular after open
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

    # chunked oversize: small st_size lie then large read
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

    # metadata drift
    path.write_text("ok\n", encoding="utf-8")
    real_fstat = os.fstat
    calls = {"n": 0}

    def drift(fd: int) -> os.stat_result:
        calls["n"] += 1
        info = real_fstat(fd)
        if calls["n"] == 2:
            values = list(info)
            values[6] = info.st_size + 1  # st_size index varies; use mtime_ns field
            # st_mtime_ns is index 12 on Linux stat_result sequence for ns fields
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

    # re-assert after read fails
    state = {"n": 0}

    def assert_then_fail(p: Path, *, purpose: str, require_single_link: bool = False) -> None:
        state["n"] += 1
        if state["n"] > 1:
            raise SPE("ACL loosened")

    monkeypatch.setattr(secret_files, "assert_owner_only_file_path", assert_then_fail)
    with pytest.raises(SecretFileError, match="changed while its policy was being read"):
        secret_files._read_owner_only_text_windows(
            path, flag="--t", limit=1000, require_single_link=False
        )

    # invalid utf-8
    bad = tmp_path / "bad"
    bad.write_bytes(b"\xff\xff")
    monkeypatch.setattr(secret_files, "assert_owner_only_file_path", lambda *a, **k: None)
    with pytest.raises(SecretFileError, match="not valid UTF-8"):
        secret_files._read_owner_only_text_windows(
            bad, flag="--t", limit=1000, require_single_link=False
        )

    # read OSError wrapper
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

    # dual-unavailable private_dir posix guard after floor true but flags false
    monkeypatch.undo()
    monkeypatch.setattr(private_dir, "owner_only_floor_available", lambda: True)
    monkeypatch.setattr(private_dir, "_WINDOWS", False)
    monkeypatch.setattr(private_dir, "_POSIX", False)
    with pytest.raises(PrivateDirError, match="unavailable"):
        private_dir.ensure_private_dir(tmp_path / "x", purpose="p")

    # secret_files nofollow without O_NOFOLLOW
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


@pytest.mark.skipif(sys.platform != "win32", reason="whoami /user is the Windows SID command")
def test_whoami_and_the_process_token_name_the_same_user() -> None:
    import synapse_channel.core.secure_path as module

    whoami_sid = module._windows_current_user_sid_whoami()
    native_sid = module._windows_current_user_sid_native()
    assert whoami_sid.startswith("S-1-")
    assert native_sid == whoami_sid


def test_windows_sid_falls_back_when_the_native_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import synapse_channel.core.secure_path as module

    def refuse() -> str:
        raise SecurePathError("native token read is unavailable")

    monkeypatch.setattr(module, "_windows_current_user_sid_native", refuse)
    monkeypatch.setattr(module, "_windows_current_user_sid_whoami", lambda: "S-1-5-21-7")
    assert module._windows_current_user_sid() == "S-1-5-21-7"


def test_whoami_sid_parser_refuses_bad_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    import synapse_channel.core.secure_path as module

    class Answer:
        def __init__(self, code: int, out: str = "", err: str = "") -> None:
            self.returncode = code
            self.stdout = out
            self.stderr = err

    def run(argv: list[str], **_kwargs: object) -> Answer:
        return answers.pop(0)

    answers = [
        Answer(0, '"HOST\\user","S-1-5-21-9"\n'),
        Answer(1, err="denied"),
        Answer(0, ""),
        Answer(0, '"HOST\\user"\n'),
    ]
    monkeypatch.setattr(module.subprocess, "run", run)  # type: ignore[attr-defined]
    assert module._windows_current_user_sid_whoami() == "S-1-5-21-9"
    with pytest.raises(SecurePathError, match="cannot resolve Windows user SID"):
        module._windows_current_user_sid_whoami()
    with pytest.raises(SecurePathError, match="no user SID"):
        module._windows_current_user_sid_whoami()
    with pytest.raises(SecurePathError, match="had no SID"):
        module._windows_current_user_sid_whoami()

    def missing(argv: list[str], **_kwargs: object) -> Answer:
        raise FileNotFoundError("whoami")

    monkeypatch.setattr(module.subprocess, "run", missing)  # type: ignore[attr-defined]
    with pytest.raises(SecurePathError, match="whoami is required"):
        module._windows_current_user_sid_whoami()


class _Win32State:
    """Shared stand-in for one ``WinDLL`` scenario. The security policy stays real."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.token_reads = 0
        self.converts = 0
        self.acl: ctypes.Array[ctypes.c_char] | None = None


def _write_token_user(buffer: object) -> None:
    """Plant a TOKEN_USER whose SID pointer the string converter ignores."""
    import ctypes
    from ctypes import wintypes

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TokenUser(ctypes.Structure):
        _fields_ = [("User", SidAndAttributes)]

    user = TokenUser()
    user.User.Sid = 0x11
    user.User.Attributes = 0
    ctypes.memmove(buffer, ctypes.addressof(user), ctypes.sizeof(user))  # type: ignore[arg-type]


def _plant_acl(ace_type: int) -> ctypes.Array[ctypes.c_char]:
    """Build an ACL the reader can walk with ``from_address``."""
    import ctypes
    from ctypes import wintypes

    class Acl(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE),
            ("Sbz1", wintypes.BYTE),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    raw = ctypes.create_string_buffer(64)
    acl = Acl.from_buffer(raw)
    acl.AclRevision = 2
    acl.AceCount = 1
    acl.AclSize = 64
    header_at = ctypes.sizeof(Acl)
    header = AceHeader.from_buffer(raw, header_at)
    header.AceType = ace_type
    header.AceFlags = 0
    header.AceSize = 24
    mask = ctypes.c_uint32.from_buffer(raw, header_at + ctypes.sizeof(AceHeader))
    mask.value = 0x001F01FF
    return raw


def _dispatch_win32(state: _Win32State, name: str, args: tuple[object, ...]) -> int:
    """Return the Win32 codes that drive one reader branch. Policy is not stubbed."""
    import ctypes

    if name == "GetCurrentProcess":
        return 1
    if name == "OpenProcessToken":
        return 0 if state.scenario == "open_fail" else 1
    if name == "GetTokenInformation":
        state.token_reads += 1
        size_ptr = args[4]
        buffer = args[2]
        if state.scenario == "size_zero":
            return 1
        if buffer is None:
            size_ptr._obj.value = 32  # type: ignore[attr-defined]
            return 0
        if state.scenario == "read_fail":
            return 0
        _write_token_user(buffer)
        return 1
    if name == "ConvertSidToStringSidW":
        state.converts += 1
        out = args[1]
        if state.scenario == "convert_fail" or (
            state.scenario == "owner_convert_fail" and state.converts == 1
        ):
            return 0
        if state.scenario == "ace_convert_fail" and state.converts >= 2:
            return 0
        # A non-allow ACE is left unread. Parsing it as allow would hand
        # Everyone to the real policy. The token SID stays the owner.
        first = args[0]
        everyone = state.scenario in {"deny_ace", "everyone_allow"}
        if everyone and state.converts >= 2 and isinstance(first, ctypes.c_void_p):
            out._obj.value = "S-1-1-0"  # type: ignore[attr-defined]
            return 1
        sid = None if state.scenario == "empty_sid" else "S-1-5-21-9"
        out._obj.value = sid  # type: ignore[attr-defined]
        return 1
    if name == "GetNamedSecurityInfoW":
        if state.scenario == "security_status":
            return 5
        args[3]._obj.value = 1  # type: ignore[attr-defined]
        args[7]._obj.value = 1  # type: ignore[attr-defined]
        if state.scenario == "null_dacl":
            args[5]._obj.value = 0  # type: ignore[attr-defined]
            return 0
        ace_type = 1 if state.scenario == "deny_ace" else 0
        state.acl = _plant_acl(ace_type)
        args[5]._obj.value = ctypes.addressof(state.acl)  # type: ignore[attr-defined]
        return 0
    return 0


def _install_win32(monkeypatch: pytest.MonkeyPatch, scenario: str) -> None:
    """Point ``ctypes.WinDLL`` at one scripted advapi32/kernel32 pair."""
    import ctypes

    state = _Win32State(scenario)

    class Call:
        def __init__(self, entry: str) -> None:
            self.entry = entry

        def __call__(self, *call_args: object) -> int:
            return _dispatch_win32(state, self.entry, call_args)

    class DLL:
        def __init__(self, _library: str, use_last_error: bool = False) -> None:
            return None

        def __getattr__(self, entry: str) -> Call:
            if entry.startswith("_"):
                raise AttributeError(entry)
            return Call(entry)

    monkeypatch.setattr(ctypes, "WinDLL", DLL, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)


def _force_windows_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    import synapse_channel.core.secure_path as module

    monkeypatch.setattr(module, "_POSIX", False)
    monkeypatch.setattr(module, "_WINDOWS", True)


def _whoami_sid(sid: str) -> Callable[..., object]:
    class Answer:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = f'"HOST\\user","{sid}"\n'
            self.stderr = ""

    def run(_argv: list[str], **_kwargs: object) -> Answer:
        return Answer()

    return run


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("open_fail", "cannot open process token"),
        ("size_zero", "cannot size token user"),
        ("read_fail", "cannot read token user"),
        ("convert_fail", "cannot convert user SID"),
        ("empty_sid", "SID is empty"),
    ],
    ids=("open_fail", "size_zero", "read_fail", "convert_fail", "empty_sid"),
)
def test_native_sid_failure_falls_back_to_whoami(
    scenario: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import synapse_channel.core.secure_path as module

    _force_windows_floor(monkeypatch)
    _install_win32(monkeypatch, scenario)
    with pytest.raises(SecurePathError, match=message):
        module._windows_current_user_sid_native()
    _install_win32(monkeypatch, scenario)
    whoami = _whoami_sid("S-1-5-21-8")
    monkeypatch.setattr(module.subprocess, "run", whoami)  # type: ignore[attr-defined]
    identified = current_user_key()
    assert identified.kind == "windows_sid"
    assert identified.value == "S-1-5-21-8"


def test_current_user_key_reads_the_process_token_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import synapse_channel.core.secure_path as module

    _force_windows_floor(monkeypatch)
    _install_win32(monkeypatch, "token_ok")

    def whoami_must_not_run(_argv: list[str], **_kwargs: object) -> object:
        raise AssertionError("whoami must not run when the process token opens")

    monkeypatch.setattr(module.subprocess, "run", whoami_must_not_run)  # type: ignore[attr-defined]
    identified = current_user_key()
    assert identified.kind == "windows_sid"
    assert identified.value == "S-1-5-21-9"


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("security_status", "cannot read security descriptor"),
        ("owner_convert_fail", "cannot convert owner SID"),
        ("ace_convert_fail", "cannot convert ACE SID"),
        ("null_dacl", "NULL DACL"),
    ],
    ids=("security_status", "owner_convert_fail", "ace_convert_fail", "null_dacl"),
)
def test_windows_owner_proof_reports_each_descriptor_failure(
    scenario: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"sekret")
    _force_windows_floor(monkeypatch)
    _install_win32(monkeypatch, scenario)
    with pytest.raises(SecurePathError, match=message):
        assert_owner_only_file_path(secret, purpose="secret")


@pytest.mark.parametrize("scenario", ["token_ok", "deny_ace"])
def test_windows_owner_proof_accepts_owner_only_aces(
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"sekret")
    _force_windows_floor(monkeypatch)
    _install_win32(monkeypatch, scenario)
    assert_owner_only_file_path(secret, purpose="secret")


def test_windows_owner_proof_refuses_an_everyone_allow_ace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"sekret")
    _force_windows_floor(monkeypatch)
    _install_win32(monkeypatch, "everyone_allow")
    with pytest.raises(SecurePathError, match="other principals"):
        assert_owner_only_file_path(secret, purpose="secret")
