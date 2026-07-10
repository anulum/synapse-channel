# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity-safe MCP bridge onboarding
"""Resolve an MCP bridge identity without trusting stray ambient state."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class McpIdentityResolution:
    """Resolved MCP bridge identity and any operator-visible safety note.

    Attributes
    ----------
    name : str
        Exact hub identity the bridge registers under.
    project : str
        Bare project namespace containing ``name``.
    source : str
        Resolution source: ``"flag"``, ``"env"``, or ``"cwd"``.
    note : str
        Non-fatal note explaining ignored ambient identity state.
    """

    name: str
    project: str
    source: str
    note: str = ""


def resolve_mcp_identity(
    explicit_name: str | None,
    *,
    env: Mapping[str, str] | None = None,
    cwd_basename: str | None = None,
    home_basename: str | None = None,
) -> McpIdentityResolution:
    """Return the exact bridge identity using the safe ``syn`` precedence.

    An explicit ``--name`` always wins. Without it, an agreeing
    ``SYN_PROJECT``/``SYN_IDENTITY`` pair supplies the exact identity; a project
    alone or the git/CWD project fallback becomes ``<project>/mcp``. A stray
    ambient identity is ignored visibly, and an implausible home/system fallback
    is refused instead of registering a shared accidental name.

    Parameters
    ----------
    explicit_name : str or None
        Value supplied through ``synapse mcp --name``.
    env : Mapping[str, str] or None, optional
        Environment used for identity resolution. Defaults to ``os.environ``.
    cwd_basename : str or None, optional
        Git-toplevel/CWD basename. Resolved lazily when omitted.
    home_basename : str or None, optional
        Home-directory basename used to reject an accidental home identity.

    Returns
    -------
    McpIdentityResolution
        Exact bridge name, project, source, and optional safety note.

    Raises
    ------
    ValueError
        If an explicit name is blank or the fallback project is implausible.
    """
    values = os.environ if env is None else env
    if explicit_name is not None:
        name = explicit_name.strip()
        if not name:
            raise ValueError("synapse mcp: --name must not be blank")
        ambient = values.get("SYN_IDENTITY", "").strip()
        note = ""
        if ambient and ambient != name:
            note = f"explicit --name overrides ambient SYN_IDENTITY={ambient}"
        return McpIdentityResolution(
            name=name,
            project=name.split("/", 1)[0],
            source="flag",
            note=note,
        )

    # Lazy import avoids a parser-registration cycle: ergonomics imports the
    # top-level CLI, which imports this MCP command module during parser assembly.
    from synapse_channel import ergonomics

    resolved = ergonomics.resolve_identity(
        env=values,
        cwd_basename=(ergonomics._cwd_basename() if cwd_basename is None else cwd_basename),
        home_basename=(
            Path(values.get("HOME", str(Path.home()))).name
            if home_basename is None
            else home_basename
        ),
    )
    if not resolved.plausible:
        raise ValueError(
            "synapse mcp: cannot derive a safe project identity here; pass "
            "--name <project>/<client> or set an agreeing SYN_PROJECT and SYN_IDENTITY"
        )
    name = resolved.identity if resolved.identity != resolved.project else f"{resolved.project}/mcp"
    note = ""
    if resolved.ignored_ambient:
        note = (
            f"ignored ambient SYN_IDENTITY={resolved.ignored_ambient} because no agreeing "
            f"SYN_PROJECT opted into it; using {name}"
        )
    return McpIdentityResolution(
        name=name,
        project=resolved.project,
        source=resolved.source,
        note=note,
    )
