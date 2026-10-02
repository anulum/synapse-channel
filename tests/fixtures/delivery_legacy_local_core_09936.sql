-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Commercial license available
-- © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
-- © Code 2020–2026 Miroslav Šotek. All rights reserved.
-- ORCID: 0009-0009-3560-0851
-- Contact: www.anulum.li | protoscience@anulum.li
-- SYNAPSE_CHANNEL — explicit receiving-hub journal recovery
BEGIN TRANSACTION;
CREATE TABLE delivery_mutations (operation_key TEXT NOT NULL, mutation_id TEXT NOT NULL, mutation_digest TEXT NOT NULL, event_seq INTEGER NOT NULL, PRIMARY KEY(operation_key, mutation_id));
CREATE TABLE delivery_notifications (notification_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL, audience TEXT NOT NULL, frame_json TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, delivered_at REAL, retired_at REAL);
INSERT INTO "delivery_notifications" VALUES('delivery:62b99fe4cb3d477adc29c4218850f8aa51b9f2555d55c5e7b78fed64166bf1a1:1','62b99fe4cb3d477adc29c4218850f8aa51b9f2555d55c5e7b78fed64166bf1a1','PROJ/bob','{"notification_id":"delivery:62b99fe4cb3d477adc29c4218850f8aa51b9f2555d55c5e7b78fed64166bf1a1:1","operation_key":"62b99fe4cb3d477adc29c4218850f8aa51b9f2555d55c5e7b78fed64166bf1a1","target":"PROJ/bob","target_incarnation":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","type":"delivery_offer"}',0,NULL,NULL);
CREATE TABLE delivery_quarantine (operation_key TEXT PRIMARY KEY, reason_code TEXT NOT NULL, observed_at REAL NOT NULL);
CREATE TABLE delivery_receipt_outbox (notification_id TEXT PRIMARY KEY, message_seq INTEGER NOT NULL, phase TEXT NOT NULL, sender TEXT NOT NULL, frame_json TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, delivered_at REAL, FOREIGN KEY(message_seq) REFERENCES delivery_receipts(message_seq));
CREATE TABLE delivery_receipts (message_seq INTEGER PRIMARY KEY, sender TEXT NOT NULL, target TEXT NOT NULL, message_id INTEGER NOT NULL, client_msg_id TEXT NOT NULL, state TEXT NOT NULL, delivered INTEGER, deferred INTEGER NOT NULL, acked_by TEXT NOT NULL, updated_event_seq INTEGER NOT NULL, FOREIGN KEY(message_seq) REFERENCES events(seq), FOREIGN KEY(updated_event_seq) REFERENCES events(seq));
CREATE TABLE delivery_requests (operation_key TEXT PRIMARY KEY, sender TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL, request_json TEXT NOT NULL, target TEXT NOT NULL, target_incarnation TEXT NOT NULL, deadline REAL NOT NULL, selected_mode TEXT NOT NULL, quality TEXT NOT NULL, stage TEXT NOT NULL, cancel_requested INTEGER NOT NULL, boundary_delivered INTEGER NOT NULL, explicitly_acknowledged INTEGER NOT NULL, ordinal INTEGER NOT NULL, latest_event_seq INTEGER NOT NULL, UNIQUE(sender, idempotency_key));
INSERT INTO "delivery_requests" VALUES('62b99fe4cb3d477adc29c4218850f8aa51b9f2555d55c5e7b78fed64166bf1a1','PROJ/alice','legacy-local','8a4e47d701fd4decd8593c4ece2d8a39192e6a336d9d8434a7041dd55f09ea80','{"allowed_fallbacks":[],"body":"Record a released journal for explicit ownership recovery.","deadline":160.0,"idempotency_key":"legacy-local","mode":"next_turn","origin_hub":"workstation","profile":3,"request_id":"legacy-local","sender":"PROJ/alice","target":"PROJ/bob","target_incarnation":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","task_id":"LEGACY-LOCAL"}','PROJ/bob','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',160.0,'next_turn','emulated','queued',0,0,0,1,2);
CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, mac TEXT);
INSERT INTO "events" VALUES(1,1.79091191688083124e+09,'delivery_intent_accepted','{"digest":"8a4e47d701fd4decd8593c4ece2d8a39192e6a336d9d8434a7041dd55f09ea80","operation_key":"62b99fe4cb3d477adc29c4218850f8aa51b9f2555d55c5e7b78fed64166bf1a1","profile":3,"request":{"allowed_fallbacks":[],"body":"Record a released journal for explicit ownership recovery.","deadline":160.0,"idempotency_key":"legacy-local","mode":"next_turn","origin_hub":"workstation","profile":3,"request_id":"legacy-local","sender":"PROJ/alice","target":"PROJ/bob","target_incarnation":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","task_id":"LEGACY-LOCAL"}}',NULL);
INSERT INTO "events" VALUES(2,1.79091191688083124e+09,'delivery_intent_queued','{"notification_id":"delivery:62b99fe4cb3d477adc29c4218850f8aa51b9f2555d55c5e7b78fed64166bf1a1:1","operation_key":"62b99fe4cb3d477adc29c4218850f8aa51b9f2555d55c5e7b78fed64166bf1a1","ordinal":1,"profile":3,"quality":"emulated","selected_mode":"next_turn","stage":"queued"}',NULL);
CREATE TABLE message_forward_inbound (origin_hub TEXT NOT NULL, forward_id TEXT NOT NULL, digest TEXT NOT NULL, result_json TEXT NOT NULL, received_at REAL NOT NULL, PRIMARY KEY(origin_hub, forward_id));
CREATE TABLE message_forward_outbox (forward_id TEXT PRIMARY KEY, peer_hub TEXT NOT NULL, sender TEXT NOT NULL, target TEXT NOT NULL, request_json TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, attempts INTEGER NOT NULL, next_attempt_at REAL NOT NULL, state TEXT NOT NULL, result_json TEXT NOT NULL, notify_sender INTEGER NOT NULL, sender_notified_at REAL);
CREATE TABLE message_forward_remote_deliveries (operation_key TEXT PRIMARY KEY, peer_hub TEXT NOT NULL, sender TEXT NOT NULL, target TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE operation_outbox (operation_key TEXT PRIMARY KEY, intent_json TEXT NOT NULL, receipt_id TEXT, FOREIGN KEY(operation_key) REFERENCES operations(operation_key));
CREATE TABLE operations (operation_key TEXT PRIMARY KEY, request_digest TEXT NOT NULL, response_json TEXT NOT NULL, response_sha256 TEXT NOT NULL, first_event_seq INTEGER NOT NULL, commit_seq INTEGER NOT NULL, committed_at REAL NOT NULL);
CREATE INDEX delivery_receipt_outbox_pending_idx ON delivery_receipt_outbox(sender, delivered_at);
CREATE INDEX delivery_pending_idx ON delivery_requests(target, target_incarnation, stage, deadline);
CREATE INDEX delivery_notification_pending_idx ON delivery_notifications(audience, delivered_at);
CREATE INDEX message_forward_outbox_due_idx ON message_forward_outbox(state, next_attempt_at);
CREATE INDEX message_forward_outbox_notify_idx ON message_forward_outbox(sender, notify_sender, sender_notified_at);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('events',2);
COMMIT;
