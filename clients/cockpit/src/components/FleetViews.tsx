// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — multi-view fleet communication instrument

import type { CSSProperties, FormEvent, JSX, KeyboardEvent } from "react";
import { memo, useMemo, useState } from "react";

import type { TimeWindow } from "../lib/brush";
import type { ClaimView } from "../lib/claims";
import {
  deriveCommunicationModel,
  deriveConversationDetail,
  layoutCommunicationWeb,
  matrixIdentities,
  type CommunicationEdge,
  type CommunicationModel,
  type CommunicationNode,
  type ConversationMessage,
  type ProjectTraffic,
} from "../lib/communications";
import {
  sendOperatorResponse,
  type MessageResponseInput,
  type OperatorActionResult,
  type SemanticResponseStatus,
} from "../lib/operatorActions";
import type { CockpitEvent } from "../types";

type FleetView = "web" | "matrix" | "projects";
type Selection =
  | { readonly kind: "node" | "project"; readonly id: string }
  | {
      readonly kind: "edge";
      readonly source: string;
      readonly target: string;
    };

interface FleetViewsProps {
  readonly events: readonly CockpitEvent[];
  readonly claims: readonly ClaimView[];
  readonly agents: readonly string[];
  readonly window: TimeWindow | null;
  readonly connected: boolean;
  readonly canMessage: boolean;
  readonly onMessagePeer?: ((identity: string) => void) | undefined;
  readonly respondToMessage?: ((input: MessageResponseInput) => Promise<OperatorActionResult>) | undefined;
}

function shortIdentity(identity: string): string {
  const slash = identity.lastIndexOf("/");
  const value = slash === -1 ? identity : identity.slice(slash + 1);
  return value.length > 18 ? `${value.slice(0, 16)}…` : value;
}

function timeAgo(ts: number): string {
  if (ts <= 0) return "quiet in window";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function countLabel(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

function activate(event: KeyboardEvent<SVGGElement>, run: () => void): void {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    run();
  }
}

function WebView({
  model,
  onSelectNode,
  onSelectEdge,
}: {
  model: CommunicationModel;
  onSelectNode: (id: string) => void;
  onSelectEdge: (source: string, target: string) => void;
}): JSX.Element {
  const layout = layoutCommunicationWeb(model);
  const labelled = new Set(model.nodes.slice(0, 14).map((node) => node.id));
  const projects = [...new Map(layout.nodes.map((node) => [node.project, node.colourIndex])).entries()];
  const priorityRoutes = model.edges.filter((edge) => edge.source !== edge.target).slice(0, 8);
  const strongest = Math.max(1, ...model.edges.map((edge) => edge.messages));
  return (
    <div className="fleet-web" data-testid="fleet-web">
      <svg viewBox="0 0 760 360" role="img" aria-label="Directed communication web grouped by project">
        <defs>
          <marker id="fleet-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
            <path d="M0,0 L7,3.5 L0,7 z" className="fleet-web__arrow" />
          </marker>
        </defs>
        {model.edges.map((edge) => {
          const from = layout.byId.get(edge.source);
          const to = layout.byId.get(edge.target);
          if (from === undefined || to === undefined || from.id === to.id) return null;
          const select = (): void => onSelectEdge(edge.source, edge.target);
          return (
            <g
              key={edge.id}
              role="button"
              tabIndex={0}
              aria-label={`${edge.source} to ${edge.target}: ${edge.messages} messages, open communication detail`}
              className="fleet-web__edge-hit"
              onClick={select}
              onKeyDown={(event) => activate(event, select)}
            >
              <line className="fleet-web__edge-target" x1={from.x} y1={from.y} x2={to.x} y2={to.y} />
              <line
                className={`fleet-web__edge fleet-web__edge--${edge.health}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                strokeWidth={0.7 + (edge.messages / strongest) * 2.3}
                markerEnd="url(#fleet-arrow)"
              >
                <title>{`${edge.source} → ${edge.target}: ${edge.messages} messages`}</title>
              </line>
            </g>
          );
        })}
        {layout.nodes.map((node) => (
          <g
            key={node.id}
            role="button"
            tabIndex={0}
            aria-label={`${node.id}, ${node.messages} message contacts`}
            className="fleet-web__node-hit"
            onClick={() => onSelectNode(node.id)}
            onKeyDown={(event) => activate(event, () => onSelectNode(node.id))}
          >
            <circle
              className={`fleet-web__halo fleet-web__halo--${node.colourIndex}`}
              cx={node.x}
              cy={node.y}
              r={node.radius + 5}
            />
            <circle
              className={`fleet-web__node fleet-web__node--${node.colourIndex}`}
              cx={node.x}
              cy={node.y}
              r={node.radius}
            />
          </g>
        ))}
        {layout.nodes.map((node) =>
          labelled.has(node.id) ? (
            <text
              key={`${node.id}-label`}
              className="fleet-web__label"
              pointerEvents="none"
              x={node.x}
              y={node.y + node.radius + 13}
            >
              <title>{node.id}</title>
              {shortIdentity(node.id)}
            </text>
          ) : null,
        )}
      </svg>
      {priorityRoutes.length > 0 && (
        <section className="fleet-web__routes" aria-label="Priority communication routes">
          <div className="fleet-web__routes-heading">
            <strong>priority routes</strong>
            <span>precise selector · full long-tail in matrix</span>
          </div>
          <div className="fleet-web__route-grid">
            {priorityRoutes.map((edge) => (
              <button
                key={edge.id}
                type="button"
                className={`fleet-web__route fleet-web__route--${edge.health}`}
                aria-label={`Select priority route ${edge.source} to ${edge.target}: ${countLabel(edge.messages, "message")}`}
                onClick={() => onSelectEdge(edge.source, edge.target)}
              >
                <span className="fleet-web__route-path">
                  <span title={edge.source}>{shortIdentity(edge.source)}</span>
                  <span aria-hidden="true">→</span>
                  <span title={edge.target}>{shortIdentity(edge.target)}</span>
                </span>
                <span className="fleet-web__route-meta">
                  {edge.messages} · {edge.health}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}
      <div className="fleet-web__legend" aria-label="Project colours">
        {projects.map(([project, colourIndex]) => (
          <span key={project}>
            <i className={`fleet-web__legend-dot fleet-web__legend-dot--${colourIndex}`} />
            {project}
          </span>
        ))}
      </div>
    </div>
  );
}

function MatrixView({
  model,
  onSelect,
}: {
  model: CommunicationModel;
  onSelect: (source: string, target: string) => void;
}): JSX.Element {
  const identities = matrixIdentities(model);
  const edges = new Map(model.edges.map((edge) => [edge.id, edge]));
  const strongest = Math.max(1, ...model.edges.map((edge) => edge.messages));
  return (
    <div className="fleet-matrix-wrap" data-testid="fleet-matrix">
      <table className="fleet-matrix">
        <caption className="visually-hidden">Sender rows and recipient columns by message volume</caption>
        <thead>
          <tr>
            <th scope="col">from \ to</th>
            {identities.map((node) => (
              <th scope="col" key={node.id} title={node.id}>
                {shortIdentity(node.id)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {identities.map((source) => (
            <tr key={source.id}>
              <th scope="row" title={source.id}>
                {shortIdentity(source.id)}
              </th>
              {identities.map((target) => {
                const edge = edges.get(`${source.id}\u0000${target.id}`);
                const strength = edge === undefined ? 0 : edge.messages / strongest;
                const style = {
                  "--cell-strength": strength,
                } as CSSProperties;
                return (
                  <td key={target.id}>
                    <button
                      type="button"
                      className={`fleet-matrix__cell fleet-matrix__cell--${edge?.health ?? "empty"}`}
                      style={style}
                      aria-label={`${source.id} to ${target.id}: ${edge?.messages ?? 0} messages`}
                      disabled={edge === undefined}
                      onClick={() => onSelect(source.id, target.id)}
                    >
                      {edge?.messages ?? ""}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProjectsView({
  projects,
  onSelect,
}: {
  projects: readonly ProjectTraffic[];
  onSelect: (id: string) => void;
}): JSX.Element {
  const maxTraffic = Math.max(1, ...projects.map((project) => project.inbound + project.outbound));
  return (
    <div className="fleet-projects" data-testid="fleet-projects">
      {projects.map((project, index) => {
        const traffic = project.inbound + project.outbound;
        const style = {
          "--project-load": traffic / maxTraffic,
        } as CSSProperties;
        return (
          <button
            key={project.id}
            type="button"
            className={`fleet-project fleet-project--${index % 6}`}
            style={style}
            onClick={() => onSelect(project.id)}
          >
            <span className="fleet-project__name">{project.id}</span>
            <span className="fleet-project__load" aria-hidden="true" />
            <span className="fleet-project__stats">
              {countLabel(project.members.length, "agent")} · {countLabel(traffic, "contact")} ·{" "}
              {countLabel(project.claims, "claim")}
            </span>
            <span className="fleet-project__time">{timeAgo(project.lastTs)}</span>
          </button>
        );
      })}
    </div>
  );
}

function NodeDetail({
  node,
  canMessage,
  onMessagePeer,
}: {
  node: CommunicationNode;
  canMessage: boolean;
  onMessagePeer?: ((identity: string) => void) | undefined;
}): JSX.Element {
  return (
    <aside className="fleet-selection" aria-label="Selected fleet identity">
      <span className="fleet-selection__eyebrow">identity</span>
      <strong className="fleet-selection__title">{node.id}</strong>
      <span className="fleet-selection__fact">
        {node.inbound} in · {node.outbound} out
      </span>
      <span className="fleet-selection__fact">
        {node.delivered} delivered · {node.deferred} deferred · {node.failed} failed
      </span>
      <span className="fleet-selection__fact">last activity {timeAgo(node.lastTs)}</span>
      {canMessage && node.exact && onMessagePeer !== undefined && (
        <button type="button" className="fleet-selection__action" onClick={() => onMessagePeer(node.id)}>
          message peer
        </button>
      )}
      <small>Operator messages are audited chat; they do not alter transport ACK state.</small>
    </aside>
  );
}

function responseResult(result: OperatorActionResult): string {
  switch (result.kind) {
    case "accepted":
      return result.detail || `response ${result.status}`;
    case "denied":
    case "rejected":
    case "unreachable":
    case "rate-limited":
      return result.detail;
    case "not-armed":
      return "Operator response route is not armed.";
    case "unauthorised":
      return "Dashboard bearer was refused.";
    case "error":
      return result.message;
  }
}

function messageTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function EdgeDetail({
  edge,
  messages,
  canRespond,
  respond,
}: {
  edge: CommunicationEdge;
  messages: readonly ConversationMessage[];
  canRespond: boolean;
  respond: (input: MessageResponseInput) => Promise<OperatorActionResult>;
}): JSX.Element {
  const [messageSeq, setMessageSeq] = useState(messages[0]?.seq ?? 0);
  const [status, setStatus] = useState<SemanticResponseStatus>("acknowledged");
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState("");
  const [working, setWorking] = useState(false);
  const selected = messages.find((message) => message.seq === messageSeq) ?? messages[0];

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (selected === undefined || working) return;
    setWorking(true);
    setOutcome("");
    const result = await respond({
      messageSeq: selected.seq,
      to: selected.source,
      status,
      note,
    });
    setOutcome(responseResult(result));
    setWorking(false);
  };

  return (
    <aside className="fleet-conversation" aria-label="Communication detail">
      <header className="fleet-conversation__header">
        <span className="fleet-selection__eyebrow">directed link</span>
        <strong>
          {edge.source} → {edge.target}
        </strong>
        <span>
          {edge.messages} sent · {edge.delivered} delivered · {edge.deferred} deferred · {edge.failed} failed
        </span>
      </header>
      <div className="fleet-conversation__timeline" aria-label="Pairwise message timeline">
        {messages.map((message) => (
          <button
            key={message.seq}
            type="button"
            aria-pressed={selected?.seq === message.seq}
            className={`fleet-message${selected?.seq === message.seq ? " fleet-message--selected" : ""}`}
            onClick={() => setMessageSeq(message.seq)}
          >
            <span className="fleet-message__meta">
              <b>#{message.seq}</b> · {messageTime(message.ts)} · {message.source} → {message.target}
            </span>
            <span className="fleet-message__body">{message.body || "(empty body)"}</span>
            <span className="fleet-message__signals">
              delivery {message.delivery}
              {message.responseStatus !== null && (
                <>
                  {" "}
                  · {message.responseEvidenceScope === "operator_commentary"
                    ? "operator commentary"
                    : message.responseEvidenceScope === "recipient"
                      ? "recipient response"
                      : "legacy response"}{" "}
                  {message.responseStatus} to #{message.responseToSeq}
                </>
              )}
            </span>
          </button>
        ))}
      </div>
      {canRespond && selected !== undefined ? (
        <form className="fleet-response" onSubmit={(event) => void submit(event)}>
          <label>
            respond to #{selected.seq}
            <select value={status} onChange={(event) => setStatus(event.target.value as SemanticResponseStatus)}>
              <option value="acknowledged">acknowledged</option>
              <option value="in_progress">in progress</option>
              <option value="needs_input">needs input</option>
              <option value="declined">declined</option>
              <option value="completed">completed</option>
            </select>
          </label>
          <label className="fleet-response__note">
            optional note
            <input value={note} maxLength={2048} onChange={(event) => setNote(event.target.value)} />
          </label>
          <button type="submit" disabled={working}>
            {working ? "sending…" : "send response"}
          </button>
          {outcome !== "" && <output>{outcome}</output>}
          <small>
            Attributed operator commentary; not recipient or task-ownership evidence. Transport ACK remains unchanged.
          </small>
        </form>
      ) : (
        <small className="fleet-conversation__viewer-note">
          Read-only detail. Semantic response controls require an armed operator.
        </small>
      )}
    </aside>
  );
}

function FleetViewsComponent({
  events,
  claims,
  agents,
  window,
  connected,
  canMessage,
  onMessagePeer,
  respondToMessage = sendOperatorResponse,
}: FleetViewsProps): JSX.Element {
  const [view, setView] = useState<FleetView>("web");
  const [selection, setSelection] = useState<Selection | null>(null);
  const model = useMemo(
    () => deriveCommunicationModel(events, claims, agents, window),
    [events, claims, agents, window],
  );
  const selectedNode = selection?.kind === "node" ? model.nodes.find((node) => node.id === selection.id) : undefined;
  const selectedProject =
    selection?.kind === "project" ? model.projects.find((project) => project.id === selection.id) : undefined;
  const selectedEdge =
    selection?.kind === "edge"
      ? model.edges.find((edge) => edge.source === selection.source && edge.target === selection.target)
      : undefined;
  const selectedConversation = useMemo(
    () =>
      selection?.kind === "edge" ? deriveConversationDetail(events, selection.source, selection.target, window) : [],
    [events, selection, window],
  );
  const failed = model.edges.filter((edge: CommunicationEdge) => edge.health === "failed").length;

  return (
    <section className="panel fleet-views" aria-label="Fleet communication views">
      <div className="fleet-views__toolbar">
        <div className="fleet-views__switch" role="tablist" aria-label="Fleet view">
          {(["web", "matrix", "projects"] as const).map((candidate) => (
            <button
              key={candidate}
              type="button"
              role="tab"
              aria-selected={view === candidate}
              className={view === candidate ? "fleet-views__view fleet-views__view--active" : "fleet-views__view"}
              onClick={() => setView(candidate)}
            >
              {candidate}
            </button>
          ))}
        </div>
        <div className="fleet-views__summary">
          <span>{model.nodes.length} identities</span>
          <span>{model.messages} messages</span>
          <span className={failed > 0 ? "fleet-views__alert" : ""}>{failed} troubled links</span>
        </div>
      </div>
      {!connected ? (
        <p className="panel__placeholder panel__placeholder--padded">Waiting for the hub.</p>
      ) : model.messages === 0 ? (
        <p className="panel__placeholder panel__placeholder--padded">
          No routed messages in this window. The communication views require the durable event feed.
        </p>
      ) : (
        <div className="fleet-views__stage">
          <div className="fleet-views__visual">
            {view === "web" ? (
              <WebView
                model={model}
                onSelectNode={(id) => setSelection({ kind: "node", id })}
                onSelectEdge={(source, target) =>
                  setSelection({
                    kind: "edge",
                    source,
                    target,
                  })
                }
              />
            ) : view === "matrix" ? (
              <MatrixView
                model={model}
                onSelect={(source, target) =>
                  setSelection({
                    kind: "edge",
                    source,
                    target,
                  })
                }
              />
            ) : (
              <ProjectsView projects={model.projects} onSelect={(id) => setSelection({ kind: "project", id })} />
            )}
          </div>
          {selectedEdge !== undefined ? (
            <EdgeDetail
              key={`${selectedEdge.source}\u0000${selectedEdge.target}`}
              edge={selectedEdge}
              messages={selectedConversation}
              canRespond={canMessage}
              respond={respondToMessage}
            />
          ) : selectedNode !== undefined ? (
            <NodeDetail node={selectedNode} canMessage={canMessage} onMessagePeer={onMessagePeer} />
          ) : selectedProject !== undefined ? (
            <aside className="fleet-selection" aria-label="Selected fleet project">
              <span className="fleet-selection__eyebrow">project</span>
              <strong className="fleet-selection__title">{selectedProject.id}</strong>
              <span className="fleet-selection__fact">{countLabel(selectedProject.members.length, "identity")}</span>
              <span className="fleet-selection__fact">
                {selectedProject.inbound} in · {selectedProject.outbound} out
              </span>
              <span className="fleet-selection__fact">
                {countLabel(selectedProject.claims, "active or stale claim")}
              </span>
            </aside>
          ) : null}
        </div>
      )}
    </section>
  );
}

export const FleetViews = memo(FleetViewsComponent);
