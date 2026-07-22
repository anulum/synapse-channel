// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — exact-event temporal lanes and project-flow projection

import { eventsInWindow, type TimeWindow } from "./brush";
import type { ClaimView } from "./claims";
import { projectOf } from "./communications";
import type { CockpitEvent } from "../types";

export const TIMELINE_LANES = ["message", "claim", "wait", "task"] as const;
export type TimelineLane = (typeof TIMELINE_LANES)[number];

export interface FleetTimelinePoint {
  readonly seq: number;
  readonly ts: number;
  readonly lane: TimelineLane;
  readonly actor: string;
  readonly project: string;
  readonly label: string;
  readonly taskId: string;
  /** Normalised position inside this retained view, from 0 through 1. */
  readonly position: number;
}

export interface FleetTimeline {
  readonly points: readonly FleetTimelinePoint[];
  readonly firstTs: number | null;
  readonly lastTs: number | null;
  readonly total: number;
  readonly limited: boolean;
}

export interface ProjectFlowProject {
  readonly id: string;
  readonly members: readonly string[];
  readonly inbound: number;
  readonly outbound: number;
  readonly claims: number;
  readonly conflicts: number;
  readonly lastTs: number;
}

export interface ProjectFlowLink {
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly messages: number;
  readonly lastTs: number;
  /** Newest-first exact retained message sequences supporting the aggregate. */
  readonly evidenceSeqs: readonly number[];
}

export interface ProjectFlowModel {
  readonly projects: readonly ProjectFlowProject[];
  readonly links: readonly ProjectFlowLink[];
  readonly messages: number;
  readonly limited: boolean;
}

export interface ProjectFlowPosition {
  readonly id: string;
  readonly x: number;
  readonly y: number;
}

export interface ProjectFlowLayout {
  readonly sources: ReadonlyMap<string, ProjectFlowPosition>;
  readonly targets: ReadonlyMap<string, ProjectFlowPosition>;
}

interface MutableProject {
  readonly members: Set<string>;
  inbound: number;
  outbound: number;
  claims: number;
  conflicts: number;
  lastTs: number;
}

interface MutableLink {
  readonly source: string;
  readonly target: string;
  messages: number;
  lastTs: number;
  readonly evidenceSeqs: number[];
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function timelineLane(event: CockpitEvent): TimelineLane | null {
  if (event.kind === "chat" || event.label.startsWith("delivery_receipt_")) return "message";
  if (event.kind === "claim" || event.kind === "lease" || event.kind === "release" || event.kind === "conflict") {
    return "claim";
  }
  if (event.kind === "presence" && (event.actor.endsWith("-rx") || event.label.toLowerCase().includes("wait"))) {
    return "wait";
  }
  if (event.kind === "task" || event.kind === "finding") return "task";
  return null;
}

function eventIdentity(event: CockpitEvent): string {
  return text(event.payload?.["sender"]) || event.actor;
}

/** Project eligible retained events onto four exact-evidence temporal lanes. */
export function deriveFleetTimeline(
  events: readonly CockpitEvent[],
  window: TimeWindow | null = null,
  limit = 80,
): FleetTimeline {
  const eligible = eventsInWindow(events, window)
    .map((event) => ({ event, lane: timelineLane(event) }))
    .filter((item): item is { event: CockpitEvent; lane: TimelineLane } => item.lane !== null)
    .sort((a, b) => a.event.ts - b.event.ts || a.event.seq - b.event.seq);
  const boundedLimit = Math.max(1, limit);
  const visible = eligible.slice(-boundedLimit);
  const firstTs = visible[0]?.event.ts ?? null;
  const lastTs = visible.at(-1)?.event.ts ?? null;
  const span = firstTs === null || lastTs === null ? 0 : lastTs - firstTs;
  return {
    points: visible.map(({ event, lane }) => {
      const actor = eventIdentity(event);
      return {
        seq: event.seq,
        ts: event.ts,
        lane,
        actor,
        project: projectOf(actor),
        label: event.label,
        taskId: event.taskId,
        position: span <= 0 || firstTs === null ? 0.5 : (event.ts - firstTs) / span,
      };
    }),
    firstTs,
    lastTs,
    total: eligible.length,
    limited: eligible.length > visible.length,
  };
}

function chatRoute(event: CockpitEvent): { source: string; target: string } | null {
  const source = text(event.payload?.["sender"]);
  const target = text(event.payload?.["target"]);
  if (source === "" || target === "") return null;
  // Non-empty sender and target values prove that payload exists here.
  if (event.payload?.["type"] !== "chat" && !Object.hasOwn(event.payload!, "payload")) return null;
  return { source, target };
}

/** Aggregate retained exact message events into a bounded project-flow model. */
export function deriveProjectFlow(
  events: readonly CockpitEvent[],
  claims: readonly ClaimView[] = [],
  window: TimeWindow | null = null,
  projectLimit = 8,
  linkLimit = 16,
): ProjectFlowModel {
  const projects = new Map<string, MutableProject>();
  const links = new Map<string, MutableLink>();
  const ensureProject = (id: string): MutableProject => {
    const existing = projects.get(id);
    if (existing !== undefined) return existing;
    const created: MutableProject = {
      members: new Set<string>(),
      inbound: 0,
      outbound: 0,
      claims: 0,
      conflicts: 0,
      lastTs: 0,
    };
    projects.set(id, created);
    return created;
  };

  for (const claim of claims) {
    if (claim.claim.owner === "") continue;
    const project = ensureProject(projectOf(claim.claim.owner));
    project.members.add(claim.claim.owner);
    project.claims += 1;
    if (claim.inConflict) project.conflicts += 1;
  }

  let messages = 0;
  for (const event of eventsInWindow(events, window)) {
    const route = chatRoute(event);
    if (route === null) continue;
    messages += 1;
    const sourceProject = projectOf(route.source);
    const targetProject = projectOf(route.target);
    const source = ensureProject(sourceProject);
    const target = ensureProject(targetProject);
    source.members.add(route.source);
    target.members.add(route.target);
    source.outbound += 1;
    target.inbound += 1;
    source.lastTs = Math.max(source.lastTs, event.ts);
    target.lastTs = Math.max(target.lastTs, event.ts);
    const id = `${sourceProject}\u0000${targetProject}`;
    const link = links.get(id) ?? {
      source: sourceProject,
      target: targetProject,
      messages: 0,
      lastTs: 0,
      evidenceSeqs: [],
    };
    link.messages += 1;
    link.lastTs = Math.max(link.lastTs, event.ts);
    link.evidenceSeqs.push(event.seq);
    links.set(id, link);
  }

  const rankedProjects = [...projects.entries()]
    .map(([id, project]): ProjectFlowProject => ({
      id,
      members: [...project.members].sort((a, b) => a.localeCompare(b)),
      inbound: project.inbound,
      outbound: project.outbound,
      claims: project.claims,
      conflicts: project.conflicts,
      lastTs: project.lastTs,
    }))
    .sort(
      (a, b) =>
        b.inbound + b.outbound - (a.inbound + a.outbound) ||
        b.conflicts - a.conflicts ||
        b.claims - a.claims ||
        a.id.localeCompare(b.id),
    );
  const boundedProjects = rankedProjects.slice(0, Math.max(1, projectLimit));
  const visibleProjects = new Set(boundedProjects.map((project) => project.id));
  const rankedLinks = [...links.entries()]
    .map(([id, link]): ProjectFlowLink => ({
      id,
      source: link.source,
      target: link.target,
      messages: link.messages,
      lastTs: link.lastTs,
      evidenceSeqs: [...link.evidenceSeqs].sort((a, b) => b - a),
    }))
    .filter((link) => visibleProjects.has(link.source) && visibleProjects.has(link.target))
    .sort((a, b) => b.messages - a.messages || b.lastTs - a.lastTs || a.id.localeCompare(b.id));
  const boundedLinks = rankedLinks.slice(0, Math.max(1, linkLimit));
  return {
    projects: boundedProjects,
    links: boundedLinks,
    messages,
    limited: rankedProjects.length > boundedProjects.length || rankedLinks.length > boundedLinks.length,
  };
}

/** Place outbound projects on the left and inbound projects on the right. */
export function layoutProjectFlow(model: ProjectFlowModel, width = 760, height = 340): ProjectFlowLayout {
  const sources = model.projects
    .filter((project) => project.outbound > 0)
    .sort((a, b) => b.outbound - a.outbound || a.id.localeCompare(b.id));
  const targets = model.projects
    .filter((project) => project.inbound > 0)
    .sort((a, b) => b.inbound - a.inbound || a.id.localeCompare(b.id));
  const positions = (projects: readonly ProjectFlowProject[], x: number): Map<string, ProjectFlowPosition> =>
    new Map(
      projects.map((project, index) => [
        project.id,
        {
          id: project.id,
          x,
          y: ((index + 1) / (projects.length + 1)) * height,
        },
      ]),
    );
  return {
    sources: positions(sources, Math.min(112, width * 0.18)),
    targets: positions(targets, Math.max(width - 112, width * 0.82)),
  };
}
