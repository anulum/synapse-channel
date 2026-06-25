# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the `syn` agent-ergonomic layer: identity-correct coordination
"""The ``syn`` command — agent-ergonomic coordination, correct by construction.

The package CLI (:mod:`synapse_channel.cli`) is the full, tested surface, but agents
rarely drive it directly: they use a handful of shorthand commands for the loop
they run every session — arm a waiter, send a message, read the inbox, glance at
the board. Hand-deployed shell wrappers used to fill that role, and they were where
the operational footguns lived: a project identity derived from the current working
directory (which the harness resets between tool calls, so a command run from the
wrong directory silently coordinated as the wrong project), a doubled ``--name``, a
waiter whose name collided with the sender it waits for.

This module makes that layer a versioned, tested part of the package, so a single
upgrade distributes the fixes. The one thing it gets right that the wrappers did
not is **identity**: it is resolved from an explicit flag or an environment
variable first and the working directory only as a last resort, and an identity
that looks accidental (the home directory, a system path, nothing at all) is
flagged loudly rather than used in silence. Everything else is a thin, correct
assembly of the arguments the package CLI already implements — the waiter already
takes over its own name and suffixes ``-rx`` to stay distinct from the sender, so
``syn arm`` uses the persistent package arm command instead of a one-shot wait.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel import cli

DEFAULT_AGENT_TYPE = "claude"
"""The agent ``type`` used to build a multi-agent identity when ``--id`` is given."""

IMPLAUSIBLE_PROJECTS = frozenset(
    {"", "tmp", "root", "home", "usr", "var", "bin", "etc", "opt", "mnt", "media", "dev", "srv"}
)
"""Resolved project names that almost certainly mean an identity was derived by
accident (a system directory or nothing) rather than a real repository."""


def _syn_home(env: Mapping[str, str]) -> Path:
    """Return the coordination home (``$SYN_HOME`` or ``~/synapse``)."""
    override = env.get("SYN_HOME", "").strip()
    return Path(override) if override else Path(env.get("HOME", str(Path.home()))) / "synapse"


@dataclass(frozen=True)
class Identity:
    """A resolved coordination identity and how it was arrived at.

    Attributes
    ----------
    project : str
        The repository/project name — the sender identity and the ``--for`` target
        a waiter wakes on.
    identity : str
        The full identity: the bare ``project``, or ``project/<type>-<id>`` when a
        multi-agent ``--id`` was given.
    source : str
        Where the project came from: ``"flag"``, ``"env"``, or ``"cwd"``.
    plausible : bool
        ``False`` when the resolved project looks accidental (the home directory, a
        system path, or empty), so the caller can warn before coordinating as it.
    """

    project: str
    identity: str
    source: str
    plausible: bool

    @property
    def waiter_name(self) -> str:
        """The distinct receiver name for a waiter (``<identity>-rx``).

        Kept distinct from the sender identity so a waiter holding the connection
        and the agent's own ``send`` for the same project never clash.
        """
        return f"{self.identity}-rx"


def is_plausible_project(project: str, *, home_basename: str) -> bool:
    """Return whether a resolved project name looks like a real repository.

    Parameters
    ----------
    project : str
        The resolved project name.
    home_basename : str
        The basename of the home directory; a project equal to it almost always
        means the identity was derived from ``$HOME`` by accident.
    """
    name = project.strip()
    return bool(name) and name not in IMPLAUSIBLE_PROJECTS and name != home_basename.strip()


def resolve_identity(
    *,
    project: str | None = None,
    agent_id: str | None = None,
    agent_type: str = DEFAULT_AGENT_TYPE,
    env: Mapping[str, str] | None = None,
    cwd_basename: str = "",
    home_basename: str = "",
) -> Identity:
    """Resolve the coordination identity, preferring explicit and env over the CWD.

    Precedence for the project, first match wins: an explicit ``project`` flag, the
    ``$SYN_PROJECT`` env var, the first segment of ``$SYN_IDENTITY``, then the
    working-directory basename. The full identity is ``project/<type>-<id>`` when
    ``agent_id`` is given, the verbatim ``$SYN_IDENTITY`` when it supplied the
    project and no flag overrode it, else the bare project.

    Parameters
    ----------
    project : str or None, optional
        Explicit project override (the ``--project`` flag).
    agent_id : str or None, optional
        Short id for a multi-agent identity (``--id``); builds ``project/<type>-<id>``.
    agent_type : str, optional
        The agent type used in a multi-agent identity. Defaults to ``"claude"``.
    env : Mapping[str, str] or None, optional
        Environment to read ``$SYN_PROJECT``/``$SYN_IDENTITY`` from; the process
        environment when ``None``.
    cwd_basename : str, optional
        The working-directory (or git-toplevel) basename, the last-resort project.
    home_basename : str, optional
        The home-directory basename, used to flag an accidental identity.

    Returns
    -------
    Identity
        The resolved identity, its source, and whether it looks plausible.
    """
    env = os.environ if env is None else env
    syn_identity = env.get("SYN_IDENTITY", "").strip()
    syn_project = env.get("SYN_PROJECT", "").strip()

    if project and project.strip():
        proj, source = project.strip(), "flag"
    elif syn_project:
        proj, source = syn_project, "env"
    elif syn_identity:
        proj, source = syn_identity.split("/", 1)[0], "env"
    else:
        proj, source = cwd_basename.strip(), "cwd"

    if agent_id and agent_id.strip():
        identity = f"{proj}/{agent_type.strip()}-{agent_id.strip()}"
    elif syn_identity and not (project and project.strip()) and not syn_project:
        identity = syn_identity
    else:
        identity = proj

    return Identity(
        project=proj,
        identity=identity,
        source=source,
        plausible=is_plausible_project(proj, home_basename=home_basename),
    )


def arm_argv(
    identity: Identity, *, directed_only: bool = True, extra: Sequence[str] = ()
) -> list[str]:
    """Build the ``synapse arm`` argv for ``syn arm`` (persistent, distinct ``-rx``)."""
    argv = ["arm", "--name", identity.waiter_name, "--for", identity.project]
    if directed_only:
        argv.append("--directed-only")
    argv.extend(extra)
    return argv


def say_argv(
    identity: Identity, target: str, message: str, *, extra: Sequence[str] = ()
) -> list[str]:
    """Build the ``synapse send`` argv for ``syn say`` (sends as the bare project)."""
    return ["send", "--name", identity.project, "--target", target, *extra, message]


def inbox_argv(identity: Identity, *, feed: str, cursor: str) -> list[str]:
    """Build the ``synapse relay`` argv for ``syn inbox`` (project-scoped, cursored delta)."""
    return ["relay", feed, "--project", identity.project, "--cursor", cursor]


def board_argv(identity: Identity, *, extra: Sequence[str] = ()) -> list[str]:
    """Build the ``synapse board`` argv for ``syn board``."""
    return ["board", "--name", identity.project, *extra]


def name_lines(identity: Identity) -> list[str]:
    """Return the human-readable report ``syn name`` prints."""
    plausible = "yes" if identity.plausible else "NO — looks accidental, set $SYN_PROJECT"
    return [
        f"project:  {identity.project}",
        f"identity: {identity.identity}",
        f"waiter:   {identity.waiter_name}",
        f"source:   {identity.source}",
        f"plausible: {plausible}",
    ]


def _cwd_basename(*, runner: Callable[[Sequence[str]], str] | None = None) -> str:
    """Return the git-toplevel basename, falling back to the working directory."""
    if runner is None:

        def runner(cmd: Sequence[str]) -> str:
            return subprocess.run(
                list(cmd), capture_output=True, text=True, check=True
            ).stdout.strip()

    try:
        top = runner(["git", "rev-parse", "--show-toplevel"])
    except (subprocess.SubprocessError, OSError):
        top = ""
    return Path(top).name if top else Path.cwd().name


def _warn_if_implausible(identity: Identity) -> None:
    """Print a loud stderr warning when the identity looks accidental."""
    if not identity.plausible:
        print(
            f"syn: WARNING — coordinating as '{identity.project}' (source: {identity.source}); "
            "this looks accidental. Set $SYN_PROJECT or pass --project.",
            file=sys.stderr,
        )


VERBS = ("name", "arm", "say", "inbox", "board")
"""The ``syn`` verbs, each a thin identity-correct wrapper over a package command."""


def build_parser() -> argparse.ArgumentParser:
    """Build the ``syn`` parser: identity flags, a verb, and pass-through arguments.

    Identity flags (``--project``/``--id``/``--type``) must precede the verb;
    everything after the verb passes through to the underlying package command, so
    ``syn arm --max-wakes 1`` and ``syn say A,B "hello"`` work without re-declaring
    each package flag.
    """
    parser = argparse.ArgumentParser(
        prog="syn", description="Agent-ergonomic Synapse coordination."
    )
    parser.add_argument("--project", default=None, help="Project identity (over $SYN_PROJECT/CWD).")
    parser.add_argument(
        "--id", dest="agent_id", default=None, help="Short id for a <project>/<type>-<id> identity."
    )
    parser.add_argument(
        "--type",
        dest="agent_type",
        default=DEFAULT_AGENT_TYPE,
        help="Agent type for a multi-agent identity.",
    )
    parser.add_argument("verb", nargs="?", choices=VERBS, help="name | arm | say | inbox | board.")
    parser.add_argument(
        "rest", nargs=argparse.REMAINDER, help="Arguments passed through to the package command."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve identity and dispatch one ``syn`` verb to the package CLI.

    Parameters
    ----------
    argv : Sequence[str] or None, optional
        Argument vector; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        The dispatched command's exit code (``2`` when no verb or a malformed
        ``say`` was given).
    """
    args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    if not args.verb:
        build_parser().print_help()
        return 2

    env = os.environ
    identity = resolve_identity(
        project=args.project,
        agent_id=args.agent_id,
        agent_type=args.agent_type,
        env=env,
        cwd_basename=_cwd_basename(),
        home_basename=Path(env.get("HOME", str(Path.home()))).name,
    )
    rest: list[str] = list(args.rest)

    if args.verb == "name":
        for line in name_lines(identity):
            print(line)
        return 0

    _warn_if_implausible(identity)
    if args.verb == "arm":
        directed_only = "--broadcasts" not in rest
        extra = [token for token in rest if token != "--broadcasts"]
        return cli.main(arm_argv(identity, directed_only=directed_only, extra=extra))
    if args.verb == "say":
        if len(rest) < 2:
            print("syn: usage: syn say <target> <message>", file=sys.stderr)
            return 2
        target, message, *extra = rest
        return cli.main(say_argv(identity, target, message, extra=extra))
    if args.verb == "inbox":
        home = _syn_home(env)
        feed = str(home / "feed.ndjson")
        cursor = str(home / f"{identity.project}.cursor")
        return cli.main(inbox_argv(identity, feed=feed, cursor=cursor))
    # args.verb == "board"
    return cli.main(board_argv(identity, extra=rest))


def alias_name() -> int:
    """Entry point for the ``syn-name`` console alias."""
    return main(["name", *sys.argv[1:]])


def alias_arm() -> int:
    """Entry point for the ``syn-wait`` console alias."""
    return main(["arm", *sys.argv[1:]])


def alias_say() -> int:
    """Entry point for the ``syn-say`` console alias."""
    return main(["say", *sys.argv[1:]])


def alias_inbox() -> int:
    """Entry point for the ``syn-inbox`` console alias."""
    return main(["inbox", *sys.argv[1:]])


def alias_board() -> int:
    """Entry point for the ``syn-board`` console alias."""
    return main(["board", *sys.argv[1:]])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
