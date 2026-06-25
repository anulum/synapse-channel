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

import shutil
import subprocess
from pathlib import Path
from typing import Protocol


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


def default_synapse_bin() -> str:
    """Return the preferred installed ``synapse`` executable path."""
    return shutil.which("synapse") or "synapse"


def user_systemd_dir(*, home: Path | None = None) -> Path:
    """Return the per-user systemd unit directory."""
    root = Path.home() if home is None else home
    return root / ".config" / "systemd" / "user"


def render_hub_unit(*, synapse_bin: str) -> str:
    """Render the local-first hub user service."""
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
        "Documentation=https://github.com/anulum/synapse-channel\n"
        "After=default.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={synapse_bin} hub --port 8876 --db %h/synapse/hub.db "
        "--relay-log %h/synapse/feed.ndjson --relay-max-lines 20000\n"
        "Restart=always\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_presence_unit(*, synapse_bin: str) -> str:
    """Render the project presence-holder template unit."""
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
        f"ExecStart={synapse_bin} listen --name %I-presence --for %I --uri ws://localhost:8876\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_arm_unit(*, synapse_bin: str) -> str:
    """Render the persistent non-LLM wake listener template unit."""
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
        "Wants=synapse-hub.service\n"
        "StartLimitIntervalSec=0\n\n"
        "[Service]\n"
        "Type=simple\n"
        "Environment=SYN_PROJECT=%I\n"
        "Environment=SYN_IDENTITY=%I\n"
        f"ExecStart={synapse_bin} arm --name %I-rx --for %I --directed-only "
        "--uri ws://localhost:8876\n"
        "Restart=always\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def escaped_instance(
    identity: str, *, template: str, runner: CommandRunner = subprocess.run
) -> str:
    """Return a systemd-safe unit instance name for ``identity``.

    ``systemd-escape`` is used when available so identities containing ``/`` or
    other special characters work. A conservative fallback keeps simple project
    names usable on systems without systemd.
    """
    proc = runner(
        ["systemd-escape", f"--template={template}", identity],
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
        "mkdir -p ~/.config/systemd/user ~/synapse",
        "synapse init --install-user-services",
        "systemctl --user daemon-reload",
        "systemctl --user enable --now synapse-hub.service",
        "systemctl --user enable --now "
        f'"$(systemd-escape --template=synapse-presence@.service {project!r})"',
        "systemctl --user enable --now "
        f'"$(systemd-escape --template=synapse-arm@.service {identity!r})"',
        f"# installed synapse binary detected as: {synapse}",
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
    data_dir = (Path.home() if home is None else home) / "synapse"
    unit_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    units = {
        "synapse-hub.service": render_hub_unit(synapse_bin=synapse),
        "synapse-presence@.service": render_presence_unit(synapse_bin=synapse),
        "synapse-arm@.service": render_arm_unit(synapse_bin=synapse),
    }
    lines = [f"ensured {unit_dir}", f"ensured {data_dir}"]
    for filename, body in units.items():
        path = unit_dir / filename
        path.write_text(body, encoding="utf-8")
        lines.append(f"wrote {path}")

    if not start:
        lines.append("run: systemctl --user daemon-reload")
        lines.append("run: systemctl --user enable --now synapse-hub.service")
        lines.append(
            "run: systemctl --user enable --now "
            f'"$(systemd-escape --template=synapse-presence@.service {project!r})"'
        )
        lines.append(
            "run: systemctl --user enable --now "
            f'"$(systemd-escape --template=synapse-arm@.service {identity!r})"'
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
