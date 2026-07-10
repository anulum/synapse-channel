// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real token-gated hub Extension Development Host acceptance

import { strict as assert } from "node:assert";
import * as vscode from "vscode";

interface ClaimRecord {
  task_id?: string;
  owner?: string;
  paths?: string[];
}

interface HubFrame {
  type?: string;
  online_agents?: string[];
  snapshot?: { active_claims?: ClaimRecord[] };
}

const EXTENSION_ID = "anulum.synapse-channel-vscode";
let probeSequence = 0;

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) {
    throw new Error(`Missing integration environment variable ${name}.`);
  }
  return value;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

async function queryHub(uri: string, token: string, request: string, response: string): Promise<HubFrame> {
  const sender = `vscode-integration-probe-${probeSequence += 1}`;
  const socket = new WebSocket(uri);
  return await new Promise<HubFrame>((resolveFrame, reject) => {
    let settled = false;
    let requested = false;
    const timer = setTimeout(() => {
      settled = true;
      socket.close();
      reject(new Error(`Timed out waiting for ${response}.`));
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
      if (frame.type === "welcome" && !requested) {
        requested = true;
        socket.send(JSON.stringify({ type: request, sender }));
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

async function waitForRoster(
  uri: string,
  token: string,
  identity: string,
  expectedPresent: boolean,
): Promise<void> {
  const deadline = Date.now() + 8_000;
  do {
    const frame = await queryHub(uri, token, "who_request", "who_snapshot");
    const present = frame.online_agents?.includes(identity) ?? false;
    if (present === expectedPresent) {
      return;
    }
    await delay(150);
  } while (Date.now() < deadline);
  throw new Error(`Identity ${identity} did not become ${expectedPresent ? "present" : "absent"}.`);
}

async function waitForClaim(
  uri: string,
  token: string,
  taskId: string,
): Promise<ClaimRecord> {
  const deadline = Date.now() + 8_000;
  do {
    const frame = await queryHub(uri, token, "state_request", "state_snapshot");
    const claim = frame.snapshot?.active_claims?.find((candidate) => candidate.task_id === taskId);
    if (claim !== undefined) {
      return claim;
    }
    await delay(150);
  } while (Date.now() < deadline);
  throw new Error(`Claim ${taskId} did not appear in the real hub state.`);
}

export async function run(): Promise<void> {
  const uri = requiredEnvironment("SYNAPSE_VSCODE_TEST_URI");
  const token = requiredEnvironment("SYNAPSE_VSCODE_TEST_TOKEN");
  const folder = vscode.workspace.workspaceFolders?.[0];
  assert.ok(folder, "The integration workspace must be open.");
  const identity = `${folder.name}/vscode`;
  const taskId = `vscode/${identity}`;
  const extension = vscode.extensions.getExtension(EXTENSION_ID);
  assert.ok(extension, `Extension ${EXTENSION_ID} was not loaded.`);

  const configuration = vscode.workspace.getConfiguration("synapse");
  await configuration.update("hubUri", uri, vscode.ConfigurationTarget.Global);
  await extension.activate();
  try {
    await vscode.commands.executeCommand("synapse.setHubToken", "incorrect-integration-token");
    await delay(400);
    await waitForRoster(uri, token, identity, false);

    await vscode.commands.executeCommand("synapse.setHubToken", token);
    await waitForRoster(uri, token, identity, true);
    assert.equal(configuration.get<string>("token"), undefined);
    assert.equal(configuration.get<string>("hubToken"), undefined);

    const document = await vscode.workspace.openTextDocument(
      vscode.Uri.joinPath(folder.uri, "sample.txt"),
    );
    await vscode.window.showTextDocument(document);
    await vscode.commands.executeCommand("synapse.claimFile");
    const claim = await waitForClaim(uri, token, taskId);
    assert.equal(claim.owner, identity);
    assert.deepEqual(claim.paths, ["sample.txt"]);

    await vscode.commands.executeCommand("synapse.clearHubToken");
    await waitForRoster(uri, token, identity, false);
    console.log("SYNAPSE_VSCODE_SECURE_HOST_ACCEPTANCE_PASS");
  } finally {
    await vscode.commands.executeCommand("synapse.clearHubToken");
    await configuration.update("hubUri", undefined, vscode.ConfigurationTarget.Global);
  }
}
