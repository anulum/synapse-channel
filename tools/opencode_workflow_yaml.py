# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — duplicate-safe YAML primitives for OpenCode workflow gates
"""Duplicate-safe YAML primitives shared by the OpenCode workflow release gates.

Both the editor and cross-platform compatibility gates must reason about the
*meaning* of a GitHub Actions workflow, not its raw text: a duplicated mapping
key, a nested ``continue-on-error``, or a widened matrix must fail closed rather
than slip past a substring check. These helpers centralise the one YAML loader
that rejects duplicate mapping keys and the recursive key probe both gates need.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast


def require_object(value: object, where: str, *, error_cls: type[Exception]) -> Mapping[str, Any]:
    """Return ``value`` as a mapping or fail closed.

    Parameters
    ----------
    value : object
        Parsed YAML node to validate.
    where : str
        Bounded diagnostic location used in the error; never file contents.
    error_cls : type of Exception
        Exception raised by the calling gate so its error taxonomy is preserved.

    Returns
    -------
    collections.abc.Mapping
        ``value`` unchanged once confirmed to be a mapping.

    Raises
    ------
    Exception
        An instance of ``error_cls`` when ``value`` is not a mapping.
    """
    if not isinstance(value, Mapping):
        raise error_cls(f"{where} must be an object")
    return value


def load_workflow_yaml(
    text: str,
    path: Path,
    *,
    error_cls: type[Exception],
    label: str,
) -> Mapping[str, Any]:
    """Parse workflow YAML into a mapping, rejecting duplicate mapping keys.

    ``yaml.SafeLoader`` silently keeps the last value for a duplicated key, which
    lets a workflow declare ``strategy`` (or any gate field) twice so the audited
    copy differs from the effective one. This loader raises instead, closing that
    bypass before any semantic check runs.

    Parameters
    ----------
    text : str
        Complete GitHub Actions workflow YAML.
    path : pathlib.Path
        Source path used only in parse diagnostics.
    error_cls : type of Exception
        Exception raised on any parse or shape failure so the calling gate keeps
        its own error taxonomy.
    label : str
        Human-readable surface name (e.g. ``"editor workflow"``) used verbatim in
        diagnostics so each gate's messages stay stable.

    Returns
    -------
    collections.abc.Mapping
        The single parsed document as a mapping.

    Raises
    ------
    Exception
        An instance of ``error_cls`` when the YAML is malformed, a mapping key is
        unhashable or duplicated, or the document is not a mapping.
    """
    import yaml

    unique_key_loader = cast(Any, type("_UniqueKeyLoader", (yaml.SafeLoader,), {}))

    def construct_unique_mapping(
        loader: Any,
        node: Any,
        deep: bool = False,
    ) -> dict[object, object]:
        loader.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise error_cls(f"{label} YAML mapping keys must be hashable") from exc
            if duplicate:
                raise error_cls(f"{label} YAML duplicates mapping key {key!r}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    unique_key_loader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_unique_mapping,
    )
    loader = unique_key_loader(text)
    try:
        document = cast(object, loader.get_single_data())
    except yaml.YAMLError as exc:
        raise error_cls(f"cannot parse {label} YAML: {path}") from exc
    finally:
        cast(Any, loader).dispose()
    return require_object(document, label, error_cls=error_cls)


def contains_mapping_key(value: object, expected: str) -> bool:
    """Return whether ``expected`` appears as a mapping key at any nested depth.

    Parameters
    ----------
    value : object
        Parsed YAML value to search recursively.
    expected : str
        Mapping key to detect (e.g. ``"continue-on-error"``).

    Returns
    -------
    bool
        ``True`` if any mapping at any depth contains ``expected`` as a key.
    """
    if isinstance(value, Mapping):
        return any(
            key == expected or contains_mapping_key(child, expected) for key, child in value.items()
        )
    if isinstance(value, list):
        return any(contains_mapping_key(child, expected) for child in value)
    return False
