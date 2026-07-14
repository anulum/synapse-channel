# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral user service setup helpers
"""Install and describe provider-neutral Synapse user services.

The package cannot assume a source checkout exists after installation, so the
``synapse init`` and ``doctor --fix`` flows render their systemd units from this
module rather than copying files out of ``deploy/``. The checked-in deploy units
remain operator-readable templates; these helpers are the installed path.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess  # nosec B404 - fixed systemctl argv, never a shell string
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from synapse_channel.service_hardening import (
    HUB_NOFILE,
    HUB_WRITE_PATHS,
    LISTENER_NOFILE,
    LISTENER_WRITE_PATHS,
    hardening_directives,
)
from synapse_channel.terminal_text import terminal_text


class CommandRunner(Protocol):
    """Callable compatible with :func:`subprocess.run` for injectable tests."""

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command and return its completed process."""


@dataclass(frozen=True, slots=True)
class ArmServiceInstallResult:
    """Outcome and operator-facing lines from one arm-service installation."""

    ok: bool
    lines: tuple[str, ...]


def default_synapse_bin() -> str:
    """Return the preferred installed ``synapse`` executable path."""
    return shutil.which("synapse") or "synapse"


def user_systemd_dir(*, home: Path | None = None) -> Path:
    """Return the per-user systemd unit directory."""
    root = Path.home() if home is None else home
    return root / ".config" / "systemd" / "user"


def render_hub_unit(*, synapse_bin: str) -> str:
    """Render the local-first hub user service."""
    executable = _unit_token(synapse_bin, label="synapse executable path")
    # The hub must not be ordered After=default.target: WantedBy=default.target
    # gives the target an implicit After= on this unit, and the resulting boot
    # ordering cycle makes systemd delete the presence/arm start jobs.
    return (
        "# SPDX-License-"
        "Identifier: AGPL-3.0-or-later\n"
        "# Commercial license available\n"
        "# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.\n"
        "# © Code 2020–2026 Miroslav Šotek. All rights reserved.\n"
        "# ORCID: 0009-0009-3560-0851\n"
        "# Contact: www.anulum.li | protoscience@anulum.li\n"
        "# SYNAPSE CHANNEL — generated user service for the coordination hub\n\n"
        "[Unit]\n"
        "Description=SYNAPSE CHANNEL coordination hub (local-first, loopback)\n"
        "Documentation=https://github.com/anulum/synapse-channel\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={executable} hub --port=8876 --db=%h/synapse/hub.db "
        "--relay-log=%h/synapse/feed.ndjson --relay-max-lines=20000\n"
        "Restart=always\n"
        "RestartSec=2\n"
        + hardening_directives(write_paths=HUB_WRITE_PATHS, nofile=HUB_NOFILE)
        + "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_presence_unit(*, synapse_bin: str) -> str:
    """Render the project presence-holder template unit."""
    executable = _unit_token(synapse_bin, label="synapse executable path")
    return (
        "# SPDX-License-"
        "Identifier: AGPL-3.0-or-later\n"
        "# Commercial license available\n"
        "# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.\n"
        "# © Code 2020–2026 Miroslav Šotek. All rights reserved.\n"
        "# ORCID: 0009-0009-3560-0851\n"
        "# Contact: www.anulum.li | protoscience@anulum.li\n"
        "# SYNAPSE CHANNEL — generated user service for provider-neutral presence\n\n"
        "[Unit]\n"
        "Description=SYNAPSE CHANNEL presence holder for %I\n"
        "Documentation=https://github.com/anulum/synapse-channel\n"
        "After=synapse-hub.service\n"
        "Wants=synapse-hub.service\n"
        "StartLimitIntervalSec=0\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={executable} listen --name=%I-presence --for=%I "
        "--uri=ws://localhost:8876\n"
        "Restart=always\n"
        "RestartSec=3\n"
        + hardening_directives(write_paths=LISTENER_WRITE_PATHS, nofile=LISTENER_NOFILE)
        + "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _unit_token(value: str, *, label: str) -> str:
    """Return a safe single-token systemd value or reject ambiguous input."""
    if (
        not value
        or terminal_text(value) != value
        or any(character.isspace() or character in {'"', "'", "\\"} for character in value)
    ):
        raise ValueError(
            f"{label} must be one non-empty token without whitespace, controls, or quotes"
        )
    return value.replace("%", "%%")


def render_arm_unit(
    *,
    synapse_bin: str,
    uri: str = "ws://localhost:8876",
    token_file: str | None = None,
) -> str:
    """Render the persistent non-LLM wake listener template unit."""
    executable = _unit_token(synapse_bin, label="synapse executable path")
    hub_uri = _unit_token(uri, label="hub URI")
    extra_argument = ""
    if token_file is not None:
        token_path = _unit_token(token_file, label="token file path")
        extra_argument = f" --token-file={token_path}"
    return (
        "# SPDX-License-"
        "Identifier: AGPL-3.0-or-later\n"
        "# Commercial license available\n"
        "# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.\n"
        "# © Code 2020–2026 Miroslav Šotek. All rights reserved.\n"
        "# ORCID: 0009-0009-3560-0851\n"
        "# Contact: www.anulum.li | protoscience@anulum.li\n"
        "# SYNAPSE CHANNEL — generated user service for a provider-neutral wake listener\n\n"
        "[Unit]\n"
        "Description=SYNAPSE CHANNEL wake listener for %I\n"
        "Documentation=https://github.com/anulum/synapse-channel\n"
        "After=synapse-hub.service\n"
        "StartLimitIntervalSec=0\n\n"
        "[Service]\n"
        "Type=simple\n"
        "Environment=SYN_PROJECT=%I\n"
        "Environment=SYN_IDENTITY=%I\n"
        f"ExecStart={executable} arm --name=%I-rx --for=%I --directed-only "
        f"--mailbox --uri={hub_uri}{extra_argument}\n"
        "Restart=always\n"
        "RestartSec=2\n"
        + hardening_directives(write_paths=LISTENER_WRITE_PATHS, nofile=LISTENER_NOFILE)
        + "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _ensure_writable_dirs(home: Path | None) -> tuple[Path, ...]:
    """Create the directories the sandboxed units are allowed to write.

    ``ReadWritePaths=`` refuses to mount a path that does not exist, so a
    hardened unit would fail its first start on a fresh machine unless the
    writable roots are created at install time: the coordination data
    directory (event store, relay feed, mailbox cursors, owner leases) and the
    machine-identity directory (trust-on-first-use key auto-provisioning).
    """
    root = Path.home() if home is None else home
    directories = (root / "synapse", root / ".local" / "share" / "synapse")
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    return directories


def _run_service_command(
    command: list[str], *, runner: CommandRunner
) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    """Run one fixed service command and normalize its failure description."""
    rendered = " ".join(command)
    try:
        proc = runner(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return None, f"failed: {rendered} — {exc}"
    if proc.returncode == 0:
        return proc, ""
    detail = (proc.stderr or proc.stdout).strip()
    suffix = f" — {detail}" if detail else ""
    return proc, f"failed: {rendered}{suffix}"


def install_arm_service(
    *,
    identity: str,
    uri: str = "ws://localhost:8876",
    synapse_bin: str | None = None,
    token_file: str | None = None,
    start: bool = False,
    home: Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> ArmServiceInstallResult:
    """Install only the permanent waiter unit and optionally enable it.

    This deliberately does not install or start a hub or presence service. The
    exact identity is escaped by ``systemd-escape`` before ``--start`` enables
    the instance; an escape or systemctl failure is returned to the CLI instead
    of being reported as a successful installation.
    """
    synapse = synapse_bin or default_synapse_bin()
    unit_dir = user_systemd_dir(home=home)
    unit_path = unit_dir / "synapse-arm@.service"
    if not identity.strip():
        return ArmServiceInstallResult(False, ("identity must not be empty",))
    try:
        body = render_arm_unit(synapse_bin=synapse, uri=uri, token_file=token_file)
        unit_dir.mkdir(parents=True, exist_ok=True)
        writable = _ensure_writable_dirs(home)
        unit_path.write_text(body, encoding="utf-8")
    except (OSError, ValueError) as exc:
        return ArmServiceInstallResult(False, (f"failed to write {unit_path} — {exc}",))

    lines = [f"ensured {unit_dir}"]
    lines.extend(f"ensured {directory}" for directory in writable)
    lines.append(f"wrote {unit_path}")
    if not start:
        lines.extend(
            (
                "run: systemctl --user daemon-reload",
                "run: systemctl --user enable --now "
                '"$(systemd-escape --template=synapse-arm@.service -- '
                f'{shlex.quote(identity)})"',
            )
        )
        return ArmServiceInstallResult(True, tuple(lines))

    escape_command = [
        "systemd-escape",
        "--template=synapse-arm@.service",
        "--",
        identity,
    ]
    escaped, failure = _run_service_command(escape_command, runner=runner)
    if escaped is None or failure:
        lines.append(failure)
        return ArmServiceInstallResult(False, tuple(lines))
    arm_unit = escaped.stdout.strip()
    if not arm_unit:
        lines.append("failed: systemd-escape returned an empty unit name")
        return ArmServiceInstallResult(False, tuple(lines))

    for command in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", arm_unit],
    ):
        _proc, failure = _run_service_command(command, runner=runner)
        if failure:
            lines.append(failure)
            return ArmServiceInstallResult(False, tuple(lines))
        lines.append(f"ok: {' '.join(command)}")
    return ArmServiceInstallResult(True, tuple(lines))


def escaped_instance(
    identity: str, *, template: str, runner: CommandRunner = subprocess.run
) -> str:
    """Return a systemd-safe unit instance name for ``identity``.

    ``systemd-escape`` is used when available so identities containing ``/`` or
    other special characters work. A conservative fallback keeps simple project
    names usable on systems without systemd.
    """
    proc = runner(
        ["systemd-escape", f"--template={template}", "--", identity],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    safe = identity.replace("/", "-").replace(" ", "-")
    if template.endswith("@.service"):
        return template.replace("@.service", f"@{safe}.service")
    return f"{template}-{safe}"


def service_suggestions(
    *, project: str, identity: str, synapse_bin: str | None = None
) -> list[str]:
    """Return exact commands for manually installing and starting user services."""
    synapse = synapse_bin or default_synapse_bin()
    return [
        "mkdir -p ~/.config/systemd/user ~/synapse ~/.local/share/synapse",
        "synapse init --install-user-services",
        "systemctl --user daemon-reload",
        "systemctl --user enable --now synapse-hub.service",
        "systemctl --user enable --now "
        '"$(systemd-escape --template=synapse-presence@.service -- '
        f'{shlex.quote(project)})"',
        "systemctl --user enable --now "
        '"$(systemd-escape --template=synapse-arm@.service -- '
        f'{shlex.quote(identity)})"',
        "synapse install-shell-hook --shell auto",
        f"# installed synapse binary detected as: {terminal_text(synapse)}",
    ]


def install_user_services(
    *,
    project: str,
    identity: str,
    synapse_bin: str | None = None,
    start: bool = False,
    home: Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> list[str]:
    """Install local-first user services and optionally start them.

    Parameters
    ----------
    project, identity : str
        Project presence identity and worker wake identity.
    synapse_bin : str or None, optional
        ``synapse`` executable path baked into generated units. Defaults to the
        executable found on ``PATH``.
    start : bool, optional
        When ``True``, run ``systemctl --user daemon-reload`` and enable/start
        hub, presence, and wake-listener units.
    home : Path or None, optional
        Home directory override for tests.
    runner : CommandRunner, optional
        Command runner override for tests.

    Returns
    -------
    list[str]
        Human-readable actions taken or exact follow-up commands.
    """
    synapse = synapse_bin or default_synapse_bin()
    unit_dir = user_systemd_dir(home=home)
    unit_dir.mkdir(parents=True, exist_ok=True)
    writable = _ensure_writable_dirs(home)

    units = {
        "synapse-hub.service": render_hub_unit(synapse_bin=synapse),
        "synapse-presence@.service": render_presence_unit(synapse_bin=synapse),
        "synapse-arm@.service": render_arm_unit(synapse_bin=synapse),
    }
    lines = [f"ensured {unit_dir}"]
    lines.extend(f"ensured {directory}" for directory in writable)
    for filename, body in units.items():
        path = unit_dir / filename
        path.write_text(body, encoding="utf-8")
        lines.append(f"wrote {path}")

    if not start:
        lines.append("run: systemctl --user daemon-reload")
        lines.append("run: systemctl --user enable --now synapse-hub.service")
        lines.append(
            "run: systemctl --user enable --now "
            '"$(systemd-escape --template=synapse-presence@.service -- '
            f'{shlex.quote(project)})"'
        )
        lines.append(
            "run: systemctl --user enable --now "
            '"$(systemd-escape --template=synapse-arm@.service -- '
            f'{shlex.quote(identity)})"'
        )
        return lines

    presence_unit = escaped_instance(project, template="synapse-presence@.service", runner=runner)
    arm_unit = escaped_instance(identity, template="synapse-arm@.service", runner=runner)
    commands = [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "synapse-hub.service"],
        ["systemctl", "--user", "enable", "--now", presence_unit],
        ["systemctl", "--user", "enable", "--now", arm_unit],
    ]
    for command in commands:
        proc = runner(command, capture_output=True, text=True, check=False)
        rendered = " ".join(command)
        if proc.returncode == 0:
            lines.append(f"ok: {rendered}")
        else:
            detail = (proc.stderr or proc.stdout).strip()
            lines.append(f"failed: {rendered}" + (f" — {detail}" if detail else ""))
    return lines
