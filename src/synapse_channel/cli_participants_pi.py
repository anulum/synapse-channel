# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pi Participant CLI options
"""Expose a pinned pi RPC participant through the existing provider factory."""

from __future__ import annotations

import argparse
from pathlib import Path

from synapse_channel.cli_participants_opencode import (
    ParticipantBuilder,
)
from synapse_channel.cli_participants_opencode import (
    build_cli_participant as build_other_cli_participant,
)
from synapse_channel.participants.headless_pi import (
    DEFAULT_BINARY,
    PiClaimBinding,
    PiParticipant,
)
from synapse_channel.participants.participant import Participant


def add_pi_connection_arguments(parser: argparse.ArgumentParser) -> None:
    """Add local pi process and owner-session controls to a turn parser."""
    group = parser.add_argument_group("pi RPC connection")
    group.add_argument("--pi-directory", default=".", help="Working directory for pi turns.")
    group.add_argument("--pi-binary", default=DEFAULT_BINARY, help="Pinned pi executable.")
    group.add_argument("--pi-session-dir", default=None, help="Owner-private pi session directory.")
    group.add_argument("--pi-resume-session", default="", help="Exact pi session UUID to resume.")
    group.add_argument("--pi-extension", default=None, help="Installed Synapse pi extension file.")
    group.add_argument("--pi-project", default=None, help="Exact Synapse project for coding tools.")
    group.add_argument("--pi-repository", default=None, help="Claimed Git worktree root.")
    group.add_argument("--pi-task-id", default=None, help="Claim task ID for coding tools.")
    group.add_argument("--pi-epoch", type=int, default=None, help="Live claim epoch at launch.")
    group.add_argument("--pi-hub-uri", default=None, help="Authoritative claim hub URI.")
    group.add_argument("--pi-synapse-bin", default="synapse", help="Synapse CLI executable.")
    group.add_argument("--pi-token-file", default=None, help="Owner-only hub token file.")


def _binding(args: argparse.Namespace) -> PiClaimBinding | None:
    """Require the whole coding-tool binding, never a partially guarded host."""
    values = (
        args.pi_extension,
        args.pi_project,
        args.pi_repository,
        args.pi_task_id,
        args.pi_epoch,
        args.pi_hub_uri,
    )
    if not any(value is not None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError(
            "pi coding tools require extension, project, repository, task, epoch and hub"
        )
    return PiClaimBinding(
        project=str(args.pi_project),
        repository=Path(args.pi_repository),
        task_id=str(args.pi_task_id),
        epoch=int(args.pi_epoch),
        uri=str(args.pi_hub_uri),
        extension=Path(args.pi_extension),
        synapse_binary=str(args.pi_synapse_bin),
        token_file=Path(args.pi_token_file) if args.pi_token_file else None,
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
    """Apply pi options or delegate all other provider-specific construction."""
    if provider != "pi" or args is None:
        return build_other_cli_participant(
            provider,
            identity=identity,
            model=model,
            timeout=timeout,
            args=args,
            fallback=fallback,
        )
    return PiParticipant(
        identity,
        directory=str(args.pi_directory),
        model=model,
        binary=str(args.pi_binary),
        session_dir=str(args.pi_session_dir) if args.pi_session_dir else None,
        claim_binding=_binding(args),
        timeout=timeout,
    )
