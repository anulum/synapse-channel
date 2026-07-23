// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic fleet communication layout

import type { CommunicationModel, CommunicationNode } from "./communicationModel";

/** Communication node with deterministic canvas geometry. */
export interface WebNode extends CommunicationNode {
  readonly x: number;
  readonly y: number;
  readonly radius: number;
  readonly colourIndex: number;
}

/** Stable graph geometry and its exact-identity lookup. */
export interface WebLayout {
  readonly nodes: readonly WebNode[];
  readonly byId: ReadonlyMap<string, WebNode>;
}

/** Stable circular sectors keep projects together and never jitter on refresh. */
export function layoutCommunicationWeb(
  model: CommunicationModel,
  width = 760,
  height = 360,
  limit = 28,
): WebLayout {
  const visible = model.nodes.filter((node) => node.messages > 0).slice(0, Math.max(1, limit));
  const projectOrder = [...new Set(visible.map((node) => node.project))].sort((left, right) => left.localeCompare(right));
  const projectIndex = new Map(projectOrder.map((project, index) => [project, index]));
  const ordered = [...visible].sort(
    (left, right) => projectIndex.get(left.project)! - projectIndex.get(right.project)! || left.id.localeCompare(right.id),
  );
  const radius = Math.max(60, Math.min(width, height) * 0.38);
  const centreX = width / 2;
  const centreY = height / 2;
  const laid = ordered.map((node, index): WebNode => {
    const angle = -Math.PI / 2 + (index / Math.max(1, ordered.length)) * Math.PI * 2;
    return {
      ...node,
      x: centreX + Math.cos(angle) * radius,
      y: centreY + Math.sin(angle) * radius,
      radius: Math.min(10, 4 + Math.sqrt(node.messages) * 0.75),
      colourIndex: projectIndex.get(node.project)! % 6,
    };
  });
  return { nodes: laid, byId: new Map(laid.map((node) => [node.id, node])) };
}

/** Return the busiest exact identities within the readable matrix bound. */
export function matrixIdentities(model: CommunicationModel, limit = 12): CommunicationNode[] {
  return model.nodes.filter((node) => node.messages > 0).slice(0, Math.max(1, limit));
}
