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

``new`` generates a 256-bit secret, stores only its digest, and writes the
secret to a new owner-only ``--secret-out`` file. It prints the secret on
stdout only when ``--print-secret`` asks for it, because terminal logs, shell
recordings and CI output keep what a command prints. ``retire``
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
from synapse_channel.core.token_set import TokenEntry, TokenSetError, load_token_set
from synapse_channel.token_set_admin import (
    add_token,
    check_operator_secret,
    format_instant,
    generate_secret,
    parse_instant,
    positive_days,
    retire_token,
    token_status,
)
from synapse_channel.token_set_hub_status import HubState, prune_gone_hubs, read_hub_states

DEFAULT_VALID_DAYS = 90
"""Validity of a new token when no end is given."""

_DAY = 86_400
_NO_DESTINATION_REFUSAL = (
    "synapse token-set new: name where the generated secret goes: --secret-out FILE "
    "writes it to a new owner-only file; --print-secret prints it once on stdout"
)
_REFUSALS = (TokenSetError, SecretFileError, PrivateDirError, OSError)


def _agents(text: str | None) -> tuple[str, ...]:
    """Split a comma list of agent names; blanks are kept so validation names them."""
    if text is None:
        return ()
    return tuple(name.strip() for name in text.split(","))


def _window(
    args: argparse.Namespace, now: int, default_days: int
) -> tuple[float | None, float | None]:
    """Return the validity window the arguments ask for."""
    not_before = parse_instant(args.not_before) if args.not_before else None
    if args.no_expiry:
        return not_before, None
    if args.not_after:
        return not_before, parse_instant(args.not_after)
    start = int(not_before) if not_before is not None else now
    days = default_days if args.days is None else args.days
    return not_before, float(start + days * _DAY)


def _running_hubs(path: Path) -> list[HubState]:
    """Return the running hubs that published a readable state for the set."""
    try:
        states = read_hub_states(path)
    except (OSError, PrivateDirError):
        return []
    return [state for state in states if state.running and state.state != "unreadable"]


def _default_days(hubs: list[HubState]) -> int:
    """Return the default validity: 90 days, or less when a running hub allows less."""
    limits = [hub.max_token_days for hub in hubs if hub.max_token_days is not None]
    return min([DEFAULT_VALID_DAYS, *limits])


def _policy_warnings(entry: TokenEntry, hubs: list[HubState], now: float) -> list[str]:
    """Name each running hub whose token policy will refuse ``entry``."""
    warnings = []
    for hub in hubs:
        days = hub.max_token_days
        if hub.require_expiry and entry.not_after is None:
            rule = "it requires an end of validity"
        elif days is not None and (entry.not_after is None or entry.not_after - now > days * _DAY):
            rule = f"it admits at most {days} days of validity"
        else:
            continue
        warnings.append(f"warning: hub {hub.name} will refuse token {entry.token_id}: {rule}")
    return warnings


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
    """Add a token to the set, generating its secret unless ``--secret-file`` names one.

    A generated secret needs a destination the operator chose: an owner-only
    ``--secret-out`` file, or stdout with ``--print-secret``. Without one the
    command refuses before it generates or writes anything.
    """
    path = Path(args.path).expanduser()
    secret_out = Path(args.secret_out).expanduser() if args.secret_out else None
    if not args.secret_file and secret_out is None and not args.print_secret:
        print(_NO_DESTINATION_REFUSAL, file=sys.stderr)
        return 2
    try:
        if args.secret_file:
            secret = read_secret_file(args.secret_file, flag="--secret-file")
            check_operator_secret(secret)
        else:
            secret = generate_secret()
        hubs = _running_hubs(path)
        not_before, not_after = _window(args, int(clock()), _default_days(hubs))
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
    for warning in _policy_warnings(entry, hubs, clock()):
        print(warning, file=sys.stderr)
    if secret_out is not None:
        print(f"secret written owner-only to {secret_out}", file=sys.stderr)
    elif args.print_secret:
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
        pruned = prune_gone_hubs(path) if args.prune else []
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
                "require_expiry": state.require_expiry,
                "max_token_days": state.max_token_days,
                "policy_refused": state.policy_refused,
            }
            for state in states
        ]
        document = {"path": str(path), "healthy": healthy, "hubs": rows, "pruned": pruned}
        print(json.dumps(document, indent=2))
        return 0 if healthy else 1
    for stem in pruned:
        print(f"{stem}: removed, its hub is gone")
    if not running:
        print(f"{path}: no running hub reports this set; its state is unknown")
    for state in states:
        detail = f" ({state.reason_code})" if state.reason_code else ""
        since = f" since {state.since}" if state.since else ""
        refused = (
            f"; {state.policy_refused} token(s) refused by its policy"
            if state.policy_refused
            else ""
        )
        print(f"{state.name}: {state.state}{detail}{since}{refused}")
    return 0 if healthy else 1


def _instant_or_none(moment: float | None) -> str | None:
    """Format an optional instant."""
    return None if moment is None else format_instant(moment)


def _add_new_parser(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``token-set new``."""
    new = nested.add_parser(
        "new",
        help="Add a token; its generated secret goes to a new owner-only --secret-out "
        "file, or to stdout with --print-secret.",
    )
    new.add_argument("path", help="Token-set file; created owner-only when absent.")
    new.add_argument("--id", required=True, help="Unique identifier, [A-Za-z0-9._-]{1,64}.")
    new.add_argument("--agents", help="Comma list of agent names the token admits (default any).")
    new.add_argument("--not-before", help="Start of validity, YYYY-MM-DDTHH:MM:SSZ (default now).")
    end = new.add_mutually_exclusive_group()
    end.add_argument(
        "--days",
        type=positive_days,
        default=None,
        help=(
            f"Days of validity from the start (default {DEFAULT_VALID_DAYS}, or the "
            "smallest --max-token-days of a running hub that follows the set)."
        ),
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
        help="Write the generated secret to this new owner-only file.",
    )
    source.add_argument(
        "--print-secret",
        action="store_true",
        help="Print the generated secret once on stdout instead; terminal logs and CI "
        "output keep it.",
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
    status.add_argument(
        "--prune",
        action="store_true",
        help="First remove the state and lock files of hubs that are gone (a killed hub "
        "leaves them); a running hub's files are never touched.",
    )
    status.set_defaults(func=_cmd_status)

    listing = nested.add_parser("list", help="Show identifiers, agents and validity.")
    listing.add_argument("path", help="Token-set file.")
    listing.add_argument("--json", action="store_true", help="Print a JSON document.")
    listing.set_defaults(func=_cmd_list)
