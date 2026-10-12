# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one row of the operations catalogue, with its native binding
"""Describe one operation of one answering component, and how it is invoked.

A row of the catalogue (schema ``operation-catalogue-v2``) is not a command of
a client. It is **one invocation with fixed parts and a closed set of
arguments**: the executable and the words that never change, or the exact
request method, and for every argument where its value goes. The same command
with one more flag can be another operation, so a caller never adds words; an
adapter builds the invocation from the row and from nothing else
(:meth:`OperationRow.argv`, :meth:`OperationRow.parameters`).

**Identity.** A row is about exactly one host, vendor, answering component,
component version, operating system, operation, transport and variant. The
component that answers is named because a client's command-line program and
its running service can be different releases.

**Effects.** A row lists every effect it has (``read``, ``change``,
``execute``, ``extend``, ``approve``, ``host``, ``destroy``, ``access``). Each
effect needs its own permission; none implies another. A row with the effect
``host`` or ``access`` is listed and never admitted.

**Qualification.** As in :mod:`~synapse_channel.operations.spec`. A row that
may run also names the fingerprint of the answering executable and the digest
of its own binding at the time it was qualified. A row whose binding changed
since is stale and may not run.

A row is data. It is produced on the host where the component runs, and it
grants nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final

from synapse_channel.operations.spec import (
    IDEMPOTENT,
    INTERACTIVE,
    QUALIFICATIONS,
    RUNNABLE,
    SCOPES,
    TRANSPORTS,
    ArgumentSpec,
    OperationSpecError,
)

EFFECTS: Final = ("read", "change", "execute", "extend", "approve", "host", "destroy", "access")
"""Every effect a row can have, in the order rows list them."""

AUTHORITY_OF_EFFECT: Final[Mapping[str, str]] = {
    "read": "operation-read",
    "change": "operation-change",
    "execute": "operation-execute",
    "extend": "operation-extend",
    "approve": "operation-approve",
    "destroy": "operation-destroy",
}
"""The permission each admissible effect needs. ``host`` and ``access`` have none."""

NEVER_ADMITTED: Final = frozenset({"host", "access"})
"""Effects whose rows are listed and never admitted."""

HUMAN_PRINCIPAL_EFFECTS: Final = frozenset({"extend", "approve"})
"""Effects that a seat's own authority never covers."""

PLACEMENTS: Final = frozenset({"flag", "switch", "position", "parameter"})
OUTPUT_MODES: Final = frozenset({"none", "text", "json"})
DISCLOSURES: Final = frozenset({"session_content", "credential_bearing"})
DISPOSITIONS: Final = frozenset({"refuse", "error", "requires_interaction", "answer_with_consent"})
MESSAGE_TRANSPORTS: Final = frozenset({"rpc", "acp", "server"})
"""Transports whose invocation is a request method with named parameters."""

MAX_LIST_ITEMS: Final = 64

_OPERATION = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
_NATIVE = re.compile(r"[!-~]{1,200}")
_WORD = re.compile(r"[ -~]{1,500}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_MAX_TEXT = 500


def _refuse(message: str) -> OperationSpecError:
    return OperationSpecError(message)


def _text(value: object, what: str, pattern: re.Pattern[str] | None = None) -> str:
    """Return ``value`` when it is bounded text matching ``pattern``, else refuse it."""
    if not isinstance(value, str) or len(value) > _MAX_TEXT:
        raise _refuse(f"{what} is text of at most {_MAX_TEXT} characters")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise _refuse(f"{what} is not in the form it must have")
    return value


def _texts(value: object, what: str, pattern: re.Pattern[str] | None = None) -> tuple[str, ...]:
    """Return ``value`` when it is a bounded tuple of such texts, else refuse it."""
    if not isinstance(value, tuple) or len(value) > MAX_LIST_ITEMS:
        raise _refuse(f"{what} is a list of at most {MAX_LIST_ITEMS} texts")
    return tuple(_text(item, f"each of {what}", pattern) for item in value)


def _whole(value: object) -> bool:
    return type(value) is int


@dataclass(frozen=True, slots=True)
class ArgumentBinding:
    """One argument of a row: its type, and where its value goes natively.

    ``placement`` is ``flag`` (the native flag, then the value), ``switch`` (a
    boolean: the native flag when true, nothing when false), ``position`` (a
    bare word, ordered by ``position``) or ``parameter`` (a named parameter of
    a request). ``max_items`` above zero makes the argument a list of at most
    that many values. ``minimum`` and ``maximum`` bound an integer.
    """

    name: str
    type: str
    placement: str
    native: str
    required: bool = True
    choices: tuple[str, ...] = ()
    description: str = ""
    position: int = -1
    max_items: int = 0
    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        """Refuse a binding that could not validate or place a value."""
        self._scalar()
        what = f"argument {self.name}"
        _text(self.placement, f"{what}: placement")
        _text(self.native, f"{what}: native name", _NATIVE)
        if self.placement not in PLACEMENTS:
            raise _refuse(f"{what}: unknown placement")
        if not _whole(self.max_items) or not 0 <= self.max_items <= MAX_LIST_ITEMS:
            raise _refuse(f"{what}: a list holds at most {MAX_LIST_ITEMS} values")
        if not _whole(self.position) or self.position < -1:
            raise _refuse(f"{what}: position is a whole number")
        if (self.placement == "position") != (self.position >= 0):
            raise _refuse(f"{what}: a position belongs to a positional argument, and only to it")
        if self.placement in {"flag", "switch"} and not self.native.startswith("-"):
            raise _refuse(f"{what}: a native flag starts with a dash")
        if self.placement == "switch" and (self.type != "boolean" or self.max_items):
            raise _refuse(f"{what}: a switch is one boolean")
        self._check_bounds(what)

    def _scalar(self) -> ArgumentSpec:
        return ArgumentSpec(self.name, self.type, self.required, self.choices, self.description)

    def _check_bounds(self, what: str) -> None:
        bounds = [bound for bound in (self.minimum, self.maximum) if bound is not None]
        if not all(_whole(bound) for bound in bounds):
            raise _refuse(f"{what}: a bound is a whole number")
        if bounds and self.type != "integer":
            raise _refuse(f"{what}: bounds belong to an integer")
        if len(bounds) == 2 and bounds[0] > bounds[1]:
            raise _refuse(f"{what}: the lower bound is above the upper bound")

    def _one(self, raw: object) -> object:
        if not isinstance(raw, str):
            raise _refuse(f"argument {self.name}: a value is text of bounded length")
        value = self._scalar().value_of(raw)
        if isinstance(value, int) and not isinstance(value, bool):
            below = self.minimum is not None and value < self.minimum
            above = self.maximum is not None and value > self.maximum
            if below or above:
                raise _refuse(f"argument {self.name}: outside its bounds")
        return value

    def value_of(self, raw: object) -> object:
        """Return ``raw`` as this argument's typed value or list of values, or refuse it."""
        if not self.max_items:
            return self._one(raw)
        if not isinstance(raw, (list, tuple)) or len(raw) > self.max_items:
            raise _refuse(f"argument {self.name}: a list of at most {self.max_items} values")
        return [self._one(item) for item in raw]


@dataclass(frozen=True, slots=True)
class NativeBinding:
    """The fixed part of an invocation.

    ``native_name`` is the vendor's own name of the thing, kept exactly and
    never executed. A command-line row names the ``executable`` and the
    ``fixed`` words that follow it; a request row names the exact ``method``;
    a pane row holds in ``method`` the command typed into the session.
    """

    native_name: str
    executable: str = ""
    fixed: tuple[str, ...] = ()
    method: str = ""

    def __post_init__(self) -> None:
        """Refuse a binding with unbounded or unprintable parts."""
        _text(self.native_name, "the native name", _WORD)
        _text(self.executable, "the executable", None if not self.executable else _WORD)
        _texts(self.fixed, "the fixed words", _NATIVE)
        _text(self.method, "the method", None if not self.method else _WORD)


@dataclass(frozen=True, slots=True)
class CallerObligation:
    """One request the component can send back while the operation runs.

    ``disposition`` says how the adapter ends it: ``refuse`` or ``error`` in
    the protocol's own form, ``requires_interaction`` when it is a question
    for a person, or ``answer_with_consent`` when a verified consent for
    exactly that request may answer it.
    """

    request: str
    disposition: str

    def __post_init__(self) -> None:
        """Refuse an obligation without a defined ending."""
        _text(self.request, "an obligation's request", _NATIVE)
        _text(self.disposition, f"obligation {self.request}: disposition")
        if self.disposition not in DISPOSITIONS:
            raise _refuse(f"obligation {self.request}: unknown disposition")


_TOKENS = ("host", "vendor", "component", "component_version", "os", "variant")
_WORDS = ("scope", "transport", "interactive", "idempotent", "qualification", "output_mode")
_VOCABULARIES = (
    ("transport", TRANSPORTS),
    ("scope", SCOPES),
    ("interactive", INTERACTIVE),
    ("idempotent", IDEMPOTENT),
    ("qualification", frozenset(QUALIFICATIONS)),
    ("output_mode", OUTPUT_MODES),
)
_IDENTITY = (*_TOKENS[:5], "operation", "transport", "variant")
_UNBOUND = frozenset(
    {"version_printed", "side_effects", "qualification", "evidence", "qualified_binding"}
)


@dataclass(frozen=True, slots=True)
class OperationRow:
    """One operation of one answering component: what it is, needs and changes."""

    host: str
    vendor: str
    component: str
    component_version: str
    os: str
    operation: str
    transport: str
    scope: str
    effects: tuple[str, ...]
    binding: NativeBinding
    variant: str = "default"
    arguments: tuple[ArgumentBinding, ...] = ()
    reserved_natives: tuple[str, ...] = ()
    obligations: tuple[CallerObligation, ...] = ()
    output_mode: str = "none"
    output_schema: str = ""
    output_projection: str = ""
    disclosures: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    needs_restart: bool = False
    interactive: str = "no"
    idempotent: str = "no"
    version_printed: str = ""
    fingerprint: str = ""
    qualification: str = "unverified"
    evidence: str = ""
    qualified_binding: str = ""

    def __post_init__(self) -> None:
        """Refuse a row outside the vocabularies or with an unusable binding."""
        self._check_texts()
        self._check_parts()
        self._check_binding()
        self._check_arguments()
        self._check_qualification()

    def _check_texts(self) -> None:
        for field in _TOKENS:
            _text(getattr(self, field), f"row {field}", _TOKEN)
        for field in (*_WORDS, "version_printed", "evidence"):
            _text(getattr(self, field), f"row {field}")
        if not isinstance(self.operation, str) or _OPERATION.fullmatch(self.operation) is None:
            raise _refuse("an operation has a dotted lower-case name of two or three parts")
        for field, allowed in _VOCABULARIES:
            if getattr(self, field) not in allowed:
                raise _refuse(f"row {field} is not a known value")
        if not isinstance(self.needs_restart, bool):
            raise _refuse("row needs_restart is true or false")
        _texts(self.side_effects, "row side_effects")
        _texts(self.reserved_natives, "row reserved_natives", _NATIVE)
        for field in ("output_schema", "output_projection"):
            value = getattr(self, field)
            _text(value, f"row {field}", _TOKEN if value else None)
        for field in ("fingerprint", "qualified_binding"):
            value = getattr(self, field)
            _text(value, f"row {field}", _DIGEST if value else None)

    def _check_parts(self) -> None:
        for field, allowed in (("effects", EFFECTS), ("disclosures", tuple(sorted(DISCLOSURES)))):
            value = _texts(getattr(self, field), f"row {field}")
            if list(value) != [word for word in allowed if word in value]:
                raise _refuse(f"row {field} lists known values once each, in their order")
        if not self.effects:
            raise _refuse("a row has at least one effect")
        if self.output_schema and self.output_mode != "json":
            raise _refuse("an output schema belongs to json output")
        if not isinstance(self.binding, NativeBinding):
            raise _refuse("row binding is a native binding")
        for field, kind in (("arguments", ArgumentBinding), ("obligations", CallerObligation)):
            value = getattr(self, field)
            if not isinstance(value, tuple) or len(value) > MAX_LIST_ITEMS:
                raise _refuse(f"row {field} is a list of at most {MAX_LIST_ITEMS}")
            if not all(isinstance(item, kind) for item in value):
                raise _refuse(f"each of row {field} is a description of its kind")

    def _check_binding(self) -> None:
        binding = self.binding
        command_line = self.transport == "cli"
        if command_line != bool(binding.executable) or command_line == bool(binding.method):
            raise _refuse(
                "a command-line row names an executable; any other row names a method, alone"
            )
        if not command_line and binding.fixed:
            raise _refuse("fixed words belong to a command-line row")
        if self.transport == "pane" and self.arguments:
            raise _refuse("a pane row takes no arguments")
        if self.obligations and self.transport not in MESSAGE_TRANSPORTS:
            raise _refuse("caller obligations belong to a request transport")
        requests = [obligation.request for obligation in self.obligations]
        if len(requests) != len(set(requests)):
            raise _refuse("a row names each obligation once")
        consent = any(item.disposition == "answer_with_consent" for item in self.obligations)
        if consent and "approve" not in self.effects:
            raise _refuse("answering with consent needs the effect approve")

    def _check_arguments(self) -> None:
        names = [argument.name for argument in self.arguments]
        natives = [argument.native for argument in self.arguments]
        if len(names) != len(set(names)) or len(natives) != len(set(natives)):
            raise _refuse("a row names each argument and each native name once")
        taken = set(self.binding.fixed) | set(self.reserved_natives)
        if taken & set(natives):
            raise _refuse("an argument is bound to a fixed or reserved native name")
        request = self.transport in MESSAGE_TRANSPORTS
        if any((argument.placement == "parameter") != request for argument in self.arguments):
            raise _refuse("parameters belong to a request row, and flags and positions do not")
        _check_positions(
            [argument for argument in self.arguments if argument.placement == "position"]
        )

    def _check_qualification(self) -> None:
        if self.qualification not in RUNNABLE:
            return
        if not self.evidence.strip() or not self.fingerprint or not self.qualified_binding:
            raise _refuse(
                "a qualified row names its evidence, the component's fingerprint and its binding"
            )

    @property
    def key(self) -> tuple[str, ...]:
        """Return what makes this row unique, transport and variant included."""
        return tuple(getattr(self, field) for field in _IDENTITY)

    @property
    def binding_digest(self) -> str:
        """Return the digest of everything a qualification vouches for."""
        bound = {name: value for name, value in self.to_json().items() if name not in _UNBOUND}
        canonical = json.dumps(bound, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()

    @property
    def stale(self) -> bool:
        """Return whether the row was qualified for another binding than it has now."""
        return self.qualification in RUNNABLE and self.qualified_binding != self.binding_digest

    @property
    def runnable(self) -> bool:
        """Return whether the qualification allows a run of exactly this binding."""
        return self.qualification in RUNNABLE and not self.stale

    @property
    def required_authorities(self) -> tuple[str, ...]:
        """Return the permission of every admissible effect; all of them are needed."""
        return tuple(AUTHORITY_OF_EFFECT[e] for e in self.effects if e in AUTHORITY_OF_EFFECT)

    @property
    def display_class(self) -> str:
        """Return one effect to show a row under. It decides nothing."""
        return self.effects[-1]

    @property
    def needs_human_principal(self) -> bool:
        """Return whether an effect of this row is never covered by a seat's own authority."""
        return bool(HUMAN_PRINCIPAL_EFFECTS.intersection(self.effects))

    def arguments_from(self, raw: Mapping[str, object]) -> dict[str, object]:
        """Return the given arguments as typed values, or refuse them.

        An argument that is not given stays absent: it is not the same as
        ``false`` or as an empty text.

        Raises
        ------
        OperationSpecError
            An argument is not part of the row, a required one is missing, or
            a value does not fit its type, its bounds or its list size.
        """
        known = {argument.name: argument for argument in self.arguments}
        unknown = sorted(set(raw) - set(known))
        if unknown:
            raise _refuse(f"not an argument of {self.operation}: {unknown[0]}")
        missing = sorted(name for name, a in known.items() if a.required and name not in raw)
        if missing:
            raise _refuse(f"{self.operation} needs the argument {missing[0]}")
        return {name: known[name].value_of(value) for name, value in raw.items()}

    def argv(self, raw: Mapping[str, object]) -> tuple[str, ...]:
        """Return the whole command line of this row for the given arguments.

        The executable, the fixed words, then flags and switches in the order
        the row lists them, then positions. A value that begins with a dash,
        or an empty value, is refused: the component could read it as a flag.

        Raises
        ------
        OperationSpecError
            The row is not a command-line row, or an argument is refused.
        """
        if self.transport != "cli":
            raise _refuse(f"{self.operation} is not a command-line row")
        values = self.arguments_from(raw)
        words = [self.binding.executable, *self.binding.fixed]
        ordered = sorted(self.arguments, key=lambda a: (a.placement == "position", a.position))
        for argument in ordered:
            if argument.name not in values:
                continue
            value = values[argument.name]
            if argument.placement == "switch":
                words.extend([argument.native] if value else [])
                continue
            rendered = _rendered(argument, value)
            if argument.placement == "flag":
                for item in rendered:
                    words.extend([argument.native, item])
            else:
                words.extend(rendered)
        return tuple(words)

    def parameters(self, raw: Mapping[str, object]) -> dict[str, object]:
        """Return the named parameters of this row's request for the given arguments.

        Raises
        ------
        OperationSpecError
            The row is not a request row, or an argument is refused.
        """
        if self.transport not in MESSAGE_TRANSPORTS:
            raise _refuse(f"{self.operation} is not a request row")
        natives = {argument.name: argument.native for argument in self.arguments}
        return {natives[name]: value for name, value in self.arguments_from(raw).items()}

    def to_json(self) -> dict[str, Any]:
        """Return the row as plain data: objects, lists, texts, numbers, booleans."""
        data = asdict(self)
        for field in ("effects", "reserved_natives", "disclosures", "side_effects"):
            data[field] = list(getattr(self, field))
        data["binding"] = {**asdict(self.binding), "fixed": list(self.binding.fixed)}
        data["arguments"] = [
            {**asdict(argument), "choices": list(argument.choices)} for argument in self.arguments
        ]
        data["obligations"] = [asdict(obligation) for obligation in self.obligations]
        return data

    @classmethod
    def from_json(cls, data: object) -> OperationRow:
        """Return the row held in plain data, or refuse it.

        Raises
        ------
        OperationSpecError
            The data is not a row: a field is missing, unknown or of the
            wrong kind.
        """
        if not isinstance(data, dict):
            raise _refuse("a catalogue row is an object")
        fields = dict(data)
        try:
            fields["binding"] = _built(NativeBinding, fields.get("binding"), ("fixed",))
            for name, kind, lists in (
                ("arguments", ArgumentBinding, ("choices",)),
                ("obligations", CallerObligation, ()),
            ):
                fields[name] = tuple(_built(kind, item, lists) for item in _list(fields, name))
            for name in ("effects", "reserved_natives", "disclosures", "side_effects"):
                fields[name] = tuple(_list(fields, name))
            return cls(**fields)
        except TypeError as exc:
            raise _refuse(f"not a catalogue row: {type(exc).__name__}") from exc


def _check_positions(arguments: list[ArgumentBinding]) -> None:
    """Refuse positional arguments whose order could be read in two ways."""
    positional = sorted(arguments, key=lambda argument: argument.position)
    if [argument.position for argument in positional] != list(range(len(positional))):
        raise _refuse("positions count from zero without a gap")
    optional_seen = False
    for index, argument in enumerate(positional):
        misplaced_list = argument.max_items and index != len(positional) - 1
        if (optional_seen and argument.required) or misplaced_list:
            raise _refuse("a required position precedes optional ones; a list comes last")
        optional_seen = optional_seen or not argument.required


def _rendered(argument: ArgumentBinding, value: object) -> list[str]:
    """Return the words of one argument's value, refusing any that could pass for a flag."""
    items = value if isinstance(value, list) else [value]
    words = [str(item).lower() if isinstance(item, bool) else str(item) for item in items]
    if not words or any(not word or word.startswith("-") for word in words):
        raise _refuse(f"argument {argument.name}: a value is not empty and begins with no dash")
    return words


def _list(fields: Mapping[str, object], name: str) -> list[object]:
    value = fields.get(name, [])
    if not isinstance(value, list):
        raise _refuse(f"row {name} is a list")
    return value


def _built(kind: Any, item: object, lists: Sequence[str]) -> Any:
    """Return ``item`` as a description of ``kind``, its listed fields made tuples."""
    if not isinstance(item, dict):
        raise _refuse("a part of a catalogue row is an object")
    fields = dict(item)
    for name in lists:
        if not isinstance(fields.get(name, []), list):
            raise _refuse(f"{name} is a list")
        fields[name] = tuple(fields.get(name, []))
    return kind(**fields)
