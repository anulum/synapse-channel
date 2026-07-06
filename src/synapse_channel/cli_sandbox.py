# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse sandbox` CLI: validate a manifest, run a tool capability-limited
"""``synapse sandbox`` — validate a manifest, pre-flight a tool, and run it capability-limited.

Three operator-facing verbs over the sandbox. ``validate`` loads a capability manifest and
reports the normalised, deny-by-default grants it declares — a dry check of the policy
before anything runs; with ``--check-paths`` it also pre-flights each filesystem grant's host
path against the live filesystem, exactly as the runner would, so an operator sees a symlink
redirect or a missing directory before an approved run refuses it. ``test`` pre-flights a
``.wasm`` tool against its manifest *without
running it*: it compiles the module, checks the entrypoint is exported, and confirms the
module matches its manifest digest, spending no fuel — a cheap gate before an approved run.
``run`` executes a ``.wasm`` tool under that manifest: it binds the manifest to the exact
module by content digest (a swapped module is refused), requires an explicit ``--approve``
so a capability-bearing run is always an operator decision, and prints the bounded run
receipt. The WASM runtime lives behind the optional ``[wasm]`` extra; ``test`` and ``run``
report the install hint when it is absent rather than failing obscurely.

Exit codes: ``0`` success; ``2`` the command could not proceed (unreadable manifest or
tool, a refused run, or the missing ``[wasm]`` extra); ``test`` additionally returns ``1``
when the pre-flight completed but the tool is not ready to run (invalid module, missing
entrypoint, or a digest that does not match its manifest), and ``validate --check-paths``
returns ``1`` when the manifest is valid but a filesystem grant's host path would be refused
here (a symlink redirect or a missing directory).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from synapse_channel.core.sandbox_paths import PreopenCheck, check_preopen_host
from synapse_channel.core.sandbox_policy import (
    CapabilityManifest,
    FilesystemGrant,
    SandboxManifestError,
    SandboxRequest,
    authorise,
    manifest_from_dict,
)
from synapse_channel.core.sandbox_receipt import PreflightReport, RunReceipt, digest_bytes
from synapse_channel.core.wasm_sandbox import (
    DEFAULT_ENTRYPOINT,
    preflight_sandboxed,
    run_sandboxed,
)

Runner = Callable[..., RunReceipt]
Attestor = Callable[[Path, RunReceipt], None]


def _attest_run(db_path: Path, receipt: RunReceipt) -> None:
    """Append a run receipt to a durable event store as a sandbox attestation."""
    from synapse_channel.core.journal import record_sandbox_run
    from synapse_channel.core.persistence import EventStore

    store = EventStore(db_path)
    try:
        record_sandbox_run(store, dict(receipt))
    finally:
        store.close()


Preflighter = Callable[..., PreflightReport]


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
    """Validate a capability manifest and print its normalised, deny-by-default grants.

    With ``--check-paths`` it additionally pre-flights each filesystem grant's host path against
    the live filesystem — the same resolution the runner performs before a run — and returns
    ``1`` when the manifest is structurally valid but a host path would be refused here (a
    symlink redirect or a missing directory), leaving ``0`` for a manifest whose grants all
    resolve and ``2`` for an unreadable or malformed manifest. Without the flag the host paths
    are left untouched, so a manifest authored off-target still validates.
    """
    try:
        manifest = _load_manifest(args.manifest)
    except SandboxManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not args.check_paths:
        _print_manifest(manifest, json_out=args.json)
        return 0
    checks = [check_preopen_host(grant.host_path) for grant in manifest.filesystem]
    _print_validation(manifest, checks, json_out=args.json)
    return 0 if all(check.ok for check in checks) else 1


def _print_manifest(manifest: CapabilityManifest, *, json_out: bool) -> None:
    """Print a validated manifest, as JSON or a readable one-line grant summary."""
    if json_out:
        print(json.dumps(manifest.to_dict(), indent=2))
        return
    print(
        f"manifest for '{manifest.tool_id}' is valid: "
        f"{len(manifest.filesystem)} filesystem, {len(manifest.network)} network grant(s), "
        f"fuel {manifest.resources.fuel}, memory {manifest.resources.memory_bytes} bytes"
    )


def _check_to_dict(grant: FilesystemGrant, check: PreopenCheck) -> dict[str, object]:
    """Render one filesystem grant and its host-path pre-flight outcome as a mapping."""
    return {
        "host_path": grant.host_path,
        "guest_path": grant.guest_path,
        "write": grant.write,
        "ok": check.ok,
        "resolved": check.resolved,
        "reason": check.reason,
    }


def _print_validation(
    manifest: CapabilityManifest,
    checks: Sequence[PreopenCheck],
    *,
    json_out: bool,
) -> None:
    """Print a manifest alongside the host-path pre-flight for each filesystem grant."""
    if json_out:
        print(
            json.dumps(
                {
                    "manifest": manifest.to_dict(),
                    "host_paths": [
                        _check_to_dict(grant, check)
                        for grant, check in zip(manifest.filesystem, checks, strict=True)
                    ],
                    "all_paths_ok": all(check.ok for check in checks),
                },
                indent=2,
            )
        )
        return
    _print_manifest(manifest, json_out=False)
    if not checks:
        print("host paths: none to pre-flight (no filesystem grants)")
        return
    print("host paths (pre-flight against the live filesystem):")
    for grant, check in zip(manifest.filesystem, checks, strict=True):
        if check.ok:
            print(f"  OK       {grant.host_path!r} -> {check.resolved!r} ({grant.perms()})")
        else:
            print(f"  REFUSED  {grant.host_path!r}: {check.reason}")


def _cmd_test(args: argparse.Namespace, *, preflight: Preflighter = preflight_sandboxed) -> int:
    """Pre-flight a tool against its manifest without running it; report readiness.

    Returns ``0`` when the tool is ready to run, ``1`` when the pre-flight completed but the
    tool is not ready (invalid module, missing entrypoint, or digest mismatch), and ``2``
    when the pre-flight could not be performed (unreadable files or the missing extra).
    """
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
    try:
        report = preflight(manifest, wasm_bytes, entrypoint=args.entrypoint)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_preflight(report, json_out=args.json)
    return 0 if report["ok"] else 1


def _yes_no(value: bool) -> str:
    """Render a boolean as a readable ``yes``/``no``."""
    return "yes" if value else "no"


def _print_preflight(report: PreflightReport, *, json_out: bool) -> None:
    """Print a preflight report, as JSON or a readable readiness summary."""
    if json_out:
        print(json.dumps(report, indent=2))
        return
    status = "ready to run" if report["ok"] else "NOT ready"
    print(f"preflight for '{report['tool_id']}': {status}")
    print(f"  module valid: {_yes_no(report['module_valid'])}")
    print(
        f"  entrypoint '{report['entrypoint']}' exported: {_yes_no(report['entrypoint_exported'])}"
    )
    print(f"  digest matches manifest: {_yes_no(report['digest_matches'])}")
    print(f"  exported functions: {', '.join(report['exported_functions']) or '(none)'}")
    if report["reason"]:
        print(f"  reason: {report['reason']}")


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


def _cmd_run(
    args: argparse.Namespace,
    *,
    runner: Runner = run_sandboxed,
    attestor: Attestor = _attest_run,
) -> int:
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
    if args.attest is not None:
        try:
            attestor(Path(args.attest).expanduser(), receipt)
        except (OSError, sqlite3.Error) as exc:
            print(f"ran the tool but could not attest it: {exc}", file=sys.stderr)
            return 2
        print(f"attested to {args.attest}")
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
    validate.add_argument(
        "--check-paths",
        action="store_true",
        help="Pre-flight each filesystem grant's host path against the live filesystem "
        "(the same resolution the runner performs); exit 1 if any would be refused here.",
    )
    validate.set_defaults(func=_cmd_validate)

    tester = group.add_parser(
        "test",
        help="Pre-flight a .wasm tool against its manifest without running it.",
    )
    tester.add_argument("tool", help="Path to the tool's .wasm module.")
    tester.add_argument("--manifest", required=True, help="Path to the manifest JSON file.")
    tester.add_argument(
        "--entrypoint", default=DEFAULT_ENTRYPOINT, help="Exported function to check for."
    )
    tester.add_argument("--json", action="store_true", help="Emit the preflight report as JSON.")
    tester.set_defaults(func=_cmd_test)

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
    runner.add_argument(
        "--attest",
        default=None,
        metavar="DB",
        help="Append the run receipt to this durable event store as an audit "
        "attestation (query it later with `synapse event-query --kind sandbox_run`).",
    )
    runner.add_argument("--json", action="store_true", help="Emit the run receipt as JSON.")
    runner.set_defaults(func=_cmd_run)
