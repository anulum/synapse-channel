# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — connect-token set management CLI
"""Manage a hub's connect-token set: ``synapse token-set new/retire/list``.

These are local file operations; they do not connect to the hub. A hub started
with ``--token-set PATH`` picks up each change on its next authentication.

``new`` generates a 256-bit secret, stores only its digest, and prints the
secret once (or writes it to an owner-only ``--secret-out`` file). ``retire``
removes a token. ``list`` shows identifiers, bound agents and validity, never
a secret or a digest. A change whose result would not validate writes nothing.
``status`` shows whether each running hub that follows the set has it in force.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from synapse_channel.core.private_dir import PrivateDirError
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.core.token_set import TokenSetError, load_token_set
from synapse_channel.token_set_admin import (
    add_token,
    check_operator_secret,
    format_instant,
    generate_secret,
    parse_instant,
    retire_token,
    token_status,
)
from synapse_channel.token_set_hub_status import read_hub_states

DEFAULT_VALID_DAYS = 90
"""Validity of a new token when no end is given."""

_DAY = 86_400
_REFUSALS = (TokenSetError, SecretFileError, PrivateDirError, OSError)


def _agents(text: str | None) -> tuple[str, ...]:
    """Split a comma list of agent names; blanks are kept so validation names them."""
    if text is None:
        return ()
    return tuple(name.strip() for name in text.split(","))


def _window(args: argparse.Namespace, now: int) -> tuple[float | None, float | None]:
    """Return the validity window the arguments ask for."""
    not_before = parse_instant(args.not_before) if args.not_before else None
    if args.no_expiry:
        return not_before, None
    if args.not_after:
        return not_before, parse_instant(args.not_after)
    start = int(not_before) if not_before is not None else now
    return not_before, float(start + args.days * _DAY)


def _write_secret_out(path: Path, secret: str) -> None:
    """Create ``path`` owner-only holding ``secret``; an existing path is refused.

    A file this call created is removed again if writing it fails, so no
    partial secret is left behind. A file that existed before is never touched.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secret + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        apply_owner_only_file(path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _cmd_new(args: argparse.Namespace, *, clock: Callable[[], float] = time.time) -> int:
    """Add a token to the set, generating its secret unless ``--secret-file`` names one."""
    path = Path(args.path).expanduser()
    secret_out = Path(args.secret_out).expanduser() if args.secret_out else None
    try:
        if args.secret_file:
            secret = read_secret_file(args.secret_file, flag="--secret-file")
            check_operator_secret(secret)
        else:
            secret = generate_secret()
        not_before, not_after = _window(args, int(clock()))
        if secret_out is not None:
            _write_secret_out(secret_out, secret)
        try:
            entry = add_token(
                path,
                token_id=args.id,
                secret=secret,
                agents=_agents(args.agents),
                not_before=not_before,
                not_after=not_after,
            )
        except BaseException:
            if secret_out is not None:
                secret_out.unlink(missing_ok=True)
            raise
    except _REFUSALS as exc:
        print(f"synapse token-set new: {exc}", file=sys.stderr)
        return 1
    start = _instant_or_none(entry.not_before) or "now"
    end = _instant_or_none(entry.not_after) or "no expiry"
    print(
        f"added token {entry.token_id} to {path}; valid {start} to {end}; "
        f"{token_status(entry, clock())} now",
        file=sys.stderr,
    )
    if secret_out is not None:
        print(f"secret written owner-only to {secret_out}", file=sys.stderr)
    elif not args.secret_file:
        print(secret, flush=True)
    return 0


def _cmd_retire(args: argparse.Namespace) -> int:
    """Remove a token; the hub refuses it from its next authentication."""
    path = Path(args.path).expanduser()
    try:
        remaining = retire_token(path, args.id)
    except _REFUSALS as exc:
        print(f"synapse token-set retire: {exc}", file=sys.stderr)
        return 1
    print(f"retired token {args.id}; {remaining} token(s) remain in {path}")
    if remaining == 0:
        print("the set is now empty and admits no connection", file=sys.stderr)
    return 0


def _cmd_list(args: argparse.Namespace, *, clock: Callable[[], float] = time.time) -> int:
    """Print identifiers, agents and validity; never a secret or a digest."""
    path = Path(args.path).expanduser()
    try:
        entries = load_token_set(path)
    except (TokenSetError, SecretFileError) as exc:
        print(f"synapse token-set list: {exc}", file=sys.stderr)
        return 1
    now = clock()
    rows = [
        {
            "id": entry.token_id,
            "agents": sorted(entry.agents),
            "not_before": _instant_or_none(entry.not_before),
            "not_after": _instant_or_none(entry.not_after),
            "status": token_status(entry, now),
        }
        for entry in entries
    ]
    if args.json:
        print(json.dumps({"path": str(path), "tokens": rows}, indent=2))
        return 0
    if not entries:
        print(f"{path}: no tokens; the set admits no connection")
    for entry in entries:
        print(
            f"{entry.token_id}  {token_status(entry, now)}"
            f"  from {_instant_or_none(entry.not_before) or 'any time'}"
            f"  until {_instant_or_none(entry.not_after) or 'no expiry'}"
            f"  for {','.join(sorted(entry.agents)) or 'any agent'}"
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Show each hub's state for the set; exit 0 only when every running hub applied it."""
    path = Path(args.path).expanduser()
    try:
        states = read_hub_states(path)
    except (OSError, PrivateDirError) as exc:
        print(f"synapse token-set status: {exc}", file=sys.stderr)
        return 1
    running = [state for state in states if state.running]
    healthy = bool(running) and all(state.state == "applied" for state in running)
    if args.json:
        rows = [
            {
                "file": state.name,
                "running": state.running,
                "state": state.state,
                "since": state.since,
                "started": state.started,
                "pid": state.pid,
                "reason_code": state.reason_code,
                "signature": state.signature,
            }
            for state in states
        ]
        print(json.dumps({"path": str(path), "healthy": healthy, "hubs": rows}, indent=2))
        return 0 if healthy else 1
    if not running:
        print(f"{path}: no running hub reports this set; its state is unknown")
    for state in states:
        detail = f" ({state.reason_code})" if state.reason_code else ""
        since = f" since {state.since}" if state.since else ""
        print(f"{state.name}: {state.state}{detail}{since}")
    return 0 if healthy else 1


def _instant_or_none(moment: float | None) -> str | None:
    """Format an optional instant."""
    return None if moment is None else format_instant(moment)


def _positive_days(text: str) -> int:
    """Parse ``--days`` as a whole number of at least one."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("--days must be at least 1")
    return value


def _add_new_parser(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``token-set new``."""
    new = nested.add_parser("new", help="Add a token and print its secret once.")
    new.add_argument("path", help="Token-set file; created owner-only when absent.")
    new.add_argument("--id", required=True, help="Unique identifier, [A-Za-z0-9._-]{1,64}.")
    new.add_argument("--agents", help="Comma list of agent names the token admits (default any).")
    new.add_argument("--not-before", help="Start of validity, YYYY-MM-DDTHH:MM:SSZ (default now).")
    end = new.add_mutually_exclusive_group()
    end.add_argument(
        "--days",
        type=_positive_days,
        default=DEFAULT_VALID_DAYS,
        help=f"Days of validity from the start (default {DEFAULT_VALID_DAYS}).",
    )
    end.add_argument("--not-after", help="End of validity, YYYY-MM-DDTHH:MM:SSZ.")
    end.add_argument("--no-expiry", action="store_true", help="Leave the token without an end.")
    source = new.add_mutually_exclusive_group()
    source.add_argument(
        "--secret-file",
        help="Owner-only file holding a secret to use instead of a generated one "
        "(refused below an estimated 128 bits).",
    )
    source.add_argument(
        "--secret-out",
        help="Write the generated secret to this new owner-only file instead of stdout.",
    )
    new.set_defaults(func=_cmd_new)


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``token-set`` subparser group."""
    token_set = subparsers.add_parser(
        "token-set", help="Manage a hub's connect-token set (new/retire/list)."
    )
    nested = token_set.add_subparsers(dest="token_set_command", required=True)
    _add_new_parser(nested)

    retire = nested.add_parser("retire", help="Remove a token; new connections with it fail.")
    retire.add_argument("path", help="Token-set file.")
    retire.add_argument("--id", required=True, help="Identifier of the token to retire.")
    retire.set_defaults(func=_cmd_retire)

    status = nested.add_parser(
        "status", help="Show whether each running hub has the set on disk in force."
    )
    status.add_argument("path", help="Token-set file.")
    status.add_argument("--json", action="store_true", help="Print a JSON document.")
    status.set_defaults(func=_cmd_status)

    listing = nested.add_parser("list", help="Show identifiers, agents and validity.")
    listing.add_argument("path", help="Token-set file.")
    listing.add_argument("--json", action="store_true", help="Print a JSON document.")
    listing.set_defaults(func=_cmd_list)
