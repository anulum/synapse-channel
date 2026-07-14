# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from synapse_channel.git.githook import (
    HOOK_MARKER,
    _binary_resolvable,
    _hook_synapse_bin,
    check_hooks,
    hook_installed,
    hooks_directory,
    install_hooks,
)


def test_hooks_directory_uses_rev_parse() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "/repo/.git/hooks"

    assert hooks_directory(runner=runner) == Path("/repo/.git/hooks")
    assert captured == [["rev-parse", "--git-path", "hooks"]]


def test_install_hooks_writes_executable_hooks(tmp_path: Path) -> None:
    lines = install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert any("post-commit" in line for line in lines)
    assert any("post-merge" in line for line in lines)
    for filename, trigger in [("post-commit", "commit"), ("post-merge", "merge")]:
        hook = tmp_path / filename
        body = hook.read_text(encoding="utf-8")
        assert HOOK_MARKER in body
        assert f"git-release --trigger={trigger}" in body
        assert "--name=ME" in body
        assert os.access(hook, os.X_OK)
        assert hook.stat().st_mode & stat.S_IXUSR


def test_install_hooks_bakes_token_file(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", token_file="/etc/synapse.token", hooks_dir=tmp_path)
    body = (tmp_path / "post-commit").read_text(encoding="utf-8")
    assert "--token-file=/etc/synapse.token" in body


def test_install_hooks_bakes_an_explicit_synapse_bin(tmp_path: Path) -> None:
    install_hooks(
        uri="ws://h", name="ME", synapse_bin="/opt/synapse/bin/synapse", hooks_dir=tmp_path
    )
    body = (tmp_path / "post-commit").read_text(encoding="utf-8")
    assert "/opt/synapse/bin/synapse git-release --trigger=commit" in body


def test_install_hooks_resolves_an_absolute_synapse_from_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    hooks_dir = tmp_path / "hooks"
    bin_dir.mkdir()
    synapse = bin_dir / "synapse"
    synapse.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    synapse.chmod(0o700)
    old_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(bin_dir)
        install_hooks(uri="ws://h", name="ME", hooks_dir=hooks_dir)
    finally:
        os.environ["PATH"] = old_path

    body = (hooks_dir / "post-commit").read_text(encoding="utf-8")
    assert f"{synapse} git-release" in body


def test_install_hooks_falls_back_to_bare_name_when_synapse_not_found(tmp_path: Path) -> None:
    empty_bin = tmp_path / "empty-bin"
    hooks_dir = tmp_path / "hooks"
    empty_bin.mkdir()
    old_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(empty_bin)
        install_hooks(uri="ws://h", name="ME", hooks_dir=hooks_dir)
    finally:
        os.environ["PATH"] = old_path

    body = (hooks_dir / "post-commit").read_text(encoding="utf-8")
    assert "\nsynapse git-release" in body  # bare name resolved from PATH at hook time


def test_install_hooks_overwrites_its_own_hook(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    lines = install_hooks(uri="ws://h", name="ME2", hooks_dir=tmp_path)
    assert all("installed" in line for line in lines)
    assert "--name=ME2" in (tmp_path / "post-commit").read_text(encoding="utf-8")


def test_install_hooks_skips_foreign_hook(tmp_path: Path) -> None:
    foreign = tmp_path / "post-commit"
    foreign.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    lines = install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert any("skipped post-commit" in line for line in lines)
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"  # untouched
    assert (tmp_path / "post-merge").exists()  # the non-conflicting one is still installed


def test_install_hooks_resolves_dir_from_runner(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", runner=lambda _a: str(tmp_path))
    assert (tmp_path / "post-commit").exists()


def test_hook_installed_true_after_install(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert hook_installed("merge", hooks_dir=tmp_path) is True
    assert hook_installed("commit", hooks_dir=tmp_path) is True


def test_hook_installed_false_when_absent(tmp_path: Path) -> None:
    assert hook_installed("merge", hooks_dir=tmp_path) is False


def test_hook_installed_false_for_a_foreign_hook(tmp_path: Path) -> None:
    (tmp_path / "post-merge").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    assert hook_installed("merge", hooks_dir=tmp_path) is False  # no marker → not ours


def test_hook_installed_unknown_trigger_is_false(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert hook_installed("push", hooks_dir=tmp_path) is False


def test_hook_installed_resolves_dir_from_runner(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert hook_installed("merge", runner=lambda _a: str(tmp_path)) is True


def test_check_hooks_reports_installed_and_resolvable_binary(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", synapse_bin=sys.executable, hooks_dir=tmp_path)
    report = check_hooks(hooks_dir=tmp_path)
    assert {entry["trigger"] for entry in report} == {"commit", "merge"}
    for entry in report:
        assert entry["installed"] is True
        assert entry["synapse_bin"] == sys.executable
        assert entry["binary_ok"] is True


def test_check_hooks_reports_missing_when_not_installed(tmp_path: Path) -> None:
    report = check_hooks(hooks_dir=tmp_path)
    assert all(entry["installed"] is False for entry in report)
    assert all(entry["binary_ok"] is False for entry in report)
    assert all(entry["synapse_bin"] is None for entry in report)


def test_check_hooks_flags_an_unresolvable_binary(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", synapse_bin="/nonexistent/synapse", hooks_dir=tmp_path)
    report = check_hooks(hooks_dir=tmp_path)
    for entry in report:
        assert entry["installed"] is True
        assert entry["synapse_bin"] == "/nonexistent/synapse"
        assert entry["binary_ok"] is False


def test_check_hooks_resolves_dir_from_runner(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", synapse_bin=sys.executable, hooks_dir=tmp_path)
    report = check_hooks(runner=lambda _a: str(tmp_path))
    assert all(entry["installed"] for entry in report)


def test_hook_synapse_bin_extracts_or_returns_none() -> None:
    assert _hook_synapse_bin("/abs/synapse git-release --trigger commit") == "/abs/synapse"
    assert _hook_synapse_bin(f"#!/bin/sh\n{HOOK_MARKER}\n") is None


def test_binary_resolvable_covers_each_path() -> None:
    assert _binary_resolvable(sys.executable) is True  # absolute, executable
    assert _binary_resolvable("/nonexistent/synapse") is False  # absolute, missing
    assert _binary_resolvable("sh") is True  # bare name on PATH
    assert _binary_resolvable("definitely-not-a-real-binary-xyz") is False  # bare, off PATH
    assert _binary_resolvable(None) is False
    assert _binary_resolvable("") is False


def test_install_hooks_shell_quotes_values(tmp_path: Path) -> None:
    # Metacharacters and a leading dash must remain one bound option value.
    install_hooks(uri="ws://h", name="--help$(touch PWNED)", hooks_dir=tmp_path)
    body = (tmp_path / "post-commit").read_text(encoding="utf-8")
    assert "--name='--help$(touch PWNED)'" in body
    assert "--name --help" not in body


def test_install_hooks_skips_binary_foreign_hook(tmp_path: Path) -> None:
    # A non-UTF-8 hook from something else must be detected and left untouched, not crash.
    (tmp_path / "post-commit").write_bytes(b"\xff\xfe\x00binary")
    lines = install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert any("skipped post-commit" in line for line in lines)
    assert (tmp_path / "post-commit").read_bytes() == b"\xff\xfe\x00binary"
