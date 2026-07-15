# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared-host / CI-host attack-class regression contract
"""Named regression contract for the shared-host / CI-host threat class.

Each test is one attack a co-tenant (or CI job) on the same host can attempt,
asserted *refused* at the public boundary of the defence that stops it. This
module is deliberately cross-cutting: the per-defence unit suites
(``test_reap``, ``test_secret_files``, ``test_dashboard_host_guard*``,
``test_private_dir``, the A2A webhook receivers) prove each mechanism in
isolation; this one reddens if *any* of them regresses in a way that reopens the
threat class the security reviewer asked to keep closed.

Covered attacks:

* predictable / world-writable temp paths (``/tmp`` clobber) — both the path
  choice (:mod:`synapse_channel.reap`) and its materialisation
  (:func:`synapse_channel.core.private_dir.ensure_private_dir`);
* symlinked / world-readable secret files (:func:`read_secret_file`);
* DNS-rebinding onto the dashboard (:mod:`synapse_channel.dashboard_host_guard`);
* DNS-rebinding / SSRF on an outbound webhook target
  (:func:`resolve_pinned_endpoints`);
* smuggled claim paths (the stable claim-identity floor; cross-platform case
  collision itself is tracked by REV-SEC-05).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.error import URLError

import pytest

from synapse_channel.core.private_dir import PrivateDirError, ensure_private_dir
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.dashboard_host_guard import allowed_host_authorities, host_allowed
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.staged_paths import _normalise_staged_path
from synapse_channel.reap import private_runtime_parent, provider_runtime_dir, runtime_dir
from synapse_channel.safe_webhook_transport import resolve_pinned_endpoints

_POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="shared-host floor is a POSIX surface")

_DASHBOARD_PORT = 8765


# --- predictable / world-writable temp paths (/tmp clobber) -----------------

# Every hostile shell environment a co-tenant could hand the process: none may
# collapse the runtime directory onto a bare, world-shared /tmp/synapse-* name
# that any local user can pre-create and clobber.
_HOSTILE_RUNTIME_ENVS: tuple[dict[str, str], ...] = (
    {},
    {"XDG_RUNTIME_DIR": ""},
    {"XDG_RUNTIME_DIR": "   "},
    {"HOME": "", "XDG_CACHE_HOME": ""},
    {"HOME": "   ", "XDG_CACHE_HOME": "   "},
)


@pytest.mark.parametrize("env", _HOSTILE_RUNTIME_ENVS)
def test_runtime_dirs_never_collapse_to_a_shared_tmp_path(env: dict[str, str]) -> None:
    for resolved in (runtime_dir(env), provider_runtime_dir(env), private_runtime_parent(env)):
        text = str(resolved)
        assert resolved != Path("/tmp/synapse-shell")
        assert resolved != Path("/tmp/synapse-provider-tmux")
        # A bare shared name has no per-user segment; the safe fallbacks carry a
        # uid key or a private-home root.
        if text.startswith("/tmp/"):
            assert f"synapse-user-{os.getuid()}" in text


@_POSIX_ONLY
def test_a_symlinked_runtime_dir_is_refused_not_followed(tmp_path: Path) -> None:
    elsewhere = tmp_path / "attacker-store"
    elsewhere.mkdir(mode=0o700)
    hijacked = tmp_path / "synapse-runtime"
    hijacked.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PrivateDirError):
        ensure_private_dir(hijacked, purpose="runtime")


@_POSIX_ONLY
def test_a_world_accessible_runtime_dir_is_tightened(tmp_path: Path) -> None:
    runtime = tmp_path / "synapse-runtime"
    runtime.mkdir(mode=0o777)
    runtime.chmod(0o777)
    ensure_private_dir(runtime, purpose="runtime")
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700


# --- symlinked / world-readable secret files --------------------------------


@_POSIX_ONLY
def test_a_world_readable_secret_file_is_refused(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("s3cr3t-value", encoding="utf-8")
    token.chmod(0o644)
    with pytest.raises(SecretFileError) as caught:
        read_secret_file(token, flag="--metrics-token-file")
    # The refusal names the flag and path but never the secret it protects.
    assert "s3cr3t-value" not in str(caught.value)


@_POSIX_ONLY
def test_a_symlinked_secret_file_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real-token"
    real.write_text("s3cr3t-value", encoding="utf-8")
    real.chmod(0o600)
    link = tmp_path / "token"
    link.symlink_to(real)
    with pytest.raises(SecretFileError) as caught:
        read_secret_file(link, flag="--message-auth-key-file")
    assert "s3cr3t-value" not in str(caught.value)


# --- DNS-rebinding onto the dashboard ---------------------------------------


def test_dashboard_refuses_a_rebound_host_on_a_concrete_bind() -> None:
    allowed = allowed_host_authorities("127.0.0.1", _DASHBOARD_PORT)
    assert host_allowed(f"attacker.example:{_DASHBOARD_PORT}", allowed) is False
    assert host_allowed(f"localhost:{_DASHBOARD_PORT}", allowed) is True


def test_dashboard_refuses_a_rebound_host_on_a_wildcard_bind() -> None:
    # A wildcard bind contributes no bind authority; a rebinding page's host is
    # still absent from the loopback-only set.
    allowed = allowed_host_authorities("0.0.0.0", _DASHBOARD_PORT)
    assert host_allowed("attacker.example", allowed) is False
    assert host_allowed("127.0.0.1", allowed) is True


def test_dashboard_refuses_an_absent_or_malformed_host() -> None:
    allowed = allowed_host_authorities("127.0.0.1", _DASHBOARD_PORT)
    assert host_allowed(None, allowed) is False
    assert host_allowed("::::not-an-authority", allowed) is False


# --- DNS-rebinding / SSRF on an outbound webhook target ---------------------


def test_webhook_refuses_a_target_that_resolves_to_a_local_address() -> None:
    # A rebinding webhook target resolves to a loopback/internal address; the
    # pin refuses it rather than connect to an internal service.
    with pytest.raises(URLError):
        resolve_pinned_endpoints("127.0.0.1", _DASHBOARD_PORT, allow_local=False)


def test_webhook_allows_a_local_target_only_when_explicitly_opted_in() -> None:
    addresses = resolve_pinned_endpoints("127.0.0.1", _DASHBOARD_PORT, allow_local=True)
    # Every pinned address is the loopback literal we asked for — a resolver may
    # return it more than once, but none may be a smuggled other host.
    assert addresses
    assert all(address == "127.0.0.1" for address in addresses)


# --- smuggled claim paths (stable claim-identity floor) ---------------------


@pytest.mark.parametrize(
    "smuggled",
    [
        "../../etc/passwd",
        "src/../../secret",
        "/etc/shadow",
        "C:\\Windows\\system32",
        "src/evil\x00.py",
    ],
)
def test_claim_path_floor_refuses_a_smuggled_path(smuggled: str) -> None:
    # A staged path that escapes the worktree, is absolute, or carries control
    # characters cannot become a claim identity. (Cross-platform case-fold
    # collision — src/Foo.py vs src/foo.py — is a distinct gap tracked by
    # REV-SEC-05 and is not asserted here.)
    with pytest.raises(GitError):
        _normalise_staged_path(smuggled)


def test_claim_path_floor_accepts_a_plain_repository_path() -> None:
    assert _normalise_staged_path("src/synapse_channel/core/private_dir.py") == (
        "src/synapse_channel/core/private_dir.py"
    )
