# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deprecated CLI compatibility-option tests
"""Exercise deprecated option actions through the production CLI parser."""

from __future__ import annotations

import pytest

from synapse_channel import cli
from synapse_channel.cli_deprecated_options import METRICS_QUERY_FLAG_REMOVAL_VERSION


def test_metrics_query_token_default_is_disabled_and_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ordinary hub parsing preserves the safe default without warning."""
    args = cli.build_parser().parse_args(["hub"])

    assert args.metrics_query_token_ok is False
    assert capsys.readouterr().err == ""


def test_metrics_query_token_opt_in_warns_with_removal_and_replacement(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The compatibility opt-in remains functional but announces its sunset."""
    args = cli.build_parser().parse_args(["hub", "--metrics-query-token-ok"])

    assert args.metrics_query_token_ok is True
    assert capsys.readouterr().err == (
        "warning: --metrics-query-token-ok is deprecated and will be removed in "
        f"{METRICS_QUERY_FLAG_REMOVAL_VERSION}; send the metrics token in the "
        "Authorization: Bearer header instead.\n"
    )


def test_metrics_query_token_help_names_deprecation_and_removal_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Hub help exposes the compatibility window before an operator opts in."""
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["hub", "--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--metrics-query-token-ok" in help_text
    assert "Deprecated:" in help_text
    assert METRICS_QUERY_FLAG_REMOVAL_VERSION in help_text
