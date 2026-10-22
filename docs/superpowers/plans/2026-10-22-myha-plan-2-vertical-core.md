# myHA Teilplan 2 – Vertikaler Kern (WP5a, WP6, WP7, WP8)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Gast löst im Browser eine Einladung ein, sieht nur seinen Raum, bedient erlaubte Aktionen mit sichtbaren Befehlszuständen, erhält gefilterte Live-Updates, wird bei Widerruf innerhalb von 5 s getrennt; ein Admin prüft dieselbe Ansicht als Simulation. Alles gegen den Simulator automatisiert, Ende-zu-Ende mit Playwright.

**Architecture:** Design §4.3–§4.5, §7. Öffentliche Auth (Redeem, Cookie, CSRF, Tickets) → Projektion und EventBus → Befehle mit Idempotenz → Preview → Gast-Web-UI mit `GuestPanel` aus dem Mockup.

**Tech Stack:** wie Teilplan 1 plus React Router, Zod, `@tabler/icons-react`, Vitest, Playwright.

**Voraussetzung:** Teilplan 1 abgeschlossen (Gate im Masterplan).

---

## Dateistruktur (neu in diesem Teilplan)

```
myha/gateway/myha_gateway/
├── auth/public.py · auth/cookies.py · auth/ratelimit.py
├── policy/loader.py                     # DB → EffectiveContext, PolicyIndex-Cache
├── projection/dashboard.py · projection/schema.py
├── events/bus.py · events/connection.py · events/ws.py
├── services/commands.py · services/stays.py (minimal) · services/preview.py · services/audit.py
└── api/public/{config,auth,me,dashboard,actions,commands,ws_ticket}.py · api/admin/{stays,invites,preview,audit}.py
myha/gateway/tests/{unit,integration,acceptance}/…
myha/web/src/
├── api/client.ts · api/types.ts (generiert) · api/schema.ts (Zod)
├── transport/{index,browser}.ts
├── i18n/{index.ts,de.json,en.json}
├── ui/{GuestPanel,ResourceCard,CommandStatus,ConnectionPill,ThemeProvider,icons}.tsx
└── guest/{main.tsx,App.tsx,routes/{Join,Dashboard,Ended}.tsx,store/{session,live}.ts}
tests/e2e/{package.json,playwright.config.ts,ingress-proxy.mjs,guest.spec.ts}
```

---

## WP5a – Öffentliche Auth Teil 1

### Task 5a.1: Rate-Limit und Cookies

**Files:**
- Create: `myha_gateway/auth/ratelimit.py`, `myha_gateway/auth/cookies.py`
- Test: `tests/unit/test_ratelimit.py`, `tests/unit/test_cookies.py`

- [ ] **Step 1: Tests:** `SlidingWindowLimiter(limit=5, window_s=60)`: 5 Treffer ok, 6. → `False`, nach 60 s wieder ok (Zeitfunktion injizierbar); Schlüssel getrennt. `session_cookie_name(short_id)` → `__Host-myha_<short_id>_session`; `set_session_cookie(response, name, token, expires_at)` setzt `Secure; HttpOnly; SameSite=Lax; Path=/` und `Max-Age` aus `expires_at`; `clear_session_cookie`.
- [ ] **Step 2–4:** rot, implementieren, grün, Commit `feat(auth): sliding-window rate limiter and host-bound session cookies`.

### Task 5a.2: Stays und Einladungen minimal (Admin-API)

**Files:**
- Create: `myha_gateway/services/stays.py`, `myha_gateway/api/admin/stays.py`, `myha_gateway/api/admin/invites.py`
- Test: `tests/integration/test_admin_stays.py`

- [ ] **Step 1: Verhalten.** `POST /api/v1/admin/stays {unit_id, role_template_id, role_version?, starts_at, ends_at, device_slots}` → Stay `draft` (Validierung: `starts_at < ends_at`, Unit existiert, Rolle veröffentlicht; `role_version` Default = zuletzt veröffentlicht). `POST /api/v1/admin/stays/{id}/invites {count, redeem_minutes=15}` → Stay wird `confirmed`; je Einladung: Secret erzeugen, nur Hash speichern, `redeem_until = min(now + redeem_minutes, ends_at)`, `redeem_minutes ≤ 1440`; Antwort enthält je Einladung `{invite_id, device_slot, join_url, qr_svg, redeem_until, usable_until}` **genau einmal**; `join_url = f"{public_origin}/join#i={secret}&inst={instance_id}&fp={primary_pin or ''}"`. `POST /api/v1/admin/invites/{id}/revoke`. `GET /api/v1/admin/stays` (Liste mit abgeleitetem Status `planned|active|checked_out|cancelled|draft`). Audit-Einträge `stay.create`, `invite.issue` (ohne Secret), `invite.revoke`. Für Tests existiert eine Hilfsfunktion `seed_room203(session)` in `tests/helpers.py`, die Unit, Bindungen (aus Simulator-Fixture bestätigt), Rolle `room_guest` v1 (Spez §7) anlegt.
- [ ] **Step 2: Tests:** Erzeugen, Einladungen ausgeben (Secret erscheint nur in der Antwort, in DB nur Hash), `redeem_minutes=2000` → 400, Einladung über Stay-Ende hinaus wird gekappt, Widerruf setzt `revoked_at`.
- [ ] **Step 3–4:** Implementieren (QR mit `segno.make(join_url, error="m").svg_inline(scale=4)`), grün, Commit `feat(admin): stays and one-time invites with local qr`.

### Task 5a.3: Redeem, Sessions, `/me`, Logout, WS-Ticket

**Files:**
- Create: `myha_gateway/auth/public.py`, `myha_gateway/api/public/{auth,me,ws_ticket,config}.py`
- Modify: `myha_gateway/app.py` (Router registrieren)
- Test: `tests/acceptance/test_matrix_guest_auth.py`, `tests/integration/test_public_auth.py`

- [ ] **Step 1: Verhalten.**
  - `GET /api/v1/public/config` → `{instance_name, languages: ["de","en"], theme_default, operator_contact}`; Rate-Limit 60/min je IP.
  - `POST /api/v1/auth/redeem {invite_secret, device_label?, client: "browser"|"native"}`: Rate-Limit 10/min je IP; Hash berechnen; in **einer** Transaktion: `UPDATE invite SET consumed_at=now, consumed_session_id=:sid WHERE secret_hash=:h AND consumed_at IS NULL AND revoked_at IS NULL AND redeem_until > now` → `rowcount == 1` sonst `401 UNAUTHENTICATED` (gleicher Fehler für unbekannt/verbraucht/abgelaufen/widerrufen); Stay laden (`confirmed`, nicht `checked_out`); Gast-Principal (`type=guest`, `stay_id`, `invite_id`, `display_name=device_label`) anlegen; Session (`audience` nach `client`, `purpose=access`, `stay_id`, `unit_ids=[stay.unit_id]`, `not_before=starts_at`, `not_after=min(ends_at, now+guest_browser_days)`, `csrf_secret`) anlegen; Browser: Cookie setzen, Antwort `{session: {not_before, not_after, waiting: bool}, csrf_token}`; Native: `{access_token, refresh_token, expires_in}` (Refresh-Familie; Rotation in Teilplan 3). Audit `auth.redeem`.
  - `require_session(purpose="access")`: Cookie (oder Bearer) → Hash → Session laden → nicht widerrufen, `now < not_after`, Stay nicht `checked_out`; `purpose=preview/test` wird an ausführenden Endpunkten abgelehnt; aktualisiert `last_seen_at` höchstens alle 60 s.
  - `require_csrf_public`: für Browser-Audience Header `X-MyHA-CSRF == session.csrf_token` und `Origin == public_origin` (in Tests `http://testserver`).
  - `GET /api/v1/me` → `{principal: {type, display_name}, session: {not_before, not_after, waiting}, csrf_token, stay: {unit_name, ends_at}, instance: {name, operator_contact}}`.
  - `POST /api/v1/auth/logout` (CSRF) → Session widerrufen, Cookie löschen.
  - `POST /api/v1/ws-ticket` (CSRF) → `{ticket, expires_in: 30}`; Ticket einmalig, Hash gespeichert.
  - `POST /api/v1/auth/session/renew` (CSRF) → setzt `not_after` neu aus Stay/Limits (nie größer), Cookie neu.
- [ ] **Step 2: Tests (rot):** `test_a22_concurrent_redeem_single_winner` (20 parallele Redeems desselben Secrets → genau ein 200, Rest 401), `test_a23_expired_redeem_window_keeps_existing_session` (Einladung abgelaufen → 401; vorher eingelöste Session funktioniert weiter), `test_a24_redeem_before_stay_start_is_waiting` (`waiting=True`, `/me` ok, Dashboard liefert leere Räume mit Status `waiting`, Aktion → `ACCESS_EXPIRED`), `test_a35_csrf_origin_ticket_revoked` (fehlender CSRF → 403; falscher Origin → 403; Ticket zweimal → zweiter 401; widerrufene Session → 401 an `/me`), `test_redeem_uniform_errors` (unbekannt/verbraucht/widerrufen liefern byte-identische Bodies), `test_cookie_attributes`, `test_native_redeem_returns_tokens_no_cookie`.
- [ ] **Step 3:** Implementieren.
- [ ] **Step 4:** grün, Commit `feat(auth): invite redeem, guest sessions, csrf, ws tickets (A22-A24, A35)`.

### Task 5a.4: Zeitplausibilität (E26)

**Files:**
- Create: `myha_gateway/services/timeguard.py`
- Modify: `myha_gateway/app.py` (Hintergrundtask), `policy/loader.py` (setzt `time_trusted`)
- Test: `tests/unit/test_timeguard.py`

- [ ] **Step 1: Tests:** Hochwassermarke in `instance.last_seen_clock`; Wanduhr vor Marke → `time_trusted=False`; monotone Uhr vs. Wanduhr springt > 60 s → `False`; plausibel → `True` zurück; optionaler `dt_synchronized=False` → `False`.
- [ ] **Step 2–4:** rot, implementieren (Task alle 60 s), grün, Commit `feat(gateway): wall-clock plausibility guard`.

---

## WP6 – Projektion, Live-Events, Befehle, Audit

### Task 6.1: PolicyIndex-Loader und Cache

**Files:**
- Create: `myha_gateway/policy/loader.py`
- Test: `tests/integration/test_policy_loader.py`

- [ ] **Step 1: Verhalten.** `PolicyLoader(engine, ha)` baut `PolicyIndex` aus DB (`resource_binding`, `role_version`, `assignment`, `group`, `membership`, `fence`) und HA-Capabilities; Cache mit Schlüssel `(policy_version, ha.generation, ha.adapter_locked)`; `context_for_session(session_id, now)` lädt Zeilen und ruft `build_context_for_session`. `invalidate()` nach jeder Policy-Mutation (die Services rufen es nach Commit auf und veröffentlichen `policy.changed`).
- [ ] **Step 2: Tests:** Index enthält bestätigte Bindungen, nicht `blocked`; nach `policy_version`-Erhöhung neuer Index; `adapter_locked` propagiert.
- [ ] **Step 3–4:** Commit `feat(policy): index loader with versioned cache`.

### Task 6.2: Projektion

**Files:**
- Create: `myha_gateway/projection/schema.py` (Pydantic-Antwortmodelle: `DashboardView`, `RoomView`, `ResourceView`, `StateView`-Positivliste je Kind, `CapabilityView`), `myha_gateway/projection/dashboard.py`
- Test: `tests/acceptance/test_matrix_projection.py`

- [ ] **Step 1: Verhalten.** `project_dashboard(ctx, index, ha, dashboard_layout, now) -> DashboardView`: Räume = Units im Scope mit mindestens einer sichtbaren Ressource; Ressource sichtbar, wenn `metadata.read` erlaubt; `state` nur mit `state.read`, Felder nach Positivliste (`light: power, level_pct`; `climate: current_c, target_c, hvac_mode`; `cover: position_pct, moving`; `sensor: value, unit`; alle: `availability, observed_at`); `capabilities` nur für erlaubte Schreibaktionen mit effektiven Intervallen/Enums; `label` aus Binding; **nie** `entity_id`, Attribute, Kontext-IDs. Reihenfolge aus `dashboard.layout`, sonst Bindungsreihenfolge. `project_resource(ctx, index, ha, resource_id, now) -> ResourceView | None`.
- [ ] **Step 2: Tests:** `test_a06_projection_hides_foreign_rooms_and_entity_ids` (Gast sieht nur Room 203; JSON enthält keinen String `light.` oder `climate.`), `test_a21_cache_keys_separate_principals` (zwei Kontexte nacheinander → unterschiedliche Ausgaben, kein Leck), `test_state_hidden_without_state_read`, `test_capabilities_reflect_fence`, `test_camera_never_projected`.
- [ ] **Step 3–4:** Commit `feat(projection): allow-listed dashboard projection`.

### Task 6.3: EventBus und Gast-WebSocket

**Files:**
- Create: `myha_gateway/events/bus.py`, `myha_gateway/events/connection.py`, `myha_gateway/events/ws.py`
- Modify: `myha_gateway/app.py`
- Test: `tests/acceptance/test_matrix_events.py`

- [ ] **Step 1: Verhalten.** `EventBus.publish(event)` mit Typen `StateChanged(entity_id)`, `PolicyChanged(policy_version)`, `SessionRevoked(session_id | stay_id)`, `ConnectionStatus`, `CommandUpdated(session_id, command)`. `ClientConnection` (eine je WS): Auth per erster Nachricht `{"type":"auth","ticket"}` innerhalb 5 s, Origin-Prüfung bei Browser-Audience; sendet `snapshot` (`generation`, `sequence 0`, `policy_version`, `dashboard`), dann Deltas `resource.updated`/`resource.removed` nur für Ressourcen im Scope (Projektion je Ereignis), `policy.changed` → neuer Snapshot, `session.revoked` → Nachricht + Close 4401, `connection.status`, `command.updated`. Max. Framegröße 64 KiB, Ping alle 20 s, Sessionprüfung alle 30 s (Ablauf → Close). `WS /api/v1/events`.
- [ ] **Step 2: Tests (rot):** `test_a19_foreign_room_event_not_delivered` (Event für `light.hallway` erreicht Gast nicht; Event für `light.room_203` schon), `test_a20_role_revoked_during_connection` (Zuweisung entfernt + `policy.changed` → neuer Snapshot ohne Ressource), `test_a25_a52_revocation_closes_within_5s` (Widerruf → `session.revoked` und Close < 5 s), `test_snapshot_generation_and_sequence`, `test_auth_timeout_closes`, `test_ticket_single_use`, `test_a34_reconnect_gets_fresh_snapshot` (nach Simulator-`drop-connections` liefert die nächste Client-Verbindung neue Generation).
- [ ] **Step 3–4:** Commit `feat(events): filtered live events over websocket (A19, A20, A25, A34, A52)`.

### Task 6.4: Befehle und Audit

**Files:**
- Create: `myha_gateway/services/commands.py`, `myha_gateway/services/audit.py`, `myha_gateway/api/public/{actions,commands}.py`, `myha_gateway/api/admin/audit.py`
- Test: `tests/acceptance/test_matrix_commands.py`, `tests/unit/test_audit.py`

- [ ] **Step 1: Verhalten.** `POST /api/v1/actions` (CSRF, Header `Idempotency-Key` Pflicht, UUID): Session → Kontext → `decide(execute=True)` → bei Ablehnung Audit (aggregiert) + öffentlicher Fehler; sonst Transaktion: `command` (`accepted`, Body-Hash) einfügen, bei bestehendem Schlüssel: gleicher Hash → 202 mit bestehendem Befehl, anderer → `409 CONFLICT`; Commit; Vorprüfung (Session nicht widerrufen, `policy_version` gleich, `ha.connected`) sonst `failed`; `ha.call_service` → `sent` (+ `ha_context_id`); Beobachtung: Task wartet bis 10 s auf ein `state_changed` der Entity, das `Expectation` erfüllt → `confirmed`; Service-Fehler → `failed`; Timeout oder `availability=offline` → `unknown`; jede Änderung → `command.updated` an die Session und Audit. Antwort `202 {command_id, status: "accepted", policy_version}`. `GET /api/v1/commands/{id}` nur für eigene Session. Beim Start: `accepted|sent` → `unknown` (A32). `audit(event)`: Felder nach Spez §15.2; `aggregate(key, window=60s)` zählt gleichartige Ablehnungen; keine Payloads außer normalisierten Parametern. `GET /api/v1/admin/audit?filter=&cursor=&limit=` (Admin).
- [ ] **Step 2: Tests (rot):** `test_a31_idempotency_same_and_different_body`, `test_a32_restart_marks_inflight_unknown`, `test_command_lifecycle_confirmed` (Simulator ändert Zustand → `confirmed` ≤ 2 s), `test_command_unknown_when_offline`, `test_command_failed_on_service_error`, `test_a07_execute_path_rejects_disallowed` (API-Ebene, nicht nur `decide`), `test_a28_a30_preview_session_cannot_execute` (Preview-Cookie an `/actions` → `ACTION_NOT_ALLOWED`, kein `call_service` im Simulator-Log), `test_audit_has_actor_and_effective_principal`, `test_audit_aggregates_denials` (100 Ablehnungen → 1 Zeile mit `aggregate_count`), `test_mode_change_expectation_marks_needs_review` (Simulator-Option: Sollwert springt beim Moduswechsel auf 28 → `failed`, Binding `needs_review`).
- [ ] **Step 3–4:** Commit `feat(commands): idempotent actions with observed confirmation and audit (A31, A32)`.

---

## WP7 – Preview und Simulation

### Task 7.1: Preview-Service und Admin-API

**Files:**
- Create: `myha_gateway/services/preview.py`, `myha_gateway/api/admin/preview.py`
- Test: `tests/acceptance/test_matrix_preview.py`

- [ ] **Step 1: Verhalten.** `POST /api/v1/admin/preview {target: {principal_id?, session_id?} | {stay_id} | {invite_id} | {group_id}, at?}` → Session `purpose=preview`, `actor_admin_id`, `preview_target`, 15 min; `GET /preview/{id}/dashboard?at=` → `project_dashboard` mit dem Kontext des Ziels (Banner-Daten `who`, `ctx`-Tabelle, `hidden`-Hinweis wie Mockup); `POST /preview/{id}/simulate {resource_id, action, parameters, at?}` → `decide(execute=False)` mit aktueller `state_view` → `{would_allow, code, public_code, normalized_parameters, matched, message}` + Audit `preview.simulate`; Preview-Sessions sind nur am Admin-Listener nutzbar (Dependency prüft `actor_admin_id == current admin`).
- [ ] **Step 2: Tests:** `test_a28_simulation_never_calls_service` (Simulator-Service-Zähler bleibt 0), `test_a29_hypothetical_future_time_changes_only_policy` (`at` nach Stay-Ende → `STAY_ENDED`; Zustände unverändert), `test_a30_preview_session_rejected_at_public_listener`, `test_a50_invite_preview_uses_planned_context` (Einladung nicht eingelöst → Kontext mit Fenster und Rolle des Stays; keine Session mit `purpose=access` entsteht), `test_group_preview_is_marked_hypothetical`.
- [ ] **Step 3–4:** Commit `feat(preview): admin preview contexts and policy simulation (A28-A30, A50)`.

---

## WP8 – Gast-Web-UI

### Task 8.1: API-Typen, Zod-Schema, Transport, Client

**Files:**
- Create: `web/src/api/types.ts` (aus `uv run myha-gateway openapi > ../web/openapi.json` + `pnpm exec openapi-typescript openapi.json -o src/api/types.ts`; Skript `pnpm gen:api`), `web/src/api/schema.ts` (Zod für `DashboardView` und WS-Events), `web/src/transport/index.ts`, `web/src/transport/browser.ts`, `web/src/api/client.ts`
- Test: `web/src/api/schema.test.ts`, `web/src/transport/browser.test.ts`

- [ ] **Step 1: Tests:** Zod akzeptiert eine gültige Dashboard-Antwort und lehnt eine mit `entity_id` oder unbekannten Feldern ab (`.strict()`); Browser-Transport sendet `X-MyHA-CSRF` und `credentials: "include"` bei POST, nie bei GET; `openSocket` sendet das Ticket als erste Nachricht; Redirect-Antworten (`response.redirected`) werden als Fehler behandelt.
- [ ] **Step 2–4:** Implementieren, grün, Commit `feat(web): typed api client with zod-validated responses and browser transport`.

### Task 8.2: Designsystem-Komponenten (Portierung des Mockups)

**Files:**
- Create: `web/src/ui/ThemeProvider.tsx`, `ConnectionPill.tsx`, `CommandStatus.tsx`, `ResourceCard.tsx`, `GuestPanel.tsx`, `icons.ts` (Mapping `tabler:bulb` → `IconBulb` usw.), `ui/guest-panel.module.css`
- Test: `web/src/ui/GuestPanel.test.tsx`

- [ ] **Step 1: Verhalten (aus `docs/reference/design/guest-panel.dc.html`):** Props `rooms`, `layout: tiles|list|focus`, `wall`, `simulate`, `explain`, `status: online|stale|waiting|expired`, `title`, `subtitle`, `footer`, `onAction(resourceId, action, params)`, `commands: Record<resourceId, CommandState>`. Karten je Kind wie im Mockup: Licht (Ein/Aus-Button je nach Zustand und Rechten, Slider bei `set_level`, Slider sendet beim Loslassen), Klima (−/+ in Schrittweite innerhalb des effektiven Bereichs, Bereichstext, Modus-Buttons für erlaubte Modi), Cover (Open/Stop/Close), Sensor (Wert). Befehlsstatus-Leiste (checking → accepted → confirmed | unknown | failed, Texte aus i18n). `status != online` deaktiviert Bedienelemente; `stale` zeigt Hinweis. Touchziele ≥ 44 px, Wand 64 px; `aria-live="polite"` für Status; sichtbarer Fokus; `prefers-reduced-motion` respektiert; Grid `repeat(auto-fill, minmax(min(100%,165px),1fr))` wie Mockup. Keine externen URLs.
- [ ] **Step 2: Tests (Testing Library):** rendert nur erlaubte Buttons (Grant nur `light.off` → kein „Einschalten“); Klima „+“ deaktiviert am Maximum; Klick ruft `onAction` mit `{"target_c":"22.0"}`; Status `expired` deaktiviert alles; Explain-Modus zeigt Regeln.
- [ ] **Step 3–4:** Commit `feat(ui): guest panel and resource cards ported from design mockup`.

### Task 8.3: i18n und Theme

**Files:**
- Create: `web/src/i18n/index.ts`, `de.json`, `en.json`
- Test: `web/src/i18n/index.test.ts`

- [ ] **Step 1:** `useI18n()` mit `t(key, vars)`; Sprache aus `localStorage` → `navigator.language` → `en`; alle Texte der Gastseiten (Join, Dashboard, Status, Fehlercodes der acht öffentlichen Fehler, Befehlszustände) in beiden Sprachen; fehlender Schlüssel → Schlüsselname (Test prüft, dass `de.json` und `en.json` dieselben Schlüssel haben). Theme: `data-theme` aus `prefers-color-scheme` → gespeicherte Wahl; Umschalter light/dark/contrast.
- [ ] **Step 2–4:** Commit `feat(web): i18n (de/en) and theme switching`.

### Task 8.4: Gast-App (Join, Dashboard, Ended) und PWA

**Files:**
- Create: `web/src/guest/App.tsx`, `routes/Join.tsx`, `routes/Dashboard.tsx`, `routes/Ended.tsx`, `store/session.ts`, `store/live.ts`, `web/public/manifest.webmanifest`, `web/src/sw.ts` (nur `/assets/*`-Cache)
- Modify: `web/src/guest/main.tsx`, `vite.config.ts` (SW-Build)
- Test: `web/src/guest/routes/Join.test.tsx`, `web/src/guest/store/live.test.ts`

- [ ] **Step 1: Join:** liest `location.hash` (`i`, `inst`, `fp`), ruft sofort `history.replaceState(null, "", "/join")`, zeigt Join-Seite wie Mockup (Hinweistext, optionales Gerätelabel, Button „Open my room“/„Mein Zimmer öffnen“), POST `auth/redeem`; Fehler → verständlicher Text mit Betreiberkontakt (aus `public/config`); ohne Fragment → Hinweis „Link unvollständig“. Nie das Secret loggen oder in den Verlauf schreiben.
- [ ] **Step 2: Dashboard:** lädt `/me` + Dashboard, öffnet WS mit Ticket, verarbeitet `snapshot`/Deltas nach Zod-Validierung, hält `commands`-Status pro Ressource aus `command.updated`, zeigt `ConnectionPill`; bei Verbindungsverlust Status `stale` mit Zeitangabe, nach 30 s Zustände entfernen und Bedienung sperren; Reconnect mit neuem Ticket (Backoff 1–10 s); `session.revoked` oder 401 → Route `/ended`; `waiting` → Status `waiting` mit Startzeit; `visibilitychange` → `/me` prüfen vor erneuter Anzeige. Aktionen: `Idempotency-Key` = `crypto.randomUUID()` je Klick.
- [ ] **Step 3: Tests:** Join entfernt das Fragment aus der URL vor dem Request; `live`-Store wendet Deltas an, entfernt Ressourcen bei neuem Snapshot, setzt `stale` und leert nach 30 s (Fake-Timer).
- [ ] **Step 4:** `pnpm build`, Lighthouse-freie Prüfung: `dist/index.html` enthält keine externen URLs (Test-Skript `scripts/check-no-external.mjs` grep über `dist/`). Commit `feat(web): guest join and live dashboard with pwa shell`.

### Task 8.5: E2E mit Playwright

**Files:**
- Create: `tests/e2e/package.json`, `playwright.config.ts`, `ingress-proxy.mjs`, `guest.spec.ts`, `helpers/gateway.ts`
- Modify: `.github/workflows/ci.yml` (Job `e2e`)

- [ ] **Step 1: Infrastruktur:** `helpers/gateway.ts` startet Simulator (`uv run myha-simulator --port 18123`), Gateway (`MYHA_TRUSTED_INGRESS_PEER=127.0.0.1 MYHA_PUBLIC_ENABLED=1 MYHA_PUBLIC_ORIGIN=http://127.0.0.1:18443 MYHA_WEB_DIST=../../myha/web/dist`), und `ingress-proxy.mjs` (Node-HTTP-Proxy auf :18099 → Admin :8099, setzt `X-Remote-User-Id: owner1`, `X-Ingress-Path`, entfernt eingehende Identitätsheader). Seed über Admin-API: Unit Room 203 mit Bindungen aus dem Simulator, Rolle `room_guest`, Stay heute, zwei Einladungen.
- [ ] **Step 2: `guest.spec.ts`:** (1) Gast öffnet `join_url` → Fragment verschwindet aus der Adressleiste → Dashboard zeigt „Room 203“ und genau die Ressourcen der Rolle, nicht „Hallway“; (2) Klick „Turn on“ → Status „Checking…“ → „Confirmed“ innerhalb 3 s; (3) Klima „+“ bis Maximum, dann deaktiviert; direkter `fetch` gegen `/api/v1/actions` mit `light.set_level` ohne Grant → `ACTION_NOT_ALLOWED`; fremde Ressource → `NOT_FOUND`; (4) Admin ruft `preview` für die zweite (nicht eingelöste) Einladung auf, `simulate climate.set_temperature 30` → `would_allow=false`; (5) Admin widerruft die Session → Gastseite zeigt „Access ended“ innerhalb 5 s; (6) Viewports 320 und 1180: Hauptaktionen sichtbar ohne horizontales Scrollen; (7) Tastatur: Tab erreicht alle Buttons, Enter löst aus.
- [ ] **Step 3:** CI-Job `e2e` (Chromium, Artefakte bei Fehler). Commit `test(e2e): guest journey from invite to revocation`.

### Task 8.6: Teilplan-2-Abschluss

- [ ] **Step 1:** Gesamt-Testlauf (pytest, Vitest, Playwright) grün; CI grün; Coverage `policy`/`auth` ≥ 85 %.
- [ ] **Step 2:** `CHANGELOG.md`, `docs/setup.md` (Gastzugang-Abschnitt), Design §14 aktualisieren.
- [ ] **Step 3:** Commit `docs: plan 2 wrap-up`. Fable: Review nach `superpowers:requesting-code-review`, dann Teilplan 3 schreiben.
