// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — temporal and project-flow visual/text evidence peers

import type { JSX, KeyboardEvent } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import { layoutProjectFlow, TIMELINE_LANES, type FleetTimeline, type ProjectFlowModel } from "../lib/fleetVisuals";
import type { CockpitLocale } from "../lib/i18n";
import { eventMatchesSelection, identityProject } from "../lib/selection";
import type { CockpitEvent } from "../types";
import type { CockpitSelection } from "../lib/workspace";

function shortIdentity(identity: string): string {
  const slash = identity.lastIndexOf("/");
  const value = slash === -1 ? identity : identity.slice(slash + 1);
  return value.length > 18 ? `${value.slice(0, 16)}…` : value;
}

function activate(event: KeyboardEvent<SVGGElement>, run: () => void): void {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    run();
  }
}

function clockTime(ts: number, locale: CockpitLocale): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ts * 1000));
}

export function TimelineView({
  timeline,
  events,
  selection,
  onSelect,
}: {
  timeline: FleetTimeline;
  events: readonly CockpitEvent[];
  selection: CockpitSelection | null;
  onSelect: (seq: number) => void;
}): JSX.Element {
  const { locale, t } = useCockpitI18n();
  const eventBySeq = new Map(events.map((event) => [event.seq, event]));
  const laneY = new Map(TIMELINE_LANES.map((lane, index) => [lane, 54 + index * 62]));
  return (
    <div className="fleet-timeline" data-testid="fleet-timeline">
      <div className="fleet-timeline__chart">
        <svg viewBox="0 0 760 330" role="group" aria-label={t("fleet.timeline.aria")}>
          <title>{t("fleet.timeline.title")}</title>
          <line className="fleet-timeline__axis" x1="132" y1="286" x2="716" y2="286" />
          {TIMELINE_LANES.map((lane) => {
            const y = laneY.get(lane)!;
            return (
              <g key={lane}>
                <text className="fleet-timeline__lane-label" x="18" y={y + 4}>
                  {lane}
                </text>
                <line className="fleet-timeline__lane" x1="132" y1={y} x2="716" y2={y} />
              </g>
            );
          })}
          {timeline.points.map((point) => {
            const x = 132 + point.position * 584;
            const y = laneY.get(point.lane)!;
            const event = eventBySeq.get(point.seq);
            const selected = event !== undefined && eventMatchesSelection(event, selection);
            const inspect = (): void => onSelect(point.seq);
            return (
              <g
                key={point.seq}
                role="button"
                tabIndex={0}
                aria-label={t("fleet.timeline.inspect", { seq: point.seq, label: point.label })}
                aria-pressed={selected}
                className={`fleet-timeline__event fleet-timeline__event--${point.lane}${selected ? " fleet-timeline__event--selected" : ""}`}
                onClick={inspect}
                onKeyDown={(keyEvent) => activate(keyEvent, inspect)}
              >
                <circle className="fleet-timeline__target" cx={x} cy={y} r="15" />
                <circle className="fleet-timeline__mark" cx={x} cy={y} r={selected ? 7 : 5}>
                  <title>{`#${point.seq} · ${point.label} · ${clockTime(point.ts, locale)}`}</title>
                </circle>
              </g>
            );
          })}
          <text className="fleet-timeline__time-label" x="132" y="312">
            {timeline.firstTs === null ? t("fleet.timeline.noTime") : clockTime(timeline.firstTs, locale)}
          </text>
          <text className="fleet-timeline__time-label fleet-timeline__time-label--end" x="716" y="312">
            {timeline.lastTs === null ? "" : clockTime(timeline.lastTs, locale)}
          </text>
        </svg>
      </div>
      <div className="fleet-evidence__heading">
        <strong>{t("fleet.timeline.exactPeer")}</strong>
        <span>
          {t("fleet.timeline.shown", { count: timeline.points.length })}
          {timeline.limited ? t("fleet.timeline.of", { total: timeline.total }) : ""}
        </span>
      </div>
      <div className="fleet-evidence__table-wrap">
        <table className="fleet-evidence">
          <caption className="visually-hidden">{t("fleet.timeline.caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("fleet.timeline.lane")}</th>
              <th scope="col">{t("fleet.timeline.sequence")}</th>
              <th scope="col">{t("fleet.timeline.time")}</th>
              <th scope="col">{t("fleet.timeline.actorProject")}</th>
              <th scope="col">{t("fleet.timeline.evidence")}</th>
            </tr>
          </thead>
          <tbody>
            {[...timeline.points].reverse().map((point) => {
              const event = eventBySeq.get(point.seq);
              const selected = event !== undefined && eventMatchesSelection(event, selection);
              return (
                <tr key={point.seq} className={selected ? "fleet-evidence__row--selected" : ""}>
                  <td>
                    <span className={`fleet-evidence__lane fleet-evidence__lane--${point.lane}`}>{point.lane}</span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="fleet-evidence__event"
                      aria-pressed={selected}
                      onClick={() => onSelect(point.seq)}
                    >
                      #{point.seq}
                    </button>
                  </td>
                  <td>
                    <time dateTime={new Date(point.ts * 1000).toISOString()}>{clockTime(point.ts, locale)}</time>
                  </td>
                  <td title={point.actor}>{point.actor || point.project}</td>
                  <td>{point.label}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function projectSelected(project: string, selection: CockpitSelection | null): boolean {
  if (selection?.kind === "project") return selection.id === project;
  if (selection?.kind === "agent") return identityProject(selection.id) === project;
  if (selection?.kind === "route") {
    return identityProject(selection.source) === project || identityProject(selection.target) === project;
  }
  return false;
}

export function ProjectFlowView({
  model,
  selection,
  onSelectProject,
  onSelectEvent,
}: {
  model: ProjectFlowModel;
  selection: CockpitSelection | null;
  onSelectProject: (id: string) => void;
  onSelectEvent: (seq: number) => void;
}): JSX.Element {
  const { t } = useCockpitI18n();
  const layout = layoutProjectFlow(model);
  const strongest = Math.max(1, ...model.links.map((link) => link.messages));
  return (
    <div className="fleet-flow" data-testid="fleet-flow">
      <svg viewBox="0 0 760 340" role="group" aria-label={t("fleet.flow.aria")}>
        <title>{t("fleet.flow.title")}</title>
        <defs>
          <marker id="fleet-flow-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
            <path d="M0,0 L7,3.5 L0,7 z" className="fleet-flow__arrow" />
          </marker>
        </defs>
        <text className="fleet-flow__column" x="24" y="22">
          {t("fleet.flow.outbound")}
        </text>
        <text className="fleet-flow__column fleet-flow__column--end" x="736" y="22">
          {t("fleet.flow.inbound")}
        </text>
        {model.links.map((link) => {
          const source = layout.sources.get(link.source);
          const target = layout.targets.get(link.target);
          if (source === undefined || target === undefined) return null;
          const evidenceSeq = link.evidenceSeqs[0]!;
          const selected =
            (selection?.kind === "event" && link.evidenceSeqs.includes(selection.seq)) ||
            (selection?.kind === "route" &&
              identityProject(selection.source) === link.source &&
              identityProject(selection.target) === link.target);
          const inspect = (): void => onSelectEvent(evidenceSeq);
          const path = `M ${source.x + 70} ${source.y} C 310 ${source.y}, 450 ${target.y}, ${target.x - 70} ${target.y}`;
          return (
            <g
              key={link.id}
              role="button"
              tabIndex={0}
              aria-label={t("fleet.flow.inspect", {
                source: link.source,
                target: link.target,
                count: link.messages,
                seq: evidenceSeq,
              })}
              aria-pressed={selected}
              className={`fleet-flow__link-hit${selected ? " fleet-flow__link-hit--selected" : ""}`}
              onClick={inspect}
              onKeyDown={(keyEvent) => activate(keyEvent, inspect)}
            >
              <path className="fleet-flow__target" d={path} />
              <path
                className="fleet-flow__link"
                d={path}
                strokeWidth={1 + (link.messages / strongest) * 5}
                markerEnd="url(#fleet-flow-arrow)"
              >
                <title>{t("fleet.flow.inspect", {
                  source: link.source,
                  target: link.target,
                  count: link.messages,
                  seq: evidenceSeq,
                })}</title>
              </path>
            </g>
          );
        })}
        {[...layout.sources.values(), ...layout.targets.values()].map((position, index) => {
          const selected = projectSelected(position.id, selection);
          const inspect = (): void => onSelectProject(position.id);
          return (
            <g
              key={`${index < layout.sources.size ? "source" : "target"}-${position.id}`}
              role="button"
              tabIndex={0}
              aria-label={t("fleet.flow.selectProject", { project: position.id })}
              aria-pressed={selected}
              className={`fleet-flow__project${selected ? " fleet-flow__project--selected" : ""}`}
              onClick={inspect}
              onKeyDown={(keyEvent) => activate(keyEvent, inspect)}
            >
              <rect x={position.x - 70} y={position.y - 18} width="140" height="36" rx="6" />
              <text x={position.x} y={position.y + 4}>
                <title>{position.id}</title>
                {shortIdentity(position.id)}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="fleet-evidence__heading">
        <strong>{t("fleet.flow.exactEvidence")}</strong>
        <span>
          {t("fleet.flow.flowCount", { count: model.links.length })}
          {model.limited ? ` · ${t("fleet.flow.bounded")}` : ""}
        </span>
      </div>
      <div className="fleet-evidence__table-wrap">
        <table className="fleet-evidence">
          <caption className="visually-hidden">
            {t("fleet.flow.caption")}
          </caption>
          <thead>
            <tr>
              <th scope="col">{t("fleet.flow.fromProject")}</th>
              <th scope="col">{t("fleet.flow.toProject")}</th>
              <th scope="col">{t("fleet.flow.retainedMessages")}</th>
              <th scope="col">{t("fleet.flow.latestEvidence")}</th>
            </tr>
          </thead>
          <tbody>
            {model.links.map((link) => {
              const evidenceSeq = link.evidenceSeqs[0]!;
              const selected = selection?.kind === "event" && link.evidenceSeqs.includes(selection.seq);
              return (
                <tr key={link.id} className={selected ? "fleet-evidence__row--selected" : ""}>
                  <td>
                    <button
                      type="button"
                      className="fleet-evidence__project"
                      onClick={() => onSelectProject(link.source)}
                    >
                      {link.source}
                    </button>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="fleet-evidence__project"
                      onClick={() => onSelectProject(link.target)}
                    >
                      {link.target}
                    </button>
                  </td>
                  <td>{link.messages}</td>
                  <td>
                    <button
                      type="button"
                      className="fleet-evidence__event"
                      aria-pressed={selected}
                      onClick={() => onSelectEvent(evidenceSeq)}
                    >
                      #{evidenceSeq}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
