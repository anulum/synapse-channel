// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real-hub rendered evidence-view acceptance

import { strict as assert } from "node:assert";
import * as vscode from "vscode";

interface HubFrame {
  type?: string;
  delivered?: boolean;
}

interface EvidenceItem {
  id: string;
  category: string;
  severity: string;
  label: string;
  description: string;
  detail: string;
}

interface RenderedEvidenceItem {
  id: string;
  label: string;
  description: string;
  tooltip: string;
  iconId: string;
}

/** Versioned extension contract exercised by the real host. */
export interface SynapseExtensionApi {
  apiVersion: 1;
  evidenceSnapshot(): readonly EvidenceItem[];
  renderedEvidenceSnapshot(): readonly RenderedEvidenceItem[];
}

let probeSequence = 0;

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

async function mutateHub(
  uri: string,
  token: string,
  mutation: Record<string, unknown>,
  response: string,
): Promise<HubFrame> {
  const sender = `vscode-evidence-writer-${probeSequence += 1}`;
  const socket = new WebSocket(uri);
  return await new Promise<HubFrame>((resolveFrame, reject) => {
    let settled = false;
    let sent = false;
    const timer = setTimeout(() => {
      settled = true;
      socket.close();
      reject(new Error(`Timed out waiting for ${response} after ${String(mutation["type"])}.`));
    }, 5_000);
    const finish = (frame: HubFrame): void => {
      settled = true;
      clearTimeout(timer);
      socket.close();
      resolveFrame(frame);
    };
    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({
        type: "heartbeat",
        sender,
        target: "System",
        payload: "online",
        token,
      }));
    });
    socket.addEventListener("message", (event: MessageEvent) => {
      let frame: HubFrame;
      try {
        frame = JSON.parse(String(event.data)) as HubFrame;
      } catch {
        return;
      }
      if (frame.type === "welcome" && !sent) {
        sent = true;
        socket.send(JSON.stringify({ ...mutation, sender }));
      } else if (frame.type === response) {
        finish(frame);
      }
    });
    socket.addEventListener("close", () => {
      if (!settled) {
        clearTimeout(timer);
        reject(new Error(`Hub closed before ${response}.`));
      }
    });
    socket.addEventListener("error", () => {
      if (!settled) {
        clearTimeout(timer);
        reject(new Error(`Hub connection failed before ${response}.`));
      }
    });
  });
}

async function waitForEvidence(
  api: SynapseExtensionApi,
  predicate: (items: readonly EvidenceItem[]) => boolean,
  label: string,
): Promise<readonly EvidenceItem[]> {
  const deadline = Date.now() + 8_000;
  do {
    const items = api.evidenceSnapshot();
    if (predicate(items)) {
      return items;
    }
    await delay(100);
  } while (Date.now() < deadline);
  throw new Error(`Editor evidence did not expose ${label}.`);
}

/** Exercise ledger claims, delivery history, commands, and actual rendered tree items. */
export async function acceptCoordinationEvidence(
  uri: string,
  token: string,
  api: SynapseExtensionApi,
): Promise<void> {
  const evidenceTask = "vscode-extension-host-evidence";
  await mutateHub(uri, token, {
    type: "ledger_progress",
    target: "System",
    task_id: evidenceTask,
    kind: "approval",
    payload: `approval subject=${evidenceTask} state=requested :: independent review pending`,
  }, "ledger_progress_posted");
  await mutateHub(uri, token, {
    type: "ledger_progress",
    target: "System",
    task_id: evidenceTask,
    kind: "assessment",
    payload: "release receipt: evidence=real token-gated Extension Host path",
  }, "ledger_progress_posted");
  const deliveryTarget = "vscode-integration-missing-target";
  const receipt = await mutateHub(uri, token, {
    type: "chat",
    target: deliveryTarget,
    payload: "integration delivery probe",
    receipt_requested: true,
  }, "delivery_receipt");
  assert.equal(receipt.delivered, false);

  const commands = await vscode.commands.getCommands(true);
  assert.ok(commands.includes("synapse.refreshEvidence"));
  assert.ok(commands.includes("synapse.showEvidence"));
  await vscode.commands.executeCommand("synapse.refreshEvidence");
  const evidence = await waitForEvidence(
    api,
    (items) => ["approval", "receipt", "delivery"].every(
      (category) => items.some((item) => item.category === category),
    ),
    "self-attested ledger claims and retained delivery evidence from the real hub",
  );

  const approval = evidence.find((item) => item.category === "approval");
  const releaseClaim = evidence.find((item) => item.category === "receipt");
  const delivery = evidence.find((item) => item.category === "delivery");
  assert.match(approval?.id ?? "", /^approval:[0-9a-f]{20}$/);
  assert.match(approval?.label ?? "", /^Ledger approval claim requested:/);
  assert.match(approval?.description ?? "", /^self-attested by /);
  assert.match(releaseClaim?.label ?? "", /^Retained release-receipt claim:/);
  assert.match(releaseClaim?.detail ?? "", /No release authority is inferred\.$/);
  assert.equal(delivery?.label, `Undeliverable messages retained: ${deliveryTarget}`);
  assert.equal(delivery?.severity, "warning");

  await vscode.commands.executeCommand("synapse.showEvidence");
  const rendered = api.renderedEvidenceSnapshot();
  for (const item of rendered) {
    assert.ok(item.id.length <= 32, "Rendered tree node IDs must stay bounded.");
  }
  assert.deepEqual(
    rendered.find((item) => item.label === approval?.label),
    {
      id: approval?.id,
      label: approval?.label,
      description: approval?.description,
      tooltip: approval?.detail,
      iconId: "warning",
    },
  );
  assert.equal(rendered.find((item) => item.label === releaseClaim?.label)?.iconId, "history");
  assert.equal(rendered.find((item) => item.label === delivery?.label)?.iconId, "warning");
}
