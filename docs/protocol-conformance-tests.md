<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — which protocol versions and features the tests exercise
-->

# Protocol conformance tests

This page lists the A2A and MCP protocol versions and features that repository
tests exercise on a real surface, and names each test. A real surface is the
product's own HTTP or HTTPS server, a real SDK client, a real stdio process or
a real hub. A test of a helper alone is not listed.

The page does not certify conformance with the whole of either standard. A
feature that is not listed here, or that is listed as **not tested**, has no
such evidence. `tests/test_protocol_conformance_tests_doc.py` checks that every
test named here exists. It does not check that the test still asserts what the
row says, so review the test before relying on a row.

## Versions under test

| Protocol | Version exercised | Where the version is fixed |
| --- | --- | --- |
| A2A | `1.0` (`A2A-Version: 1.0` header or query); specification `1.0.0` | `synapse_channel.a2a.A2A_PROTOCOL_VERSION`, `a2a_http_protocol.SUPPORTED_A2A_VERSION` |
| A2A | No `A2A-Version` header: the pre-1.0 compatibility path, without task continuation | `a2a_http_protocol` |
| MCP | `2025-11-25` on HTTPS; `2024-11-05`, `2025-03-26` and `2025-06-18` are negotiated as requested | MCP Python SDK `1.30.0`, pinned in `pyproject.toml` |
| MCP | stdio tests do not pin a version; they use what the SDK client sends | MCP Python SDK `1.30.0` |

The JSON-RPC binding of the A2A bridge uses the pre-1.0 method names
(`message/send`, `tasks/get`, `tasks/cancel`). The A2A 1.0 names (`SendMessage`,
`GetTask`) exist only on the optional gRPC subset.

## A2A bridge

| Feature | Tests | Not tested |
| --- | --- | --- |
| Bearer required on every route family | `tests/test_a2a_security.py::test_auth_token_protects_a2a_routes`, `tests/test_a2a_route_auth_versions.py::test_missing_bearer_is_refused_with_the_aip193_body_and_no_effect`, `tests/test_a2a_server_routes.py::test_delete_route_requires_authorization`, `tests/test_a2a_grpc.py::test_grpc_policy_requires_bearer_before_any_bridge_effect` | — |
| Constant-time bearer comparison | `tests/test_a2a_security.py::test_auth_token_uses_constant_time_bearer_comparison` | — |
| Client bearer never forwarded to the hub | `tests/test_a2a_credential_isolation.py::test_client_bearer_never_reaches_the_hub_on_any_message_route`, `tests/test_a2a_credential_isolation.py::test_unauthenticated_request_forwards_nothing_to_the_hub` | Push webhooks carry only the configured `authentication` (`tests/test_a2a_server_push.py::test_push_config_origin_change_without_reauthorization_drops_credentials`); no test sends a client bearer and then inspects webhook headers |
| Host and Origin authority | `tests/test_a2a_security.py::test_untrusted_or_missing_host_is_refused_before_every_route`, `tests/test_a2a_security.py::test_disallowed_origin_is_refused_on_every_method` | — |
| Task life cycle: working, completed, failed, canceled | `tests/test_a2a_server_routes.py::test_message_send_creates_completed_task_and_forwards_text_to_synapse`, `tests/test_a2a_server_lifecycle.py::test_timeout_marks_open_task_failed`, `tests/test_a2a_server_correlation.py::test_handle_synapse_frame_correlates_reply_and_completes_task` | `REJECTED`, `INPUT_REQUIRED` and `AUTH_REQUIRED` are not produced by the bridge |
| Task continuation (A2A 1.0) | `tests/test_a2a_tck_http.py::test_v1_existing_task_continues_and_infers_context`, `tests/test_a2a_tck_http.py::test_v1_terminal_task_rejects_followup` | — |
| Duplicate task id, duplicate hub reply | `tests/test_a2a_server_jsonrpc_validation.py::test_message_send_rejects_duplicate_task_id`, `tests/test_a2a_server_correlation.py::test_duplicate_correlated_reply_does_not_append_second_completion` | A repeated `messageId` is not deduplicated; push delivery is not deduplicated |
| Cancellation | `tests/test_a2a_server_routes.py::test_cancel_route_success_and_unknown_post_route`, `tests/test_a2a_route_auth_versions.py::test_cancel_is_idempotent_and_sends_nothing_to_the_hub` | Cancelling a terminal task returns it unchanged; `TaskNotCancelable` is not raised for cancel. Local cancellation does not stop a remote agent |
| Version negotiation | `tests/test_a2a_tck_http.py::test_explicit_unsupported_version_returns_aip193_error`, `tests/test_a2a_tck_http.py::test_compatible_or_patch_versions_are_accepted`, `tests/test_a2a_route_auth_versions.py::test_unsupported_version_is_refused_on_rpc_and_stream_before_any_task` | gRPC version errors |
| Error mapping (REST, AIP-193) | `tests/test_a2a_tck_http.py::test_v1_unknown_message_task_id_returns_task_not_found`, `tests/test_a2a_server_jsonrpc_validation.py::test_bad_json_returns_a2a_problem_json` | — |
| Error mapping (JSON-RPC) | `tests/test_a2a_server_jsonrpc_validation.py::test_json_rpc_unknown_method_returns_method_not_found_error` | The A2A codes `-32001` to `-32007` are not emitted; an unknown task is `-32602`; malformed JSON on `/rpc` is an HTTP 400 problem, not `-32700` |

## HTTP MCP server

| Feature | Tests | Not tested |
| --- | --- | --- |
| Bearer verification and live revocation | `tests/test_mcp_http_auth.py::test_invalid_identity_refused_over_https`, `tests/test_mcp_http_auth.py::test_authentication_and_live_revocation`, `tests/test_mcp_http_application.py::test_http_admission_origin_metadata_and_revocation` | Claim-level 401 cases run against an SDK app with the production verifier, not the production router |
| Subject-to-seat identity, session isolation | `tests/test_mcp_http_application.py::test_native_identities_mutations_resources_and_reconnect`, `tests/test_mcp_http_policy.py::test_foreign_authenticated_subject_cannot_use_a_bound_seat` | — |
| Client bearer never forwarded to the hub or an outbound MCP server | `tests/test_mcp_http_credential_isolation.py::test_bearer_never_reaches_the_hub_through_any_mutation_or_read`, `tests/test_mcp_http_agent.py::test_operation_keys_remain_bound_to_native_principals`, `tests/test_mcp_http_credential_isolation.py::test_the_mcp_server_cannot_reach_the_outbound_mcp_client` | The outbound check reads static imports only; a module named at run time is outside it |
| Mutation life cycle and operation id | `tests/test_mcp_http_application.py::test_native_identities_mutations_resources_and_reconnect` | — |
| Duplicates and retry | `tests/test_mcp_http_application.py::test_mutation_retry_survives_a_real_hub_and_http_restart`, `tests/test_mcp_http_server.py::test_reply_budget_retains_committed_mutation_identity` | Receiver deduplication of directed chat is tested natively only |
| Cancellation and deadlines | `tests/test_mcp_http_server.py::test_native_refusal_obeys_dispatch_deadline_and_recovers`, `tests/test_mcp_outcomes_native.py::test_lost_real_grant_stays_unknown_and_inquiry_does_not_replay` | `notifications/cancelled` during an in-flight native mutation |
| Version negotiation and header | `tests/test_mcp_http_protocol_versions.py::test_versions_and_error_codes_on_the_production_router` | — |
| JSON-RPC error codes | `tests/test_mcp_http_protocol_versions.py::test_versions_and_error_codes_on_the_production_router` | — |

### Known deviations of MCP SDK 1.30.0

These are pinned by `tests/test_mcp_http_protocol_versions.py`, so an SDK
upgrade that changes them fails a test instead of silently changing this page:

- An unknown JSON-RPC method is answered `-32602`; JSON-RPC 2.0 names `-32601`.
- A session negotiated at `2025-11-25` accepts a request whose
  `MCP-Protocol-Version` header names another supported version. An
  unsupported header value is refused with HTTP 400 and `-32600`.
