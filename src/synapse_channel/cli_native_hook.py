# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — command line for recording native messages from a client hook
"""``synapse native-hook`` and ``synapse native-seat``.

``native-hook claude`` is what an agent client runs after a session-send tool
call: it reads the call from standard input, writes the record to a spool on
disk, and then tries to put the spool on the hub. It prints nothing and always
exits 0: the send has already happened and the hook cannot fail it. The client
does wait for the hook, for as long as the spool's lock and the hub's answer
take, up to the waits given and the client's own hook timeout. The hub token is read only
after the record is on disk: an unreadable token file leaves the record
pending instead of losing it.

``native-hook flush`` tries the spool again and ``native-hook status`` prints
its counts. ``native-seat`` keeps the local register that says which seat a
native session is.
"""

from __future__ import annotations

import argparse
import os
import sys
from functools import partial
from pathlib import Path
from typing import Any

from synapse_channel.cli_native_record import exchange_record
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.core.native_message import (
    NativeMessageError,
    native_idempotency_key,
    parse_native_message_record,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.native_record_hook import completing_sender, run_hook
from synapse_channel.native_seat_register import (
    SeatRegisterError,
    default_register_dir,
    load,
    register,
    seat_of_name,
    seat_of_session,
)
from synapse_channel.native_spool import (
    DEFAULT_MAX_ATTEMPTS,
    Sender,
    SendResult,
    counts,
    default_spool_dir,
    flush,
    retry_failed,
)

MAX_STDIN_BYTES = 16 * 1024 * 1024
"""Largest hook input read."""

_TRANSIENT_REFUSALS = frozenset({"native_record_unavailable", "native_record_rate_limited"})
"""Refusal codes after which a later attempt can succeed."""

_FINAL_ERRORS = frozenset({"idempotency_conflict"})
"""Error-frame codes that refuse the record itself."""

HOOK_READY_TIMEOUT = 3.0
"""Default seconds a hook waits for the hub's welcome; the client waits for the hook."""

HOOK_REPLY_TIMEOUT = 5.0
"""Default seconds a hook waits for the hub's verdict."""


TOKEN_ENV = "SYNAPSE_TOKEN"  # nosec B105
"""Environment variable read when neither token option is given."""


def hub_sender(
    record: dict[str, Any],
    *,
    uri: str,
    token: str | None,
    token_file: str | None = None,
    ready_timeout: float = HOOK_READY_TIMEOUT,
    reply_timeout: float = HOOK_REPLY_TIMEOUT,
) -> SendResult:
    """Put one complete record on the hub as its sender's seat.

    The token is ``token``, else the content of ``token_file`` read through the
    owner-only secret floor, else the ``SYNAPSE_TOKEN`` environment variable. A
    token file that cannot be used leaves the record pending.

    A record the hub's own parser would refuse is final without a connection.
    The hub's confirmation is the only success. A refusal by the hub is final,
    except when the hub says it is unavailable or the recorder is rate limited;
    so is a retry key already used for a different record. Everything else (no
    hub, no answer, a hub too old for the verb, a seat name held by another
    connection, another error frame) may succeed later.
    """
    try:
        parse_native_message_record(record)
    except NativeMessageError as exc:
        return SendResult(False, True, f"record refused before sending: {exc}")
    secret = token or os.environ.get(TOKEN_ENV) or None
    if not token and token_file:
        try:
            secret = read_secret_file(token_file, flag="--token-file")
        except (OSError, SecretFileError) as exc:
            return SendResult(False, False, f"cannot read token file: {exc}", hub_asked=False)
    verdict, failure = exchange_record(
        record,
        uri=uri,
        seat=str(record["sender_seat"]),
        token=secret,
        idem_key=native_idempotency_key(record),
        ready_timeout=ready_timeout,
        reply_timeout=reply_timeout,
    )
    if verdict is None:
        return SendResult(False, False, failure)
    kind = verdict.get("type")
    if kind == MessageType.NATIVE_MESSAGE_RECORDED:
        seq = verdict.get("audit_seq")
        return SendResult(True, False, "recorded", seq if isinstance(seq, int) else None)
    code = str(verdict.get("error_code") or "refused")
    refused = kind == MessageType.NATIVE_MESSAGE_REJECTED and code not in _TRANSIENT_REFUSALS
    final = refused or code in _FINAL_ERRORS
    return SendResult(False, final, f"refused by the hub ({code})")


def _directories(args: argparse.Namespace) -> tuple[Path, Path]:
    spool = Path(args.spool_dir) if args.spool_dir else default_spool_dir()
    seats = Path(args.register_dir) if args.register_dir else default_register_dir()
    return spool, seats


def _flush_command(args: argparse.Namespace, spool: Path, send: Sender) -> int:
    """Run ``native-hook flush`` and print what it did."""
    revived = retry_failed(spool) if args.retry_failed else 0
    report = None if revived is None else flush(spool, send, max_attempts=args.max_attempts)
    if report is None or report.busy:
        print(f"nothing flushed: another flush is running; pending {counts(spool)['pending']}")
        return 1
    print(
        f"recorded {report.recorded}, failed {report.failed}, "
        f"pending {report.left_pending}"
        + (f", revived {revived}" if args.retry_failed else "")
        + ("; stopped early, the hub did not answer" if report.stopped_early else "")
    )
    return 0 if report.left_pending == 0 and report.failed == 0 else 1


def _cmd_native_hook(args: argparse.Namespace) -> int:
    """Dispatch ``synapse native-hook``."""
    spool, seats = _directories(args)
    send = partial(
        hub_sender,
        uri=args.uri,
        token=args.hub_token,
        token_file=args.hub_token_file,
        ready_timeout=args.ready_timeout,
        reply_timeout=args.reply_timeout,
    )
    if args.hook_command == "claude":
        try:
            raw = sys.stdin.buffer.read(MAX_STDIN_BYTES)
        except OSError:
            return 0
        run_hook(
            raw,
            spool_dir=spool,
            register_dir=seats,
            send=send,
            max_attempts=args.max_attempts,
        )
        return 0
    if args.hook_command == "flush":
        return _flush_command(args, spool, completing_sender(seats, send))
    for state, count in counts(spool).items():
        print(f"{state}: {count}")
    return 0


def _cmd_native_seat(args: argparse.Namespace) -> int:
    """Dispatch ``synapse native-seat``."""
    directory = Path(args.register_dir) if args.register_dir else default_register_dir()
    if args.seat_command == "register":
        try:
            path = register(
                directory,
                native_session=args.session_id,
                seat=args.seat,
                session_name=args.name,
            )
        except (OSError, SeatRegisterError) as exc:
            print(f"synapse native-seat: {exc}")
            return 1
        print(f"registered {args.session_id} as {args.seat}: {path}")
        return 0
    registrations = load(directory)
    if args.seat_command == "resolve":
        found = (
            seat_of_session(registrations, args.session_id)
            if args.session_id
            else seat_of_name(registrations, args.name or "")
        )
        if found is None:
            print("not registered")
            return 1
        print(found.seat)
        return 0
    for item in registrations:
        name = f" name {item.session_name}" if item.session_name else ""
        print(f"{item.registered_at} {item.native_session} -> {item.seat}{name}")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``native-hook`` and ``native-seat``."""
    hook = subparsers.add_parser(
        "native-hook",
        help="Record session-send tool calls from a client hook, through a spool on disk.",
    )
    hook_commands = hook.add_subparsers(dest="hook_command", required=True)
    for name, text in (
        ("claude", "Read one PostToolUse call from standard input; print nothing; exit 0."),
        ("flush", "Try to put the spooled records on the hub."),
        ("status", "Print how many spool entries each state holds."),
    ):
        command = hook_commands.add_parser(name, help=text)
        command.add_argument("--spool-dir", default=None, help="Spool directory.")
        command.add_argument("--register-dir", default=None, help="Seat register directory.")
        command.add_argument("--uri", default=default_hub_uri())
        # Kept out of the entry point's token handling, which exits before the
        # command runs when the file is unreadable; the record is spooled first.
        command.add_argument(
            "--token", dest="hub_token", default=None, help="Shared-secret token of the hub."
        )
        command.add_argument(
            "--token-file",
            dest="hub_token_file",
            default=None,
            help="Read the token from this owner-only file instead.",
        )
        command.add_argument(
            "--max-attempts",
            type=int,
            default=DEFAULT_MAX_ATTEMPTS,
            help="Transport failures after which an entry becomes failed.",
        )
        command.add_argument("--ready-timeout", type=float, default=HOOK_READY_TIMEOUT)
        command.add_argument("--reply-timeout", type=float, default=HOOK_REPLY_TIMEOUT)
        if name == "flush":
            command.add_argument(
                "--retry-failed", action="store_true", help="Also try failed entries again."
            )
        command.set_defaults(func=_cmd_native_hook)

    seat = subparsers.add_parser(
        "native-seat",
        help="Keep the local register of which seat a vendor's native session is.",
    )
    seat_commands = seat.add_subparsers(dest="seat_command", required=True)
    add = seat_commands.add_parser("register", help="Record that a native session is a seat.")
    add.add_argument("--session-id", required=True, help="Native session identifier.")
    add.add_argument("--seat", required=True, help="Seat identity, PROJECT/seat.")
    add.add_argument("--name", default=None, help="Name other sessions address it by.")
    resolve = seat_commands.add_parser("resolve", help="Print the seat of a session or a name.")
    target = resolve.add_mutually_exclusive_group(required=True)
    target.add_argument("--session-id", default=None)
    target.add_argument("--name", default=None)
    listing = seat_commands.add_parser("list", help="Print every registration, oldest first.")
    for command in (add, resolve, listing):
        command.add_argument("--register-dir", default=None, help="Seat register directory.")
        command.set_defaults(func=_cmd_native_seat)
