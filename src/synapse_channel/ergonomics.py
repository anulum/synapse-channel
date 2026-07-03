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
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel import ack as ack_command
from synapse_channel import cli
from synapse_channel import commit as commit_command
from synapse_channel import locks as locks_command
from synapse_channel import reap as reap_command

CliDispatcher = Callable[[list[str] | None], int]
"""Callable surface used to dispatch into the package CLI."""

ReapRunner = Callable[["Identity", Sequence[str]], int]
"""Callable surface used to dispatch the identity-scoped ``syn reap`` command."""

LocksRunner = Callable[["Identity", Sequence[str]], int]
"""Callable surface used to dispatch the identity-scoped ``syn locks`` command."""

AckRunner = Callable[["Identity", Sequence[str]], int]
"""Callable surface used to dispatch the identity-scoped ``syn ack`` command."""

CommitRunner = Callable[["Identity", Sequence[str]], int]
"""Callable surface used to dispatch the lease-guarded ``syn commit`` command."""

DEFAULT_AGENT_TYPE = "claude"
"""The agent ``type`` used to build a multi-agent identity when ``--id`` is given."""

IMPLAUSIBLE_PROJECTS = frozenset(
    {"", "tmp", "root", "home", "usr", "var", "bin", "etc", "opt", "mnt", "media", "dev", "srv"}
)
"""Resolved project names that almost certainly mean an identity was derived by
accident (a system directory or nothing) rather than a real repository."""


def syn_home(env: Mapping[str, str]) -> Path:
    """Return the coordination home (``$SYN_HOME`` or ``~/synapse``)."""
    override = env.get("SYN_HOME", "").strip()
    return Path(override) if override else Path(env.get("HOME", str(Path.home()))) / "synapse"


_syn_home = syn_home
"""Original private name, kept so existing callers keep working."""


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
    elif syn_identity and not (project and project.strip()):
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
    identity: Identity,
    target: str,
    message: str,
    *,
    as_project: bool = False,
    extra: Sequence[str] = (),
) -> list[str]:
    """Build the ``synapse send`` argv for ``syn say``.

    Multi-agent sessions send as their full identity by default so directed
    replies can return to the exact terminal. ``as_project`` preserves the old
    project-level sender when a deliberate shared project voice is wanted.
    """
    sender = identity.project if as_project else identity.identity
    return ["send", "--name", sender, "--target", target, *extra, message]


def _format_seconds(value: float) -> str:
    """Format a CLI seconds value without a redundant trailing ``.0``."""
    return str(int(value)) if value.is_integer() else str(value)


def ask_argv(
    identity: Identity,
    target: str,
    message: str,
    *,
    wait_seconds: float = 30.0,
    require_recipient: bool = True,
    extra: Sequence[str] = (),
) -> list[str]:
    """Build the ``synapse send`` argv for ``syn ask``.

    ``syn ask`` is a question-oriented wrapper: it sends as the resolved identity,
    waits for replies, and by default asks the hub to confirm that at least one
    online recipient matched the target.
    """
    argv = [
        "send",
        "--name",
        identity.identity,
        "--target",
        target,
        "--wait-seconds",
        _format_seconds(float(wait_seconds)),
    ]
    if require_recipient:
        argv.append("--require-recipient")
    argv.extend(extra)
    argv.append(message)
    return argv


def inbox_argv(identity: Identity, *, feed: str, cursor: str) -> list[str]:
    """Build the ``synapse relay`` argv for ``syn inbox`` (project-scoped, cursored delta)."""
    return ["relay", feed, "--project", identity.project, "--cursor", cursor]


def split_as_names(rest: Sequence[str], env: Mapping[str, str]) -> list[str]:
    """Extract the ``--as NAME`` identities an inbox run should also drain.

    One reader often answers to several names — a terminal identity, an
    agent identity, and a role name like ``<project>/coordinator``. A
    message addressed to a name whose inbox nobody reads lands durably in
    the feed and wakes no one; ``--as`` lets a single ``syn inbox`` drain
    those extra names too. Explicit ``--as`` flags win; without any, the
    comma-separated ``$SYN_ALIASES`` environment variable supplies the
    standing set. Blank entries are dropped.
    """
    names: list[str] = []
    tokens = list(rest)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--as" and index + 1 < len(tokens):
            names.append(tokens[index + 1].strip())
            index += 2
            continue
        if token.startswith("--as="):
            names.append(token[len("--as=") :].strip())
        index += 1
    if not names:
        names = [item.strip() for item in env.get("SYN_ALIASES", "").split(",")]
    return [name for name in names if name]


def aliased_inbox_argv(name: str, *, feed: str, home: Path) -> list[str]:
    """Build the relay argv draining one extra ``--as`` identity.

    A bare project name gets the project-stable inbox (matching the name,
    ``project/...`` sub-addresses, and broadcasts); a full ``a/b`` identity
    gets the exact-name inbox. Each name advances its OWN cursor file, so
    draining a role name never consumes another reader's delta.
    """
    cursor = str(home / f"{name.replace('/', '__')}.cursor")
    if "/" in name:
        return ["relay", feed, "--for", name, "--cursor", cursor]
    return ["relay", feed, "--project", name, "--cursor", cursor]


def board_argv(identity: Identity, *, extra: Sequence[str] = ()) -> list[str]:
    """Build the ``synapse board`` argv for ``syn board``."""
    return ["board", "--name", identity.project, *extra]


def who_argv(identity: Identity, *, extra: Sequence[str] = ()) -> list[str]:
    """Build the ``synapse who`` argv for ``syn who``.

    The resolved identity is passed as ``--name``. With ``--me``, the package CLI
    uses a separate temporary query connection and inspects this identity plus
    its ``-rx`` waiter.
    """
    return ["who", "--name", identity.identity, *extra]


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
            # Fixed-argv git metadata probe; no shell or user command text.
            return subprocess.run(  # nosec
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


def _run_ack(identity: Identity, rest: Sequence[str]) -> int:
    """Dispatch ``syn ack`` to the acknowledgement command module."""
    return ack_command.main(identity, rest)


VERBS = (
    "name",
    "arm",
    "say",
    "ask",
    "inbox",
    "board",
    "who",
    "reap",
    "locks",
    "ack",
    "commit",
)
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
    parser.add_argument(
        "verb",
        nargs="?",
        choices=VERBS,
        help="name | arm | say | ask | inbox | board | who | reap | locks | ack | commit.",
    )
    parser.add_argument(
        "rest", nargs=argparse.REMAINDER, help="Arguments passed through to the package command."
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    cwd_basename: str | None = None,
    dispatcher: CliDispatcher = cli.main,
    reap_runner: ReapRunner = reap_command.main,
    locks_runner: LocksRunner = locks_command.main,
    ack_runner: AckRunner = _run_ack,
    commit_runner: CommitRunner = commit_command.main,
) -> int:
    """Resolve identity and dispatch one ``syn`` verb to the package CLI.

    Parameters
    ----------
    argv : Sequence[str] or None, optional
        Argument vector; defaults to ``sys.argv[1:]``.
    env : Mapping[str, str] or None, optional
        Environment mapping used for identity and relay-home resolution.
    cwd_basename : str or None, optional
        Git toplevel/CWD basename. When omitted, resolved from the process.
    dispatcher : callable, optional
        Package CLI entry point to call for verbs that delegate to ``synapse``.
    reap_runner : callable, optional
        Identity-scoped waiter cleanup entry point for ``syn reap``.
    locks_runner : callable, optional
        Identity-scoped lease listing entry point for ``syn locks``.
    ack_runner : callable, optional
        Identity-scoped task acknowledgement entry point for ``syn ack``.
    commit_runner : callable, optional
        Identity-scoped, lease-guarded git commit entry point for ``syn commit``.

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

    env = os.environ if env is None else env
    identity = resolve_identity(
        project=args.project,
        agent_id=args.agent_id,
        agent_type=args.agent_type,
        env=env,
        cwd_basename=_cwd_basename() if cwd_basename is None else cwd_basename,
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
        extra = [item for item in rest if item != "--broadcasts"]
        return dispatcher(arm_argv(identity, directed_only=directed_only, extra=extra))
    if args.verb == "say":
        if len(rest) < 2:
            print("syn: usage: syn say [--as-project] <target> <message>", file=sys.stderr)
            return 2
        as_project = False
        if "--as-project" in rest:
            rest.remove("--as-project")
            as_project = True
        if len(rest) < 2:
            print("syn: usage: syn say [--as-project] <target> <message>", file=sys.stderr)
            return 2
        target, message, *extra = rest
        return dispatcher(say_argv(identity, target, message, as_project=as_project, extra=extra))
    if args.verb == "ask":
        wait_seconds = 30.0
        require_recipient = True
        ask_extra: list[str] = []
        while rest and rest[0].startswith("--"):
            option = rest.pop(0)
            if option == "--wait":
                if not rest:
                    print(
                        "syn: usage: syn ask [--wait SECONDS] <target> <message>", file=sys.stderr
                    )
                    return 2
                try:
                    wait_seconds = float(rest.pop(0))
                except ValueError:
                    print("syn: --wait needs a number of seconds", file=sys.stderr)
                    return 2
            elif option == "--no-require-recipient":
                require_recipient = False
            else:
                ask_extra.append(option)
                if rest and not rest[0].startswith("--"):
                    ask_extra.append(rest.pop(0))
        if len(rest) < 2:
            print("syn: usage: syn ask [--wait SECONDS] <target> <message>", file=sys.stderr)
            return 2
        target, message, *trailing = rest
        ask_extra.extend(trailing)
        return dispatcher(
            ask_argv(
                identity,
                target,
                message,
                wait_seconds=wait_seconds,
                require_recipient=require_recipient,
                extra=ask_extra,
            )
        )
    if args.verb == "inbox":
        home = _syn_home(env)
        feed = str(home / "feed.ndjson")
        cursor = str(home / f"{identity.project}.cursor")
        code = dispatcher(inbox_argv(identity, feed=feed, cursor=cursor))
        for name in split_as_names(rest, env):
            print(f"--- inbox as {name} ---")
            code = max(code, dispatcher(aliased_inbox_argv(name, feed=feed, home=home)))
        return code
    if args.verb == "who":
        return dispatcher(who_argv(identity, extra=rest))
    if args.verb == "reap":
        return reap_runner(identity, rest)
    if args.verb == "locks":
        return locks_runner(identity, rest)
    if args.verb == "ack":
        return ack_runner(identity, rest)
    if args.verb == "commit":
        return commit_runner(identity, rest)
    # args.verb == "board"
    return dispatcher(board_argv(identity, extra=rest))


def alias_name(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-name`` console alias."""
    return dispatcher(["name", *(sys.argv[1:] if argv is None else argv)])


def alias_arm(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-wait`` console alias."""
    return dispatcher(["arm", *(sys.argv[1:] if argv is None else argv)])


def alias_say(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-say`` console alias."""
    return dispatcher(["say", *(sys.argv[1:] if argv is None else argv)])


def alias_ask(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-ask`` console alias."""
    return dispatcher(["ask", *(sys.argv[1:] if argv is None else argv)])


def alias_inbox(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-inbox`` console alias."""
    return dispatcher(["inbox", *(sys.argv[1:] if argv is None else argv)])


def alias_board(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-board`` console alias."""
    return dispatcher(["board", *(sys.argv[1:] if argv is None else argv)])


def alias_reap(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-reap`` console alias."""
    return dispatcher(["reap", *(sys.argv[1:] if argv is None else argv)])


def alias_locks(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-locks`` console alias."""
    return dispatcher(["locks", *(sys.argv[1:] if argv is None else argv)])


def alias_ack(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-ack`` console alias."""
    return dispatcher(["ack", *(sys.argv[1:] if argv is None else argv)])


def alias_commit(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: Callable[[list[str]], int] = main,
) -> int:
    """Entry point for the ``syn-commit`` console alias."""
    return dispatcher(["commit", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
