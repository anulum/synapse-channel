# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — contract conflicts are found from exact versions in real repositories
"""Branches that share no file, yet one breaks what the other was built against.

Every case is a real git repository with real branches. A conflict is found
only from the bytes of the spec and the digest a dependent records.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zlib
from pathlib import Path

import pytest

from synapse_channel.git.contract_conflicts import (
    Contract,
    ContractError,
    Dependent,
    changed_paths,
    check,
    digest,
    parse_manifest,
    read_at,
    recorded,
    resolve,
)

CLIENT = {"kind": "client", "path": "clients/python/", "record": "clients/python/CONTRACT"}
DEPLOYMENT = {"kind": "deployment", "path": "deploy/service.yaml", "record": "deploy/CONTRACT"}
MANIFEST = json.dumps(
    {
        "contracts": [
            {"name": "public-api", "spec": "api/openapi.yaml", "dependents": [CLIENT, DEPLOYMENT]}
        ]
    },
    indent=2,
)
SPEC_V1 = b"openapi: 3.1.0\npaths:\n  /ping: {}\n"
SPEC_V2 = b"openapi: 3.1.0\npaths:\n  /ping: {}\n  /pong: {}\n"


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` with an isolated identity and return its output."""
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=environment
    ).stdout


def _record(spec: bytes) -> str:
    """Return the record line a dependent built against ``spec`` holds."""
    return f"public-api sha256:{hashlib.sha256(spec).hexdigest()}\n"


def _commit(repo: Path, files: dict[str, str | bytes], message: str) -> None:
    """Write ``files`` and commit them."""
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", message)


def _repo(tmp_path: Path) -> Path:
    """Return a repository whose main holds the contract and both dependents at v1."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _commit(
        repo,
        {
            "contracts.json": MANIFEST,
            "api/openapi.yaml": SPEC_V1,
            "clients/python/client.py": "VERSION = 1\n",
            "clients/python/CONTRACT": _record(SPEC_V1),
            "deploy/service.yaml": "image: v1\n",
            "deploy/CONTRACT": _record(SPEC_V1),
        },
        "base",
    )
    return repo


def _branch(repo: Path, name: str, files: dict[str, str | bytes]) -> None:
    """Create ``name`` from main with one commit of ``files``."""
    _git(repo, "checkout", "-q", "-b", name, "main")
    _commit(repo, files, name)
    _git(repo, "checkout", "-q", "main")


def test_a_client_regenerated_against_the_old_contract_conflicts(tmp_path: Path) -> None:
    """Branch A changes the API; branch B regenerates the client against v1."""
    repo = _repo(tmp_path)
    _branch(
        repo,
        "api",
        {
            "api/openapi.yaml": SPEC_V2,
            "clients/python/CONTRACT": _record(SPEC_V2),
            "deploy/CONTRACT": _record(SPEC_V2),
        },
    )
    _branch(repo, "client", {"clients/python/client.py": "VERSION = 2\n"})

    conflicts = list(check(repo, "main", ["api", "client"]).conflicts)

    assert [conflict.kind for conflict in conflicts] == ["stale_dependent"]
    assert conflicts[0].to_json() == {
        "kind": "stale_dependent",
        "contract": "public-api",
        "evidence": {
            "changes_contract": "api",
            "changes_contract_oid": resolve(repo, "api"),
            "changes_dependent": "client",
            "changes_dependent_oid": resolve(repo, "client"),
            "dependent": "clients/python/",
            "record": "clients/python/CONTRACT",
            "recorded": digest(SPEC_V1),
            "spec_digest": digest(SPEC_V2),
        },
    }


def test_a_client_regenerated_against_the_new_contract_does_not(tmp_path: Path) -> None:
    """The other branch records the new digest: built against the same version."""
    repo = _repo(tmp_path)
    _branch(
        repo,
        "api",
        {
            "api/openapi.yaml": SPEC_V2,
            "clients/python/CONTRACT": _record(SPEC_V2),
            "deploy/CONTRACT": _record(SPEC_V2),
        },
    )
    _branch(
        repo,
        "client",
        {"clients/python/client.py": "VERSION = 2\n", "clients/python/CONTRACT": _record(SPEC_V2)},
    )

    assert check(repo, "main", ["api", "client"]).conflicts == ()


def test_a_contract_change_without_regeneration_is_a_record_mismatch(tmp_path: Path) -> None:
    """Changing the spec while a dependent still records v1 is evidence of a forgotten step."""
    repo = _repo(tmp_path)
    _branch(repo, "api", {"api/openapi.yaml": SPEC_V2, "clients/python/CONTRACT": _record(SPEC_V2)})

    conflicts = list(check(repo, "main", ["api"]).conflicts)

    assert [(c.kind, c.evidence["dependent"]) for c in conflicts] == [
        ("record_mismatch", "deploy/service.yaml")
    ]
    assert conflicts[0].evidence["recorded"] == digest(SPEC_V1)


def test_unrelated_work_and_untouched_dependents_are_not_conflicts(tmp_path: Path) -> None:
    """A branch that touches no dependent, or a spec that did not change, conflicts with nothing."""
    repo = _repo(tmp_path)
    _branch(
        repo,
        "api",
        {
            "api/openapi.yaml": SPEC_V2,
            "clients/python/CONTRACT": _record(SPEC_V2),
            "deploy/CONTRACT": _record(SPEC_V2),
        },
    )
    _branch(repo, "docs", {"README.md": "docs\n"})
    _branch(repo, "client", {"clients/python/client.py": "VERSION = 3\n"})

    assert check(repo, "main", ["api", "docs"]).conflicts == ()
    assert check(repo, "main", ["docs", "client"]).conflicts == ()


def test_a_whitespace_only_spec_change_is_flagged_as_byte_identity_requires(
    tmp_path: Path,
) -> None:
    """Bytes changed, meaning did not: flagged on purpose, counted as a false block."""
    repo = _repo(tmp_path)
    spaced = SPEC_V1 + b"\n"
    _branch(
        repo,
        "spaces",
        {
            "api/openapi.yaml": spaced,
            "clients/python/CONTRACT": _record(spaced),
            "deploy/CONTRACT": _record(spaced),
        },
    )
    _branch(repo, "deploy", {"deploy/service.yaml": "image: v2\n"})

    assert [c.kind for c in check(repo, "main", ["spaces", "deploy"]).conflicts] == [
        "stale_dependent"
    ]


def test_a_missing_or_foreign_record_is_reported_as_such(tmp_path: Path) -> None:
    """An absent record and a record naming another contract are evidence, not a pass."""
    repo = _repo(tmp_path)
    _branch(
        repo,
        "api",
        {
            "api/openapi.yaml": SPEC_V2,
            "clients/python/CONTRACT": "other-api " + digest(SPEC_V2) + "\n",
        },
    )
    _git(repo, "checkout", "-q", "api")
    _git(repo, "rm", "-q", "deploy/CONTRACT")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "drop")
    _git(repo, "checkout", "-q", "main")
    contract = parse_manifest(MANIFEST)[0]

    api = resolve(repo, "api")
    assert recorded(repo, api, contract, contract.dependents[0]) == "unreadable"
    assert recorded(repo, api, contract, contract.dependents[1]) == "absent"
    assert {c.evidence["recorded"] for c in check(repo, "main", ["api"]).conflicts} == {
        "unreadable",
        "absent",
    }


def test_a_repository_without_a_manifest_has_no_contracts(tmp_path: Path) -> None:
    """No contracts.json at the base means there is nothing to check."""
    repo = tmp_path / "plain"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _commit(repo, {"a.txt": "a\n"}, "a")

    report = check(repo, "main", ["main"])
    assert (report.configured, report.conflicts, report.manifest) == (False, (), "absent")
    assert digest(None) == "absent"


def test_unknown_revisions_and_incomparable_ranges_are_refused(tmp_path: Path) -> None:
    """A revision that is not a commit is refused rather than read as empty."""
    repo = _repo(tmp_path)

    main_oid = resolve(repo, "main")
    with pytest.raises(ContractError, match="is not a commit"):
        resolve(repo, "no-such-branch")
    with pytest.raises(ContractError, match="is not a commit"):
        check(repo, "main", ["no-such-branch"])
    assert changed_paths(repo, main_oid, main_oid) == ()
    assert read_at(repo, main_oid, "api/missing.yaml") is None
    assert read_at(repo, main_oid, "missing/dir/file.yaml") is None
    with pytest.raises(ContractError, match="is not a regular file"):
        read_at(repo, main_oid, "api")


def test_a_dependent_path_matches_only_itself_or_what_lies_inside_it(tmp_path: Path) -> None:
    """A file named like the prefix's sibling is not inside the dependent."""
    repo = _repo(tmp_path)
    _branch(
        repo,
        "api",
        {
            "api/openapi.yaml": SPEC_V2,
            "clients/python/CONTRACT": _record(SPEC_V2),
            "deploy/CONTRACT": _record(SPEC_V2),
        },
    )
    _branch(repo, "sibling", {"clients/python-extra/x.py": "x\n", "deploy/service.yaml.bak": "b\n"})
    _branch(repo, "exact", {"deploy/service.yaml": "image: v3\n"})

    assert check(repo, "main", ["api", "sibling"]).conflicts == ()
    assert [c.evidence["dependent"] for c in check(repo, "main", ["api", "exact"]).conflicts] == [
        "deploy/service.yaml"
    ]


def _manifest(*contracts: object) -> str:
    """Return a manifest text listing ``contracts``."""
    return json.dumps({"contracts": list(contracts)})


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{", "is not JSON"),
        ('{"contracts": [], "contracts": []}', "repeats a member name"),
        ("[]", 'only a "contracts" list'),
        ('{"other": []}', 'only a "contracts" list'),
        ('{"contracts": 1}', 'only a "contracts" list'),
        (_manifest({"name": "a"}), "name, spec and optional dependents"),
        (_manifest(1), "name, spec and optional dependents"),
        (_manifest({"name": "-bad", "spec": "a"}), "contract name is"),
        (_manifest({"name": "a", "spec": "a", "dependents": 1}), "dependents are a list"),
        (_manifest({"name": "a", "spec": "/abs"}), "a contract's spec"),
        (_manifest({"name": "a", "spec": "a/../b"}), "a contract's spec"),
        (_manifest({"name": "a", "spec": "a\\b"}), "a contract's spec"),
        (_manifest({"name": "a", "spec": ""}), "a contract's spec"),
        (_manifest({"name": "a", "spec": 3}), "a contract's spec"),
        (_manifest({"name": "a", "spec": "spec.yaml/"}), "a contract's spec"),
        (_manifest({"name": "a", "spec": "spec.yaml//"}), "a contract's spec"),
        (_manifest({"name": "a", "spec": "a\u0000b"}), "a contract's spec"),
        ('{"contracts": [{"name": "a", "spec": "api/\\ud800.yaml"}]}', "not valid UTF-8"),
        (
            _manifest(
                {
                    "name": "a",
                    "spec": "a",
                    "dependents": [{"kind": "client", "path": "clients//", "record": "r"}],
                }
            ),
            "a dependent's path",
        ),
        (
            _manifest(
                {
                    "name": "a",
                    "spec": "a",
                    "dependents": [{"kind": "client", "path": "clients/", "record": "r/"}],
                }
            ),
            "a dependent's record",
        ),
        (
            _manifest(
                {
                    "name": "a",
                    "spec": "a",
                    "dependents": [{"kind": "client", "path": "/", "record": "r"}],
                }
            ),
            "a dependent's path",
        ),
        (_manifest({"name": "a", "spec": "a", "dependents": [{"kind": "client"}]}), "exactly"),
        (_manifest({"name": "a", "spec": "a", "dependents": [1]}), "exactly kind, path"),
        (
            _manifest(
                {
                    "name": "a",
                    "spec": "a",
                    "dependents": [{"kind": "lib", "path": "p", "record": "r"}],
                }
            ),
            "kind is one of",
        ),
        (
            _manifest(
                {
                    "name": "a",
                    "spec": "a",
                    "dependents": [{"kind": ["client"], "path": "p", "record": "r"}],
                }
            ),
            "kind is one of",
        ),
        (
            _manifest(
                {"name": "a", "spec": "a", "dependents": [{"kind": {}, "path": "p", "record": "r"}]}
            ),
            "kind is one of",
        ),
        (_manifest({"name": "a", "spec": "a"}, {"name": "a", "spec": "b"}), "unique"),
    ],
)
def test_malformed_manifests_are_refused(text: str, message: str) -> None:
    """Each malformed manifest is refused with its reason."""
    with pytest.raises(ContractError, match=message):
        parse_manifest(text)


def test_a_well_formed_manifest_is_read_exactly() -> None:
    """The manifest's contracts and dependents come back as declared."""
    assert parse_manifest(MANIFEST) == (
        Contract(
            "public-api",
            "api/openapi.yaml",
            (
                Dependent("client", "clients/python/", "clients/python/CONTRACT"),
                Dependent("deployment", "deploy/service.yaml", "deploy/CONTRACT"),
            ),
        ),
    )
    assert parse_manifest(_manifest()) == ()


def test_the_cli_reports_by_exit_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``synapse contracts check`` exits 1 with evidence, 0 without, 2 on an unknown branch."""
    from synapse_channel.cli import main

    repo = _repo(tmp_path)
    _branch(
        repo,
        "api",
        {
            "api/openapi.yaml": SPEC_V2,
            "clients/python/CONTRACT": _record(SPEC_V2),
            "deploy/CONTRACT": _record(SPEC_V2),
        },
    )
    _branch(repo, "client", {"clients/python/client.py": "VERSION = 2\n"})
    command = ["contracts", "check", "--repo", str(repo), "--base", "main"]

    assert main([*command, "--branch", "api", "--branch", "client"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [c["kind"] for c in report["conflicts"]] == ["stale_dependent"]
    assert report["advisory"] == "evidence only; nothing was claimed, released or blocked"
    assert main([*command, "--branch", "api"]) == 0
    capsys.readouterr()
    assert main([*command, "--branch", "missing"]) == 2
    assert "is not a commit" in capsys.readouterr().err


def test_a_base_that_is_not_an_ancestor_is_refused(tmp_path: Path) -> None:
    """The report's base must be the exact common ancestor; a later base is refused."""
    repo = _repo(tmp_path)
    _branch(repo, "api", {"api/openapi.yaml": SPEC_V2})
    _commit(repo, {"README.md": "moved on\n"}, "main moves")

    with pytest.raises(ContractError, match="is not an ancestor"):
        check(repo, "main", ["api"])


def test_a_branch_that_changes_or_drops_a_declaration_is_refused(tmp_path: Path) -> None:
    """Dropping a dependent from contracts.json must not look like the absence of a conflict."""
    repo = _repo(tmp_path)
    dropped = _manifest({"name": "public-api", "spec": "api/openapi.yaml", "dependents": [CLIENT]})
    _branch(repo, "drop", {"contracts.json": dropped})

    with pytest.raises(ContractError, match="changes contracts.json"):
        check(repo, "main", ["drop"])


def test_each_report_freezes_the_commits_it_read(tmp_path: Path) -> None:
    """A branch that moves after one report is read at its new commit by the next one."""
    repo = _repo(tmp_path)
    _branch(repo, "client", {"clients/python/client.py": "VERSION = 2\n"})
    first = check(repo, "main", ["client"])
    _git(repo, "checkout", "-q", "client")
    _commit(repo, {"clients/python/client.py": "VERSION = 3\n"}, "client moves")
    _git(repo, "checkout", "-q", "main")
    second = check(repo, "main", ["client"])

    assert first.branches["client"] != second.branches["client"]
    assert second.branches["client"] == resolve(repo, "client")
    assert first.to_json()["base_oid"] == resolve(repo, "main")
    assert first.to_json()["manifest_digest"] == digest(MANIFEST.encode())


def test_the_cli_says_not_configured_rather_than_no_conflict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base without contracts.json exits 3 with configured false, never 0."""
    from synapse_channel.cli import main

    repo = tmp_path / "plain"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _commit(repo, {"a.txt": "a\n"}, "a")

    assert main(["contracts", "check", "--repo", str(repo), "--branch", "main"]) == 3
    assert json.loads(capsys.readouterr().out)["configured"] is False


def test_a_damaged_tree_is_refused_rather_than_read_as_unchanged(tmp_path: Path) -> None:
    """With a tree object missing, the comparison fails loudly instead of reporting no change."""
    repo = _repo(tmp_path)
    _branch(repo, "client", {"clients/python/client.py": "VERSION = 2\n"})
    tree = _git(repo, "rev-parse", "client:clients/python").strip()
    (repo / ".git" / "objects" / tree[:2] / tree[2:]).unlink()

    with pytest.raises(ContractError, match="cannot compare"):
        changed_paths(repo, resolve(repo, "main"), resolve(repo, "client"))


def _object_file(repo: Path, oid: str) -> Path:
    """Return the loose object file that holds ``oid`` in ``repo``."""
    return repo / ".git" / "objects" / oid[:2] / oid[2:]


def _cli(repo: Path, *branches: str) -> tuple[int, dict[str, object] | None, str]:
    """Run the public command in a child interpreter; return exit, report and stderr."""
    import sys

    argv = ["contracts", "check", "--repo", str(repo), "--base", "main"]
    for branch in branches:
        argv += ["--branch", branch]
    source = str(Path(__file__).resolve().parents[1] / "src")
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([source, os.environ.get("PYTHONPATH", "")]),
    }
    done = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", *argv],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=environment,
    )
    return done.returncode, json.loads(done.stdout) if done.stdout else None, done.stderr


def test_a_declared_spec_missing_at_the_base_is_refused_not_clean(tmp_path: Path) -> None:
    """A spec the manifest names but the base lacks exits 2, never configured and clean."""
    repo = _repo(tmp_path)
    _git(repo, "rm", "-q", "api/openapi.yaml")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "drop spec")

    with pytest.raises(ContractError, match="is not in the base"):
        check(repo, "main", ["main"])
    code, report, error = _cli(repo, "main")

    assert (code, report) == (2, None)
    assert "api/openapi.yaml of public-api is not in the base" in error
    assert "Traceback" not in error


def test_a_branch_that_deletes_the_spec_reports_it_as_absent(tmp_path: Path) -> None:
    """Deleting the spec on a branch is a change; its dependents record another digest."""
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "drop", "main")
    _git(repo, "rm", "-q", "api/openapi.yaml")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "drop spec")
    _git(repo, "checkout", "-q", "main")

    conflicts = check(repo, "main", ["drop"]).conflicts

    assert {(c.kind, c.evidence["spec_digest"]) for c in conflicts} == {
        ("record_mismatch", "absent")
    }


@pytest.mark.parametrize("target", ["api/openapi.yaml", "contracts.json", "deploy/CONTRACT"])
def test_a_missing_object_is_refused_rather_than_read_as_absent(
    tmp_path: Path, target: str
) -> None:
    """With a declared file's real blob gone, the public command exits 2 with the reason.

    The branch changes the spec, so the base spec, the manifest and the
    untouched deployment record are all read.
    """
    repo = _repo(tmp_path)
    _branch(repo, "api", {"api/openapi.yaml": SPEC_V2, "clients/python/CONTRACT": _record(SPEC_V2)})
    blob = _git(repo, "rev-parse", f"main:{target}").strip()
    _object_file(repo, blob).unlink()

    code, report, error = _cli(repo, "api")

    assert (code, report) == (2, None)
    assert f"cannot read {target}" in error and "missing or damaged" in error
    assert "Traceback" not in error


def test_a_blob_whose_bytes_do_not_match_its_id_is_refused(tmp_path: Path) -> None:
    """A loose object rewritten with other bytes under the same name is damage, not a version."""
    repo = _repo(tmp_path)
    blob = _git(repo, "rev-parse", "main:api/openapi.yaml").strip()
    forged = b"openapi: 3.1.0\npaths: {}\n"
    path = _object_file(repo, blob)
    path.chmod(0o644)
    path.write_bytes(zlib.compress(b"blob %d\0" % len(forged) + forged))

    with pytest.raises(ContractError, match="missing or damaged"):
        read_at(repo, resolve(repo, "main"), "api/openapi.yaml")


def test_a_damaged_tree_cannot_be_listed(tmp_path: Path) -> None:
    """With the tree holding a declared file gone, reading it fails loudly."""
    repo = _repo(tmp_path)
    tree = _git(repo, "rev-parse", "main:api").strip()
    _object_file(repo, tree).unlink()

    with pytest.raises(ContractError, match="cannot list api/openapi.yaml"):
        read_at(repo, resolve(repo, "main"), "api/openapi.yaml")


def test_a_symbolic_link_spec_is_not_a_regular_file(tmp_path: Path) -> None:
    """A spec committed as a symbolic link is refused instead of hashing the link text."""
    repo = tmp_path / "linked"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "api").mkdir()
    (repo / "api" / "real.yaml").write_bytes(SPEC_V1)
    (repo / "api" / "openapi.yaml").symlink_to("real.yaml")
    _commit(repo, {"contracts.json": MANIFEST}, "linked spec")

    with pytest.raises(ContractError, match="is not a regular file"):
        check(repo, "main", ["main"])


@pytest.mark.parametrize(
    "kind",
    [["client"], {}],
)
def test_the_cli_refuses_a_malformed_kind_with_exit_2(tmp_path: Path, kind: object) -> None:
    """A kind that is valid JSON but not a string is refused as unusable input, not a finding."""
    repo = _repo(tmp_path)
    broken = _manifest(
        {
            "name": "public-api",
            "spec": "api/openapi.yaml",
            "dependents": [{"kind": kind, "path": "clients/python/", "record": "r"}],
        }
    )
    _commit(repo, {"contracts.json": broken}, "broken kind")

    code, report, error = _cli(repo, "main")

    assert (code, report) == (2, None)
    assert "kind is one of" in error and "Traceback" not in error


def test_the_cli_refuses_a_repeated_trailing_slash(tmp_path: Path) -> None:
    """``spec.yaml//`` is an empty segment and is refused, never checked as clean."""
    repo = _repo(tmp_path)
    _commit(
        repo,
        {"contracts.json": _manifest({"name": "public-api", "spec": "api/openapi.yaml//"})},
        "slashes",
    )

    code, report, error = _cli(repo, "main")

    assert (code, report) == (2, None)
    assert "a contract's spec is a plain repository-relative path" in error


def test_a_sha256_repository_is_read_and_verified(tmp_path: Path) -> None:
    """Object ids of a SHA-256 repository are verified with SHA-256."""
    repo = tmp_path / "wide"
    subprocess.run(
        ["git", "init", "-q", "--object-format=sha256", "-b", "main", str(repo)], check=True
    )
    _commit(repo, {"contracts.json": MANIFEST, "api/openapi.yaml": SPEC_V1}, "base")
    main_oid = resolve(repo, "main")

    assert len(main_oid) == 64
    assert read_at(repo, main_oid, "api/openapi.yaml") == SPEC_V1


def test_the_cli_refuses_an_unpaired_surrogate_path(tmp_path: Path) -> None:
    """An escaped unpaired surrogate is valid JSON but no path; it is refused with exit 2."""
    repo = _repo(tmp_path)
    _commit(
        repo,
        {"contracts.json": '{"contracts": [{"name": "public-api", "spec": "api/\\ud800.yaml"}]}'},
        "surrogate",
    )

    code, report, error = _cli(repo, "main")

    assert (code, report) == (2, None)
    assert "is not valid UTF-8 text" in error and "Traceback" not in error


def test_a_non_ascii_spec_path_is_read_exactly(tmp_path: Path) -> None:
    """A valid Unicode path is read from git's raw bytes, and the public check runs clean."""
    repo = _repo(tmp_path)
    unicode_manifest = _manifest({"name": "public-api", "spec": "api/spéc-ü.yaml"})
    _commit(repo, {"contracts.json": unicode_manifest, "api/spéc-ü.yaml": SPEC_V1}, "unicode")

    assert read_at(repo, resolve(repo, "main"), "api/spéc-ü.yaml") == SPEC_V1
    code, report, error = _cli(repo, "main")
    assert (code, error) == (0, "")
    assert report is not None and report["configured"] is True and report["conflicts"] == []
