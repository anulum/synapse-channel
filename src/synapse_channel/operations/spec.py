# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — description of one operation of a vendor client
"""Describe one operation of one vendor client on one host.

An operation is something a client can be asked to do: queue a message into
a session, read a setting, change one. Its description says what it needs,
what it changes and how well it is known. The description is data: it is
produced on the host where the client runs, and it grants nothing.

How well an operation is known is its **qualification**, in this order:

``unverified``
    Assumed to exist, for example for a client version not seen before.
``help_text``
    Read from the client's own help. Help text is not a run.
``run_once``
    Run for real at least once, with an evidence reference.
``accepted``
    Accepted after the runs its acceptance asks for, with an evidence reference.

Only ``run_once`` and ``accepted`` operations may run, and both need a
reference to their evidence.

Arguments are typed. A secret is never an argument value: an argument of
type ``secret_ref`` holds a reference (``ref:<name>``) that is resolved on
the execution host, so that no secret enters a record, a catalogue or a feed.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Final

from synapse_channel.core.errors import SynapseError

TRANSPORTS: Final = frozenset({"cli", "rpc", "acp", "server", "pane"})
SCOPES: Final = frozenset({"session", "project", "user", "host"})
INTERACTIVE: Final = frozenset({"no", "operator_step", "launch_time_only"})
IDEMPOTENT: Final = frozenset({"yes", "no", "reconcilable"})
QUALIFICATIONS: Final = ("unverified", "help_text", "run_once", "accepted")
"""Qualification levels, weakest first."""

RUNNABLE: Final = frozenset({"run_once", "accepted"})
"""Qualification levels at which an operation may run."""

ARGUMENT_TYPES: Final = frozenset({"string", "integer", "boolean", "uuid", "enum", "secret_ref"})

_OPERATION = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
_SECRET_REF = re.compile(r"ref:[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_MAX_STRING = 4096
_MAX_TEXT = 500
_MAX_ITEMS = 64


def _text(value: object, what: str, *, limit: int = _MAX_TEXT) -> str:
    """Return ``value`` when it is text of bounded length, else refuse it."""
    if not isinstance(value, str) or len(value) > limit:
        raise OperationSpecError(f"{what} is text of at most {limit} characters")
    return value


def _texts(value: object, what: str) -> tuple[str, ...]:
    """Return ``value`` when it is a tuple of bounded texts, else refuse it."""
    if not isinstance(value, tuple) or len(value) > _MAX_ITEMS:
        raise OperationSpecError(f"{what} is a list of at most {_MAX_ITEMS} texts")
    return tuple(_text(item, f"each of {what}") for item in value)


class OperationSpecError(SynapseError, ValueError):
    """An operation's description, or an argument given to it, is not usable."""

    code = "operation_spec"


@dataclass(frozen=True, slots=True)
class ArgumentSpec:
    """One argument of an operation."""

    name: str
    type: str
    required: bool = True
    choices: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        """Refuse a description that could not validate a value."""
        _text(self.name, "an argument name")
        _text(self.type, f"argument {self.name}: type")
        _texts(self.choices, f"argument {self.name}: choices")
        _text(self.description, f"argument {self.name}: description")
        if _NAME.fullmatch(self.name) is None:
            raise OperationSpecError("an argument name is lower-case letters, digits, underscores")
        if self.type not in ARGUMENT_TYPES:
            raise OperationSpecError(f"argument {self.name}: unknown type")
        if (self.type == "enum") != bool(self.choices):
            raise OperationSpecError(
                f"argument {self.name}: choices belong to an enum, and only to it"
            )
        if not isinstance(self.required, bool):
            raise OperationSpecError(f"argument {self.name}: required is true or false")

    def value_of(self, raw: str) -> object:
        """Return ``raw`` as this argument's type, or refuse it."""
        if not isinstance(raw, str) or len(raw) > _MAX_STRING:
            raise OperationSpecError(f"argument {self.name}: a value is text of bounded length")
        converters: dict[str, Callable[[], object]] = {
            "string": lambda: raw,
            "integer": lambda: _integer(raw),
            "boolean": lambda: {"true": True, "false": False}.get(raw),
            "uuid": lambda: raw if _is_uuid(raw) else None,
            "enum": lambda: raw if raw in self.choices else None,
            "secret_ref": lambda: raw if _SECRET_REF.fullmatch(raw) else None,
        }
        value = converters[self.type]()
        if value is None:
            raise OperationSpecError(f"argument {self.name}: not a valid {self.type}")
        return value


def _integer(raw: str) -> int | None:
    return int(raw) if re.fullmatch(r"-?[0-9]{1,18}", raw) else None


def _is_uuid(raw: str) -> bool:
    try:
        return str(uuid.UUID(raw)) == raw
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """What one operation of one client on one host is, needs and changes."""

    vendor: str
    client_version: str
    os: str
    host: str
    operation: str
    transport: str
    scope: str
    required_authority: str
    arguments: tuple[ArgumentSpec, ...] = ()
    side_effects: tuple[str, ...] = ()
    needs_restart: bool = False
    interactive: str = "no"
    idempotent: str = "no"
    qualification: str = "unverified"
    evidence: str = ""

    def __post_init__(self) -> None:
        """Refuse a description outside the vocabularies."""
        for field in (
            "vendor",
            "client_version",
            "os",
            "host",
            "required_authority",
            "operation",
            "transport",
            "scope",
            "interactive",
            "idempotent",
            "qualification",
            "evidence",
        ):
            _text(getattr(self, field), f"operation {field}")
        _texts(self.side_effects, "operation side_effects")
        if not isinstance(self.arguments, tuple) or len(self.arguments) > _MAX_ITEMS:
            raise OperationSpecError(f"operation arguments is a list of at most {_MAX_ITEMS}")
        if not all(isinstance(argument, ArgumentSpec) for argument in self.arguments):
            raise OperationSpecError("each operation argument is an argument description")
        for field in ("vendor", "client_version", "os", "host", "required_authority"):
            if _TOKEN.fullmatch(getattr(self, field)) is None:
                raise OperationSpecError(f"operation {field} is a short token without spaces")
        if _OPERATION.fullmatch(self.operation) is None:
            raise OperationSpecError("an operation has a dotted lower-case name, like a.b")
        for field, allowed in (
            ("transport", TRANSPORTS),
            ("scope", SCOPES),
            ("interactive", INTERACTIVE),
            ("idempotent", IDEMPOTENT),
            ("qualification", frozenset(QUALIFICATIONS)),
        ):
            if getattr(self, field) not in allowed:
                raise OperationSpecError(f"operation {field} is not a known value")
        if not isinstance(self.needs_restart, bool):
            raise OperationSpecError("operation needs_restart is true or false")
        names = [argument.name for argument in self.arguments]
        if len(names) != len(set(names)):
            raise OperationSpecError("an operation names each argument once")
        if self.qualification in RUNNABLE and not self.evidence.strip():
            raise OperationSpecError("a qualified operation names its evidence")

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        """Return what makes this row unique: host, vendor, version, OS, operation."""
        return (self.host, self.vendor, self.client_version, self.os, self.operation)

    @property
    def runnable(self) -> bool:
        """Return whether the qualification allows a run at all."""
        return self.qualification in RUNNABLE

    def arguments_from(self, raw: Mapping[str, str]) -> dict[str, object]:
        """Return the given arguments as typed values, or refuse them.

        Raises
        ------
        OperationSpecError
            An argument is not part of the operation, a required one is
            missing, or a value does not fit its type.
        """
        known = {argument.name: argument for argument in self.arguments}
        unknown = sorted(set(raw) - set(known))
        if unknown:
            raise OperationSpecError(f"not an argument of {self.operation}: {unknown[0]}")
        missing = sorted(
            name for name, argument in known.items() if argument.required and name not in raw
        )
        if missing:
            raise OperationSpecError(f"{self.operation} needs the argument {missing[0]}")
        return {name: known[name].value_of(value) for name, value in raw.items()}

    def to_json(self) -> dict[str, Any]:
        """Return the description as plain data: objects, lists, texts, booleans."""
        data = asdict(self)
        data["side_effects"] = list(self.side_effects)
        data["arguments"] = [
            {**asdict(argument), "choices": list(argument.choices)} for argument in self.arguments
        ]
        return data

    @classmethod
    def from_json(cls, data: object) -> OperationSpec:
        """Return the description held in plain data, or refuse it.

        Raises
        ------
        OperationSpecError
            The data is not a description: a field is missing, unknown or of
            the wrong kind.
        """
        if not isinstance(data, dict):
            raise OperationSpecError("an operation description is an object")
        fields = dict(data)
        raw_arguments = fields.pop("arguments", [])
        raw_effects = fields.pop("side_effects", [])
        if not isinstance(raw_arguments, list) or not isinstance(raw_effects, list):
            raise OperationSpecError("operation arguments and side_effects are lists")
        try:
            arguments = tuple(_argument_from(item) for item in raw_arguments)
            return cls(**fields, arguments=arguments, side_effects=tuple(raw_effects))
        except TypeError as exc:
            raise OperationSpecError(f"not an operation description: {type(exc).__name__}") from exc


def _argument_from(item: object) -> ArgumentSpec:
    """Return the argument description held in plain data, or refuse it."""
    if not isinstance(item, dict):
        raise OperationSpecError("an argument description is an object")
    fields = dict(item)
    choices = fields.pop("choices", [])
    if not isinstance(choices, list):
        raise OperationSpecError("argument choices is a list")
    return ArgumentSpec(**fields, choices=tuple(choices))
