# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact-identity ergonomic inbox dispatch
"""Build and run privacy-safe ``syn inbox`` relay reads."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class InboxIdentity(Protocol):
    """Identity fields required by the inbox dispatcher."""

    @property
    def project(self) -> str:
        """Return the project segment of the identity."""
        ...

    @property
    def identity(self) -> str:
        """Return the full exact identity."""
        ...


CliDispatcher = Callable[[list[str]], int]


@dataclass(frozen=True)
class InboxOptions:
    """Validated primary scope and additional inbox aliases."""

    exact_name: str | None
    project_wide: bool
    aliases: tuple[str, ...]
    source: str = "feed"
    uri: str | None = None
    token_file: str | None = None
    limit: int = 50
    since: int | None = None


def inbox_argv(
    identity: InboxIdentity,
    *,
    feed: str,
    cursor: str,
    project_wide: bool = False,
) -> list[str]:
    """Build an exact-identity relay read, or an explicit project-wide read."""
    scope = ["--project", identity.project] if project_wide else ["--for", identity.identity]
    return ["relay", feed, *scope, "--cursor", cursor]


def exact_inbox_argv(name: str, *, feed: str, cursor: str) -> list[str]:
    """Build one exact-name relay read with an independent cursor."""
    return ["relay", feed, "--for", name, "--cursor", cursor]


def split_as_names(rest: Sequence[str], env: Mapping[str, str]) -> list[str]:
    """Extract explicit ``--as NAME`` values or the standing alias set."""
    names: list[str] = []
    tokens = list(rest)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--as" and index + 1 < len(tokens):  # nosec B105 # flag, not a credential
            names.append(tokens[index + 1].strip())
            index += 2
            continue
        if token.startswith("--as="):
            names.append(token[len("--as=") :].strip())
        index += 1
    if not names:
        names = [item.strip() for item in env.get("SYN_ALIASES", "").split(",")]
    return [name for name in names if name]


def _store_source_option(tokens: Sequence[str], index: int, source_options: dict[str, str]) -> int:
    """Record one source flag and return the index of the following token."""
    flag, separator, inline = tokens[index].partition("=")
    if flag in source_options:
        raise ValueError(f"{flag} may be supplied only once")
    if separator:
        value = inline.strip()
        next_index = index + 1
    else:
        if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
            raise ValueError(f"{flag} requires a nonblank value")
        value = tokens[index + 1].strip()
        next_index = index + 2
    if not value:
        raise ValueError(f"{flag} requires a nonblank value")
    source_options[flag] = value
    return next_index


def _store_separate_identity(
    tokens: Sequence[str], index: int, aliases: list[str], exact_name: str | None
) -> tuple[int, str | None]:
    """Record ``--as`` or ``--name`` and return the next index and exact name."""
    token = tokens[index]
    if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
        raise ValueError(f"{token} requires a nonblank identity")
    value = tokens[index + 1].strip()
    if not value:
        raise ValueError(f"{token} requires a nonblank identity")
    if token == "--as":  # nosec B105 # a command-line flag, not a credential
        aliases.append(value)
        return index + 2, exact_name
    if exact_name is not None:
        raise ValueError("--name may be supplied only once")
    return index + 2, value


def _store_inline_identity(token: str, aliases: list[str], exact_name: str | None) -> str | None:
    """Record ``--as=`` or ``--name=`` and return the exact name."""
    option, _, raw_value = token.partition("=")
    value = raw_value.strip()
    if not value:
        raise ValueError(f"{option} requires a nonblank identity")
    if option == "--as":
        aliases.append(value)
        return exact_name
    if exact_name is not None:
        raise ValueError("--name may be supplied only once")
    return value


def _finish_inbox_options(
    *,
    aliases: list[str],
    exact_name: str | None,
    project_wide: bool,
    source_options: dict[str, str],
    env: Mapping[str, str],
) -> InboxOptions:
    """Validate the collected inbox flags and build the options."""
    if exact_name is not None and project_wide:
        raise ValueError("--name and --project-wide are mutually exclusive")
    if not aliases:
        aliases = [item.strip() for item in env.get("SYN_ALIASES", "").split(",")]
    source = source_options.get("--source", env.get("SYN_INBOX_SOURCE", "feed"))
    if source not in {"feed", "hub"}:
        raise ValueError("--source must be feed or hub")
    try:
        limit = int(source_options.get("--limit", "50"))
    except ValueError:
        raise ValueError("--limit must be an integer from 1 to 100") from None
    if not 1 <= limit <= 100:
        raise ValueError("--limit must be an integer from 1 to 100")
    since = _since_option(source_options.get("--since"))
    if source == "hub" and project_wide:
        raise ValueError("hub inbox requires an exact identity; --project-wide is not supported")
    if source == "feed" and any(flag != "--source" for flag in source_options):
        raise ValueError("--uri, --token-file, --limit and --since require --source hub")
    return InboxOptions(
        exact_name=exact_name,
        project_wide=project_wide,
        aliases=tuple(dict.fromkeys(name for name in aliases if name)),
        source=source,
        uri=source_options.get("--uri"),
        token_file=source_options.get("--token-file"),
        limit=limit,
        since=since,
    )


def _since_option(raw: str | None) -> int | None:
    """Return an explicit hub sequence, or ``None`` when the flag is absent."""
    if raw is None:
        return None
    try:
        since = int(raw)
    except ValueError:
        raise ValueError("--since must be a nonnegative integer") from None
    if since < 0 or since > 9223372036854775807:
        raise ValueError("--since must be a nonnegative integer")
    return since


def parse_inbox_options(rest: Sequence[str], env: Mapping[str, str]) -> InboxOptions:
    """Parse inbox-only flags and reject anything that would be silently ignored."""
    tokens = list(rest)
    aliases: list[str] = []
    exact_name: str | None = None
    project_wide = False
    source_options: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, _, _inline = token.partition("=")
        if flag in {"--source", "--uri", "--token-file", "--limit", "--since"}:
            index = _store_source_option(tokens, index, source_options)
            continue
        if token in {"--as", "--name"}:
            index, exact_name = _store_separate_identity(tokens, index, aliases, exact_name)
            continue
        if token.startswith("--as=") or token.startswith("--name="):
            exact_name = _store_inline_identity(token, aliases, exact_name)
            index += 1
            continue
        if token == "--project-wide":  # nosec B105 # a command-line flag, not a credential
            project_wide = True
            index += 1
            continue
        raise ValueError(f"unsupported inbox option: {token}")
    return _finish_inbox_options(
        aliases=aliases,
        exact_name=exact_name,
        project_wide=project_wide,
        source_options=source_options,
        env=env,
    )


def aliased_inbox_argv(name: str, *, feed: str, home: Path) -> list[str]:
    """Build an extra alias read; a bare alias explicitly means a project."""
    cursor = str(_cursor_path(home, name))
    if "/" in name:
        return exact_inbox_argv(name, feed=feed, cursor=cursor)
    return ["relay", feed, "--project", name, "--cursor", cursor]


def run_inbox(
    identity: InboxIdentity,
    rest: Sequence[str],
    env: Mapping[str, str],
    dispatcher: CliDispatcher,
    *,
    home: Path,
) -> int:
    """Read the selected source for an exact primary inbox and explicit aliases.

    ``--source hub`` (or ``SYN_INBOX_SOURCE=hub``) reads authenticated durable
    pages from ``--uri``/``SYNAPSE_URI`` with source-isolated local cursors.
    The default feed source retains the established offline local behaviour.

    Parameters
    ----------
    identity : InboxIdentity
        Resolved project and exact primary identity.
    rest : Sequence[str]
        Inbox flags selecting the source and explicit aliases.
    env : Mapping[str, str]
        Coordination environment, including endpoint and credential-file paths.
    dispatcher : Callable
        Existing package dispatcher used by the local feed source.
    home : Path
        Local coordination home containing source-specific cursors.

    Returns
    -------
    int
        Zero on successful reads, one on unavailable source, two on invalid flags.
    """
    try:
        options = parse_inbox_options(rest, env)
    except ValueError as exc:
        print(f"syn inbox: {exc}", file=sys.stderr)
        return 2

    if options.source == "hub":
        from synapse_channel.core.secret_files import SecretFileError, read_secret_file
        from synapse_channel.hub_inbox import (
            INBOX_READY_HANG_GUARD_SECONDS,
            read_hub_inbox,
        )

        uri = options.uri or env.get("SYNAPSE_URI", "").strip() or "ws://127.0.0.1:8876"
        token_file = options.token_file or env.get("SYNAPSE_TOKEN_FILE", "").strip()
        try:
            token = read_secret_file(token_file, flag="--token-file") if token_file else None
        except SecretFileError:
            print("syn inbox: cannot read hub credential file", file=sys.stderr)
            return 1
        code = 0
        for name in dict.fromkeys((options.exact_name or identity.identity, *options.aliases)):
            code = max(
                code,
                asyncio.run(
                    read_hub_inbox(
                        uri=uri,
                        identity=name,
                        home=home,
                        token=token,
                        limit=options.limit,
                        timeout=INBOX_READY_HANG_GUARD_SECONDS,
                        since=options.since,
                    )
                ),
            )
        return code

    feed = str(home / "feed.ndjson")
    primary_name = options.exact_name or identity.identity
    cursor_name = identity.project if options.project_wide else primary_name
    cursor = str(_cursor_path(home, cursor_name))
    if options.exact_name is not None:
        primary_argv = exact_inbox_argv(primary_name, feed=feed, cursor=cursor)
    else:
        primary_argv = inbox_argv(
            identity,
            feed=feed,
            cursor=cursor,
            project_wide=options.project_wide,
        )
    code = dispatcher(primary_argv)
    for name in options.aliases:
        print(f"--- inbox as {name} ---")
        code = max(code, dispatcher(aliased_inbox_argv(name, feed=feed, home=home)))
    return code


def _cursor_path(home: Path, name: str) -> Path:
    """Return the established flat cursor path for one identity or project."""
    return home / f"{name.replace('/', '__')}.cursor"
