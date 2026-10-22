# Acceptance matrix

The acceptance matrix turns the 52 risks of the product specification
([section 19](reference/2026-10-22-ha-access-ui-spezifikation-chatgpt.md#19-abnahmematrix), German) into checks that a
release has to pass. Each case is an automated backend or API test or a documented device test. A control that is
merely hidden in the user interface never counts as a pass. The design maps every threat to its mechanism and cases
in [section 10](superpowers/specs/2026-10-22-myha-design.md#10-sicherheit--zuordnung-bedrohung--mechanismus) and
assigns the cases to work packages (WP) in section 13.

This page shows the status after Plan 1 and the first Plan 2 tasks (up to 8.4), verified on 2026-10-22 against
`origin/main`. The work is split into four plans:

- **Plan 1, foundation:** gateway, simulator, Home Assistant client, data model, policy engine, admin identity.
- **Plan 2, vertical core:** guest redemption, sessions, projection, live events, commands, preview, guest web UI.
- **Plan 3, web pilot:** admin UI, stays and checkout, setup, backup and restore, packaging, installation on a real
  Home Assistant.
- **Plan 4, native:** Android, Fire OS and iOS apps.

Task numbers refer to the plan files in `docs/superpowers/plans/`. Plans 3 and 4 are not written yet, so their rows
name the work package of design section 13 instead.

Statuses:

- `automated`: a green test on `main` exercises the case.
- `documented`: verified by hand or by evidence recorded in a document, such as a row of the [S0 report](s0-report.md).
- `planned (Plan N)`: not implemented yet. The plan in brackets delivers the first green check.
- `n/a`: dropped by a design decision, which the row cites.
- `(part)` after `automated` or `documented`: the evidence does not reach a named part of the expected result yet,
  because the component that would show it does not exist. The notes name the missing part and the plan that adds it.

Cases A06 to A18 are checked at the policy layer. The pure decision function is the single enforcement point (design
section 4.1), so Plan 1 tests it directly against the simulator's fixture world. Plan 2 repeats the most important
ones through the HTTP and WebSocket layers where the notes say so. Gateway test paths are relative to `myha/gateway/tests/`
and written `file::test`. Web and end-to-end tests carry their path from the repository root and the test title.

## Matrix

| # | Case | Status | Evidence | Notes |
|---|------|--------|----------|-------|
| A01 | Forged identity header on the public listener | automated | `acceptance/test_matrix_admin.py::test_a01_public_listener_ignores_identity_headers`<br>`integration/test_listeners.py::test_public_listener_has_no_admin_routes` | The public app has no admin route and ignores identity headers, even from the ingress peer. |
| A02 | Other app calls the ingress port directly | automated | `acceptance/test_matrix_admin.py::test_a02_wrong_peer_rejected`<br>`acceptance/test_matrix_admin.py::test_every_admin_api_route_requires_the_guard`<br>`integration/test_listeners.py::test_peer_guard_denies_websocket_handshake`<br>`unit/test_settings.py::test_options_json_cannot_change_trusted_ingress_peer` | The peer address of the connection decides, never a header, and app options cannot change the trusted peer. |
| A03 | Missing or duplicate identity header, old or unknown Supervisor | automated (part) | `acceptance/test_matrix_admin.py::test_a03_missing_duplicate_headers_and_old_supervisor`<br>`unit/test_supervisor.py::test_unparsable_version_is_unavailable` | A missing or duplicate header gives 403. An old Supervisor gives 503 with the reason `supervisor_version`, an unreachable or unparsable one `supervisor_unreachable` (minimum 2026.10.3, S0 row 2). Missing: the update hint in the admin UI (Plan 3). |
| A04 | Admin demoted during an open session | automated (part) | `acceptance/test_matrix_admin.py::test_a04_demoted_admin_blocked_on_next_mutation`<br>`acceptance/test_matrix_admin.py::test_cached_role_expires_after_five_seconds` | The next change is refused, and plain reads reuse a role for at most 5 s. Missing: closing the live admin view (admin WebSocket), Plan 3. |
| A05 | Core role query fails or its schema changes | automated | `acceptance/test_matrix_admin.py::test_a05_auth_list_failure_no_fallback`<br>`acceptance/test_matrix_admin.py::test_admin_role_comes_from_core_only` | Covers an error answer, a schema without groups and an unreachable Core. No fallback to an earlier success. The query itself was confirmed on a real Core (S0 row 1). |
| A06 | Guest requests a foreign resource ID | automated | `acceptance/test_matrix_policy.py::test_a06_foreign_resource_is_not_found`<br>`acceptance/test_matrix_projection.py::test_a06_projection_hides_foreign_rooms_and_entity_ids` | Foreign and unknown IDs give the same decision and public message (`NOT_FOUND`). The projection shows no foreign room and no HA entity ID. The action route repeats the check in Plan 2 (task 6.4, phase 2). |
| A07 | User may only switch off | automated | `acceptance/test_matrix_policy.py::test_a07_off_only` | On and dimming with a positive level are refused. Toggle is no action at all (A13). Execution path through the API: Plan 2 (task 6.4, phase 2). |
| A08 | User may only switch on | automated | `acceptance/test_matrix_policy.py::test_a08_on_only` | Off, level 0, relative steps and extra parameters are refused. |
| A09 | Temperature right 19–24 °C in 0.5 steps | automated | `acceptance/test_matrix_policy.py::test_a09_temperature_range_and_step` | 21.5 passes. 24.1, 21.3, NaN, Infinity, a number instead of a string and an extra `hvac_mode` are refused. |
| A10 | Two grants allow 18–20 and 22–24 | automated | `acceptance/test_matrix_policy.py::test_a10_two_grants_do_not_merge`<br>`unit/test_policy_index.py::test_two_grants_keep_their_intervals_apart` | 21 stays refused. Each grant allows only its own range. |
| A11 | Wide grant and narrow personal fence | automated | `acceptance/test_matrix_policy.py::test_a11_fence_narrows_wide_grant`<br>`unit/test_policy_index.py::test_a_fence_clips_every_interval_and_drops_what_is_empty` | A fence only narrows and can never allow. |
| A12 | Grant and explicit deny hit the same action | automated | `acceptance/test_matrix_policy.py::test_a12_deny_wins`<br>`unit/test_decide.py::test_a_deny_on_an_action_the_request_needs_wins` | |
| A13 | Unknown action, field, wildcard or area target in a guest request | automated | `acceptance/test_matrix_policy.py::test_a13_unknown_action_and_wildcards`<br>`unit/test_policy_model.py::test_extra_or_missing_fields_are_schema_errors` | The action set is closed, and every action becomes one explicit Home Assistant call. Nothing is passed through. |
| A14 | New device appears in a granted HA area | automated | `acceptance/test_matrix_policy.py::test_a14_new_device_not_granted`<br>`acceptance/test_matrix_drift.py::test_a14_new_entity_in_area_not_granted` | No binding means no grant, also not through a raw entity ID. The loaded index offers a new entity in a granted area to nobody. |
| A15 | Template, group or universal actuator without confirmed semantics | automated | `acceptance/test_matrix_policy.py::test_a15_group_entity_blocked`<br>`unit/test_adapters.py::test_group_template_and_wrapper_entities_are_blocked`<br>`integration/test_policy_loader.py::test_blocked_binding_is_never_offered`<br>`acceptance/test_matrix_drift.py::test_a15_replaced_device_marks_needs_review` | Such entities are `blocked`. Reads and writes answer like an unknown ID. A device replaced under the same entity ID needs review again. |
| A16 | HA temperature unit or device capabilities change | automated | `acceptance/test_matrix_policy.py::test_a16_needs_review_blocks_writes_only`<br>`acceptance/test_matrix_policy.py::test_a16b_adapter_locked`<br>`integration/test_ha_client.py::test_temperature_unit_decides_the_adapter_lock_also_at_runtime`<br>`acceptance/test_matrix_drift.py::test_a16_capability_change_marks_needs_review`<br>`acceptance/test_matrix_drift.py::test_a16_area_change_marks_needs_review`<br>`acceptance/test_matrix_drift.py::test_a16_unit_switch_locks_writes_without_review` | A changed unit locks the adapter until it is checked again. Changed abilities or a changed area mark the binding `needs_review`. `needs_review` and the lock refuse writes and keep reads. |
| A17 | Cover intermediate position with one allowed direction | automated | `acceptance/test_matrix_policy.py::test_a17_cover_position_requires_both_directions`<br>`acceptance/test_matrix_policy.py::test_a17b_every_position_fence_bounds_the_ends`<br>`acceptance/test_matrix_policy.py::test_a17b_open_and_close_stay_inside_the_position_range`<br>`unit/test_adapters.py::test_cover_set_position_needs_every_direction_it_may_move` | A position between the ends needs both directions. Simulator only, not proven on real cover hardware (design E9). |
| A18 | Climate mode enables a forbidden setpoint or control type | automated | `acceptance/test_matrix_policy.py::test_a18_mode_cannot_bypass_fence`<br>`acceptance/test_matrix_policy.py::test_a18_mode_cannot_keep_a_target_between_two_grants`<br>`acceptance/test_matrix_policy.py::test_a18_a_deny_on_set_temperature_does_not_lift_the_mode_check`<br>`acceptance/test_matrix_policy.py::test_a18_mode_check_ranges_are_not_snapped_to_the_grid`<br>`unit/test_policy_model.py::test_mode_is_one_of_off_cool_heat` | The pre-check in `decide` refuses cool or heat while the reported target is outside the effective range or unknown. The check after the switch (design E7) comes with the commands in Plan 2 (task 6.4). How real split units behave is open (S0 row 14). |
| A19 | Live event for a foreign room after a filtered snapshot | automated | `acceptance/test_matrix_events.py::test_a19_foreign_room_event_not_delivered` | |
| A20 | Role revoked during an open WebSocket | automated | `acceptance/test_matrix_events.py::test_a20_role_change_during_connection` | The resource disappears from the client with the new snapshot. |
| A21 | Old admin or guest cache used for another principal | automated | `acceptance/test_matrix_projection.py::test_a21_cache_keys_separate_principals` | |
| A22 | One-time QR redeemed on two devices at once | automated | `acceptance/test_matrix_guest_auth.py::test_a22_concurrent_redeem_single_winner` | Exactly one atomic consumption wins. |
| A23 | Redeem window over, stay still active | automated | `acceptance/test_matrix_guest_auth.py::test_a23_expired_redeem_window_keeps_existing_session` | |
| A24 | Redeem before the stay starts | automated (part) | `acceptance/test_matrix_guest_auth.py::test_a24_redeem_before_stay_start_is_waiting`<br>`acceptance/test_matrix_projection.py::test_a24_waiting_dashboard_is_empty`<br>`acceptance/test_matrix_policy.py::test_stay_window` | The session is `waiting` and its dashboard has no rooms. Missing: an action refused through the API while waiting, Plan 2 (task 6.4, phase 2). |
| A25 | Checkout with an open browser | automated (part) | `acceptance/test_matrix_events.py::test_a25_a52_revocation_closes_within_5s`<br>`acceptance/test_matrix_events.py::test_a25_a52_revocation_closes_the_socket_within_5s` | A revoked session is refused at once and its socket closes within 5 s. Missing: checkout and refresh families, Plan 3 (WP10, WP5b). |
| A26 | Old static room QR after checkout | planned (Plan 3) | Plan 3 (WP10) | By design there is no static room QR (design section 6). The entry page without a valid secret grants nothing. |
| A27 | Direct access to earlier guest history | automated (part) | `acceptance/test_matrix_projection.py::test_a27_no_history_fields`<br>`unit/test_decide.py::test_history_is_never_granted_in_the_mvp` | The projection carries no history field, and `history.read` is never granted. Missing: access after checkout, Plan 3 (WP10). |
| A28 | Foreign view performs a click | automated | `acceptance/test_matrix_preview.py::test_a28_simulation_never_calls_service` | Simulation only calls the decision function. |
| A29 | Hypothetical preview time in the future | automated | `acceptance/test_matrix_preview.py::test_a29_hypothetical_future_time_changes_only_policy` | |
| A30 | Simulation context sent to the real action route | planned (Plan 2) | Plan 2 (task 7.1, phase 2) | Every public route that needs an access session, the action route included, answers a preview session with 401, which is stricter than `ACTION_NOT_ALLOWED` (design section 4.5). The policy layer already refuses a preview context on execution: `acceptance/test_matrix_policy.py::test_preview_context_rejected_on_execute`. |
| A31 | Same idempotency key repeated | planned (Plan 2) | Plan 2 (task 6.4, phase 2) | |
| A32 | Gateway crashes after sending, before the result | planned (Plan 2) | Plan 2 (task 6.4, phase 2) | Commands in flight become `unknown` at start, and nothing is sent again. |
| A33 | Second action of a room preset fails | n/a | Design decision E21 | Room presets are not part of this release, so there is nothing to test. |
| A34 | Network loss and later reconnect | automated (part) | `acceptance/test_matrix_events.py::test_a34_reconnect_gets_fresh_snapshot`<br>`integration/test_ha_client.py::test_reconnects_after_the_connection_drops` | A client that reconnects gets a fresh snapshot, and the gateway reloads Core after a drop. Missing: refusing commands while Core is unreachable, Plan 2 (task 6.4, phase 2). |
| A35 | CSRF, forbidden origin, used WS ticket, rotated refresh token, revoked or expired session | automated (part) | `acceptance/test_matrix_guest_auth.py::test_a35_csrf_origin_revoked`<br>`acceptance/test_matrix_events.py::test_a35_ticket_single_use`<br>`acceptance/test_matrix_events.py::test_a35_browser_ticket_needs_the_public_origin`<br>`acceptance/test_matrix_admin.py::test_mutation_needs_a_csrf_token_of_today_or_yesterday` | The browser parts and the admin listener's CSRF proof are covered. Missing: the rotated refresh token, Plan 3 (WP5b). |
| A36 | Wrong TLS host name or disallowed key (native) | documented (part) | S0 row 16 | HTTPS and WSS fail identically on a wrong pin, a wrong host name and an untrusted chain. Shown with OkHttp on the JVM. Open: Android (Conscrypt) and iOS, Plan 4 (WP15). |
| A37 | Planned replacement pin and rotation with open connections | documented (part) | S0 row 16 | After a rotation the JVM spike closes tracked WebSockets with 1012 and drops late frames. A call already running still finishes under the old profile until the call registry exists. Open: Android and iOS, Plan 4 (WP15). |
| A38 | Self-signed certificate in default mode | documented (part) | S0 row 16 | An untrusted chain is refused, and a matching pin never replaces chain validation (JVM). Open: Android and iOS, Plan 4 (WP15). |
| A39 | Restore of a backup taken before QR use or session revocation | planned (Plan 3) | Plan 3 (WP12) | Building block: restore mode keeps the public listener closed and creates no instance (`integration/test_startup.py::test_restore_export_keeps_public_closed_and_creates_no_instance`). |
| A40 | Normal update or backup without restore | planned (Plan 3) | Plan 3 (WP12) | The backup hooks are placeholders, and S0 row 12 is open. |
| A41 | 320 px, tablets, 2560 px, 200 % text | planned (Plan 3) | Plan 3 (WP14) | The guest end-to-end run checks 320 px and 1180 px: `tests/e2e/guest.spec.ts` "(6) no horizontal scrolling at 320 and 1180 px". |
| A42 | Keyboard and screen reader only | planned (Plan 3) | Plan 3 (WP9a, WP9b, WP14) | The guest end-to-end run checks the keyboard path of the guest panel: `tests/e2e/guest.spec.ts` "(7) Tab reaches every button and Enter presses it". The screen reader run is manual. |
| A43 | Released Fire device without Google Play Services | planned (Plan 4) | Plan 4 (WP15) | Needs a real device. |
| A44 | Real guest Wi-Fi with isolated clients | planned (Plan 3) | Plan 3 (WP11) | Documented test with the test QR of the setup assistant. |
| A45 | Diagnostic, audit and proxy logs after login and enrollment | automated (part) | `unit/test_logsetup.py::test_redaction_rules`<br>`unit/test_logsetup.py::test_setup_logging_redacts_exception_text`<br>`integration/test_serve_logging.py::test_real_server_config_and_redacted_logs`<br>`integration/test_ha_client.py::test_wrong_token_reports_auth_invalid_and_never_logs_it`<br>`integration/test_admin_audit.py::test_failed_redeem_shows_neither_ip_nor_secret`<br>`unit/test_audit.py::test_secret_looking_keys_are_refused` | The log filter replaces tokens, secrets, passwords, cookies, authorization headers and long random strings, also in tracebacks and access logs. The audit writer refuses secret-like parameter names, and a failed redemption shows neither address nor secret. Missing: the audit of commands, Plan 2 (task 6.4, phase 2), and the check after resident login and native enrollment, Plan 3 (WP5b, WP14). |
| A46 | 72-hour wall operation and target client load | planned (Plan 3) | Plan 3 (WP14) | Load script and measured goals are documentation work. Only the decision latency is bounded today: `acceptance/test_matrix_policy.py::test_decide_p95_under_20ms`. |
| A47 | Backup archive inspected for live data and secrets | planned (Plan 3) | Plan 3 (WP12) | S0 row 12 is open: the hook run and the exclude patterns are not yet proven on a real installation. |
| A48 | Pre or post hook failure, aborted export or stopped app | planned (Plan 3) | Plan 3 (WP12) | |
| A49 | Enrollment or refresh response redirects to another valid HTTPS origin | planned (Plan 3) | Plan 3 (WP5b) | Neither listener redirects (`integration/test_listeners.py::test_trailing_slash_is_not_found_instead_of_redirect`). The native transport takes paths only (S0 row 16). |
| A50 | Invite previewed before its first redemption | automated | `acceptance/test_matrix_preview.py::test_a50_invite_preview_uses_planned_context`<br>`unit/test_policy_index.py::test_invite_preview_is_the_preview_of_its_stay` | |
| A51 | Extend stay, switch browser or switch room | planned (Plan 3) | Plan 3 (WP10) | |
| A52 | Revocation while the client is offline or suspended | automated (part) | `acceptance/test_matrix_events.py::test_a25_a52_revocation_closes_within_5s`<br>`acceptance/test_matrix_events.py::test_a25_a52_revocation_closes_the_socket_within_5s`<br>`myha/web/src/guest/store/live.test.ts` "a lost connection turns stale and drops all states after 30 s"<br>`myha/web/src/guest/store/live.test.ts` "the session end follows the server's clock" | Server side: refusal at once and a close within 5 s. Client side: local end timer and stale window. Missing: the end-to-end revocation run, `tests/e2e/guest.spec.ts` "(5) revoking the session ends the guest page within 5 s", skipped until Plan 2 phase 2. |

## How to update

A change of status belongs in the commit that causes it. When a test lands for a planned case, name it in the Evidence
column as `file::test`, set the status to `automated` and drop the plan reference. Keep `(part)` until the missing part
named in the notes is covered. Tests carry their case in the name (`test_a09_…`), so
`grep -rhoE "def test_a[0-9]{2}[a-z]?_\w+" myha/gateway/tests` lists what exists, and `uv run pytest -q tests/acceptance`
in `myha/gateway` runs the matrix. Evidence that no test can give, such as a device run, an operator procedure or an
S0 probe, goes into the S0 report or a document of its own. Cite its row or section and use `documented`. A design
decision that drops a case sets `n/a` and names the decision, as E21 does for A33. Keep the Known gaps below in step,
and check every row against a fresh test run before a release.

## Known gaps

- **Guest side.** Commands are not on `main` yet, so A30 to A32 and the command parts of A07, A24, A34 and A45 wait for
  Plan 2 phase 2 (tasks 6.4 and 7.1). The guest end-to-end run in `tests/e2e/guest.spec.ts` has four running tests
  (invite link, 320 and 1180 px, keyboard path, ingress stand-in) and four skipped until then (action confirmed,
  refused actions, invite preview, revocation). Checkout, refresh families and moving rooms (A25 to A27, A49, A51) come
  with Plan 3.
- **Real Home Assistant.** A01 to A05 run against the simulator and a faked ingress peer. The proof through a real
  ingress comes with the installation in Plan 3 (WP13). Several S0 rows are open or rest on source reading only:
  ingress headers (row 3), `GET /info` without `hassio_api` (row 4), the client address at the published port (row 11),
  `GET /host/info` (row 13) and the setpoint of split units after a mode change (row 14, relevant to A18).
- **Backup and restore (A39, A40, A47, A48).** The hooks are placeholders. S0 row 12 is open: nobody has shown yet that
  a hot backup runs the hooks and that `backup_exclude` keeps live state out of the archive. A47 and A48 have no
  evidence until then, and the restore design depends on the outcome.
- **Native transport (A36 to A38, A43).** Pinning and identical HTTPS and WSS failures are shown on the JVM only
  (S0 row 16). Android with Conscrypt, a Fire device without Play Services, iOS (simulator in CI, real hardware needs a
  developer account), HTTP/2 connection coalescing, TLS resumption and proxies are open.
- **Parts of green cases.** A03 needs the update hint in the admin UI and A04 the live admin view (Plan 3). The notes
  of A24, A25, A27, A34, A35, A45 and A52 name their missing parts and the plan that adds them.
- **Cases that need people or time (A41, A42, A44, A46).** Real viewports and devices, a screen reader, a real guest
  Wi-Fi with isolated clients and a 72-hour run cannot be automated. They stay `planned` until a procedure and a
  result are recorded in the documentation.
- **Covers (A17).** Only the simulator exercises covers (design E9). No real cover hardware has been tried.
