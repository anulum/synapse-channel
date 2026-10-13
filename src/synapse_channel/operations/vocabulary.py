# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the registered words of operation names
"""Say which operation names exist.

An operation is named ``<object>.<action>`` or ``<object>.<aspect>.<action>``
in this product's words, never in a vendor's: ``session.list``,
``plugin.marketplace.list``. The words are registered in
``vocabulary.json`` beside this module, each with its meaning:

* **objects**: what the operation is about;
* **aspects**: a named part of one object, registered for that object only;
* **actions**: what is done.

A name with a word that is not registered is refused. A word is added by a
reviewed change of that file, with its meaning and a higher ``revision``; no
name is derived from a vendor's command by a rule. The same name means the
same effect on the same scope for every vendor.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from types import MappingProxyType

from synapse_channel.operations.spec import OperationSpecError

SCHEMA = "operation-vocabulary-v1"
"""Schema name of the vocabulary as plain data."""

_WORD = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_WORDS = 256
_MAX_MEANING = 300


def _meanings(value: object, what: str) -> Mapping[str, str]:
    """Return ``value`` when it maps registered words to their meanings, else refuse it."""
    if not isinstance(value, dict) or len(value) > _MAX_WORDS:
        raise OperationSpecError(f"the vocabulary's {what} map at most {_MAX_WORDS} words")
    for word, meaning in value.items():
        if not isinstance(word, str) or _WORD.fullmatch(word) is None:
            raise OperationSpecError(f"a word among the {what} is not a lower-case word")
        if not isinstance(meaning, str) or not 0 < len(meaning.strip()) <= _MAX_MEANING:
            raise OperationSpecError(f"{word} among the {what} has no bounded meaning")
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """The registered objects, aspects and actions, each with its meaning."""

    revision: int
    objects: Mapping[str, str]
    aspects: Mapping[str, Mapping[str, str]]
    actions: Mapping[str, str]

    def parts(self, name: object) -> tuple[str, str, str]:
        """Return the object, the aspect (or an empty text) and the action of ``name``.

        Raises
        ------
        OperationSpecError
            The name has not two or three parts, or one of them is not
            registered; an aspect counts only for the object it is registered for.
        """
        words = name.split(".") if isinstance(name, str) else []
        if len(words) not in (2, 3) or not all(words):
            raise OperationSpecError("an operation is named object.action or object.aspect.action")
        thing, action = words[0], words[-1]
        aspect = words[1] if len(words) == 3 else ""
        if thing not in self.objects:
            raise OperationSpecError(f"{name}: the object {thing} is not registered")
        if aspect and aspect not in self.aspects.get(thing, {}):
            raise OperationSpecError(f"{name}: the aspect {aspect} is not registered for {thing}")
        if action not in self.actions:
            raise OperationSpecError(f"{name}: the action {action} is not registered")
        return thing, aspect, action

    @classmethod
    def from_json(cls, data: object) -> Vocabulary:
        """Return the vocabulary held in plain data, or refuse it.

        Raises
        ------
        OperationSpecError
            The data is not a vocabulary of this schema, a word or a meaning
            is malformed, or aspects are registered for an unknown object.
        """
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise OperationSpecError("not an operation vocabulary of this schema")
        if set(data) != {"schema", "revision", "objects", "aspects", "actions"}:
            raise OperationSpecError("an operation vocabulary has exactly its five fields")
        revision = data["revision"]
        if type(revision) is not int or revision < 1:
            raise OperationSpecError("the vocabulary's revision is a whole number from one")
        objects = _meanings(data["objects"], "objects")
        raw_aspects = data["aspects"]
        if not isinstance(raw_aspects, dict) or not set(raw_aspects) <= set(objects):
            raise OperationSpecError("aspects are registered per registered object")
        aspects = {
            thing: _meanings(words, f"aspects of {thing}") for thing, words in raw_aspects.items()
        }
        return cls(
            revision, objects, MappingProxyType(aspects), _meanings(data["actions"], "actions")
        )


@cache
def packaged() -> Vocabulary:
    """Return the vocabulary shipped with this version of the package."""
    resource = files("synapse_channel").joinpath("operations").joinpath("vocabulary.json")
    return Vocabulary.from_json(json.loads(resource.read_text(encoding="utf-8")))
