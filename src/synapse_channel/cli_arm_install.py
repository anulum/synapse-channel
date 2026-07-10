# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — permanent waiter service CLI
"""Install the persistent ``synapse arm`` systemd user service."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from synapse_channel.service_setup import (
    ArmServiceInstallResult,
    install_arm_service,
)

ArmServiceInstaller = Callable[..., ArmServiceInstallResult]


class RawTokenAction(argparse.Action):
    """Remember that ``--token`` came from argv before shared resolution."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        """Store the token and mark its origin as the process argument list."""
        del parser, option_string
        setattr(namespace, self.dest, values)
        namespace.raw_token_supplied = True


def add_arm_install_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the optional ``install`` action and its focused arguments."""
    parser.add_argument(
        "arm_action",
        nargs="?",
        choices=("install",),
        metavar="{install}",
        help="Install a permanent systemd user waiter instead of running interactively.",
    )
    parser.add_argument(
        "--identity",
        default=None,
        metavar="PROJECT/AGENT",
        help="Exact identity served by the permanent waiter (required with install).",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="After installation, daemon-reload and enable/start the exact identity unit.",
    )
    parser.add_argument(
        "--synapse-bin",
        default=None,
        help="Synapse executable path baked into the generated unit; defaults to PATH lookup.",
    )


def maybe_install_arm(
    args: argparse.Namespace,
    *,
    installer: ArmServiceInstaller = install_arm_service,
    platform_name: str | None = None,
) -> int | None:
    """Handle ``arm install`` or return ``None`` for the normal arm runtime."""
    action = getattr(args, "arm_action", None)
    if action is None:
        if any(
            (
                getattr(args, "identity", None),
                bool(getattr(args, "start", False)),
                getattr(args, "synapse_bin", None),
            )
        ):
            print(
                "--identity, --start, and --synapse-bin require `synapse arm install`",
                file=sys.stderr,
            )
            return 2
        return None

    identity = str(getattr(args, "identity", "") or "").strip()
    if not identity:
        print("synapse arm install requires --identity PROJECT/AGENT", file=sys.stderr)
        return 2
    platform = sys.platform if platform_name is None else platform_name
    if not platform.startswith("linux"):
        print(
            "synapse arm install currently supports Linux systemd user services only; "
            "on Windows use WSL with systemd enabled.",
            file=sys.stderr,
        )
        return 2

    token_file = getattr(args, "token_file", None)
    if getattr(args, "raw_token_supplied", False) or (
        getattr(args, "token", None) and not token_file
    ):
        print(
            "a permanent waiter will not embed --token or SYNAPSE_TOKEN; "
            "store the secret in a protected file and pass --token-file PATH",
            file=sys.stderr,
        )
        return 2
    resolved_token_file = None
    if token_file:
        resolved_token_file = str(Path(str(token_file)).expanduser().resolve())

    result = installer(
        identity=identity,
        uri=args.uri,
        synapse_bin=getattr(args, "synapse_bin", None),
        token_file=resolved_token_file,
        start=bool(getattr(args, "start", False)),
    )
    for line in result.lines:
        print(line)
    return 0 if result.ok else 1
