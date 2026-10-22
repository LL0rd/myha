# myHA Teilplan 2 – Vertikaler Kern (WP5a, WP6, WP7, WP8)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Gast löst im Browser eine Einladung ein, sieht nur seinen Raum, bedient erlaubte Aktionen mit sichtbaren Befehlszuständen, erhält gefilterte Live-Updates, wird bei Widerruf innerhalb von 5 s getrennt; ein Admin prüft dieselbe Ansicht als Simulation. Alles gegen den Simulator automatisiert, Ende-zu-Ende mit Playwright.

**Architecture:** Design §4.3–§4.5, §7. Öffentliche Auth (Redeem, Cookie, CSRF, Tickets) → Projektion und EventBus → Befehle mit Idempotenz → Preview → Gast-Web-UI mit `GuestPanel` aus dem Mockup.

**Tech Stack:** wie Teilplan 1 plus React Router, Zod, `@tabler/icons-react`, Vitest, Playwright.

**Voraussetzung:** Teilplan 1 abgeschlossen (Gate im Masterplan).

**Revision 2** (nach Plan-Review): Dev-Seed-CLI für E2E, minimaler Audit-Writer vorgezogen, Zeitplausibilität ohne Loader-Abhängigkeit, `GET /api/v1/dashboard` und Admin-Sessions/Revoke als eigene Tasks, Drift-Erkennung im Loader (A16), Expectation mit Prüfbereich, WS-Ablauf-Timer, A22 mit injiziertem Limiter, E2E über `localhost`.

---

## Dateistruktur (neu in diesem Teilplan)

```
myha/gateway/myha_gateway/
├── auth/public.py · auth/cookies.py · auth/ratelimit.py
├── policy/loader.py · policy/drift.py
├── projection/dashboard.py · projection/schema.py
├── events/bus.py · events/connection.py · events/ws.py
├── services/audit.py · services/stays.py · services/commands.py · services/preview.py · services/timeguard.py · services/sessions.py
├── devseed.py
└── api/public/{config,auth,me,dashboard,actions,commands,ws_ticket}.py · api/admin/{stays,invites,sessions,preview,audit}.py
myha/gateway/tests/helpers.py (erweitert) · tests/{unit,integration,acceptance}/…
myha/web/src/
├── api/client.ts · api/types.ts (generiert) · api/schema.ts (Zod)
├── transport/{index,browser}.ts
├── i18n/{index.ts,de.json,en.json}
├── ui/{GuestPanel,ResourceCard,CommandStatus,ConnectionPill,ThemeProvider,icons}.tsx · ui/guest-panel.module.css
└── guest/{App.tsx,routes/{Join,Dashboard,Ended}.tsx,store/{session,live}.ts} · sw.ts · public/manifest.webmanifest
tests/e2e/{package.json,playwright.config.ts,ingress-proxy.mjs,guest.spec.ts,helpers/gateway.ts}
scripts/check-no-external.mjs
```

---

## WP5a – Öffentliche Auth Teil 1

### Task 5a.1: Rate-Limit, Cookies, minimaler Audit-Writer

**Files:**
- Create: `myha_gateway/auth/ratelimit.py`, `auth/cookies.py`, `services/audit.py`
- Test: `tests/unit/test_ratelimit.py`, `tests/unit/test_cookies.py`, `tests/unit/test_audit.py`

- [ ] **Step 1: Tests:** `SlidingWindowLimiter(limit=5, window_s=60, clock=…)`: 5 ok, 6. `False`, nach 60 s wieder ok; Schlüssel getrennt. `session_cookie_name(short_id)` → `__Host-myha_<short_id>_session`; `set_session_cookie(response, name, token, expires_at)` → `Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age`; `clear_session_cookie`. `audit(session, event: AuditEvent)` schreibt eine Zeile mit `actor`, `effective_principal`, `operation`, `result`, `reason_code`, `resource_id`, `session_id`, `policy_version`, `normalized_params` (keine anderen Payloads); `AuditEvent` verbietet Felder wie `token`, `secret`.
- [ ] **Step 2–4:** rot, implementieren, grün, Commit `feat(auth): rate limiter, host-bound cookies and audit writer`.

### Task 5a.2: Stays und Einladungen minimal (Admin-API) und Dev-Seed

**Files:**
- Create: `myha_gateway/services/stays.py`, `api/admin/stays.py`, `api/admin/invites.py`, `myha_gateway/devseed.py`, `tests/helpers.py` (erweitert um `seed_room203(engine, ha)`)
- Modify: `myha_gateway/__main__.py` (Subcommand `dev-seed room203`)
- Test: `tests/integration/test_admin_stays.py`, `tests/integration/test_devseed.py`

- [ ] **Step 1: Verhalten.** `POST /api/v1/admin/stays {unit_id, role_template_id, role_version?, starts_at, ends_at, device_slots}` → `draft` (Validierung: `starts_at < ends_at`, Unit vorhanden, Rolle veröffentlicht; Default `role_version` = zuletzt veröffentlicht). `POST /api/v1/admin/stays/{id}/invites {count, redeem_minutes=15}` (`fresh=True`): Stay → `confirmed`; je Einladung Secret erzeugen, Hash speichern, `redeem_until = min(now + redeem_minutes, ends_at)`, `redeem_minutes ≤ 1440`; Antwort **einmalig** `{invite_id, device_slot, join_url, qr_svg, redeem_until, usable_until}`, `join_url = f"{ctx.public_origin}/join#i={secret}&inst={instance.id}&fp={primary_pin or ''}"`. `POST /api/v1/admin/invites/{id}/revoke`. `GET /api/v1/admin/stays` (abgeleiteter Status). Audit `stay.create`, `invite.issue` (ohne Secret), `invite.revoke`. Jede Mutation erhöht `policy_version` und ruft `ctx.policy.invalidate()` (ab Task 6.1; bis dahin No-op-Hook in `AppContext`).
  `devseed.seed_room203(engine, ha)`: legt Unit „Room 203“ (`unit_203`) mit bestätigten Bindungen aus dem Simulator-Fixture (`res_203_light`, `res_203_bedside`, `res_203_trv`, `res_203_blind`, `res_203_hum`; Klima `confirmed_modes={"off","heat"}`), Unit „Living room“ (`unit_lr`, `res_lr_ceiling`, `res_lr_ac` mit `confirmed_modes={"off","cool","heat"}`), Rolle `room_guest` v1 (Spez §7 plus `light.set_level` auf `res_203_light`, Deny `history.read`) und einen Stay `confirmed` für heute 00:00 bis morgen 23:59 UTC mit zwei Einladungen an; gibt die `join_url`s zurück. Idempotent (zweiter Lauf ändert nichts). CLI `myha-gateway dev-seed room203` druckt die `join_url`s **nur** auf stdout und nur, wenn `MYHA_PUBLIC_ENABLED=1` gesetzt ist (Dev/Test).
- [ ] **Step 2: Tests:** Stay/Einladungen erzeugen; Secret nur in der Antwort; `redeem_minutes=2000` → 400; Kappung am Stay-Ende; Widerruf; `seed_room203` idempotent, liefert zwei URLs mit Fragment `i=`.
- [ ] **Step 3–4:** Implementieren (QR `segno.make(join_url, error="m").svg_inline(scale=4)`), grün, Commit `feat(admin): stays, one-time invites with local qr, dev seed`.

### Task 5a.3: Redeem, Sessions, `/me`, Logout, WS-Ticket, Renew

**Files:**
- Create: `myha_gateway/auth/public.py`, `api/public/{auth,me,ws_ticket,config}.py`
- Modify: `myha_gateway/app.py` (Router), `deps.py` (Limiter injizierbar: `AppContext.limiters`)
- Test: `tests/acceptance/test_matrix_guest_auth.py`, `tests/integration/test_public_auth.py`

- [ ] **Step 1: Verhalten** (Design §4.3):
  - `GET /api/v1/public/config` → `{instance_name, languages, theme_default, operator_contact}`; 60/min je IP.
  - `POST /api/v1/auth/redeem {invite_secret, device_label?, client}`: 10/min je IP (Tests injizieren einen großzügigen Limiter für A22); Transaktion (`BEGIN IMMEDIATE`): `UPDATE invite SET consumed_at=:now, consumed_session_id=:sid WHERE secret_hash=:h AND consumed_at IS NULL AND revoked_at IS NULL AND redeem_until > :now` → `rowcount == 1`, sonst `401 UNAUTHENTICATED` mit byte-identischem Body; Stay `confirmed` und nicht `checked_out`; Gast-Principal (`guest`, `stay_id`, `invite_id`, `display_name=device_label[:60]`); Session (`audience`, `purpose=access`, `stay_id`, `unit_ids=[stay.unit_id]`, `not_before=starts_at`, `not_after=min(ends_at, now+guest_browser_days)`, `csrf_secret`). Browser → Cookie + `{session: {not_before, not_after, waiting}, csrf_token}`; Native → `{access_token, refresh_token, expires_in}` (Rotation in Teilplan 3). Audit `auth.redeem`.
  - `require_session(purpose="access")`: Cookie oder Bearer → Hash → Session; nicht widerrufen, `now < not_after`, Stay nicht `checked_out`; `purpose` prüfen; `last_seen_at` höchstens alle 60 s.
  - `require_csrf_public`: Browser-Audience: `X-MyHA-CSRF == session.csrf_token`, `Origin == ctx.public_origin`; Native: keine Prüfung.
  - `GET /api/v1/me` → `{principal, session: {not_before, not_after, waiting}, csrf_token, stay: {unit_name, ends_at}, instance: {name, operator_contact}}`.
  - `POST /api/v1/auth/logout`, `POST /api/v1/ws-ticket` (`{ticket, expires_in: 30}`, einmalig, Hash), `POST /api/v1/auth/session/renew` (nie größer als Stay/Limits).
- [ ] **Step 2: Tests (rot):** `test_a22_concurrent_redeem_single_winner` (20 parallele Redeems, Limiter mit `limit=1000` → genau ein 200, Rest 401, kein `database is locked`), `test_a23_expired_redeem_window_keeps_existing_session`, `test_a24_redeem_before_stay_start_is_waiting` (nur `waiting=True` und `/me`; Dashboard- und Aktionsteil in 6.2/6.4), `test_a35_csrf_origin_revoked` (fehlender CSRF → 403; falscher Origin → 403; widerrufene Session → 401; Ticket-Doppelnutzung in 6.3), `test_redeem_uniform_errors`, `test_cookie_attributes`, `test_native_redeem_returns_tokens_no_cookie`, `test_renew_never_extends_beyond_stay`.
- [ ] **Step 3–4:** Implementieren, grün, Commit `feat(auth): invite redeem, guest sessions, csrf, ws tickets (A22-A24, A35 part)`.

### Task 5a.4: Zeitplausibilität (E26)

**Files:**
- Create: `myha_gateway/services/timeguard.py`
- Modify: `myha_gateway/app.py` (Hintergrundtask alle 60 s)
- Test: `tests/unit/test_timeguard.py`

- [ ] **Step 1:** `TimeGuard.check(now_wall, now_mono) -> bool` mit persistierter Hochwassermarke `instance.last_seen_clock`; Wanduhr vor Marke oder Sprung > 60 s gegenüber der monotonen Uhr → `instance.time_trusted=False` + Audit `time.untrusted`; plausibel → `True` zurück (Audit `time.trusted`); optional `dt_synchronized=False` (aus `ha.supervisor.host_info()`, falls erreichbar) → `False`. `instance.time_trusted` wird beim Kontextbau gelesen (bereits in `context.py`).
- [ ] **Step 2–4:** rot, implementieren, grün, Commit `feat(gateway): wall-clock plausibility guard`.

---

## WP6 – Projektion, Live-Events, Befehle, Audit

### Task 6.1: PolicyIndex-Loader, Cache und Drift-Erkennung

**Files:**
- Create: `myha_gateway/policy/loader.py`, `policy/drift.py`
- Modify: `deps.py` (`ctx.policy = PolicyLoader(...)`), `services/stays.py` (ruft `invalidate()`)
- Test: `tests/integration/test_policy_loader.py`, `tests/acceptance/test_matrix_drift.py`

- [ ] **Step 1: Verhalten.** `PolicyLoader(engine, ha)`: `index(now) -> PolicyIndex` baut aus DB (`resource_binding`, `role_version`, `assignment`, `group`, `membership`, `fence`) und HA (`adapter.capabilities(state, registry_entity, unit)`); Cache-Schlüssel `(instance.policy_version aus der DB – ein billiger Einzelzeilen-Select je Aufruf, damit Änderungen anderer Prozesse wie `dev-seed` sichtbar sind, ha.generation, ha.adapter_locked)`; `context_for_session(session_id, now)`; `invalidate()`. `drift.py` hat zwei Auslöser: (a) **jedes `state_changed` einer gebundenen Entity** – Fingerprint aus den neuen Attributen gegen `capability_fingerprint` (billig, kein Indexbau nötig); (b) Registry-Events (`entity_registry_updated`, `device_registry_updated`, `area_registry_updated`) und jeder Indexbau – `unique_id`, effektiver Bereich (`entity.area_id or device.area_id`), Existenz. Abweichung → `status=needs_review` + `policy_version`+1 + Audit `binding.needs_review` + `policy.changed`; Umbenennung (`entity_id` neu, `unique_id` gleich) → `entity_id` aktualisieren + Audit `binding.renamed`; Entity verschwunden → `needs_review`.
- [ ] **Step 2: Tests:** Index enthält bestätigte, nicht `blocked`; neue Version → neuer Index; `adapter_locked` propagiert; `policy_version`-Änderung durch einen zweiten Prozess (direktes DB-Update im Test) wird beim nächsten `index()` bemerkt; `test_a14_new_entity_in_area_not_granted`; `test_a16_capability_change_marks_needs_review` (`_sim/entity/attributes supported_features` → über `state_changed` innerhalb 1 s `needs_review`, Schreiben `UPSTREAM_UNAVAILABLE`, Lesen weiter); `test_a16_area_change_marks_needs_review` (Bereich über das Gerät geändert); `test_a15_replaced_device_marks_needs_review` (`registry/replace`); `test_rename_keeps_binding`.
- [ ] **Step 3–4:** Commit `feat(policy): index loader with versioned cache and registry drift detection (A14-A16)`.

### Task 6.2: Projektion und `GET /api/v1/dashboard`

**Files:**
- Create: `myha_gateway/projection/schema.py`, `projection/dashboard.py`, `api/public/dashboard.py`
- Test: `tests/acceptance/test_matrix_projection.py`

- [ ] **Step 1: Verhalten.** `project_dashboard(ctx, index, ha, layout, now) -> DashboardView`: Räume = Units im Scope mit ≥ 1 sichtbarer Ressource; sichtbar = `metadata.read` erlaubt; `state` nur mit `state.read` (Positivliste je Kind: light `power, level_pct`; climate `current_c, target_c, hvac_mode`; cover `position_pct, cover_state`; sensor `value, unit`; alle `availability, observed_at`); `capabilities` nur für erlaubte Schreibaktionen (`intervals` mit `step`/`origin`, `allowed`); `label`/`icon` aus Binding; **nie** `entity_id`, HA-Attribute, Kontext-IDs; Reihenfolge aus `dashboard.layout`, sonst Bindungsreihenfolge; `status` des Dashboards `online|waiting|expired` aus Fenster/Session. `GET /api/v1/dashboard` (Session) → `DashboardView` plus `policy_version`; im Zustand `waiting` leere Räume.
- [ ] **Step 2: Tests:** `test_a06_projection_hides_foreign_rooms_and_entity_ids` (JSON enthält weder `light.` noch `climate.` noch `camera`), `test_a21_cache_keys_separate_principals`, `test_state_hidden_without_state_read`, `test_capabilities_reflect_fence`, `test_a24_waiting_dashboard_is_empty`, `test_a27_no_history_fields` (keine Historienfelder, `history.read` nie projiziert).
- [ ] **Step 3–4:** Commit `feat(projection): allow-listed dashboard projection and endpoint`.

### Task 6.3: EventBus, Gast-WebSocket, Admin-Sessions und Widerruf

**Files:**
- Create: `myha_gateway/events/bus.py`, `events/connection.py`, `events/ws.py`, `services/sessions.py`, `api/admin/sessions.py`
- Modify: `app.py`
- Test: `tests/acceptance/test_matrix_events.py`, `tests/integration/test_admin_sessions.py`

- [ ] **Step 1: Verhalten.** `EventBus.publish(event)` mit `StateChanged(entity_id)`, `PolicyChanged(policy_version)`, `SessionRevoked(session_id | stay_id, reason)`, `ConnectionStatus`, `CommandUpdated(session_id, command)`. `ClientConnection`: Auth per erster Nachricht `{"type":"auth","ticket"}` innerhalb 5 s (sonst Close 4401), Origin-Prüfung bei Browser-Audience; `snapshot` (`generation`, `sequence 0`, `policy_version`, `dashboard`), dann `resource.updated`/`resource.removed` (Projektion je Ereignis, nur Scope), `policy.changed` → neuer Snapshot, `session.revoked` → Nachricht + Close 4401, `connection.status`, `command.updated`; **Ablauf-Timer auf `session.not_after`** (schließt pünktlich), zusätzlich Prüfung alle 30 s (Widerruf-Fallback), Ping 20 s, Frame ≤ 64 KiB. `WS /api/v1/events`. `services/sessions.py`: `revoke_session(id, reason)`, `revoke_stay_sessions(stay_id, reason)` (Sessions + Refresh-Familien + offene Einladungen in einer Transaktion, danach `SessionRevoked`). Admin: `GET /api/v1/admin/sessions?stay_id=` (`fresh=True`), `POST /api/v1/admin/sessions/{id}/revoke`.
- [ ] **Step 2: Tests (rot):** `test_a19_foreign_room_event_not_delivered`, `test_a20_role_change_during_connection` (Bewohner mit Gruppenzuweisung; Zuweisung entfernt → `policy.changed` → neuer Snapshot ohne Ressource), `test_a25_a52_revocation_closes_within_5s`, `test_expiry_timer_closes_at_not_after`, `test_snapshot_generation_and_sequence`, `test_auth_timeout_closes`, `test_a35_ticket_single_use`, `test_a34_reconnect_gets_fresh_snapshot`, `test_admin_revoke_endpoint_requires_fresh_admin`.
- [ ] **Step 3–4:** Commit `feat(events): filtered live events, session revocation (A19, A20, A25, A34, A52)`.

### Task 6.4: Befehle

**Files:**
- Create: `myha_gateway/services/commands.py`, `api/public/{actions,commands}.py`, `api/admin/audit.py`
- Modify: `services/audit.py` (Aggregation), `app.py`
- Test: `tests/acceptance/test_matrix_commands.py`

- [ ] **Step 1: Verhalten.** `POST /api/v1/actions` (CSRF, `Idempotency-Key` UUID Pflicht): Session → Kontext → `decide(execute=True)` mit `state_view` → Ablehnung: Audit (aggregiert) + öffentlicher Fehler; sonst Transaktion: `command` (`accepted`, `body_hash`) einfügen; bestehender Schlüssel: gleicher Hash → 202 mit bestehendem Befehl, anderer → 409 `CONFLICT`; Commit; Vorprüfung (Session nicht widerrufen, `policy_version` unverändert, `ha.connected`) sonst `failed`; `ha.call_service` → `sent` (+ `ha_context_id`); `Expectation` (vom Adapter, bei `set_mode cool/heat` mit `target_c_range = decision.check_range`); **sofort nach `sent` prüfen** (Gerät schon im Ziel → `confirmed`), dann auf `state_changed` bis 10 s; `Expectation.violated_range` → `failed` + Audit + Binding `needs_review` + `policy_version`+1; Service-Fehler → `failed`; Timeout/offline → `unknown`; jede Änderung → `command.updated` + Audit. `202 {command_id, status, policy_version}`. `GET /api/v1/commands/{id}` (eigene Session). Start: `accepted|sent` → `unknown`. Audit-Aggregation `(session_id, operation, reason_code)` je Minute mit `aggregate_count`; `GET /api/v1/admin/audit?filter=&cursor=&limit=` (`fresh=True`).
- [ ] **Step 2: Tests (rot):** `test_a31_idempotency_same_and_different_body`, `test_a32_restart_marks_inflight_unknown`, `test_command_lifecycle_confirmed`, `test_command_confirmed_when_already_in_target_state`, `test_command_unknown_when_offline`, `test_command_failed_on_service_error`, `test_a07_execute_path_rejects_disallowed` (API-Ebene; kein Service-Call im Simulator-Log), `test_a24_action_before_stay_start_access_expired`, `test_audit_has_actor_and_effective_principal`, `test_audit_aggregates_denials`, `test_mode_change_setpoint_jump_marks_needs_review` (`_sim/climate/mode-setpoint`), `test_revoked_between_decide_and_send_fails`.
- [ ] **Step 3–4:** Commit `feat(commands): idempotent actions with observed confirmation and audit (A31, A32)`.

---

## WP7 – Preview und Simulation

### Task 7.1: Preview-Service und Admin-API

**Files:**
- Create: `myha_gateway/services/preview.py`, `api/admin/preview.py`
- Test: `tests/acceptance/test_matrix_preview.py`

- [ ] **Step 1: Verhalten.** `POST /api/v1/admin/preview {target, at?}` (`fresh=True`) mit `target` ∈ `{principal_id, session_id?} | {stay_id} | {invite_id} | {group_id}` → Session `purpose=preview`, `actor_admin_id`, `preview_target`, 15 min; `GET /preview/{id}/dashboard?at=` (`fresh=True`) → `{banner: {who, purpose, expires_at}, context: [...], hidden_note, dashboard}`; `POST /preview/{id}/simulate {resource_id, action, parameters, at?}` → `decide(execute=False)` mit Live-`state_view` → `{would_allow, code, public_code, normalized_parameters, matched, message}` + Audit `preview.simulate`; Preview-Sessions nur am Admin-Listener und nur für `actor_admin_id == current admin`.
- [ ] **Step 2: Tests:** `test_a28_simulation_never_calls_service` (Service-Call-Log leer), `test_a29_hypothetical_future_time_changes_only_policy`, `test_a30_preview_session_rejected_at_public_listener` (Cookie der Preview-Session an `/api/v1/actions` → `ACTION_NOT_ALLOWED`, an `/me` → 401), `test_a50_invite_preview_uses_planned_context` (keine `access`-Session entsteht, Einladung bleibt uneingelöst), `test_group_preview_is_marked_hypothetical`, `test_preview_other_admin_cannot_use`.
- [ ] **Step 3–4:** Commit `feat(preview): admin preview contexts and policy simulation (A28-A30, A50)`.

---

## WP8 – Gast-Web-UI

### Task 8.1: OpenAPI-Export, API-Typen, Zod, Transport, Client

**Files:**
- Modify: `myha_gateway/__main__.py` (Subcommand `openapi [--admin|--public]` schreibt das Schema nach stdout)
- Create: `web/src/api/types.ts` (generiert: `pnpm gen:api` = `uv --directory ../gateway run myha-gateway openapi --public > openapi.json && openapi-typescript openapi.json -o src/api/types.ts`), `web/src/api/schema.ts`, `web/src/transport/index.ts`, `transport/browser.ts`, `web/src/api/client.ts`
- Test: `web/src/api/schema.test.ts`, `web/src/transport/browser.test.ts`

- [ ] **Step 1: Tests:** Zod (`.strict()`) akzeptiert eine gültige Dashboard-Antwort, lehnt `entity_id` oder unbekannte Felder ab; WS-Events `snapshot`, `resource.updated`, `resource.removed`, `policy.changed`, `session.revoked`, `command.updated`, `connection.status` validiert. Browser-Transport: POST mit `X-MyHA-CSRF`, `credentials: "include"`, `redirect: "manual"` und Fehler bei `opaqueredirect`; `openSocket` sendet Ticket als erste Nachricht.
- [ ] **Step 2–4:** Implementieren, grün, Commit `feat(web): typed api client with zod validation and browser transport`.

### Task 8.2: Designsystem-Komponenten (Portierung des Mockups)

**Files:**
- Create: `web/src/ui/ThemeProvider.tsx`, `ConnectionPill.tsx`, `CommandStatus.tsx`, `ResourceCard.tsx`, `GuestPanel.tsx`, `icons.ts`, `guest-panel.module.css`
- Test: `web/src/ui/GuestPanel.test.tsx`

- [ ] **Step 1: Verhalten (aus `docs/reference/design/guest-panel.dc.html`):** Props `rooms`, `layout`, `wall`, `simulate`, `explain`, `status`, `title`, `subtitle`, `footer`, `commands`, `onAction(resourceId, action, params)`. Karten je Kind wie im Mockup (Licht: Button je Zustand/Rechten, Slider bei `set_level` sendet beim Loslassen; Klima: −/+ in Schrittweite im effektiven Bereich, Bereichstext, Modus-Buttons für `allowed`; Cover: Open/Stop/Close; Sensor: Wert); Befehlsstatus-Leiste; `status != online` deaktiviert Bedienung; Touchziele ≥ 44 px (Wand 64 px); `aria-live="polite"`; Fokus sichtbar; `prefers-reduced-motion`; Grid wie Mockup; keine externen URLs (Icons aus `@tabler/icons-react`).
- [ ] **Step 2: Tests:** nur erlaubte Buttons; Klima „+“ am Maximum deaktiviert; Klick → `onAction` mit `{"target_c":"22.0"}`; `expired` deaktiviert alles; Explain zeigt Regeln.
- [ ] **Step 3–4:** Commit `feat(ui): guest panel and resource cards ported from design mockup`.

### Task 8.3: i18n und Theme

**Files:**
- Create: `web/src/i18n/index.ts`, `de.json`, `en.json`
- Test: `web/src/i18n/index.test.ts`

- [ ] **Step 1:** `useI18n()`, `t(key, vars)`; Sprache `localStorage` → `navigator.language` → `en`; alle Texte der Gastseiten inkl. der acht öffentlichen Fehlercodes und Befehlszustände; Test: beide Dateien haben dieselben Schlüssel. Theme: `prefers-color-scheme` → gespeicherte Wahl; Umschalter light/dark/contrast.
- [ ] **Step 2–4:** Commit `feat(web): i18n (de/en) and theme switching`.

### Task 8.4: Gast-App (Join, Dashboard, Ended) und PWA

**Files:**
- Create: `web/src/guest/App.tsx`, `routes/{Join,Dashboard,Ended}.tsx`, `store/{session,live}.ts`, `web/public/manifest.webmanifest`, `web/src/sw.ts`, `scripts/check-no-external.mjs`
- Modify: `web/src/guest/main.tsx`, `vite.guest.config.ts` (SW-Build als eigener Entry nach `dist/guest/sw.js`)
- Test: `web/src/guest/routes/Join.test.tsx`, `web/src/guest/store/live.test.ts`

- [ ] **Step 1: Join:** liest `location.hash` (`i`, `inst`, `fp`), sofort `history.replaceState(null, "", "/join")`; Seite wie Mockup (Hinweis, optionales Gerätelabel, Button); POST `auth/redeem`; Fehler → Text mit Betreiberkontakt; ohne Fragment → „Link unvollständig“. Secret nie loggen.
- [ ] **Step 2: Dashboard:** `/me` + `GET /api/v1/dashboard`, WS mit Ticket; `snapshot`/Deltas nach Zod; `commands` aus `command.updated`; `ConnectionPill`; Verbindungsverlust → `stale` mit Zeitangabe, nach 30 s Zustände entfernen und sperren; Reconnect mit neuem Ticket (Backoff 1–10 s); `session.revoked`/401 → `/ended`; `waiting` → Startzeit; **lokaler Timer auf `not_after`** → `/ended`; `visibilitychange` → `/me` prüfen; `Idempotency-Key = crypto.randomUUID()` je Klick.
- [ ] **Step 3: Tests:** Join entfernt Fragment vor dem Request; `live`-Store wendet Deltas an, ersetzt bei Snapshot, `stale` → leer nach 30 s (Fake-Timer); Ablauf-Timer.
- [ ] **Step 4:** `pnpm build`; `node scripts/check-no-external.mjs myha/web/dist/guest` prüft nur Lade-Kontexte (`src=`, `href=`, `url(`, `import(`, `fetch(`, `new WebSocket(`) auf absolute `http(s)://`-Ziele, die nicht `localhost`/relativ sind; reine Textvorkommen wie Reacts Error-Decoder-URL sind erlaubt. Grün. Commit `feat(web): guest join and live dashboard with pwa shell`.

### Task 8.5: E2E mit Playwright

**Files:**
- Create: `tests/e2e/package.json`, `playwright.config.ts`, `ingress-proxy.mjs`, `helpers/gateway.ts`, `guest.spec.ts`
- Modify: `.github/workflows/ci.yml` (Job `e2e`)

- [ ] **Step 1: Infrastruktur.** `helpers/gateway.ts` startet: Simulator (`uv --directory ../../myha/gateway run myha-simulator --port 18123`); Gateway mit `MYHA_DATA_DIR=<tmp> MYHA_HA_WS_URL=ws://127.0.0.1:18123/api/websocket MYHA_SUPERVISOR_URL=http://127.0.0.1:18123 SUPERVISOR_TOKEN=sim-token MYHA_TRUSTED_INGRESS_PEER=127.0.0.1 MYHA_ADMIN_PORT=18099 MYHA_PUBLIC_PORT=18443 MYHA_PUBLIC_ENABLED=1 MYHA_PUBLIC_ORIGIN=http://localhost:18443 MYHA_WEB_DIST=../../myha/web/dist`; `ingress-proxy.mjs` auf :18098 → 127.0.0.1:18099 mit `X-Remote-User-Id: owner1`, `X-Ingress-Path: /api/hassio_ingress/e2e` (entfernt eingehende Identitätsheader); Seed: `uv run myha-gateway dev-seed room203` (gleiche Env, **vor** dem Gateway-Start oder danach – der Loader liest `policy_version` aus der DB) → `join_url`s aus stdout. **Gast-API-Prüfungen** laufen aus dem Browser heraus (`page.evaluate(fetch)`), weil Secure-Cookies nur im Browserkontext an `http://localhost` gesendet werden; **Admin-Aufrufe** laufen Node-seitig über den Ingress-Proxy (`request`-Kontext mit Header-Injektion), nie aus der Gastseite (cross-origin, kein CORS).
- [ ] **Step 2: `guest.spec.ts`:** (1) Join-URL öffnen → Fragment verschwindet → Dashboard „Room 203“ mit genau den Rollen-Ressourcen, kein „Hallway“/„Living room“; (2) Hauptlicht ist im Fixture an: „Turn off“ → „Checking…“ → „Confirmed“ ≤ 3 s, danach „Turn on“ erscheint; (3) Klima „+“ bis Maximum deaktiviert; `fetch` `light.set_level` auf `res_203_bedside` → `ACTION_NOT_ALLOWED`; `res_lr_ceiling` → `NOT_FOUND`; (4) Admin über Proxy: `preview {invite_id}` für die zweite Einladung, `simulate climate.set_temperature 30` → `would_allow=false`; (5) Admin `GET /admin/sessions` → `revoke` → Gastseite „Access ended“ ≤ 5 s; (6) Viewports 320 und 1180 ohne horizontales Scrollen; (7) Tastatur: Tab erreicht alle Buttons, Enter löst aus.
- [ ] **Step 3:** CI-Job `e2e` (`pnpm exec playwright install --with-deps chromium`, Artefakte bei Fehler). Commit `test(e2e): guest journey from invite to revocation`.

### Task 8.6: Teilplan-2-Abschluss

- [ ] **Step 1:** pytest, Vitest, Playwright, CI grün; Coverage `policy`/`auth` ≥ 85 %.
- [ ] **Step 2:** `CHANGELOG.md`, `docs/setup.md` (Gastzugang), Design §14 aktualisieren.
- [ ] **Step 3:** Commit `docs: plan 2 wrap-up`. Fable: Review, dann Teilplan 3 schreiben (enthält ausdrücklich: Routen `/login` und `/pair`, Admin-WS-Teil von A04, Audit-Aufbewahrungsjob, Submodule `vendor/myha`, Refresh-Rotation, Trustprofil).
