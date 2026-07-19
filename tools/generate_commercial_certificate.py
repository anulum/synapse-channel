#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — commercial licence certificate generator
"""Generate a commercial licence certificate from an operator template.

The emitted certificate excludes the template's internal issuing notes. It
must still be reviewed and signed by the licensor before delivery.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "docs"
    / "internal"
    / "templates"
    / "commercial_license_certificate_template.md"
)
INTERNAL_NOTES_HEADING = "### Issuing notes (internal, not printed on the certificate)"
CERTIFICATE_HEADING = "## Commercial Licence Certificate"
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def _private_opener(path: str, flags: int) -> int:
    """Open a certificate without following symlinks and with mode 0600."""
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)


def _iso_date(value: str, label: str) -> date:
    """Parse one ISO calendar date or raise a user-facing validation error."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO 8601 calendar date: {value!r}") from exc


def _field(value: object, label: str) -> str:
    """Return one bounded single-line certificate field."""
    text = str(value)
    if not text.strip():
        raise ValueError(f"{label} must not be empty")
    if "\n" in text or "\r" in text:
        raise ValueError(f"{label} must be a single line")
    if len(text.encode("utf-8")) > 4096:
        raise ValueError(f"{label} exceeds 4096 UTF-8 bytes")
    return text


def _render_template(template: str, fields: dict[str, str]) -> str:
    """Fill the printable template section and reject schema drift."""
    _preamble, certificate_marker, certificate_and_notes = template.partition(CERTIFICATE_HEADING)
    if not certificate_marker:
        raise ValueError("certificate template lacks the required certificate output boundary")
    printable_body, notes_marker, _internal_notes = certificate_and_notes.partition(
        INTERNAL_NOTES_HEADING
    )
    if not notes_marker:
        raise ValueError("certificate template lacks the required internal-notes output boundary")
    printable = certificate_marker + printable_body

    placeholders = set(PLACEHOLDER_RE.findall(printable))
    unknown = sorted(placeholders - fields.keys())
    missing = sorted(fields.keys() - placeholders)
    if unknown:
        raise ValueError(f"certificate template has unknown placeholders: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"certificate template is missing placeholders: {', '.join(missing)}")

    rendered = PLACEHOLDER_RE.sub(lambda match: fields[match.group(1)], printable)
    if PLACEHOLDER_RE.search(rendered):
        raise ValueError("certificate contains unresolved placeholders")
    return rendered.rstrip() + "\n"


def generate(args: argparse.Namespace) -> str:
    """Fill the certificate template with validated command-line fields."""
    template_path = Path(args.template)
    if not template_path.is_file():
        raise FileNotFoundError(f"Certificate template not found: {template_path}")

    issue_date = _field(args.issue_date or date.today().isoformat(), "issue date")
    term_start = _field(args.term_start, "term start")
    term_end = _field(args.term_end, "term end")
    _iso_date(issue_date, "issue date")
    if _iso_date(term_start, "term start") > _iso_date(term_end, "term end"):
        raise ValueError("term start must not be after term end")

    fields = {
        "CERT_ID": _field(args.cert_id, "certificate ID"),
        "ISSUE_DATE": issue_date,
        "LICENSEE_ORG": _field(args.licensee_org, "licensee organisation"),
        "LICENSEE_ADDRESS": _field(args.licensee_address, "licensee address"),
        "LICENSEE_CONTACT": _field(args.licensee_contact, "licensee contact"),
        "LICENCE_TYPE": _field(args.licence_type, "licence type"),
        "COVERED_VERSIONS": _field(args.covered_versions, "covered versions"),
        "SCOPE": _field(args.scope, "scope"),
        "SEATS_OR_UNLIMITED": _field(args.seats, "seats"),
        "TERM_START": term_start,
        "TERM_END": term_end,
        "SUPPORT_TIER": _field(args.support_tier, "support tier"),
        "AGREEMENT_REF": _field(
            args.agreement_ref or "accompanying commercial agreement",
            "agreement reference",
        ),
        "OPTIONAL_SIGNATURE_OR_HASH": _field(
            args.optional_signature_or_hash
            or "No separate cryptographic signature or content hash is attached.",
            "signature or hash",
        ),
    }
    template = template_path.read_text(encoding="utf-8")
    return _render_template(template, fields)


def main(argv: list[str] | None = None) -> int:
    """Generate one certificate and return a process exit code."""
    parser = argparse.ArgumentParser(
        description="Generate a SYNAPSE CHANNEL commercial licence certificate.",
    )
    parser.add_argument("--cert-id", required=True, help="Certificate ID, e.g. SC-CL-2026-0001")
    parser.add_argument(
        "--issue-date", default=None, help="Issue date (ISO 8601); defaults to today"
    )
    parser.add_argument("--licensee-org", required=True, help="Licensee organisation name")
    parser.add_argument("--licensee-address", required=True, help="Licensee registered address")
    parser.add_argument("--licensee-contact", required=True, help="Licensee contact email")
    parser.add_argument(
        "--licence-type", required=True, help="Licence type, e.g. 'Organisation Licence'"
    )
    parser.add_argument(
        "--covered-versions",
        required=True,
        help="Covered versions, e.g. 'all 0.x and 1.x releases during the term'",
    )
    parser.add_argument("--scope", required=True, help="Permitted scope")
    parser.add_argument("--seats", required=True, help="Seats or 'unlimited'")
    parser.add_argument("--term-start", required=True, help="Term start date (ISO 8601)")
    parser.add_argument("--term-end", required=True, help="Term end date (ISO 8601)")
    parser.add_argument("--support-tier", default="none", help="Support tier")
    parser.add_argument(
        "--agreement-ref", default=None, help="Reference to the accompanying agreement"
    )
    parser.add_argument(
        "--optional-signature-or-hash",
        default=None,
        help="Optional signature or content hash line",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=TEMPLATE_PATH,
        help="Template path; defaults to the local internal operator template",
    )
    parser.add_argument("--output", default="-", help="Output file; '-' prints to stdout")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output file; never enabled by default",
    )
    args = parser.parse_args(argv)

    try:
        certificate = generate(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output == "-":
        print(certificate, end="")
        return 0

    out_path = Path(args.output)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if args.force else "x"
        with open(  # noqa: PTH123 - opener controls permissions and symlink handling.
            out_path,
            mode,
            encoding="utf-8",
            newline="\n",
            opener=_private_opener,
        ) as output:
            output.write(certificate)
        out_path.chmod(0o600)
    except OSError as exc:
        print(f"error: cannot write certificate: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote certificate to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
