// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Studio access descriptor and read-only role pill
"use strict";

(function () {
  const config = window.__SYN_STUDIO__ || {};
  const accessUrl = config.accessUrl || "/dashboard-access.json";
  const pollMs = Math.max(1000, Number(config.pollMs) || 5000);
  const roles = new Set(["viewer", "operator", "admin"]);
  const capabilityNames = ["read", "message_send", "task_declare", "task_update"];
  const listeners = new Set();
  let started = false;
  let state = unavailable();

  function unavailable() {
    return Object.freeze({
      phase: "unavailable",
      principal: null,
      role: null,
      capabilities: Object.freeze({
        read: false, message_send: false, task_declare: false, task_update: false,
      }),
    });
  }

  function exactKeys(value, names) {
    const keys = Object.keys(value).sort();
    return keys.length === names.length && names.slice().sort().every((name, index) => name === keys[index]);
  }

  function parseDescriptor(value) {
    if (!value || typeof value !== "object" || !exactKeys(value, [
      "version", "principal", "role", "capabilities", "operator_armed", "trust_boundary",
    ])) return null;
    if (value.version !== 1 || typeof value.principal !== "string" || value.principal === "") return null;
    if (!roles.has(value.role) || typeof value.operator_armed !== "boolean") return null;
    if (typeof value.trust_boundary !== "string" || !value.capabilities || typeof value.capabilities !== "object") return null;
    if (!exactKeys(value.capabilities, capabilityNames)) return null;
    for (const name of capabilityNames) if (typeof value.capabilities[name] !== "boolean") return null;
    return Object.freeze({
      phase: "ready",
      principal: value.principal,
      role: value.role,
      capabilities: Object.freeze({ ...value.capabilities }),
    });
  }

  function authHeaders() {
    try {
      const token = sessionStorage.getItem("synapse-cockpit-bearer") ||
        sessionStorage.getItem("synapse-dashboard-token");
      return token && token.trim() ? { Authorization: "Bearer " + token.trim() } : {};
    } catch (_error) {
      return {};
    }
  }

  function publish(next) {
    state = next;
    const pill = document.getElementById("cc-access");
    if (pill) pill.textContent = next.phase === "ready"
      ? next.role + " · " + next.principal
      : "access unavailable";
    for (const listener of listeners) listener();
  }

  async function refresh() {
    try {
      const response = await fetch(accessUrl, {
        cache: "no-store",
        headers: authHeaders(),
      });
      if (!response.ok) throw new Error("access unavailable");
      const next = parseDescriptor(await response.json());
      if (!next) throw new Error("malformed access descriptor");
      publish(next);
    } catch (_error) {
      publish(unavailable());
    }
    return state;
  }

  async function loop() {
    await refresh();
    window.setTimeout(loop, pollMs);
  }

  function start() {
    if (started) return;
    started = true;
    void loop();
  }

  function subscribe(listener) {
    listeners.add(listener);
    return function () { listeners.delete(listener); };
  }

  window.SynapseStudioAccess = Object.freeze({
    parseDescriptor,
    refresh,
    snapshot: function () { return state; },
    start,
    subscribe,
  });
  start();
})();
