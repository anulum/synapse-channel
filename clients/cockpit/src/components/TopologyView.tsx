// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the fleet topology panel: agents, held tasks, conflict ties

import { memo } from "react";

import type { BranchConflictView, ClaimView } from "../lib/claims";
import type { FederationState } from "../lib/federation";
import { layoutFederation, layoutTopology, ROW_PITCH } from "../lib/topology";

/** Horizontal positions of the two columns in the SVG's 640-unit width. */
const AGENT_X = 200;
const TASK_X = 440;

function lastSegment(name: string): string {
  const slash = name.lastIndexOf("/");
  return slash === -1 ? name : name.slice(slash + 1);
}

interface TopologyViewProps {
  /** Claim rows as the claims lib ranked them. */
  readonly claims: readonly ClaimView[];
  /** The hub's advisory branch conflicts. */
  readonly conflicts: readonly BranchConflictView[];
  /** Live roster size, for the stated idle remainder. */
  readonly liveAgentCount: number;
  /** Whether a snapshot has arrived at all. */
  readonly connected: boolean;
  /** The federation posture feed, for the peering band. */
  readonly federation?: FederationState;
}

function TopologyViewComponent({
  claims,
  conflicts,
  liveAgentCount,
  connected,
  federation,
}: TopologyViewProps): JSX.Element {
  const layout = layoutTopology(claims, conflicts, liveAgentCount);
  const posture = federation?.data ?? null;
  const band = layoutFederation(posture?.peerings ?? []);

  return (
    <section className="panel" aria-label="Fleet topology">
      <div className="panel__head">
        <span>Topology</span>
        <span className="panel__count">{layout.claims.length}</span>
        <span className="panel__sub">
          {layout.idleAgents > 0
            ? `claim edges · ${layout.idleAgents} idle agents not drawn`
            : "claim edges"}
        </span>
      </div>
      <div className="panel__body panel__body--flush" tabIndex={0}>
        {!connected ? (
          <p className="panel__placeholder panel__placeholder--padded">Waiting for the hub.</p>
        ) : layout.agents.length === 0 ? (
          <p className="panel__placeholder panel__placeholder--padded">
            No file scopes are held right now — there is no topology to draw.
          </p>
        ) : (
          <svg
            className="topology"
            viewBox={`0 0 640 ${layout.height}`}
            role="img"
            aria-label="Agents on the left, the tasks they hold on the right; a line per claim, a red tie per conflict"
          >
            {layout.claims.map((edge) => (
              <line
                key={`${edge.agent}:${edge.taskId}`}
                className={`topology__edge topology__edge--${edge.state}`}
                x1={AGENT_X + 6}
                y1={edge.fromY}
                x2={TASK_X - 6}
                y2={edge.toY}
              />
            ))}
            {layout.conflicts.map((tie) => (
              <path
                key={`${tie.a}:${tie.b}`}
                className="topology__tie"
                d={`M ${AGENT_X + 6} ${tie.fromY} C ${AGENT_X + 40} ${tie.fromY}, ${AGENT_X + 40} ${tie.toY}, ${AGENT_X + 6} ${tie.toY}`}
              />
            ))}
            {layout.agents.map((agent) => (
              <g key={agent.name}>
                <circle
                  className={`topology__node${agent.inConflict ? " topology__node--conflict" : ""}`}
                  cx={AGENT_X}
                  cy={agent.y}
                  r={4}
                />
                <text className="topology__label topology__label--agent" x={AGENT_X - 10} y={agent.y + 3}>
                  <title>{agent.name}</title>
                  {lastSegment(agent.name)}
                </text>
              </g>
            ))}
            {layout.tasks.map((task) => (
              <g key={task.taskId}>
                <rect
                  className={`topology__node topology__node--task${task.stale ? " topology__node--stale" : ""}`}
                  x={TASK_X - 4}
                  y={task.y - 4}
                  width={8}
                  height={8}
                />
                <text className="topology__label" x={TASK_X + 10} y={task.y + 3}>
                  <title>{task.taskId}</title>
                  {task.taskId}
                </text>
              </g>
            ))}
            <text className="topology__column" x={AGENT_X} y={ROW_PITCH / 2}>
              agents
            </text>
            <text className="topology__column" x={TASK_X} y={ROW_PITCH / 2}>
              held tasks
            </text>
          </svg>
        )}
        <div className="topology-federation">
          <span className="topology-federation__head">federation</span>
          {federation === undefined || federation.status === "absent" ? (
            <p className="panel__placeholder">
              Posture surface not served (/federation.json) — the peering band
              appears when the operator passes a federation store.
            </p>
          ) : posture === null ? (
            <p className="panel__placeholder">
              {federation.status === "error"
                ? `Federation feed failed: ${federation.error ?? "unknown"}`
                : "Waiting for the federation feed."}
            </p>
          ) : band.peers.length === 0 ? (
            <p className="panel__placeholder">No peerings imported — a single-hub posture.</p>
          ) : (
            <svg
              className="topology"
              viewBox={`0 0 640 ${band.height}`}
              role="img"
              aria-label="This hub on the left, imported peer domains on the right; edge colour is the peering's lifecycle state"
            >
              {band.peers.map((peer) => (
                <line
                  key={`edge:${peer.domain}`}
                  className={`topology__peer-edge topology__peer-edge--${peer.state}`}
                  x1={AGENT_X + 6}
                  y1={band.hubY}
                  x2={TASK_X - 6}
                  y2={peer.y}
                />
              ))}
              <circle className="topology__node topology__node--hub" cx={AGENT_X} cy={band.hubY} r={5} />
              <text className="topology__label topology__label--agent" x={AGENT_X - 10} y={band.hubY + 3}>
                this hub
              </text>
              {band.peers.map((peer) => (
                <g key={peer.domain}>
                  <circle
                    className={`topology__node topology__node--peer topology__node--peer-${peer.state}`}
                    cx={TASK_X}
                    cy={peer.y}
                    r={4}
                  />
                  <text className="topology__label" x={TASK_X + 10} y={peer.y + 3}>
                    <title>{peer.detail}</title>
                    {peer.domain}
                  </text>
                </g>
              ))}
            </svg>
          )}
        </div>
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const TopologyView = memo(TopologyViewComponent);
