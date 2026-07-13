// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — external upgrade acceptance hub probe

import { WebSocket, type RawData } from "ws";

async function roster(uri: string, token: string): Promise<string[]> {
  const sender = `vscode-upgrade-probe-${Date.now()}`;
  const socket = new WebSocket(uri);
  return await new Promise<string[]>((resolve, reject) => {
    const timer = setTimeout(() => {
      socket.close();
      reject(new Error("Timed out waiting for the upgrade roster."));
    }, 5_000);
    socket.on("open", () => socket.send(JSON.stringify({
      type: "heartbeat",
      sender,
      target: "System",
      payload: "online",
      token,
    })));
    socket.on("message", (data: RawData) => {
      const frame = JSON.parse(String(data)) as { type?: string; online_agents?: string[] };
      if (frame.type === "welcome") {
        socket.send(JSON.stringify({ type: "who_request", sender }));
      } else if (frame.type === "who_snapshot") {
        clearTimeout(timer);
        socket.close();
        resolve(frame.online_agents ?? []);
      }
    });
    socket.on("error", () => {
      clearTimeout(timer);
      reject(new Error("Upgrade roster probe failed."));
    });
  });
}

export async function waitForRoster(
  uri: string,
  token: string,
  identity: string,
  expected: boolean,
): Promise<void> {
  const deadline = Date.now() + 15_000;
  do {
    if ((await roster(uri, token)).includes(identity) === expected) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  } while (Date.now() < deadline);
  throw new Error(`Upgrade identity ${identity} did not become ${expected ? "present" : "absent"}.`);
}
