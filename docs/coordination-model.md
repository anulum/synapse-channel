# Coordination model

The hub composes a handful of independent mechanisms into one coordination plane.

## 1. Plan

Any agent declares work on the shared blackboard. A declared task has an id, a
title, a description, and optional dependencies. The hub refuses dependency
cycles, so the set of *ready* tasks (open, with every dependency finished) is
always well-defined.

A declared task is the **plan**; a claim is the **lease** on doing it. The two
share a task id but stay independent, so the simple claim flow keeps working with
no plan entry at all.

## 2. Claim

An agent leases a task by id. The claim may declare a **file scope** — a
`worktree` and a set of `paths`. The hub refuses a claim whose file scope
overlaps another agent's live claim; agents in different worktrees never contend.

Every lease:

- **expires**, so a crashed agent never holds a claim forever;
- carries an **epoch**, so a paused or superseded agent cannot act on a dead
  claim;
- carries a **version** for optimistic concurrency, so a stale update is refused.

## 3. Work

The owner moves the task through a typed lifecycle
(`claimed → working → input_required → done`/`failed`); the hub rejects an
illegal transition. The owner can save a **checkpoint** — an opaque resume token
that survives lease expiry.

## 4. Hand off and recover

- **Atomic handoff** transfers a held task to another *online* agent in one step,
  with no release/re-claim window for a third agent to grab it. Scope, status,
  and checkpoint move with it.
- An **LLM-free supervisor** watches the plan and re-offers tasks that stall (no
  progress while in progress, or blocked with every dependency finished).
- A task taken over after its lease lapses **resumes from its last checkpoint**
  rather than restarting.

## 5. Route

Workers advertise **capability cards** describing their skills and the task
classes they can take; the hub aggregates them into a manifest. A request can be
classified into a task class and routed to the matching backend, reserving heavy
models for the genuinely hard requests.

## Durability and reconnection

With `--db`, the hub records every authoritative mutation to an append-only
SQLite event log (WAL) and rebuilds its state by replaying it on start-up. A
reconnecting agent uses an idempotency key (so a retried claim is applied once)
and a resume cursor (to fetch exactly the messages it missed).
