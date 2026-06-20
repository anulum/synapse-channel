# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — classify a request and route it to a tiered chat backend
"""Route a request to the cheapest backend that can handle it.

Not every message needs a large model. This module classifies a prompt into a
coarse task class and routes it to a matching :mod:`~synapse_channel.chat_backends`
backend, so trivial requests are answered by a cheap rule-based path and only the
genuinely hard ones reach a heavy model.

* :func:`classify` is the LLM-free policy — a deterministic function of the
  prompt (its length and a small set of keywords) returning ``rule``, ``slm``,
  or ``heavy``.
* :class:`TieredChatClient` is a :class:`~synapse_channel.chat_backends.ChatBackend`
  that holds one backend per class and dispatches :meth:`generate` through the
  classifier, falling back to a default class when no backend is registered for
  the chosen one.

The classifier is intentionally simple and explainable; it is a routing heuristic,
not a model. Tune the thresholds for your workload.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from synapse_channel.chat_backends import ChatBackend

DEFAULT_RULE_MAX_CHARS = 24
"""Prompts at or below this length are treated as trivial (the ``rule`` class)."""

DEFAULT_HEAVY_MIN_CHARS = 240
"""Prompts at or above this length are treated as hard (the ``heavy`` class)."""

_WORD = re.compile(r"[a-z][a-z-]*")

HEAVY_KEYWORDS = frozenset(
    {
        "design",
        "architecture",
        "architect",
        "prove",
        "proof",
        "refactor",
        "analyse",
        "analyze",
        "optimise",
        "optimize",
        "derive",
        "algorithm",
        "strategy",
        "trade-off",
        "tradeoff",
        "benchmark",
    }
)
"""Words that mark a request as needing the heavy class regardless of length."""


class TaskClass:
    """The coarse routing classes a request can fall into."""

    RULE = "rule"
    SLM = "slm"
    HEAVY = "heavy"


def classify(
    prompt: str,
    *,
    rule_max_chars: int = DEFAULT_RULE_MAX_CHARS,
    heavy_min_chars: int = DEFAULT_HEAVY_MIN_CHARS,
) -> str:
    """Classify a prompt into a routing task class.

    The policy is deterministic: a short prompt is ``rule``; a long prompt or one
    containing a heavy keyword is ``heavy``; everything else is ``slm``.

    Parameters
    ----------
    prompt : str
        The user prompt to classify; leading/trailing whitespace is ignored.
    rule_max_chars : int, optional
        Length at or below which a prompt is ``rule``.
    heavy_min_chars : int, optional
        Length at or above which a prompt is ``heavy``.

    Returns
    -------
    str
        One of :attr:`TaskClass.RULE`, :attr:`TaskClass.SLM`, or
        :attr:`TaskClass.HEAVY`.
    """
    text = prompt.strip()
    if len(text) <= rule_max_chars:
        return TaskClass.RULE
    words = set(_WORD.findall(text.lower()))
    if len(text) >= heavy_min_chars or (words & HEAVY_KEYWORDS):
        return TaskClass.HEAVY
    return TaskClass.SLM


class TieredChatClient:
    """A chat backend that routes :meth:`generate` to a per-class backend.

    Parameters
    ----------
    backends : Mapping[str, ChatBackend]
        One backend per task class. The ``default_class`` must be present.
    default_class : str, optional
        Class used when the classifier picks one with no registered backend.
        Defaults to :attr:`TaskClass.SLM`.
    classifier : Callable[[str], str], optional
        The prompt classifier; defaults to :func:`classify`. Injectable for tests.

    Raises
    ------
    ValueError
        If ``default_class`` has no backend in ``backends``.
    """

    def __init__(
        self,
        backends: Mapping[str, ChatBackend],
        *,
        default_class: str = TaskClass.SLM,
        classifier: Callable[[str], str] = classify,
    ) -> None:
        if default_class not in backends:
            raise ValueError(f"No backend registered for default class '{default_class}'.")
        self._backends = dict(backends)
        self._default_class = default_class
        self._classify = classifier
        self.last_class = ""

    def route(self, prompt: str) -> str:
        """Return the task class :func:`classify` assigns to ``prompt``."""
        return self._classify(prompt)

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """Classify ``user_prompt`` and delegate to the matching backend.

        Parameters
        ----------
        system_prompt : str
            System prompt forwarded to the chosen backend.
        user_prompt : str
            User prompt, both classified and forwarded.

        Returns
        -------
        str
            The chosen backend's reply.
        """
        task_class = self._classify(user_prompt)
        self.last_class = task_class
        backend = self._backends.get(task_class, self._backends[self._default_class])
        return backend.generate(system_prompt=system_prompt, user_prompt=user_prompt)
