// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — VS Code hub connection and fleet state controller

/**
 * Own the extension's WebSocket lifecycle and mutable hub projection.
 *
 * Activation and credential prompts stay in `extension.ts`; transport policy
 * and credential keys stay in `hubAuth.ts`; view-model decisions stay in
 * `fleetModel.ts`. This controller joins those boundaries without owning the
 * command-registration or SecretStorage UI surfaces.
 */

import * as vscode from "vscode";
import { hubConnectionVerdict, registrationHeartbeat } from "./hubAuth.js";
import {
  boardItems,
  claimMarks,
  claimRequest,
  hubHealth,
  statusBarText,
  type BoardItem,
  type ClaimMark,
  type RawClaim,
  type RawTask,
} from "./fleetModel.js";

interface HubEnvelope {
  type?: string;
  online_agents?: string[];
  tasks?: RawTask[];
  active_claims?: RawClaim[];
  board?: { tasks?: RawTask[] };
  snapshot?: { active_claims?: RawClaim[] };
}

interface HubState {
  connected: boolean;
  agents: string[];
  tasks: RawTask[];
  claims: RawClaim[];
}

interface BoardSink {
  replace(items: BoardItem[]): void;
}

const CLAIM_TASK_PREFIX = "vscode";

export class FleetController {
  private readonly state: HubState = { connected: false, agents: [], tasks: [], claims: [] };
  private socket: WebSocket | undefined;
  private connectionEpoch = 0;
  private heartbeatTimer: ReturnType<typeof setInterval> | undefined;

  constructor(
    private readonly identity: string,
    private readonly statusBar: vscode.StatusBarItem,
    private readonly board: BoardSink,
    private readonly onChange: () => void,
  ) {}

  connect(uri: string, token?: string): string | undefined {
    this.closeConnection();
    const verdict = hubConnectionVerdict(uri);
    if (!verdict.allowed) {
      this.render();
      return verdict.reason;
    }

    const epoch = this.connectionEpoch;
    const socket = new WebSocket(verdict.uri);
    this.socket = socket;
    let registration = JSON.stringify(registrationHeartbeat(this.identity, token));
    const onOpen = (): void => {
      socket.removeEventListener("open", onOpen);
      if (epoch !== this.connectionEpoch) {
        registration = "";
        return;
      }
      const frame = registration;
      registration = "";
      socket.send(frame);
    };
    socket.addEventListener("open", onOpen);
    socket.addEventListener("close", () => {
      if (epoch !== this.connectionEpoch) {
        return;
      }
      this.stopHeartbeat();
      this.socket = undefined;
      this.state.connected = false;
      this.render();
    });
    socket.addEventListener("message", (event: MessageEvent) => {
      if (epoch === this.connectionEpoch) {
        this.ingest(String(event.data), socket, epoch);
      }
    });
    this.render();
    return undefined;
  }

  private ingest(raw: string, socket: WebSocket, epoch: number): void {
    let frame: HubEnvelope;
    try {
      frame = JSON.parse(raw) as HubEnvelope;
    } catch {
      return;
    }
    const becameReady = frame.type === "welcome" && !this.state.connected;
    if (frame.type === "welcome") {
      this.state.connected = true;
    }
    if (frame.online_agents) {
      this.state.agents = frame.online_agents;
    }
    const tasks = frame.tasks ?? frame.board?.tasks;
    if (tasks) {
      this.state.tasks = tasks;
    }
    const claims = frame.active_claims ?? frame.snapshot?.active_claims;
    if (claims) {
      this.state.claims = claims;
    }
    if (becameReady) {
      this.startHeartbeat(socket, epoch);
      this.send("who_request");
      this.send("board_request");
      this.send("state_request");
    } else if (frame.type === "claim_granted" || frame.type === "release_granted") {
      this.send("state_request");
    }
    this.render();
  }

  private startHeartbeat(socket: WebSocket, epoch: number): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (epoch === this.connectionEpoch && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(registrationHeartbeat(this.identity)));
      }
    }, 20_000);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== undefined) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = undefined;
    }
  }

  private send(type: string, extra: Record<string, unknown> = {}): boolean {
    const socket = this.socket;
    if (!this.state.connected || socket === undefined || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    socket.send(JSON.stringify({ type, sender: this.identity, ...extra }));
    return true;
  }

  private closeConnection(): void {
    this.connectionEpoch += 1;
    this.stopHeartbeat();
    const socket = this.socket;
    this.socket = undefined;
    this.state.connected = false;
    this.state.agents = [];
    this.state.tasks = [];
    this.state.claims = [];
    socket?.close();
  }

  render(): void {
    const health = hubHealth(this.state.connected, this.state.agents);
    const marks = this.marks();
    const mine = marks.filter((mark) => mark.mine).length;
    this.statusBar.text = statusBarText(health, mine);
    this.statusBar.show();
    this.board.replace(boardItems(this.state.tasks));
    this.onChange();
  }

  marks(): ClaimMark[] {
    return claimMarks(this.state.claims, this.identity);
  }

  claimSnapshot(): readonly RawClaim[] {
    return this.state.claims;
  }

  claimActiveFile(): void {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return;
    }
    const path = vscode.workspace.asRelativePath(editor.document.uri, false);
    const request = claimRequest(`${CLAIM_TASK_PREFIX}/${this.identity}`, path);
    this.send("claim", { task_id: request.taskId, paths: request.paths });
  }

  releaseActiveFile(): void {
    if (!vscode.window.activeTextEditor) {
      return;
    }
    this.send("release", { task_id: `${CLAIM_TASK_PREFIX}/${this.identity}` });
  }

  dispose(): void {
    this.closeConnection();
  }
}
