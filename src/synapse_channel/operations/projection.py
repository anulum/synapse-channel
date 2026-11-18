# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — select a row's projection from its JSON output and digest it as canonical JSON
"""Select the projection a catalogue row names from its output, and digest it.

A row with ``output_mode: json`` may name ``projection_paths``: the JSON paths
whose values make up its projection. Core applies them itself, so two
identities that run the same read row on the same state compute the same
digest, which is what a component's own state is declared and verified by.

A path is keys joined by ``.``; a key followed by ``[]`` steps into every
element of that array, in its order (``settings.theme``, ``plugins[].name``).
There are no filters, wildcards or indexes, so two readers cannot disagree
about what a path selects.

The projection is an object whose keys are the path texts and whose values
are what each path selects. A path the output does not have is a refusal,
never a missing or empty value; so is a step into something that is not an
object or not an array. The output is parsed as one JSON value; a duplicate
key, ``NaN`` and ``Infinity`` are refused. A number with a fraction may appear
in the output, but a selected value must be one that Core's canonical JSON
(:func:`~synapse_channel.core.aef_canonical.canonical_json`) accepts, so a
selected fraction is refused. The digest is the SHA-256 of that canonical
JSON, as 64 lower-case hex digits.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import NoReturn

from synapse_channel.core.aef_canonical import AefCanonicalizationError, canonical_json
from synapse_channel.core.errors import SynapseError

PROJECTION_PATH = re.compile(
    r"[A-Za-z0-9_-]{1,64}(?:\[\])?(?:\.[A-Za-z0-9_-]{1,64}(?:\[\])?){0,15}"
)
"""A projection path: 1 to 16 keys joined by ``.``, each key optionally followed by ``[]``."""

MAX_PATH_LENGTH = 256
"""The longest projection path, in characters."""

MAX_PATHS = 64
"""The most projection paths one row names."""


class ProjectionError(SynapseError, ValueError):
    """Raised when a row's output gives no projection; the message says why."""

    code = "projection_failed"


def project(output: str, paths: Sequence[str], *, truncated: bool = False) -> dict[str, object]:
    """Return the projection of ``output`` that ``paths`` select.

    Parameters
    ----------
    output : str
        The read row's kept standard output.
    paths : Sequence of str
        The row's ``projection_paths``, in its order.
    truncated : bool, optional
        Whether the runner cut the output at its limit.

    Returns
    -------
    dict of str to object
        Each path text and the value it selects, in the order of ``paths``.

    Raises
    ------
    ProjectionError
        The output was cut, names no path, does not parse as one JSON value,
        lacks a path, or a selected value is outside Core's canonical JSON.
    """
    if truncated:
        raise ProjectionError("the output was cut at the runner's limit")
    if not paths:
        raise ProjectionError("the row names no projection paths")
    document = _parse(output)
    projection: dict[str, object] = {}
    for path in paths:
        value = _walk(document, path.split("."), path)
        try:
            canonical_json(value)
        except AefCanonicalizationError as exc:
            raise ProjectionError(
                f"{path} selects a value outside Core's canonical JSON: {exc}"
            ) from exc
        projection[path] = value
    return projection


def projection_digest(projection: Mapping[str, object]) -> str:
    """Return the SHA-256 of ``projection`` as Core's canonical JSON, in 64 hex digits."""
    return hashlib.sha256(canonical_json(dict(projection))).hexdigest()


def _parse(output: str) -> object:
    """Return ``output`` as one JSON value, refusing duplicate keys, ``NaN`` and ``Infinity``."""
    try:
        return json.loads(output, object_pairs_hook=_unique, parse_constant=_no_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProjectionError(f"the output is not one JSON value: {exc}") from exc


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Return the object of ``pairs``, refusing a key given twice."""
    found: dict[str, object] = {}
    for key, value in pairs:
        if key in found:
            raise ProjectionError(f"the output names the key {key!r} twice in one object")
        found[key] = value
    return found


def _no_constant(constant: str) -> NoReturn:
    raise ProjectionError(f"the output holds {constant}, which JSON does not define")


def _walk(value: object, steps: list[str], path: str) -> object:
    """Return what the remaining ``steps`` of ``path`` select from ``value``."""
    if not steps:
        return value
    step, rest = steps[0], steps[1:]
    each = step.endswith("[]")
    key = step[:-2] if each else step
    if not isinstance(value, dict):
        raise ProjectionError(f"{path}: {key} is read from something that is not an object")
    if key not in value:
        raise ProjectionError(f"{path}: the output has no {key}")
    found = value[key]
    if not each:
        return _walk(found, rest, path)
    if not isinstance(found, list):
        raise ProjectionError(f"{path}: {key}[] steps into something that is not an array")
    return [_walk(item, rest, path) for item in found]
