# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — commercial licence certificate generator regressions

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "tools" / "generate_commercial_certificate.py"
INTERNAL_HEADING = "### Issuing notes (internal, not printed on the certificate)"
TEMPLATE = """# Internal template preamble

Do not print this preamble.

## Commercial Licence Certificate

ID={{CERT_ID}}
ISSUED={{ISSUE_DATE}}
ORG={{LICENSEE_ORG}}
ADDRESS={{LICENSEE_ADDRESS}}
CONTACT={{LICENSEE_CONTACT}}
TYPE={{LICENCE_TYPE}}
VERSIONS={{COVERED_VERSIONS}}
SCOPE={{SCOPE}}
SEATS={{SEATS_OR_UNLIMITED}}
START={{TERM_START}}
END={{TERM_END}}
SUPPORT={{SUPPORT_TIER}}
AGREEMENT={{AGREEMENT_REF}}
AUTH={{OPTIONAL_SIGNATURE_OR_HASH}}

### Issuing notes (internal, not printed on the certificate)
private operator note
"""


def _args(template: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        str(GENERATOR),
        "--template",
        str(template),
        "--cert-id",
        "SC-CL-2026-0001",
        "--issue-date",
        "2026-07-20",
        "--licensee-org",
        r"Example\1 AG",
        "--licensee-address",
        "Bahnhofstrasse 1, 8001 Zürich, Switzerland",
        "--licensee-contact",
        "legal@example.com",
        "--licence-type",
        "Organisation Licence",
        "--covered-versions",
        "0.x and 1.x",
        "--scope",
        "internal use",
        "--seats",
        "unlimited",
        "--term-start",
        "2026-07-20",
        "--term-end",
        "2027-07-20",
        *extra,
    ]


def _run(template: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _args(template, *extra),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_preserves_backslashes_and_excludes_internal_notes(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    template.write_text(TEMPLATE, encoding="utf-8")

    result = _run(template)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("## Commercial Licence Certificate\n")
    assert "Internal template preamble" not in result.stdout
    assert "Do not print this preamble" not in result.stdout
    assert r"ORG=Example\1 AG" in result.stdout
    assert INTERNAL_HEADING not in result.stdout
    assert "private operator note" not in result.stdout
    assert "{{" not in result.stdout
    assert "No separate cryptographic signature" in result.stdout


def test_cli_rejects_unknown_template_placeholder(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    template.write_text(
        TEMPLATE.replace(INTERNAL_HEADING, "UNKNOWN={{NEW_FIELD}}\n\n" + INTERNAL_HEADING),
        encoding="utf-8",
    )

    result = _run(template)

    assert result.returncode == 2
    assert "unknown placeholders: NEW_FIELD" in result.stderr


def test_cli_rejects_invalid_term_order(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    template.write_text(TEMPLATE, encoding="utf-8")
    args = _args(template)
    args[args.index("2026-07-20", args.index("--term-start"))] = "2028-01-01"

    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "term start must not be after term end" in result.stderr


def test_cli_refuses_overwrite_unless_force_is_explicit(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    output = tmp_path / "certificate.md"
    template.write_text(TEMPLATE, encoding="utf-8")
    output.write_text("preserve me\n", encoding="utf-8")

    refused = _run(template, "--output", str(output))

    assert refused.returncode == 2
    assert output.read_text(encoding="utf-8") == "preserve me\n"

    replaced = _run(template, "--output", str(output), "--force")

    assert replaced.returncode == 0, replaced.stderr
    assert output.read_text(encoding="utf-8").startswith("## Commercial Licence Certificate\n")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_cli_force_does_not_follow_output_symlinks(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    target = tmp_path / "existing.md"
    output = tmp_path / "certificate.md"
    template.write_text(TEMPLATE, encoding="utf-8")
    target.write_text("preserve target\n", encoding="utf-8")
    output.symlink_to(target)

    result = _run(template, "--output", str(output), "--force")

    assert result.returncode == 2
    assert target.read_text(encoding="utf-8") == "preserve target\n"


def test_cli_reports_missing_template_without_traceback(tmp_path: Path) -> None:
    template = tmp_path / "missing.md"

    result = _run(template)

    assert result.returncode == 2
    assert "Certificate template not found" in result.stderr
    assert "Traceback" not in result.stderr
