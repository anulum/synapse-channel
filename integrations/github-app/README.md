<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE GITHUB APP — managed-layer skeleton operator guide
-->

# SYNAPSE GitHub App skeleton

This independently installable package is the hosting-neutral stage 2 of the
Managed GitHub App plan. It verifies signed pull-request webhooks, authenticates
as a GitHub App installation, reads bounded open-PR file inventories, reuses
SYNAPSE's existing file-scope conflict finder, and creates a completed neutral
Check Run. It does not register, host, deploy, or persist an App.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before integrating it. The document is
the contract: GitHub concerns stay here, the local core stays single-dependency,
and every result remains advisory.

## Render the least-privilege manifest

After a hosting URL is chosen, an operator can inspect the exact registration
manifest without submitting it:

```bash
synapse-github-app-manifest --base-url https://app.example.org
```

The manifest requests only pull-request read and Checks write access, subscribes
only to `pull_request`, and points the webhook and manifest callback at the given
base URL. Do not submit it until the stage 3 host implements the callback and
provides approved secret custody.

## Application service seam

```python
from synapse_github_app import GitHubApi, GitHubAppService

api = GitHubApi()
service = GitHubAppService(
    api=api,
    app_issuer="Iv1.example-client-id",
    private_key_pem=private_key_pem,
    webhook_secret=webhook_secret,
)

result = service.handle(headers=request_headers, body=raw_request_body)
```

The host supplies unmodified request bytes and headers, then maps the returned
result or typed error onto its HTTP framework. The package never binds a socket
or reads secrets from ambient environment variables.

## Stage 2 limits

- pull-request events only;
- 1 MiB webhook and 64-level JSON-depth bounds;
- up to 100 open pull requests and 3,000 changed files per pull request;
- 4 MiB per REST response, no redirects, and HTTPS except explicit loopback test
  mode;
- completed `neutral` checks only; no comments, merge blocking, or code writes;
- no retries or delivery de-duplication until a host and persistence boundary are
  approved.

Incomplete inventories never produce a clean result. A confirmed overlap may be
reported with an explicit incompleteness warning; otherwise evaluation fails
without creating a Check Run.

## Local verification

From this directory, with the root package installed from the same checkout:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy
python -m bandit -q -r src -c pyproject.toml
python -m pytest
python -m build
```

The repository's `github-app` workflow repeats these gates from the hash-locked
development requirements and inspects the built wheel.

The wheel and source distribution carry the repository's full `LICENSE` and
`NOTICE.md`; the package is AGPL-3.0-or-later with a separate commercial licence
available from the contact named in that notice.
