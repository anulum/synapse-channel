# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode-specific Participant CLI connection options
"""Configure OpenCode participants without widening the generic registry factory."""

from __future__ import annotations

import argparse
from typing import Protocol

from synapse_channel.participants.headless_opencode import (
    DEFAULT_BINARY,
    OpenCodeParticipant,
)
from synapse_channel.participants.opencode_api import (
    DEFAULT_ENDPOINT,
    OpenCodeApiParticipant,
)
from synapse_channel.participants.participant import Participant


class ParticipantBuilder(Protocol):
    """The stable generic Participant factory contract used as a fallback."""

    def __call__(
        self,
        provider: str,
        *,
        identity: str,
        model: str,
        timeout: float,
        probe: bool = False,
    ) -> Participant:
        """Build one registry participant through the stable generic contract."""
        ...


def add_opencode_connection_arguments(parser: argparse.ArgumentParser) -> None:
    """Add password-file-safe OpenCode connection controls to a turn parser."""
    group = parser.add_argument_group("OpenCode connection")
    group.add_argument(
        "--opencode-directory",
        default=".",
        help="Project directory for opencode/opencode-api turns (default: current directory).",
    )
    group.add_argument(
        "--opencode-endpoint",
        default=None,
        help=(
            "Server URL: enables `run --attach` for opencode and overrides the "
            "loopback default for opencode-api."
        ),
    )
    group.add_argument(
        "--opencode-username",
        default="opencode",
        help="Basic-auth username for an attached OpenCode server.",
    )
    group.add_argument(
        "--opencode-password-file",
        default=None,
        help="Owner-only OpenCode server password file; literal passwords are not accepted.",
    )
    group.add_argument(
        "--opencode-binary",
        default=DEFAULT_BINARY,
        help="OpenCode executable used by the headless provider.",
    )
    group.add_argument(
        "--opencode-thinking",
        action="store_true",
        help="Include source-verified thinking events in a headless OpenCode turn.",
    )
    group.add_argument(
        "--opencode-allow-insecure-http",
        action="store_true",
        help="Explicitly allow remote cleartext HTTP; literal loopback HTTP needs no opt-out.",
    )


def build_cli_participant(
    provider: str,
    *,
    identity: str,
    model: str,
    timeout: float,
    args: argparse.Namespace | None,
    fallback: ParticipantBuilder,
) -> Participant:
    """Build a turn participant, applying CLI connection options only to OpenCode."""
    if args is None or provider not in {"opencode", "opencode-api"}:
        return fallback(provider, identity=identity, model=model, timeout=timeout)

    directory = str(args.opencode_directory)
    endpoint = args.opencode_endpoint
    username = str(args.opencode_username)
    password_file = args.opencode_password_file
    allow_insecure_http = bool(args.opencode_allow_insecure_http)
    if provider == "opencode":
        return OpenCodeParticipant(
            identity,
            directory=directory,
            model=model,
            binary=str(args.opencode_binary),
            attach=str(endpoint) if endpoint else "",
            username=username,
            password_file=str(password_file) if password_file else None,
            allow_insecure_http=allow_insecure_http,
            thinking=bool(args.opencode_thinking),
            timeout=timeout,
        )
    return OpenCodeApiParticipant(
        identity,
        directory=directory,
        model=model,
        endpoint=str(endpoint) if endpoint else DEFAULT_ENDPOINT,
        username=username,
        password_file=str(password_file) if password_file else None,
        allow_insecure_http=allow_insecure_http,
        timeout=timeout,
    )
