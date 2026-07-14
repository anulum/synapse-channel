#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — MCP public-distribution verification command
"""Verify released PyPI and official MCP Registry records without publishing."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tools.mcp_registry_release import (
    OFFICIAL_PYPI_API,
    OFFICIAL_REGISTRY_API,
    REPO_ROOT,
    JsonFetcher,
    Phase,
    ReleaseContractError,
    VerificationUnavailable,
    fetch_json,
    load_contract,
    verify_distribution,
)


@dataclass(frozen=True)
class CliArgs:
    """Parsed release-verification command arguments."""

    phase: Phase
    expect_version: str | None
    timeout: float
    json_output: bool


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse release-verification arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("package", "registry"), default="registry")
    parser.add_argument("--expect-version")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--json", action="store_true", dest="json_output")
    namespace = parser.parse_args(argv)
    if namespace.timeout <= 0:
        parser.error("--timeout must be positive")
    return CliArgs(
        phase=cast(Phase, namespace.phase),
        expect_version=cast(str | None, namespace.expect_version),
        timeout=cast(float, namespace.timeout),
        json_output=cast(bool, namespace.json_output),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = REPO_ROOT,
    fetcher: JsonFetcher = fetch_json,
    pypi_api_base: str = OFFICIAL_PYPI_API,
    registry_api: str = OFFICIAL_REGISTRY_API,
) -> int:
    """Run public release verification; return 0 match, 1 drift, or 2 unavailable."""
    args = parse_args(argv)
    try:
        contract = load_contract(root, expect_version=args.expect_version)
        result = verify_distribution(
            contract,
            phase=args.phase,
            timeout=args.timeout,
            fetcher=fetcher,
            pypi_api_base=pypi_api_base,
            registry_api=registry_api,
        )
    except (ReleaseContractError, VerificationUnavailable) as exc:
        print(f"MCP distribution verification unavailable: {exc}", file=sys.stderr)
        return 2
    if args.json_output:
        print(json.dumps(result.as_json(), sort_keys=True))
    elif result.ok:
        print(f"MCP {result.phase} verification passed for {contract.name} {contract.version}")
    else:
        for error in result.errors:
            print(f"MCP distribution mismatch: {error}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
