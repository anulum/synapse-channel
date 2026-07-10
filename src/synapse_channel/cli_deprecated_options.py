# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deprecated CLI option actions
"""Keep compatibility flags visible while warning at argument-parse time."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

METRICS_QUERY_FLAG_REMOVAL_VERSION = "0.101.0"
"""Planned release removing the metrics query-token compatibility flag."""


class DeprecatedMetricsQueryTokenAction(argparse.Action):
    """Store the compatibility opt-in and emit its removal warning.

    The action preserves the historical boolean parser result while making the
    removal version and safer bearer-header path visible at the moment an
    operator opts in.
    """

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        """Set the destination and warn without changing parse compatibility.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            Parser dispatching the action.
        namespace : argparse.Namespace
            Destination namespace for the compatibility boolean.
        values : str, sequence, or None
            Unused value payload; this zero-argument action receives ``None``.
        option_string : str or None, optional
            Option spelling selected by ``argparse``.
        """
        del parser, values, option_string
        setattr(namespace, self.dest, True)
        print(
            f"warning: {self.option_strings[0]} is deprecated and will be removed in "
            f"{METRICS_QUERY_FLAG_REMOVAL_VERSION}; send the metrics token in the "
            "Authorization: Bearer header instead.",
            file=sys.stderr,
        )
