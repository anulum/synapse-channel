// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — JavaScript SDK against an isolated Python hub

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, createHmac, generateKeyPairSync, randomBytes, sign } from "node:crypto";
import { once } from "node:events";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { MessageType, SynapseClient } from "../dist/index.js";

function exchange(client, type, send) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      unsubscribe();
      reject(new Error(`Timed out waiting for ${type}`));
    }, 5000);
    const unsubscribe = client.on(type, (message) => {
      clearTimeout(timer);
      unsubscribe();
      resolve(message);
    });
    try {
      send();
    } catch (error) {
      clearTimeout(timer);
      unsubscribe();
      reject(error);
    }
  });
}

test("SDK interoperates with an authenticated Python hub", { timeout: 25000 }, async (t) => {
  assert.equal(typeof globalThis.WebSocket, "function", "Use Node 22+ for this test");
  const python = process.env.SYNAPSE_TEST_PYTHON ?? "python3";
  const child = spawn(
    python.includes("/") ? resolve(python) : python,
    [fileURLToPath(new URL("./hub_fixture.py", import.meta.url))],
    {
      cwd: fileURLToPath(new URL("../../../", import.meta.url)),
      env: {
        ...process.env,
        PYTHONPATH: fileURLToPath(new URL("../../../src/", import.meta.url)),
      },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  let diagnostics = "";
  child.stderr.on("data", (chunk) => {
    diagnostics = (diagnostics + chunk.toString()).slice(-65536);
  });
  const exited = new Promise((resolve) => {
    child.once("error", (error) => resolve({ error }));
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
  const watchdog = setTimeout(() => child.kill("SIGKILL"), 20000);
  const lines = createInterface({ input: child.stdout });
  t.after(async () => {
    lines.close();
    child.stdin.end();
    const killTimer = setTimeout(() => child.kill("SIGKILL"), 3000);
    const result = await exited;
    clearTimeout(killTimer);
    clearTimeout(watchdog);
    assert.deepEqual(result, { code: 0, signal: null }, diagnostics);
  });
  const ready = once(lines, "line", { signal: AbortSignal.timeout(5000) });
  const result = await Promise.race([
    ready.then(([line]) => JSON.parse(line)),
    exited.then((status) => {
      throw new Error(`Hub exited before readiness: ${JSON.stringify(status)} ${diagnostics}`);
    }),
  ]);
  const options = { uri: result.uri, token: "integration-only-token", readyTimeoutMs: 3000 };
  const alice = new SynapseClient({ ...options, name: "js-alice" });
  const bob = new SynapseClient({ ...options, name: "js-bob" });
  const rejected = new SynapseClient({ ...options, name: "js-invalid", token: "wrong" });
  t.after(() => { alice.close(); bob.close(); rejected.close(); });

  await assert.rejects(rejected.connect(), /hub closed the connection before welcoming/);
  assert.equal(rejected.isReady, false);
  await alice.connect();
  await bob.connect();
  assert.equal(alice.isReady, true);
  assert.equal(bob.isReady, true);

  const chat = await exchange(bob, MessageType.Chat, () =>
    alice.chat("real hub delivery", { target: "js-bob" }));
  assert.equal(chat.sender, "js-alice");
  assert.equal(chat.payload, "real hub delivery");
  await exchange(alice, MessageType.ClaimGranted, () => alice.claim("alice-task", ["shared.py"]));
  await exchange(bob, MessageType.ClaimDenied, () => bob.claim("bob-task", ["shared.py"]));
  await exchange(alice, MessageType.ReleaseGranted, () => alice.release("alice-task"));
  await exchange(bob, MessageType.ClaimGranted, () => bob.claim("bob-task", ["shared.py"]));
  await exchange(bob, MessageType.ReleaseGranted, () => bob.release("bob-task"));
  await exchange(alice, MessageType.StateSnapshot, () => alice.requestState());
  await exchange(alice, MessageType.BoardSnapshot, () => alice.requestBoard());
  await exchange(alice, MessageType.WhoSnapshot, () => alice.requestWho());
  const remote = await exchange(alice, MessageType.Error, () => alice.requestWho("nowhere"));
  assert.match(remote.payload, /unknown_hub/);

  alice.close();
  assert.equal(alice.isReady, false);
  await alice.connect();
  assert.equal(alice.isReady, true);
  await exchange(alice, MessageType.StateSnapshot, () => alice.requestState());
});

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

test("SDK transfers an attachment through the governed Python hub", { timeout: 25000 }, async (t) => {
  assert.equal(typeof globalThis.WebSocket, "function", "Use Node 22+ for this test");
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const publicRaw = publicKey.export({ type: "spki", format: "der" }).subarray(-32);
  const hmacSecret = randomBytes(32).toString("hex");
  const python = process.env.SYNAPSE_TEST_PYTHON ?? "python3";
  const child = spawn(
    python.includes("/") ? resolve(python) : python,
    [fileURLToPath(new URL("./hub_fixture.py", import.meta.url))],
    {
      cwd: fileURLToPath(new URL("../../../", import.meta.url)),
      env: {
        ...process.env,
        PYTHONPATH: fileURLToPath(new URL("../../../src/", import.meta.url)),
        SYNAPSE_TEST_ATTACHMENT: "1",
        SYNAPSE_TEST_ID_PUBLIC: publicRaw.toString("base64"),
        SYNAPSE_TEST_HMAC_SECRET: hmacSecret,
      },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  let diagnostics = "";
  child.stderr.on("data", (chunk) => {
    diagnostics = (diagnostics + chunk.toString()).slice(-65536);
  });
  const exited = new Promise((resolve) => {
    child.once("error", (error) => resolve({ error }));
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
  const watchdog = setTimeout(() => child.kill("SIGKILL"), 20000);
  const lines = createInterface({ input: child.stdout });
  t.after(async () => {
    lines.close();
    child.stdin.end();
    const killTimer = setTimeout(() => child.kill("SIGKILL"), 3000);
    const result = await exited;
    clearTimeout(killTimer);
    clearTimeout(watchdog);
    assert.deepEqual(result, { code: 0, signal: null }, diagnostics);
  });
  const ready = once(lines, "line", { signal: AbortSignal.timeout(5000) });
  const result = await Promise.race([
    ready.then(([line]) => JSON.parse(line)),
    exited.then((status) => {
      throw new Error(`Hub exited before readiness: ${JSON.stringify(status)} ${diagnostics}`);
    }),
  ]);
  let sequence = 0;
  const client = new SynapseClient({
    uri: result.uri,
    name: "proj/js-alice",
    token: "integration-only-token",
    signRegistration(frame) {
      const signed = { ...frame, signature: {
        version: 1, key_id: "js-id", algorithm: "ed25519",
        nonce: randomBytes(12).toString("hex"), sequence: 1,
        signed_at: Math.floor(Date.now() / 1000),
      } };
      signed.signature.value = sign(null, Buffer.from(canonical(signed)), privateKey).toString("base64");
      return signed;
    },
    signAttachment(frame) {
      const signed = { ...frame, auth: {
        alg: "hmac-sha256", kid: "js-hmac", nonce: randomBytes(12).toString("hex"),
        sequence: ++sequence, timestamp: Math.floor(Date.now() / 1000),
      } };
      signed.auth.value = createHmac("sha256", hmacSecret).update(canonical(signed)).digest("hex");
      return signed;
    },
  });
  t.after(() => client.close());
  await client.connect();
  const body = Buffer.from("signed JS evidence");
  const digest = createHash("sha256").update(body).digest("hex");
  const begin = await exchange(client, MessageType.AttachmentResult, () => client.attachment(
    MessageType.AttachmentBegin,
    { scope: "proj", digest, length: body.length, media_type: "text/plain",
      provenance: "claim:C12", expires_at: Math.floor(Date.now() / 1000) + 60 },
  ));
  assert.equal(begin.ok, true, JSON.stringify(begin));
  const uploadId = begin.upload_id;
  const chunk = await exchange(client, MessageType.AttachmentResult, () => client.attachment(
    MessageType.AttachmentChunk,
    { scope: "proj", upload_id: uploadId, offset: 0, body: body.toString("base64") },
  ));
  assert.equal(chunk.received, body.length, JSON.stringify(chunk));
  const committed = await exchange(client, MessageType.AttachmentResult, () => client.attachment(
    MessageType.AttachmentCommit, { scope: "proj", upload_id: uploadId },
  ));
  assert.equal(committed.metadata.digest, digest, JSON.stringify(committed));
  const read = await exchange(client, MessageType.AttachmentResult, () => client.attachment(
    MessageType.AttachmentRead, { scope: "proj", digest, offset: 0 },
  ));
  assert.equal(Buffer.from(read.body, "base64").toString(), body.toString(), JSON.stringify(read));
  assert.equal(read.eof, true);
});
