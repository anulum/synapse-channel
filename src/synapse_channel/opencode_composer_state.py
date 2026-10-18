# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — launch-bound native wake state
"""Keep staged-wake evidence local to the native process launch that produced it."""

from __future__ import annotations

import hashlib
import re
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from synapse_channel.opencode_composer import PROTOCOL
from synapse_channel.opencode_composer_files import (
    ComposerError,
    probe_lock,
    read_document,
    write_document,
)

if TYPE_CHECKING:
    from synapse_channel.agent_tmux import AgentTmuxConfig


def profile_directory(config: AgentTmuxConfig) -> Path:
    """Derive one private profile for an exact seat and tmux session.

    Parameters
    ----------
    config : AgentTmuxConfig
        Logical identity, session and registry location.

    Returns
    -------
    pathlib.Path
        Profile path beneath the owner-only registry directory.
    """
    from synapse_channel.agent_tmux import registry_path

    key = hashlib.sha256((config.identity + "\0" + config.session).encode()).hexdigest()[:24]
    return registry_path(config).parent / f"opencode-{key}"


def current_launch_nonce(config: AgentTmuxConfig) -> str:
    """Read the private launch marker for this exact native seat and session.

    Parameters
    ----------
    config : AgentTmuxConfig
        Managed client whose profile was prepared before native qualification.

    Returns
    -------
    str
        Validated hexadecimal launch marker, replaced for every new client.

    Raises
    ------
    ValueError, OSError
        Profile privacy, protocol, identity or launch marker is invalid.
    """
    profile = read_document(profile_directory(config) / "profile.json")
    nonce = profile.get("launch_nonce")
    if (
        profile.get("protocol") != PROTOCOL
        or profile.get("identity") != config.identity
        or profile.get("session") != config.session
        or not isinstance(nonce, str)
        or not re.fullmatch(r"[0-9a-f]{32}", nonce)
    ):
        raise ComposerError("native wake launch marker is unavailable")
    return nonce


def launch_has_changed(config: AgentTmuxConfig, recorded_nonce: object) -> bool:
    """Recognise a known retired launch without guessing from absent metadata.

    Parameters
    ----------
    config : AgentTmuxConfig
        Current managed native client.
    recorded_nonce : object
        Launch marker recorded before the pending hint was pasted.

    Returns
    -------
    bool
        True only for two valid, different launch markers. Unknown historical
        metadata retains the staged state and requires exact input recovery.

    Raises
    ------
    ValueError, OSError
        A previously known launch cannot be compared with the current profile.
    """
    return (
        isinstance(recorded_nonce, str)
        and re.fullmatch(r"[0-9a-f]{32}", recorded_nonce) is not None
        and recorded_nonce != current_launch_nonce(config)
    )


def native_wake_transaction(config: AgentTmuxConfig) -> AbstractContextManager[None]:
    """Serialise the entire native wake independently from individual probes.

    Parameters
    ----------
    config : AgentTmuxConfig
        Exact identity/session whose profile owns the transaction lock.

    Returns
    -------
    contextlib.AbstractContextManager
        Nonblocking exclusive wake.lock context, independent of probe.lock.
    """
    return probe_lock(profile_directory(config), name="wake.lock")


def set_stage_verified(config: AgentTmuxConfig, verified: bool) -> None:
    """Persist whether this launch's pending hint was observed in its composer.

    Parameters
    ----------
    config : AgentTmuxConfig
        Exact identity/session whose wake is being prepared.
    verified : bool
        Whether the current staged hint was matched by native readback.

    Raises
    ------
    ValueError, OSError
        The current launch or durable private stage document is unavailable.
    """
    write_document(
        profile_directory(config),
        "stage.json",
        {
            "identity": config.identity,
            "session": config.session,
            "launch_nonce": current_launch_nonce(config),
            "verified": verified,
        },
    )


def stage_is_verified(config: AgentTmuxConfig) -> bool:
    """Refuse consumption evidence originating from a different native launch.

    Parameters
    ----------
    config : AgentTmuxConfig
        Exact identity/session whose pending wake is being recovered.

    Returns
    -------
    bool
        Whether private verification belongs to the current launch. Missing,
        unreadable, historical or unrelated metadata returns False.
    """
    try:
        document = read_document(profile_directory(config) / "stage.json")
        nonce = current_launch_nonce(config)
    except (OSError, ValueError):
        return False
    return document == {
        "identity": config.identity,
        "session": config.session,
        "launch_nonce": nonce,
        "verified": True,
    }
