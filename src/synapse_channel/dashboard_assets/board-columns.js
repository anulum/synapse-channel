// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — safe read-only board-column DOM renderer
"use strict";

(function () {
  const COLUMN_TONES = Object.freeze({
    open: "neutral",
    claimed: "warn",
    working: "warn",
    input_required: "bad",
    blocked: "bad",
    closed: "good",
    other: "neutral",
  });

  // Disclosures the reader opened. A refresh rebuilds the columns, so the open
  // state is kept here and restored, keyed by host and column.
  const openDisclosures = new Set();

  function cardLimit(options) {
    const limit = options && typeof options === "object" ? options.cardLimit : undefined;
    return Number.isInteger(limit) && limit > 0 ? limit : null;
  }

  function collapsedColumns(options) {
    const ids = options && typeof options === "object" ? options.collapsed : undefined;
    return Array.isArray(ids) ? ids.map(String) : [];
  }

  function rows(value) {
    return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
  }

  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value == null ? "" : value);
    return item;
  }

  function exactStatus(card) {
    const board = String(card.board_status || "");
    const claim = String(card.claim_status || "");
    if (board && claim) return board + " · " + claim;
    return claim || board || "unknown";
  }

  function appendMeta(host, label, value) {
    if (!value) return;
    const row = node("div", "syn-board-card__meta");
    row.append(node("span", "syn-board-card__meta-label", label), node("span", "", value));
    host.appendChild(row);
  }

  function renderCard(card) {
    const article = node("article", "syn-board-card");
    const title = String(card.title || card.task_id || "unnamed task");
    article.dataset.column = String(card.column || "other");
    article.setAttribute("aria-label", String(card.task_id || title) + ": " + exactStatus(card));
    article.append(
      node("div", "syn-board-card__id", card.task_id || "unnamed"),
      node("div", "syn-board-card__title", title),
      node("div", "syn-board-card__status", exactStatus(card)),
    );
    const flags = node("div", "syn-board-card__flags");
    if (card.ready === true) flags.appendChild(node("span", "syn-board-flag", "ready"));
    if (card.declared === false) flags.appendChild(node("span", "syn-board-flag", "ad-hoc claim"));
    if (card.lease_stale === true) flags.appendChild(node("span", "syn-board-flag", "stale lease"));
    if (flags.childElementCount) article.appendChild(flags);
    appendMeta(article, "owner", card.owner || card.suggested_owner);
    const blockedBy = Array.isArray(card.blocked_by) ? card.blocked_by.join(", ") : "";
    appendMeta(article, "waiting on", blockedBy);
    const unknown = Array.isArray(card.unknown_dependencies)
      ? card.unknown_dependencies.join(", ") : "";
    appendMeta(article, "dependency state unknown", unknown);
    return article;
  }

  function disclosure(key, summary, hidden) {
    const details = node("details", "syn-board-more");
    details.open = openDisclosures.has(key);
    details.addEventListener("toggle", () => {
      if (details.open) openDisclosures.add(key);
      else openDisclosures.delete(key);
    });
    details.appendChild(node("summary", "syn-board-more__summary", summary));
    for (const card of hidden) details.appendChild(renderCard(card));
    return details;
  }

  function renderColumn(column, hostKey, limit, collapsed) {
    const section = node("section", "syn-board-column");
    const columnId = String(column.id || "other");
    const tasks = rows(column.tasks);
    const folded = collapsed.includes(columnId);
    const shown = folded ? 0 : limit === null ? tasks.length : Math.min(limit, tasks.length);
    section.dataset.column = columnId;
    section.dataset.tone = COLUMN_TONES[columnId] || "neutral";
    const heading = node("div", "syn-board-column__head");
    heading.append(
      node("h3", "syn-board-column__title", column.label || columnId),
      node("span", "syn-board-column__count", tasks.length),
    );
    const body = node("div", "syn-board-column__body");
    if (tasks.length) {
      for (const task of tasks.slice(0, shown)) body.appendChild(renderCard(task));
      const hidden = tasks.slice(shown);
      if (hidden.length) {
        const summary = folded
          ? hidden.length + " " + String(column.label || columnId).toLowerCase()
          : hidden.length + " more of " + tasks.length;
        body.appendChild(disclosure(hostKey + ":" + columnId, summary, hidden));
      }
    } else {
      body.appendChild(node("div", "syn-board-column__empty", "No tasks"));
    }
    section.append(heading, body);
    return section;
  }

  // options.cardLimit: a positive integer bounds the cards shown per column; the
  // rest stay in the page inside a closed disclosure. options.collapsed: column
  // ids whose cards are all inside the disclosure. Without usable options every
  // card is rendered, as before. The count in a column head is always the whole.
  function render(host, projection, options) {
    if (!host) return;
    const limit = cardLimit(options);
    const collapsed = collapsedColumns(options);
    const hostKey = host.id || "board";
    host.replaceChildren();
    const columns = rows(projection && projection.columns);
    if (!columns.length) {
      host.appendChild(node("div", "syn-board-empty", "Board projection unavailable"));
      return;
    }
    for (const column of columns) {
      host.appendChild(renderColumn(column, hostKey, limit, collapsed));
    }
    if (projection.source_truncated === true) {
      const shown = Number(projection.declared_tasks) || 0;
      const total = Number(projection.total_declared_tasks) || shown;
      host.appendChild(
        node("p", "syn-board-source-note", "Source snapshot truncated: showing " + shown + " of " + total),
      );
    }
  }

  window.SynapseBoardColumns = Object.freeze({ render });
})();
