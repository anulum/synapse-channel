# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the systemd sandbox block: content, wiring, and drift gates
"""The systemd hardening block: content, unit wiring, and anti-drift gates.

Four protections:

1. **Directive content** — the block is exactly the empirically validated
   user-manager-safe set; directives that fail under ``systemd --user`` with
   ``218/CAPABILITIES`` (``ProtectClock=``, ``ProtectKernelModules=``,
   ``PrivateDevices=``, ``CapabilityBoundingSet=``) must never reappear, and
   ``ProtectHome=`` must stay ``read-only`` so a pipx ``synapse`` under
   ``%h/.local/bin`` remains executable.
2. **Renderer wiring** — every generated unit (hub, presence, arm) carries the
   block with its unit-specific write paths and file-descriptor ceiling.
3. **Write-path coverage, code-derived** — the paths the sandboxed services
   write at runtime are computed from the real modules (mailbox cursor, owner
   lease, machine identity, the hub's ``--db``/``--relay-log`` arguments), not
   hard-coded here, so relocating any of that storage without widening
   ``ReadWritePaths=`` fails this file instead of failing the fleet at 02:00.
4. **Deploy-template drift** — the checked-in ``deploy/*.service`` operator
   templates must carry the exact same ``[Service]`` sandbox block as the
   renderers; the two surfaces are hand-synchronised and this is the gate.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from synapse_channel.machine_identity import identity_dir
from synapse_channel.mailbox_cursor import cursor_path
from synapse_channel.owner_lease import lease_path
from synapse_channel.service_hardening import (
    HUB_NOFILE,
    HUB_WRITE_PATHS,
    LISTENER_NOFILE,
    LISTENER_WRITE_PATHS,
    hardening_directives,
)
from synapse_channel.service_setup import (
    render_arm_unit,
    render_hub_unit,
    render_presence_unit,
)

_DEPLOY = Path(__file__).resolve().parent.parent / "deploy"

_FORBIDDEN_IN_USER_UNITS = (
    "CapabilityBoundingSet=",
    "ProtectClock=",
    "ProtectKernelModules=",
    "PrivateDevices=",
    "MemoryDenyWriteExecute=",
    "ProtectHome=yes",
)


def _rendered_units() -> dict[str, str]:
    return {
        "synapse-hub.service": render_hub_unit(synapse_bin="%h/.local/bin/synapse"),
        "synapse-presence@.service": render_presence_unit(synapse_bin="%h/.local/bin/synapse"),
        "synapse-arm@.service": render_arm_unit(synapse_bin="%h/.local/bin/synapse"),
    }


def _service_block(unit_text: str) -> str:
    """Return the ``[Service]`` section body of a unit file."""
    lines = unit_text.splitlines()
    start = lines.index("[Service]") + 1
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("["):
            break
        body.append(line)
    return "\n".join(body).strip()


def _sandbox_lines(unit_text: str) -> list[str]:
    """Return the sandbox directive lines of a unit's ``[Service]`` section."""
    block = _service_block(unit_text)
    skip_prefixes = ("Type=", "Environment=", "ExecStart", "Restart", "#")
    return [line for line in block.splitlines() if line and not line.startswith(skip_prefixes)]


# ---------------------------------------------------------------------------
# 1. Directive content
# ---------------------------------------------------------------------------


def test_block_is_the_validated_user_manager_safe_set() -> None:
    block = hardening_directives(write_paths=("%h/synapse",), nofile=4096)
    assert block == (
        "NoNewPrivileges=yes\n"
        "UMask=0077\n"
        "PrivateTmp=yes\n"
        "ProtectSystem=strict\n"
        "ProtectHome=read-only\n"
        "ReadWritePaths=%h/synapse\n"
        "ProtectKernelTunables=yes\n"
        "ProtectControlGroups=yes\n"
        "RestrictNamespaces=yes\n"
        "RestrictRealtime=yes\n"
        "RestrictSUIDSGID=yes\n"
        "LockPersonality=yes\n"
        "SystemCallArchitectures=native\n"
        "LimitNOFILE=4096\n"
    )


def test_block_joins_multiple_write_paths_on_one_line() -> None:
    block = hardening_directives(write_paths=LISTENER_WRITE_PATHS, nofile=LISTENER_NOFILE)
    assert "ReadWritePaths=%h/synapse %h/.local/share/synapse\n" in block
    assert f"LimitNOFILE={LISTENER_NOFILE}\n" in block


def test_no_forbidden_directive_anywhere() -> None:
    surfaces = list(_rendered_units().values()) + [
        (_DEPLOY / name).read_text(encoding="utf-8")
        for name in ("synapse-hub.service", "synapse-presence@.service", "synapse-arm@.service")
    ]
    for text in surfaces:
        for forbidden in _FORBIDDEN_IN_USER_UNITS:
            assert forbidden not in text, (
                f"{forbidden} fails or breaks under systemd --user; "
                "see service_hardening module docstring"
            )


# ---------------------------------------------------------------------------
# 2. Renderer wiring
# ---------------------------------------------------------------------------


def test_hub_unit_carries_hub_block() -> None:
    unit = render_hub_unit(synapse_bin="%h/.local/bin/synapse")
    assert hardening_directives(write_paths=HUB_WRITE_PATHS, nofile=HUB_NOFILE) in unit
    assert f"LimitNOFILE={HUB_NOFILE}" in unit


def test_presence_and_arm_units_carry_listener_block() -> None:
    listener_block = hardening_directives(write_paths=LISTENER_WRITE_PATHS, nofile=LISTENER_NOFILE)
    assert listener_block in render_presence_unit(synapse_bin="%h/.local/bin/synapse")
    assert listener_block in render_arm_unit(synapse_bin="%h/.local/bin/synapse")


def test_rendered_units_still_parse_as_unit_files() -> None:
    for name, text in _rendered_units().items():
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        parser.read_string(text, source=name)
        assert parser.has_section("Service"), name
        assert parser.has_section("Unit"), name


# ---------------------------------------------------------------------------
# 3. Write-path coverage, derived from the real storage modules
# ---------------------------------------------------------------------------


def _expanded(write_paths: tuple[str, ...], home: Path) -> list[Path]:
    return [Path(entry.replace("%h", str(home))) for entry in write_paths]


def _is_under(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def test_listener_write_paths_cover_every_client_storage_location(tmp_path: Path) -> None:
    roots = _expanded(LISTENER_WRITE_PATHS, tmp_path)
    cursor = cursor_path("PROJECT/agent", base=tmp_path / "synapse" / "mailbox-cursor")
    lease = lease_path("PROJECT/agent", base=tmp_path / "synapse" / "owner-lease")
    identity = identity_dir(base=tmp_path / ".local" / "share")
    for target in (cursor, lease, identity):
        assert _is_under(target, roots), f"{target} is outside ReadWritePaths {roots}"


def test_listener_default_storage_stays_under_the_declared_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A systemd user service does not inherit a shell's XDG_DATA_HOME; the
    # sandbox is declared against the fallback, so the check must use it too.
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    home = Path.home()
    roots = _expanded(LISTENER_WRITE_PATHS, home)
    assert _is_under(cursor_path("x"), roots), "default mailbox cursor moved"
    assert _is_under(lease_path("x"), roots), "default owner lease moved"
    assert _is_under(identity_dir(), roots), "default machine identity moved"


def test_hub_write_paths_cover_the_db_and_relay_arguments() -> None:
    unit = render_hub_unit(synapse_bin="%h/.local/bin/synapse")
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    written_arguments = [token for token in exec_line.split() if token.startswith("%h/")]
    assert written_arguments, "hub ExecStart no longer writes under %h — update this test"
    roots = _expanded(HUB_WRITE_PATHS, Path("/HOME"))
    for token in written_arguments:
        target = Path(token.replace("%h", "/HOME"))
        assert _is_under(target, roots), f"{token} is outside ReadWritePaths"


# ---------------------------------------------------------------------------
# 4. Deploy-template drift
# ---------------------------------------------------------------------------


def test_deploy_templates_carry_the_exact_renderer_sandbox_block() -> None:
    for name, rendered in _rendered_units().items():
        deployed = (_DEPLOY / name).read_text(encoding="utf-8")
        assert _sandbox_lines(deployed) == _sandbox_lines(rendered), (
            f"deploy/{name} sandbox block drifted from the renderer; update both surfaces together"
        )
