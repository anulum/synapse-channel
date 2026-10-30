# Build identity

`synapse --version` and `synapse doctor` print one artefact:

- the package version;
- the source commit when this module is inside the source checkout whose
  `pyproject.toml` names `synapse-channel`, otherwise `unknown` (an install
  that merely sits inside some other git tree does not report that tree);
- the wheel sha256 when ``direct_url.json`` recorded one; `installed from a
  wheel (digest not recorded)` when the install has ``WHEEL`` metadata but no
  recorded digest; `not a wheel` for an editable or local directory install,
  including one whose packaging metadata also contains ``WHEEL``. A `file:`
  URL is never opened or hashed;
- the declared profile. The package default is `loopback`, printed as
  `declared profile: loopback (package default)`. A known
  `SYNAPSE_QUALIFIED_PROFILE` (`loopback`, `team-secure`, `paranoid`, or
  `secure`) prints that name with source `(environment)`. Any other value
  stays the package default. This is a declaration, not qualification
  evidence: the variable is not bound to a receipt. `doctor --json` records
  the same pair as `declared_profile` and `profile_source`.

When this source checkout is newer than the latest dated release in
`CHANGELOG.md`, or that file still has notes under `[Unreleased]`, the report
says `this build is newer than the published release <version>; see CHANGELOG
[Unreleased]` and `development branch, not yet released`. The version comes
from the changelog heading, not from a typed feature list.

An installed wheel prints no development mark. The mark is read only when
this module lives in the source checkout (`src/synapse_channel` under a tree
whose `pyproject.toml` names `synapse-channel`) and that tree's
`CHANGELOG.md` can be read. A wheel does not ship that changelog beside the
installed module, so there is no published release to compare and the report
does not invent a mark. `pip install synapse-channel` installs the published
release, which does not contain the features named by the development mark.

The public A2A Agent Card carries the package version and those development
marks. It does not carry the source commit, the wheel digest, or the declared
profile. `synapse --version`, `synapse doctor`, and the authenticated MCP
instructions carry the full identity.
