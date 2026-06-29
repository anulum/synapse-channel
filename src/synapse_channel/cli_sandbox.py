# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse sandbox` CLI: validate a manifest, run a tool capability-limited
"""``synapse sandbox`` — validate a capability manifest and run a sandboxed tool.

Two operator-facing verbs over the sandbox. ``validate`` loads a capability manifest and
reports the normalised, deny-by-default grants it declares — a dry check before anything
runs. ``run`` executes a ``.wasm`` tool under that manifest: it binds the manifest to the
exact module by content digest (a swapped module is refused), requires an explicit
``--approve`` so a capability-bearing run is always an operator decision, and prints the
bounded run receipt. The WASM runtime lives behind the optional ``[wasm]`` extra; ``run``
reports the install hint when it is absent rather than failing obscurely.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from synapse_channel.core.sandbox_policy import (
    CapabilityManifest,
    SandboxManifestError,
    SandboxRequest,
    authorise,
    manifest_from_dict,
)
from synapse_channel.core.sandbox_receipt import RunReceipt, digest_bytes
from synapse_channel.core.wasm_sandbox import DEFAULT_ENTRYPOINT, run_sandboxed

Runner = Callable[..., RunReceipt]


def _load_manifest(path: str) -> CapabilityManifest:
    """Read and validate a manifest JSON file, raising ``SandboxManifestError`` on any fault."""
    try:
        raw = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise SandboxManifestError(f"could not read manifest file: {path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SandboxManifestError(f"manifest is not valid JSON: {exc}") from exc
    return manifest_from_dict(data)


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate a capability manifest and print its normalised, deny-by-default grants."""
    try:
        manifest = _load_manifest(args.manifest)
    except SandboxManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(manifest.to_dict(), indent=2))
    else:
        print(
            f"manifest for '{manifest.tool_id}' is valid: "
            f"{len(manifest.filesystem)} filesystem, {len(manifest.network)} network grant(s), "
            f"fuel {manifest.resources.fuel}, memory {manifest.resources.memory_bytes} bytes"
        )
    return 0


def _request_for(manifest: CapabilityManifest, content_digest: str) -> SandboxRequest:
    """Build the run request a tool makes when it uses exactly the grants it declares."""
    return SandboxRequest(
        tool_id=manifest.tool_id,
        content_digest=content_digest,
        filesystem=tuple((grant.guest_path, grant.write) for grant in manifest.filesystem),
        network=tuple((endpoint.host, endpoint.port) for endpoint in manifest.network),
        memory_bytes=manifest.resources.memory_bytes,
        fuel=manifest.resources.fuel,
        wall_clock_ms=manifest.resources.wall_clock_ms,
    )


def _cmd_run(args: argparse.Namespace, *, runner: Runner = run_sandboxed) -> int:
    """Run a sandboxed tool under its manifest, gated by the module digest and operator approval."""
    try:
        manifest = _load_manifest(args.manifest)
    except SandboxManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        wasm_bytes = Path(args.tool).expanduser().read_bytes()
    except OSError:
        print(f"could not read tool module: {args.tool}", file=sys.stderr)
        return 2
    inputs = b""
    if args.input is not None:
        try:
            inputs = Path(args.input).expanduser().read_bytes()
        except OSError:
            print(f"could not read input file: {args.input}", file=sys.stderr)
            return 2

    decision = authorise(manifest, _request_for(manifest, digest_bytes(wasm_bytes)))
    if not decision.allowed:
        print(
            f"refused: {decision.reason} (the tool does not match its approved manifest)",
            file=sys.stderr,
        )
        return 2
    if not args.approve:
        print(
            "this run would grant capabilities; re-run with --approve to confirm", file=sys.stderr
        )
        return 2

    try:
        receipt = runner(manifest, wasm_bytes, inputs, entrypoint=args.entrypoint)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_receipt(receipt, json_out=args.json)
    return 0


def _print_receipt(receipt: RunReceipt, *, json_out: bool) -> None:
    """Print a run receipt, as JSON or a readable summary."""
    if json_out:
        print(json.dumps(receipt, indent=2))
        return
    print(
        f"ran '{receipt['tool_id']}' — exit {receipt['exit']}, fuel used {receipt['fuel_used']}, "
        f"output {receipt['output_digest']}"
    )
    print(f"granted: {', '.join(receipt['granted_capabilities'])}")
    if receipt["reason"]:
        print(f"reason: {receipt['reason']}")


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``sandbox`` command group."""
    parser = subparsers.add_parser(
        "sandbox",
        help="Validate a capability manifest and run a tool capability-limited (experimental).",
    )
    group = parser.add_subparsers(dest="sandbox_command", required=True)

    validate = group.add_parser("validate", help="Validate a capability manifest.")
    validate.add_argument("manifest", help="Path to the manifest JSON file.")
    validate.add_argument(
        "--json", action="store_true", help="Emit the normalised manifest as JSON."
    )
    validate.set_defaults(func=_cmd_validate)

    runner = group.add_parser("run", help="Run a .wasm tool under a capability manifest.")
    runner.add_argument("tool", help="Path to the tool's .wasm module.")
    runner.add_argument("--manifest", required=True, help="Path to the manifest JSON file.")
    runner.add_argument("--input", default=None, help="Path to an input file passed to the tool.")
    runner.add_argument(
        "--entrypoint", default=DEFAULT_ENTRYPOINT, help="Exported function to call."
    )
    runner.add_argument(
        "--approve", action="store_true", help="Confirm the capability grant (required to run)."
    )
    runner.add_argument("--json", action="store_true", help="Emit the run receipt as JSON.")
    runner.set_defaults(func=_cmd_run)
