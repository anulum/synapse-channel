# Changelog

The full, versioned changelog is maintained in the repository's
[`CHANGELOG.md`](https://github.com/anulum/synapse-channel/blob/main/CHANGELOG.md),
following [Semantic Versioning](https://semver.org/).

Current `0.x` releases do not promise backward compatibility across minor
releases. A deliberate pre-1.0 public API or wire change must update its frozen
contract and include changelog plus migration notes; a wire-incompatible change
also bumps `WIRE_PROTOCOL_VERSION`. The complete contract is in
[API and wire stability](api-stability.md).

Each release entry lists what was added, changed, or removed. Notable milestones
to date include the shared blackboard, atomic handoff, the LLM-free supervisor,
resumable checkpoints, connect authentication, capability cards, and task-class
routing.
