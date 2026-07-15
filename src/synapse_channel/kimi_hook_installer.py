# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure planner for installing the Kimi claim hook into config.toml
"""Plan a validated Synapse ``[[hooks]]`` block in Kimi's ``config.toml``.

This is the pure half of installation: render the provider command, recognise one
exact marker pair, validate TOML before and after a transform, and plan idempotent
install/uninstall content. :mod:`synapse_channel.kimi_hook_config_file` owns the
bounded, race-aware filesystem boundary.
"""

from __future__ import annotations

import json
import sys

from synapse_channel.cli_claim_hook_common import (
    hook_timeout,
    render_hook_command,
)
from synapse_channel.core.errors import SynapseError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib

KIMI_HOOK_MARKER_BEGIN = "synapse-channel:kimi-hook:begin"
"""Opening marker for the Synapse-owned hook block inside ``config.toml``."""

KIMI_HOOK_MARKER_END = "synapse-channel:kimi-hook:end"
"""Closing marker for the Synapse-owned hook block inside ``config.toml``."""

_BEGIN_LINE = f"# {KIMI_HOOK_MARKER_BEGIN}"
_END_LINE = f"# {KIMI_HOOK_MARKER_END}"


class KimiHookInstallerError(SynapseError, ValueError):
    """A Kimi hook block or surrounding TOML config is unsafe to transform."""

    code = "kimi_hook_installer"


def render_hook_config(
    *,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
) -> str:
    """Return a token-safe Kimi config fragment for file edits and Bash."""
    command = render_hook_command(
        command="kimi-claim-hook",
        identity=identity,
        uri=uri,
        ready_timeout=ready_timeout,
        token_file=token_file,
        synapse_bin=synapse_bin,
    )
    return "\n".join(
        (
            "[[hooks]]",
            'event = "PreToolUse"',
            'matcher = "^(Write|Edit|Bash)$"',
            f"command = {json.dumps(command, ensure_ascii=False)}",
            f"timeout = {hook_timeout(ready_timeout)}",
            "",
        )
    )


def validate_config_toml(text: str) -> None:
    """Raise when non-empty ``text`` is not valid TOML."""
    if not text.strip():
        return
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise KimiHookInstallerError(
            "Kimi config.toml is not valid TOML; refusing to edit it."
        ) from exc


def _marker_bounds(text: str) -> tuple[int, int] | None:
    """Return the one exact marker pair, rejecting partial or duplicate blocks."""
    lines = text.splitlines()
    begins = [index for index, line in enumerate(lines) if line.strip() == _BEGIN_LINE]
    ends = [index for index, line in enumerate(lines) if line.strip() == _END_LINE]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise KimiHookInstallerError(
            "Kimi config.toml has partial, duplicated, or misordered Synapse hook markers."
        )
    return begins[0], ends[0]


def _strip_hook_block(text: str) -> str:
    """Return ``text`` with any Synapse hook block (and its padding) removed."""
    lines = text.splitlines()
    bounds = _marker_bounds(text)
    if bounds is None:
        return text
    begin, end = bounds
    remaining = lines[:begin] + lines[end + 1 :]
    while remaining and not remaining[0].strip():
        remaining.pop(0)
    while remaining and not remaining[-1].strip():
        remaining.pop()
    return "\n".join(remaining) + "\n" if remaining else ""


def contains_hook_block(text: str) -> bool:
    """Return whether ``text`` already carries a Synapse KIMI hook block."""
    return _marker_bounds(text) is not None


def render_marked_hook_block(
    *,
    identity: str,
    uri: str,
    ready_timeout: float,
    token_file: str | None,
    synapse_bin: str | None,
) -> str:
    """Render the marker-wrapped hook block ready to append to ``config.toml``."""
    inner = render_hook_config(
        identity=identity,
        uri=uri,
        ready_timeout=ready_timeout,
        token_file=token_file,
        synapse_bin=synapse_bin,
    )
    return f"# {KIMI_HOOK_MARKER_BEGIN}\n{inner}# {KIMI_HOOK_MARKER_END}\n"


def plan_install_hook(existing: str | None, block: str) -> str:
    """Return ``config.toml`` content after idempotently installing ``block``.

    Any prior Synapse hook block is stripped first, then the fresh block is
    appended after the file's own content.
    """
    current = existing or ""
    validate_config_toml(current)
    validate_config_toml(block)
    base = _strip_hook_block(current).rstrip()
    if not base:
        result = block
    else:
        result = f"{base}\n\n{block}"
    validate_config_toml(result)
    return result


def plan_uninstall_hook(existing: str) -> str:
    """Return ``config.toml`` content after removing the Synapse hook block."""
    validate_config_toml(existing)
    result = _strip_hook_block(existing)
    validate_config_toml(result)
    return result
