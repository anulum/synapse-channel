// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Studio durable-event and operator-action feed client
"use strict";

(function () {
  function items(value) {
    return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
  }

  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value == null ? "" : value);
    return item;
  }

  function empty(host, message) {
    if (!host) return;
    host.replaceChildren(node("div", "cc-empty", message));
  }

  function feedRow(first, second, third) {
    const row = node("div", "syn-row cc-feed-row");
    row.append(node("span", "", first), node("span", "", second), node("span", "", third));
    return row;
  }

  function eventSubject(event) {
    const payload = event.payload && typeof event.payload === "object" ? event.payload : {};
    return payload.task_id || payload.target || payload.sender || payload.owner ||
      payload.action || payload.kind || "";
  }

  function renderEvents(documentJson) {
    const host = document.getElementById("cc-livefeed-list");
    if (!host) return;
    const events = items(documentJson && documentJson.events);
    if (!events.length && host.childElementCount && !host.querySelector(".cc-empty")) return;
    if (!events.length) {
      empty(host, "waiting for new events");
      return;
    }
    if (host.querySelector(".cc-empty")) host.replaceChildren();
    for (const event of events.slice(-20)) {
      host.prepend(feedRow("#" + String(event.seq || ""), event.kind || "event", eventSubject(event)));
    }
    while (host.childElementCount > 20) host.removeChild(host.lastElementChild);
  }

  function renderOperatorActions(documentJson) {
    const host = document.getElementById("cc-actions-list");
    if (!host) return;
    const actions = items(documentJson && documentJson.actions);
    host.replaceChildren();
    if (!actions.length) {
      empty(
        host,
        documentJson && documentJson.present
          ? "no operator relay actions yet"
          : "operator-actions feed not configured",
      );
      return;
    }
    for (const action of actions.slice(-15).reverse()) {
      const label = [action.action || "action", action.task_id || "", action.status || ""]
        .filter(Boolean)
        .join(" · ");
      host.appendChild(
        feedRow("#" + String(action.seq || ""), action.direction || "relay", label),
      );
    }
  }

  function start(config) {
    const eventsUrl = String(config.eventsUrl || "/events.json");
    const operatorActionsUrl = String(config.operatorActionsUrl || "/operator-actions.json");
    const pollMs = Math.max(1000, Number(config.pollMs) || 5000);
    let eventCursor = "latest";
    let eventFeedConfigured = true;
    let operatorActionsConfigured = true;

    async function pollEvents() {
      if (!eventFeedConfigured) return;
      try {
        const url = eventsUrl + "?since=" + encodeURIComponent(String(eventCursor)) + "&limit=20";
        const response = await fetch(url, { cache: "no-store" });
        if (response.status === 404) {
          eventFeedConfigured = false;
          empty(document.getElementById("cc-livefeed-list"), "event feed not configured");
          return;
        }
        if (!response.ok) throw new Error("events " + response.status);
        const documentJson = await response.json();
        if (documentJson.next_cursor != null) eventCursor = Number(documentJson.next_cursor);
        renderEvents(documentJson);
      } catch (_error) {
        empty(document.getElementById("cc-livefeed-list"), "event feed unavailable");
      } finally {
        if (eventFeedConfigured) setTimeout(pollEvents, pollMs);
      }
    }

    async function pollOperatorActions() {
      if (!operatorActionsConfigured) return;
      try {
        const response = await fetch(operatorActionsUrl + "?limit=15", { cache: "no-store" });
        if (response.status === 404) {
          operatorActionsConfigured = false;
          renderOperatorActions({ present: false, actions: [] });
          return;
        }
        if (!response.ok) throw new Error("operator-actions " + response.status);
        renderOperatorActions(await response.json());
      } catch (_error) {
        empty(document.getElementById("cc-actions-list"), "operator-actions feed unavailable");
      } finally {
        if (operatorActionsConfigured) setTimeout(pollOperatorActions, pollMs);
      }
    }

    void pollEvents();
    void pollOperatorActions();
  }

  window.SynapseStudioFeeds = Object.freeze({ renderEvents, renderOperatorActions, start });
})();
