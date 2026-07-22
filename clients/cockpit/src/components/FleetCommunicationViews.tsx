// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — communication web, matrix, and project projections

import type { CSSProperties, JSX, KeyboardEvent } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { MessageKey } from "../lib/i18n";
import {
  layoutCommunicationWeb,
  matrixIdentities,
  type CommunicationModel,
  type ProjectTraffic,
} from "../lib/communications";
import type { CockpitSelection } from "../lib/workspace";

function shortIdentity(identity: string): string {
  const slash = identity.lastIndexOf("/");
  const value = slash === -1 ? identity : identity.slice(slash + 1);
  return value.length > 18 ? `${value.slice(0, 16)}…` : value;
}

function countLabel(
  count: number,
  singular: MessageKey,
  plural: MessageKey,
  t: (key: MessageKey) => string,
): string {
  return `${count} ${t(count === 1 ? singular : plural)}`;
}

function timeAgo(ts: number, t: (key: MessageKey, values?: Readonly<Record<string, string | number>>) => string): string {
  if (ts <= 0) return t("fleet.relative.quiet");
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (seconds < 60) return t("fleet.relative.seconds", { count: seconds });
  if (seconds < 3600) return t("fleet.relative.minutes", { count: Math.floor(seconds / 60) });
  return t("fleet.relative.hours", { count: Math.floor(seconds / 3600) });
}

function activate(event: KeyboardEvent<SVGGElement>, run: () => void): void {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    run();
  }
}

export function FleetWebView({
  model,
  onSelectNode,
  onSelectEdge,
  selection,
}: {
  readonly model: CommunicationModel;
  readonly onSelectNode: (id: string) => void;
  readonly onSelectEdge: (source: string, target: string) => void;
  readonly selection: CockpitSelection | null;
}): JSX.Element {
  const { t } = useCockpitI18n();
  const layout = layoutCommunicationWeb(model);
  const labelled = new Set(model.nodes.slice(0, 14).map((node) => node.id));
  const projects = [...new Map(layout.nodes.map((node) => [node.project, node.colourIndex])).entries()];
  const priorityRoutes = model.edges.filter((edge) => edge.source !== edge.target).slice(0, 8);
  const strongest = Math.max(1, ...model.edges.map((edge) => edge.messages));
  return (
    <div className="fleet-web" data-testid="fleet-web">
      <svg viewBox="0 0 760 360" role="img" aria-label={t("fleet.web.aria")}>
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
          const selected =
            selection?.kind === "route" &&
            selection.source === edge.source &&
            selection.target === edge.target;
          return (
            <g
              key={edge.id}
              role="button"
              tabIndex={0}
              aria-label={t("fleet.web.selectRoute", {
                source: edge.source,
                target: edge.target,
                count: edge.messages,
              })}
              className={`fleet-web__edge-hit${selected ? " fleet-web__edge-hit--selected" : ""}`}
              aria-pressed={selected}
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
                <title>{t("fleet.matrix.route", {
                  source: edge.source,
                  target: edge.target,
                  count: edge.messages,
                })}</title>
              </line>
            </g>
          );
        })}
        {layout.nodes.map((node) => {
          const selected = selection?.kind === "agent" && selection.id === node.id;
          return (
            <g
              key={node.id}
              role="button"
              tabIndex={0}
              aria-label={t("fleet.web.selectIdentity", { identity: node.id, count: node.messages })}
              className={`fleet-web__node-hit${selected ? " fleet-web__node-hit--selected" : ""}`}
              aria-pressed={selected}
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
          );
        })}
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
        <section className="fleet-web__routes" aria-label={t("fleet.web.priority")}>
          <div className="fleet-web__routes-heading">
            <strong>{t("fleet.web.priority")}</strong>
            <span>{t("fleet.web.priorityHint")}</span>
          </div>
          <div className="fleet-web__route-grid">
            {priorityRoutes.map((edge) => (
              <button
                key={edge.id}
                type="button"
                className={`fleet-web__route fleet-web__route--${edge.health}${
                  selection?.kind === "route" &&
                  selection.source === edge.source &&
                  selection.target === edge.target
                    ? " fleet-web__route--selected"
                    : ""
                }`}
                aria-pressed={
                  selection?.kind === "route" &&
                  selection.source === edge.source &&
                  selection.target === edge.target
                }
                aria-label={t("fleet.web.selectPriority", {
                  source: edge.source,
                  target: edge.target,
                  messageCount: countLabel(
                    edge.messages,
                    "fleet.noun.message",
                    "fleet.noun.messages",
                    t,
                  ),
                })}
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
      <div className="fleet-web__legend" aria-label={t("fleet.web.projectColours")}>
        {projects.map(([project, colourIndex]) => (
          <span key={project}>
            <i className={`fleet-web__legend-dot fleet-web__legend-dot--${colourIndex}`} aria-hidden="true" />
            {project}
          </span>
        ))}
      </div>
    </div>
  );
}

export function FleetMatrixView({
  model,
  onSelect,
  selection,
}: {
  readonly model: CommunicationModel;
  readonly onSelect: (source: string, target: string) => void;
  readonly selection: CockpitSelection | null;
}): JSX.Element {
  const { t } = useCockpitI18n();
  const identities = matrixIdentities(model);
  const edges = new Map(model.edges.map((edge) => [edge.id, edge]));
  const strongest = Math.max(1, ...model.edges.map((edge) => edge.messages));
  return (
    <div className="fleet-matrix-wrap" data-testid="fleet-matrix">
      <table className="fleet-matrix">
        <caption className="visually-hidden">{t("fleet.matrix.caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("fleet.matrix.fromTo")}</th>
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
                const style = { "--cell-strength": strength } as CSSProperties;
                const selected =
                  selection?.kind === "route" &&
                  selection.source === source.id &&
                  selection.target === target.id;
                return (
                  <td key={target.id}>
                    <button
                      type="button"
                      className={`fleet-matrix__cell fleet-matrix__cell--${edge?.health ?? "empty"}${
                        selected ? " fleet-matrix__cell--selected" : ""
                      }`}
                      aria-pressed={selected}
                      style={style}
                      aria-label={t("fleet.matrix.route", {
                        source: source.id,
                        target: target.id,
                        count: edge?.messages ?? 0,
                      })}
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

export function FleetProjectsView({
  projects,
  onSelect,
  selection,
}: {
  readonly projects: readonly ProjectTraffic[];
  readonly onSelect: (id: string) => void;
  readonly selection: CockpitSelection | null;
}): JSX.Element {
  const { t } = useCockpitI18n();
  const maxTraffic = Math.max(1, ...projects.map((project) => project.inbound + project.outbound));
  return (
    <div className="fleet-projects" data-testid="fleet-projects">
      {projects.map((project, index) => {
        const traffic = project.inbound + project.outbound;
        const style = { "--project-load": traffic / maxTraffic } as CSSProperties;
        return (
          <button
            key={project.id}
            type="button"
            className={`fleet-project fleet-project--${index % 6}${
              selection?.kind === "project" && selection.id === project.id
                ? " fleet-project--selected"
                : ""
            }`}
            aria-pressed={selection?.kind === "project" && selection.id === project.id}
            style={style}
            onClick={() => onSelect(project.id)}
          >
            <span className="fleet-project__name">{project.id}</span>
            <span className="fleet-project__load" aria-hidden="true" />
            <span className="fleet-project__stats">
              {countLabel(project.members.length, "fleet.noun.agent", "fleet.noun.agents", t)} ·{" "}
              {countLabel(traffic, "fleet.noun.contact", "fleet.noun.contacts", t)} ·{" "}
              {countLabel(project.claims, "fleet.noun.claim", "fleet.noun.claims", t)}
            </span>
            <span className="fleet-project__time">{timeAgo(project.lastTs, t)}</span>
          </button>
        );
      })}
    </div>
  );
}
