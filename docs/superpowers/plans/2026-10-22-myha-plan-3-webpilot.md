# myHA Teilplan 3 – Webpilot (WP5b, WP9a, WP9b, WP10, WP11, WP12, WP13, WP14)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Betreiber richtet myHA vollständig in der Admin-UI im HA-Ingress ein und betreibt damit Gäste, Bewohner und Wandtablets; native Clients erhalten rotierende Token und ein Trustprofil; Backup und Restore reaktivieren keinen Zugang; die App läuft auf der Referenzinstallation mit echtem Admin-SSO, Licht und Klima.

**Architecture:** Design §3–§8. Je Arbeitspaket zuerst Dienste und Admin-API mit Abnahmetests, danach die Admin-Screens auf genau dieser API. Laufzeitsteuerung des Public-Listeners, Zertifikatswechsel und Restore laufen über `AppContext` (`refresh_public()`, `public_listener`, `restore_mode`). Zum Schluss Paketierung, reale Installation auf der Referenzinstallation und Doku.

**Tech Stack:** wie Teilpläne 1 und 2; neu nur `@radix-ui/react-dialog` und `@radix-ui/react-tabs` im Admin-Bundle sowie `@axe-core/playwright` in `tests/e2e`. Keine neue Python-Abhängigkeit: SPKI-Pins liest ein kleiner DER-Leser (Task 11.3), Proxy-Header übernimmt uvicorns `ProxyHeadersMiddleware` (Task 11.2), Argon2id kommt aus dem vorhandenen `argon2-cffi`.

**Design-Referenz:** `docs/superpowers/specs/2026-10-22-myha-design.md` – bei Widerspruch zwischen Plan und Design gilt das Design; Abweichungen an Fable melden. Der Plan folgt dem Design-Stand von Commit `e7e26cb` (Regel „Cover“ analog E7, „ein Deny erweitert nie“, implizites `metadata.read`, Principal-Status `active` | `disabled`, Admin-Endpunkte `GET session` und `POST session/check`). Abnahmefälle: Spez §19 (`docs/reference/2026-10-22-ha-access-ui-spezifikation-chatgpt.md`), Status in `docs/acceptance.md`.

**Revision 2** (nach Plan-Review): 27 Befunde eingearbeitet, unter anderem Export-Platzhalter für NOT-NULL-Spalten, Benutzername beim Credential-Reset, Zimmerwechsel mit Einladungen und Bestätigungssperre, CIDR-Normalisierung, Pins nur aus systemvertrauten Ketten, E28 über den laufenden SSL-Kontext, Prüfung `time`, Restprüfung statt Task 1.6, Aufteilung in 12.3/12.4, Markierung der Schritte, die den Betreiber brauchen.

**Voraussetzung:** Gate Teilplan 2 → 3 (Masterplan). Teilplan 3 baut auf den Dateien und Schnittstellen aus Teilplan 2 auf: `auth/{public,cookies,ratelimit}.py`, `services/{audit,stays,sessions,preview,commands,timeguard}.py`, `policy/{loader,drift}.py`, `events/{bus,connection,ws}.py`, `projection/*`, `api/public/*`, `api/admin/{stays,invites,sessions,preview,audit}.py`, `devseed.py`, Web `src/{api,transport,i18n,ui,guest}`, `tests/e2e/*`. Weichen Namen in der Umsetzung ab, gilt der Code; der Implementierer passt den Task an und meldet die Abweichung an Fable.

---

## Konventionen dieses Teilplans

Zusätzlich zu den Konventionen des Masterplans:

- **Pfade:** `myha_gateway/…` und `tests/…` relativ zu `myha/gateway/`, außer `tests/e2e/…` (Repo-Root, wie in Teilplan 2); `web/…` relativ zu `myha/`; alles andere relativ zum Repo-Root.
- **Prüfbefehle** (in jedem Task „Gateway-Prüfung“, „Web-Prüfung“, „E2E-Prüfung“):

```bash
cd myha/gateway && uv run pytest -q && uv run ruff check && uv run ruff format --check && uv run mypy   # Gateway-Prüfung
cd myha/web && pnpm test && pnpm lint && pnpm typecheck && pnpm build                                 # Web-Prüfung
cd tests/e2e && pnpm exec playwright test                                                             # E2E-Prüfung
```

- **Commit:** `git add <Dateien des Tasks>` und `git commit -m "<Message>" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`; Fable-Commits mit `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Admin-Routen:** Lese-GETs hängen an `require_admin(fresh=False)`; Mutationen und die sensiblen GETs aus E5 an `require_csrf` (frische Rollenprüfung plus CSRF). Jeder Admin-Router außer `session` und `restore` deklariert `not_in_restore` (Task 9a.1) als Router-Dependency. Mutationen admin-editierbarer Zeilen verlangen `expected_version` und antworten bei Abweichung `409 CONFLICT` mit `details.current_version`. Jede rechtewirksame Änderung erhöht `instance.policy_version` in derselben Transaktion; nach dem Commit folgen `ctx.policy.invalidate()` und `PolicyChanged` auf dem Bus. Jede Admin-Mutation schreibt ein Audit-Ereignis mit `actor = "admin:<user_id>"`. Diese Bausteine liegen ab Task 5b.2 in `services/admin_write.py`.
- **Schreibtransaktionen:** eine `write_session` je Vorgang, darin keine zweite Session und kein Netz-I/O (Docstring von `db.write_session`); Bus-Ereignisse und Listener-Neustarts erst nach dem Commit.
- **Browser-Mutationen nur per `fetch`** mit Header, nie per Formular-POST: Unter `Referrer-Policy: no-referrer` sendet ein Formular-POST `Origin: null` (A35, A49).
- **Geheimnisse:** Einladungs-Secrets, Passwörter, Token, Poll-Token, Tickets, CSRF-Token und `server_secret` erscheinen nie in Logs, Audit, Diagnose, URL-Pfaden oder `localStorage`. Formatprüfung von Token vor `hash_secret` (Regex base64url, 43 Zeichen), weil einzelne Surrogates sonst `UnicodeEncodeError` auslösen.
- **Migrationen:** Dateiname `NNNN_<name>.py` mit der nächsten freien Nummer nach dem Stand von Teilplan 2, `down_revision` = bisheriger Head; `test_migrated_schema_matches_models` bleibt grün. Ein späterer Batch-Rebuild von `instance` muss den Singleton-Trigger aus Task 12.3 neu anlegen; einer von `audit_event` braucht `table_kwargs={"sqlite_autoincrement": True}`.
- **Admin-Testfixtures:** `ctx`, `clocks`, `admin_app` und `admin_client` liegen in `tests/acceptance/conftest.py`. Integrationstests unter `tests/integration/` nutzen sie ebenfalls; falls Teilplan 2 sie noch nicht nach `tests/conftest.py` verschoben hat, verschiebt Task 5b.2 sie dorthin.
- **Vorgaben aus Teilplan 2 (Commit `963f700`, verbindlich):** Fähigkeiten werden über `DeviceCapabilities.to_json`/`from_json` gespeichert und gelesen; `decide()` bekommt `execute` immer ausdrücklich; Loader und Admin-Antworten lesen `policy_version` und `time_trusted` aus der DB, nie aus `ctx.instance`; nach einem Publish wird zuerst der Index neu gebaut; HA-Aufrufe und Lesesessions finden nie innerhalb einer offenen Schreibsession statt (der Laufzeit-Wächter aus Teilplan 2 lehnt sie ab), also HA-Zustand und Fähigkeiten vor `write_session` lesen; das Body-Limit des Public-Listeners gilt auch für Login, Refresh und Pairing.
- **„braucht den Betreiber“:** Der Schritt braucht eine Entscheidung, ein Gerät oder eine Einstellung von Dimitri (offene Punkte 1, 10, 11, 14) und blockiert nur sich selbst.
- **Referenzinstallation:** Schritte mit **„Fable, nach Zustimmung“** führt Fable selbst aus, erst nachdem Dimitri im Chat ausdrücklich zugestimmt hat. Subagenten bereiten nur Code und Skripte vor. Berichte und Doku enthalten keine LAN-IPs, Ingress-Token, HA-User-IDs, Entity-IDs der Referenzinstallation oder Klarnamen Dritter.

## Reihenfolge

| Block | Tasks | Hinweis |
|---|---|---|
| 0 | 13.1 | Nur Restprüfung: Task 1.6 aus Teilplan 1 ist als `fe5c10b` gelandet. |
| 1 | 5b.1–5b.8 | Öffentliche Auth Teil 2 |
| 2 | 9a.1–9a.7, danach 9a.8–9a.11 | Admin-API, dann Admin-UI Teil 1 |
| 3 | 10.1–10.7 | Aufenthalte vollständig |
| 4 | 11.1–11.6 | Setup-Assistent, TLS-Modi, Trustprofil |
| 5 | 12.1–12.4 | Backup und Restore |
| 6 | 9b.1–9b.8 | Admin-UI Teil 2 auf den APIs aus Teilplan 2 (Preview), 10, 11 und 12 |
| 7 | 13.2, 13.3, 13.4, 14.1–14.4 | Code, E2E und Lastskript vor der realen Installation; in 14.4 braucht nur der 72-Stunden-Lauf den Betreiber |
| 8 | 13.5–13.7 | „Fable, nach Zustimmung“; Schritte mit „braucht den Betreiber“ warten auf Dimitri und blockieren nur sich selbst |
| 9 | 14.5–14.7 | Doku, Abschluss, Submodule im privaten Repo |

Task 13.3 (pnpm-Hash) läuft in Block 7 mit; der Hook-Smoke steht in Task 12.2. Es läuft immer nur ein Implementierer je Repository. Nach jedem Arbeitspaket folgt ein Review-Subagent (Spec-Treue, dann Codequalität).

## Abnahmefälle in diesem Teilplan

| Fall | Stand nach Teilplan 2 | Teilplan 3 | Nachweis |
|---|---|---|---|
| A01–A05 real | automatisiert gegen den Simulator | 13.5, 13.6 | über den echten Ingress: `ingress-get api/v1/admin/session`, Admin-UI in der HA-Seitenleiste, A01 per `curl` am Public Origin; A04 mit einem herabgestuften Testbenutzer (braucht den Betreiber) |
| A03 (Hinweis in der UI) | Guard antwortet 503 mit `details.reason` | 9a.8 | Vitest: `supervisor_version` zeigt den sperrenden Update-Hinweis mit Ist- und Mindestversion |
| A04 (Live-Admin) | Mutationen grün | 9a.7 | `test_a04_admin_ws_closes_after_demotion` |
| A18 real | automatisiert | 13.6 Step 2 | Moduswechsel an der echten Klimaanlage gegen den Prüfbereich |
| A19, A21–A24, A28–A32, A34, A52 | grün | — | Die Regression tragen die Tests aus Teilplan 2, die in der Gateway-Prüfung jedes Tasks laufen; 14.1 ergänzt nur den Browserpfad |
| A20 | grün über direkte DB-Änderung | 9a.3 über die Admin-API | `test_a20_membership_removed_via_admin_api_updates_open_connection` |
| A25 | Widerruf schließt ≤ 5 s | 10.2 Checkout | `test_a25_checkout_ends_sessions_refresh_families_and_invites` |
| A26 | offen | 10.2 | `test_a26_no_static_room_qr` (einschließlich der Einladungen aus `stays/{id}/move`) |
| A27 | keine Historienfelder | 10.2 | `test_a27_next_stay_sees_nothing_of_previous_stay` |
| A33 | entfällt (E21) | — | — |
| A35 | CSRF, Origin, Ticket, Widerruf | 5b.4, 5b.5 | `test_a35_rotated_refresh_token_revokes_family`, `test_a35_expired_access_token_rejected`, Login-Origin-Test |
| A36–A38, A43 | Teilplan 4 | Serverteil: Trustprofil und Leaf-Pins (5b.3, 11.3, 11.4) | Geräte in Teilplan 4 |
| A39 | offen | 12.4 | `test_a39_restore_does_not_reactivate_codes_sessions_or_passwords` (mit neu gesetztem Benutzernamen); optional real (13.6 Step 4) |
| A40 | offen | 12.4, 13.2, 13.6 | `test_a40_backup_without_restore_keeps_live_state`, `test_a40_restore_never_widens`, `test_a40_fingerprint_upgrade_neither_widens_nor_flags`; real: Update per `rebuild` ohne `needs_review`-Welle (13.6 Step 6); `docs/restore.md` |
| A41 | offen | 14.2 | `a11y.spec.ts`: Viewports und 200 % Root-Schriftgröße bei 320 und 768 px |
| A42 | Gast-Tastatur (Teilplan 2) | 9a/9b, 14.2, 13.6 | Vitest, `a11y.spec.ts`; Screenreader manuell für Login, Dashboard, Rechteeditor und Vorschau |
| A44 | offen | 11.5 Serverteil, 13.6 real | Setup-Tests; Nachweis im echten Gast-WLAN (braucht den Betreiber) |
| A45 | Redaction-Filter | 14.3 | `test_a45_logs_audit_and_diagnostics_hold_no_secrets` |
| A46 | offen | 14.4 | Lastskript; der 72-Stunden-Lauf braucht den Betreiber |
| A47 | offen | 12.1, 13.6 | `test_a47_export_holds_no_live_state_or_secrets`; reales Archiv |
| A48 | offen | 12.1–12.4, 13.6 | `test_a48_*`; „gestoppte App“ als dokumentierter Lauf (13.6 Step 4) |
| A49 | Transport ohne Redirects | 5b.5, 5b.6 | `test_a49_auth_endpoints_never_redirect` |
| A50 | grün | 9b.3 | „Diese Einladung prüfen“ in der UI; Regression durch die Tests aus Teilplan 2 |
| A51 | offen | 10.3–10.5 | `test_a51_*` einschließlich `test_a51_moved_draft_cannot_bypass_confirm` |

---

## Dateistruktur (neu oder wesentlich geändert)

```
myha/gateway/myha_gateway/
├── public_listener.py · proxy.py · net.py
├── auth/passwords.py (erweitert) · auth/tokens.py · auth/pairing.py · auth/trust.py
├── backup/__init__.py · backup/export.py · backup/restore.py · backup/status.py
├── events/admin_hub.py · events/admin_ws.py
├── services/admin_write.py · principals.py · bindings.py · groups.py · roles.py · assignments.py
│   · fences.py · housekeeping.py · setup.py · diagnostics.py · metrics.py · stays.py (erweitert)
├── api/public/{login,refresh,pair,trust}.py
├── api/admin/{principals,units,ha,bindings,dashboard,groups,pairings,roles,assignments,fences,
│              explain,setup,backup,restore,diagnostics}.py
└── db/migrations/versions/NNNN_auth_part2.py · NNNN_stay_move.py · NNNN_instance_singleton.py
myha/gateway/tests/acceptance/test_matrix_auth_part2.py · test_matrix_stays.py · test_matrix_backup.py · test_matrix_logs.py
myha/web/src/admin/{App.tsx,session.tsx,rules.ts,admin.module.css}
myha/web/src/admin/api/{client,events,types}.ts · i18n/{de,en}.json · screens/*.tsx
myha/web/src/guest/routes/{Login,Pair,DeviceTest}.tsx
myha/rootfs/opt/myha/backup-export · backup-cleanup        (echte Hooks)
tests/e2e/{admin,resident,a11y}.spec.ts · ingress-proxy.mjs (erweitert)
scripts/install-on-ha.py (erweitert) · scripts/load-test.py
docs/restore.md · docs/device-matrix.md (neu) · docs/setup.md · docs/security.md (vervollständigt)
```

Jede Datei hat eine Verantwortung: `auth/tokens.py` gibt Token aus und rotiert sie, `auth/pairing.py` kennt nur den Pairing-Zustand, `auth/trust.py` liest Zertifikate und hält das Trustprofil, `public_listener.py` startet und stoppt nur den Public-Server, `backup/export.py` exportiert nur, `backup/restore.py` stellt nur wieder her.

---

## Vorgezogen: WP13 Task 13.1 – Restprüfung S0-Probe

Task 1.6 aus Teilplan 1 ist als `fe5c10b` gelandet: Probe-Route, CLI `probe`, Lifespan-Hook, `probe_mode` in `Settings` und in der Optionen-Whitelist, Probe-Zweig im Run-Skript; `/healthz` liefert nur `status` und `client_ip`. Der A01-Beleg `test_public_listener_has_no_admin_routes` bleibt. Übrig ist nur die Restprüfung.

**Files:** keine, außer die Prüfung findet etwas außerhalb der Allowlist.

- [ ] **Step 1 – Prüfen:** `grep -rni probe myha scripts .github --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=dist`. Erlaubt sind: die S0-Dummies `myha/rootfs/opt/myha/backup-export` und `backup-cleanup` (Task 12.2 ersetzt sie), der Whitelist-Kommentar in `myha_gateway/settings.py`, `tests/unit/test_settings.py` (ein alter `probe_mode`-Schlüssel wird ignoriert), `tests/integration/test_listeners.py::test_no_probe_route_and_no_probe_cli`, der Dateiname `probe.tar` in `tests/unit/test_install_script.py` und der Kommentar in `tests/integration/test_startup.py`.
- [ ] **Step 2 – Nur bei weiteren Treffern:** entfernen, Gateway-Prüfung, Commit `chore: remove S0 probe leftovers`. Ohne Treffer kein Commit.

---

## WP5b – Öffentliche Auth Teil 2

Abnahme: A35 (rotierter Refresh-Token, abgelaufene Session), A49, Token-Tests; Serverteil für A36–A38.

### Task 5b.1: Schema für Pairing, nativen Access-Ablauf und Pin-Policy

**Files:**
- Create: `myha_gateway/db/migrations/versions/NNNN_auth_part2.py`
- Modify: `myha_gateway/db/models.py`, `myha_gateway/db/__init__.py` (`ensure_instance`)
- Test: `tests/unit/test_db.py`

| Tabelle | Neue Spalte | Typ, Default | Zweck |
|---|---|---|---|
| `session` | `access_expires_at` | `datetime \| None` | Ablauf des aktuellen nativen Access-Tokens; `None` bei Browser-Sessions |
| `instance` | `trust_policy` | `str`, `server_default="pin_optional"` | `pin_required` \| `pin_optional` (E28) |
| `pairing_request` | `status` | `str`, `server_default="pending"` | `pending` \| `approved` \| `rejected` \| `consumed`; abgelaufen heißt `expires_at <= now` |
| | `audience` | `str` | `browser` \| `native` aus `pair/start` |
| | `device_label` | `str \| None` | höchstens 60 Zeichen |
| | `client_ip` | `str \| None` | nur Anzeige für den Admin; der Aufräumjob löscht die Zeile |
| | `principal_id` | `str \| None`, FK `principal.id` | bestätigter Display-Principal |
| | `unit_ids` | `list[str]`, `server_default="[]"` | bestätigter Scope, bei `approved` nie leer |
| | `decided_by`, `decided_at` | `str \| None`, `datetime \| None` | Admin-User-ID und Zeitpunkt |
| | `failed_attempts` | `int`, `server_default="0"` | falsche Code-Eingaben des Admins |

- [ ] **Step 1 – Tests (rot):** `test_db.py`: Migration legt die Spalten mit Defaults an; `ensure_instance` liefert `trust_policy == "pin_optional"`; eine roh eingefügte `pairing_request`-Zeile ohne die neuen Felder erhält `status == "pending"`, `unit_ids == []`, `failed_attempts == 0`; `test_migrated_schema_matches_models` bleibt grün. Lauf: `uv run pytest tests/unit/test_db.py -q` → FAIL.
- [ ] **Step 2 – Implementieren:** Modelle ergänzen, Migration per Autogenerate erzeugen und von Hand prüfen (Batch-Modus, `server_default` gesetzt, `UtcDateTime` als `sa.String()`).
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(db): pairing state, native access expiry and pin policy columns`.

### Task 5b.2: Passwörter und Bewohnerverwaltung

**Files:**
- Modify: `myha_gateway/auth/passwords.py`, `myha_gateway/deps.py` (`hash_slots`), `myha_gateway/app.py` (Router), `myha_gateway/services/sessions.py` (`revoke_principal_sessions`, falls Teilplan 2 ihn nicht hat)
- Create: `myha_gateway/services/admin_write.py`, `myha_gateway/services/principals.py`, `myha_gateway/api/admin/principals.py`
- Test: `tests/unit/test_passwords.py`, `tests/integration/test_admin_principals.py`

`services/admin_write.py` (gemeinsame Bausteine aller Admin-Mutationen; hat Teilplan 2 gleichwertige Helfer, etwa in `services/stays.py`, werden sie hierher verschoben statt dupliziert):

```python
def check_version(row: Versioned, expected: int) -> None:
    """Optimistic lock: 409 CONFLICT with details.current_version unless they match; then bump."""

async def bump_policy(session: AsyncSession) -> int:
    """instance.policy_version += 1 inside this write transaction; returns the new version."""

def after_policy_change(ctx: AppContext, version: int) -> None:
    """After the commit: ctx.policy.invalidate() and PolicyChanged(version) on the bus."""

def admin_actor(admin: AdminIdentity) -> str:
    return f"admin:{admin.user_id}"
```

`auth/passwords.py` (mypy strict, Coverage-Gate); die Semaphore `AppContext.hash_slots = asyncio.Semaphore(2)` (höchstens zwei Hashes gleichzeitig: begrenzt CPU und 2 × 64 MiB) liegt im Kontext, nicht modulweit, damit jeder Event-Loop und jeder Test seine eigene hat:

```python
# shortcut: provisional until S0 row 15 is measured (Task 13.6); check_needs_rehash upgrades
# existing hashes on the next login when these change.
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)

def normalize_username(raw: str) -> str: ...   # NFKC, casefold, strip; then ^[\w.@+-]{3,64}$ (Unicode letters allowed) else ValueError
def validate_password(password: str) -> str: ...  # NFKC; 10..512 characters else ValueError
async def hash_password(password: str, slots: asyncio.Semaphore) -> str: ...  # worker thread under slots
async def verify_password(stored: str | None, password: str, slots: asyncio.Semaphore) -> bool: ...
    # None (unknown user) verifies against a lazily created dummy hash: same cost, always False
def needs_rehash(stored: str) -> bool: ...
```

Admin-API (`api/admin/principals.py`, Router mit `not_in_restore` ab Task 9a.1):
- `GET /api/v1/admin/principals?type=resident|display` (`fresh=False`) → `[{id, type, display_name, status, username, has_password, locale, version, active_sessions}]`, nie Gäste.
- `POST /api/v1/admin/principals` (`require_csrf`): `{type: "resident", display_name, username, password, locale?}` oder `{type: "display", display_name}` → 201; normalisierter Benutzername schon vergeben → 409; Passwort nie in der Antwort; Audit `principal.create` mit `resource_id = principal_id`.
- `PATCH /api/v1/admin/principals/{id}` `{display_name?, status?: "active"|"disabled", locale?, expected_version}`; `disabled` widerruft alle Sessions und Refresh-Familien des Principals (Grund `principal_disabled`) und erhöht `policy_version`.
- `PUT /api/v1/admin/principals/{id}/credential` `{password, username?, expected_version}` (nur Bewohner): legt das Credential an oder ersetzt es. `username` ist Pflicht, solange der Bewohner keins hat (`has_password == false`, etwa nach einem Restore, Task 12.4), sonst optional zum Umbenennen; ein vergebener normalisierter Name → 409. Neuer Hash, `changed_at = now`, `must_reset = False` (gespeichert, nicht durchgesetzt), alle Sessions des Principals widerrufen (Grund `password_reset`); Audit `credential.reset` ohne Passwort und ohne Namen.

Nicht in diesem Teilplan: Passwortänderung durch den Bewohner selbst; `must_reset` bleibt `false` und wird nicht durchgesetzt (Entscheidung 4).

- [ ] **Step 1 – Tests (rot):** Unit: `normalize_username("  Ｍia.K ") == "mia.k"`; `normalize_username("Jürgen") == "jürgen"`; Leerzeichen innen oder `/` → `ValueError`; Passwort mit 9 bzw. 513 Zeichen → `ValueError`; `hash_password`/`verify_password` Rundlauf; falsches Passwort → `False`; `verify_password(None, "x")` → `False` und prüft gegen den Dummy-Hash (Zähler per Monkeypatch, kein Zeitvergleich); `needs_rehash` wahr für einen Hash mit `time_cost=2`. Integration: Bewohner anlegen → 201, Hash in der DB beginnt mit `$argon2id$`, Passwort nicht in der Antwort; Benutzername `MIA.k` nach `mia.k` → 409; POST ohne CSRF → 403; Reset widerruft eine vorhandene Session (`revoke_reason == "password_reset"`); Bewohner ohne Credential: `PUT credential` ohne `username` → 400, mit freiem Namen → 200 und Login möglich, mit vergebenem Namen → 409; Deaktivieren widerruft und erhöht `policy_version`; veraltete `expected_version` → 409 mit `current_version`; Liste ohne Gäste. Lauf: `uv run pytest tests/unit/test_passwords.py tests/integration/test_admin_principals.py -q` → FAIL.
- [ ] **Step 2 – Implementieren:** wie oben; Fixtures gemäß Konvention nach `tests/conftest.py` verschieben, falls nötig.
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(auth): argon2id passwords and resident management api`.

### Task 5b.3: Trustprofil

**Files:**
- Create: `myha_gateway/auth/trust.py`, `myha_gateway/api/public/trust.py`
- Modify: `myha_gateway/app.py`
- Test: `tests/unit/test_trust.py`, `tests/integration/test_me_trust.py`

```python
PIN_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")  # base64 of a 32-byte SHA-256 digest

class TrustPin(BaseModel):  # extra="forbid", frozen
    spki_sha256: Annotated[str, StringConstraints(pattern=PIN_RE.pattern)]
    role: Literal["primary", "backup"]
    valid_from: datetime

class TrustProfile(BaseModel):  # extra="forbid", frozen; at most one primary, no pin twice
    instance_id: str
    public_origin: str
    pins: tuple[TrustPin, ...]
    policy: Literal["pin_required", "pin_optional"]

def trust_profile(instance: Instance, public_origin: str) -> TrustProfile:
    """From instance.trust_pins and instance.trust_policy; a malformed row raises (fail closed)."""
```

`GET /api/v1/me/trust` verlangt eine Session mit `purpose` `access` oder `test` (Cookie oder Bearer) und liefert `trust_profile(ctx.instance, ctx.public_origin)`; `pin_optional` mit leerer Liste heißt reine Systemvertrauensprüfung (E28).

- [ ] **Step 1 – Tests (rot):** zwei `primary` → `ValueError`; Pin mit 42 Zeichen oder ohne `=` → Validierungsfehler; doppelter Pin → Fehler; Endpunkt ohne Session → 401; mit Session → Profil mit `public_origin == ctx.public_origin`, `Cache-Control: no-store`. Lauf: `uv run pytest tests/unit/test_trust.py tests/integration/test_me_trust.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(auth): trust profile endpoint for native clients`.

### Task 5b.4: Native Token-Familien – Ausgabe, Rotation, Wiederverwendung (A35)

**Files:**
- Create: `myha_gateway/auth/tokens.py`, `myha_gateway/api/public/refresh.py`
- Modify: `myha_gateway/auth/public.py` (Bearer-Prüfung, nativer Redeem über `issue_family`), `myha_gateway/deps.py` (Limiter `refresh_ip` 60/min), `myha_gateway/app.py`
- Test: `tests/unit/test_tokens.py`, `tests/acceptance/test_matrix_auth_part2.py`

```python
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # new_secret(); checked before hash_secret

@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int        # seconds until access_expires_at
    not_after: datetime    # end of the family = session.not_after

@dataclass(frozen=True)
class RefreshRejected:
    reason: Literal["unknown", "reuse", "inactive"]
    session_id: str | None   # set on reuse: the caller publishes SessionRevoked after the commit

async def issue_family(db: AsyncSession, session: Session, now: datetime, minutes: int) -> TokenPair:
    """session.family_id, token_hash and access_expires_at = min(now + minutes, not_after); first refresh row."""

async def rotate(db: AsyncSession, refresh_token: str, now: datetime, minutes: int) -> TokenPair | RefreshRejected:
    """Single use, inside a write session (BEGIN IMMEDIATE):
    UPDATE refresh_token SET rotated_at = :now, used_at = :now
     WHERE token_hash = :h AND rotated_at IS NULL
    rowcount 1 and the session active and now < not_after → new access token (session.token_hash,
    access_expires_at) and a new refresh row of the same family.
    rowcount 0 but the hash exists → reuse: the session and every refresh row of its family are
    revoked (revoke_reason "refresh_reuse")."""
```

Verhalten:
- `POST /api/v1/auth/refresh {refresh_token}`: kein Cookie, kein CSRF; 60/min je IP. Falsches Format, unbekannt, Wiederverwendung oder inaktive Session → 401 `UNAUTHENTICATED` mit byte-identischem Body; Wiederverwendung zusätzlich Audit `auth.refresh_reuse` und nach dem Commit `SessionRevoked`. Erfolg → `{access_token, refresh_token, expires_in, not_after, trust}` mit `trust = trust_profile(...)` (Design §4.3).
- `require_session` (Teilplan 2): Bearer authentifiziert nur Sessions mit `audience == "native"` und `access_expires_at > now`; das Cookie nur `audience == "browser"`.
- Nativer Redeem (Teilplan 2) nutzt `issue_family`; die Familie eines Gastes endet spätestens am Aufenthaltsende (`not_after = stay.ends_at`).
- Parallele Rotation desselben Tokens: genau eine gewinnt, die andere zählt als Wiederverwendung und sperrt die Familie. So verlangt es Spez §8.4; Clients serialisieren ihre Refresh-Aufrufe.

- [ ] **Step 1 – Unit-Tests (rot):** `tests/unit/test_tokens.py`: `TOKEN_RE` lehnt 42 und 44 Zeichen, `=`-Padding, Nicht-ASCII und `"\ud800" * 43` ab; `issue_family` setzt `family_id`, `token_hash` und `access_expires_at` und legt eine Refresh-Zeile an; `rotate` liefert ein neues Paar und markiert die alte Zeile; zwei parallele `rotate` desselben Tokens → genau ein `TokenPair` und ein `RefreshRejected("reuse")`, die Familie ist widerrufen. Lauf: `uv run pytest tests/unit/test_tokens.py -q` → FAIL.
- [ ] **Step 2 – `auth/tokens.py` implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Abnahmetests (rot):** `tests/acceptance/test_matrix_auth_part2.py`: `test_a35_rotated_refresh_token_revokes_family` (nativ einlösen → Paar 1; Refresh mit Paar 1 → Paar 2; erneut Paar 1 → 401; danach Paar 2 → 401; Bearer aus Paar 2 auf `/me` → 401; offene WS-Verbindung der Session erhält `session.revoked` innerhalb 5 s; Audit `auth.refresh_reuse` vorhanden); `test_a35_expired_access_token_rejected` (Uhr hinter `access_expires_at` → `/me` 401, Refresh liefert ein gültiges neues Access-Token); `test_bearer_never_authenticates_a_browser_session` und umgekehrt; `test_refresh_never_exceeds_family_end`. Lauf: `uv run pytest tests/acceptance/test_matrix_auth_part2.py -q` → FAIL.
- [ ] **Step 4 – Refresh-Endpunkt, Bearer-Prüfung in `require_session` und nativen Redeem umstellen;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Grün:** Gateway-Prüfung.
- [ ] **Step 6 – Commit:** `feat(auth): native token families with rotation and reuse detection (A35)`.

### Task 5b.5: Bewohner-Login (A49)

**Files:**
- Create: `myha_gateway/api/public/login.py`
- Modify: `myha_gateway/app.py`, `myha_gateway/deps.py` (Limiter `login_ip` 10/min, `login_user` 5/min)
- Test: `tests/integration/test_login.py`, `tests/acceptance/test_matrix_auth_part2.py`

`POST /api/v1/auth/login {username, password, client: "browser"|"native", device_label?}` prüft in dieser Reihenfolge:
1. Limiter je IP → 429 `RATE_LIMITED` mit `Retry-After`.
2. `client == "browser"`: `Origin` muss exakt `ctx.public_origin` sein, sonst 403 (Login-CSRF). Native Clients senden kein `Origin` und erhalten nie ein Cookie.
3. Benutzername normalisieren; der Limiter je Benutzername zählt auch unbekannte und ungültige Namen (Schlüssel: Kleinschreibung, auf 64 Zeichen gekürzt).
4. Credential über `username_normalized` laden, Principal `type == "resident"` und `status == "active"`.
5. `verify_password(hash or None, password, ctx.hash_slots)` im Worker-Thread; beide Listener teilen den Event-Loop.
6. Jeder Fehlschlag → 401 `UNAUTHENTICATED` „Invalid username or password“, byte-identisch für unbekannten Namen, falsches Passwort, deaktivierten Principal und ungültigen Namen; anonymes Audit `auth.login` mit Ergebnis `denied` und ohne den versuchten Namen (A45).
7. Erfolg: bei Bedarf neu hashen; Session mit `purpose=access`, `principal_id`, `unit_ids=[]`, `not_after = now + resident_browser_days` (Browser) bzw. `now + resident_refresh_days` (nativ, dann `issue_family`), `csrf_secret`, `device_label[:60]`. Browser → Cookie (Teilplan 2) und `{session, csrf_token}`; nativ → `TokenPair`. Audit `auth.login` mit dem Principal als Akteur.

- [ ] **Step 1 – Tests (rot):** Erfolg im Browser (Cookie-Attribute wie Teilplan 2, `/me` zeigt `principal.type == "resident"`); Erfolg nativ (Token, kein `Set-Cookie`); `r_unknown.content == r_wrong_password.content == r_disabled.content`; sechster Versuch für denselben Namen von verschiedenen IPs → 429; elfter von derselben IP → 429; Browser mit fremdem oder fehlendem `Origin` → 403; nativ ohne `Origin` → 200; Hash mit `time_cost=2` ist nach dem Login neu gehasht; während eines Logins (Verify im Thread auf 0,3 s verlangsamt) antwortet `/healthz` parallel in unter 0,1 s; `not_after == now + 30 Tage`. `test_a49_auth_endpoints_never_redirect` (in `tests/acceptance/test_matrix_auth_part2.py`): `login`, `redeem`, `refresh`, `session/renew` und `logout` je mit Slash am Ende, falscher Methode, falschem Content-Type und fremdem `Host` → nie 3xx, nie `Location`; ohne Variante antwortet jede Route nicht mit 404, die Liste prüft also echte Routen (Task 5b.6 ergänzt `pair/start` und `pair/poll`). Lauf: `uv run pytest tests/integration/test_login.py tests/acceptance/test_matrix_auth_part2.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung; Coverage `myha_gateway/auth/*` ≥ 85 %.
- [ ] **Step 4 – Commit:** `feat(auth): resident login with argon2id, rate limits and uniform errors (A49)`.

### Task 5b.6: Display-Pairing

**Files:**
- Create: `myha_gateway/auth/pairing.py`, `myha_gateway/api/public/pair.py`, `myha_gateway/api/admin/pairings.py`
- Modify: `myha_gateway/app.py`, `myha_gateway/deps.py` (Limiter `pair_start_ip` 5/min, `pair_poll_ip` 30/min)
- Test: `tests/integration/test_pairing.py`

Zustandsautomat (bei `client == "browser"` verlangen Start und Poll wie der Login `Origin` exakt gleich `ctx.public_origin`, weil der Poll ein Cookie setzt):
1. **Start** – `POST /api/v1/auth/pair/start {device_label?, client}`: höchstens 20 offene, nicht abgelaufene Anfragen insgesamt, sonst 429. Neue Zeile mit `id = new_id("pair")`, `code = f"{secrets.randbelow(10**6):06d}"`, `poll_token_hash`, `expires_at = now + 10 min`, `status = pending`, `audience`, `device_label[:60]`, `client_ip`. Antwort `{pairing_id, code, poll_token, expires_at}`; anonymes Audit `pairing.start`.
2. **Admin-Liste** – `GET /api/v1/admin/pairings` (`require_csrf`: sensibel wie Sessions, Liste der sensiblen GETs in Task 11.6) → offene, nicht abgelaufene Anfragen `{id, device_label, client_ip, created_at, expires_at}` **ohne Code**: Der Admin liest den Code am Tablet ab, so bestätigt er genau dieses Gerät.
3. **Bestätigen** – `POST /api/v1/admin/pairings/{id}/approve {code, principal_id? | display_name?, unit_ids}` (`require_csrf`): Anfrage offen und nicht abgelaufen, sonst 409. Code zeitkonstant vergleichen; falsch → `failed_attempts + 1` und 400, beim fünften Fehlversuch `status = rejected`. `unit_ids` nicht leer und alle vorhanden, sonst 400: Eine leere Liste hieße „keine Obergrenze“ und ist für Displays verboten. Bestehender aktiver Display-Principal oder neuer (`type = display`, `display_name`). Speichern: `status = approved`, `principal_id`, `unit_ids`, `decided_by`, `decided_at`; Audit `pairing.approve`.
4. **Ablehnen** – `POST /api/v1/admin/pairings/{id}/reject` → `rejected`; Audit.
5. **Poll** – `POST /api/v1/auth/pair/poll {pairing_id, poll_token}`: Token-Format und Hash prüfen, sonst 401 (einheitlich). Abgelaufen oder abgelehnt → `{status: "expired"|"rejected"}`; offen → `{status: "pending", expires_at}`; bestätigt → eine Schreibtransaktion: `UPDATE pairing_request SET status = 'consumed', approved_session_id = :sid WHERE id = :id AND status = 'approved'` mit `rowcount == 1`, sonst 401; Session `purpose = access`, `principal_id`, `audience`, **`unit_ids = request.unit_ids`**, `not_after = now + display_days`, `csrf_secret`, `device_label`. Browser → Cookie und `{status: "paired", session, csrf_token}`; nativ → `{status: "paired", ...TokenPair}`. Audit `pairing.paired`. Jeder weitere Poll → 401.

- [ ] **Step 1 – Tests für Start und Poll (rot):** `tests/integration/test_pairing.py`: Start liefert einen sechsstelligen Code, ein Poll-Token und den Ablauf; Browser-Start und -Poll mit fremdem `Origin` → 403; Poll einer offenen Anfrage → `pending`; abgelaufen → `expired`; falsches Poll-Token → 401; 21. offene Anfrage → 429; das Poll-Token steht weder im Log (`capfd`) noch im Audit. Lauf: `uv run pytest tests/integration/test_pairing.py -q` → FAIL.
- [ ] **Step 2 – `auth/pairing.py` und `api/public/pair.py` (Start, Poll ohne Abschluss) implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Tests für Admin-Seite und Abschluss (rot):** Admin-Liste ohne Code; fünf falsche Codes → `rejected`; leeres `unit_ids` → 400; unbekannte Unit → 400; kompletter Ablauf im Browser und nativ; `session.unit_ids` entspricht der bestätigten Liste, und bei einer Rolle über zwei Units mit nur einer bestätigten Unit zeigt das Dashboard nur diese; Bestätigen einer abgelaufenen Anfrage → 409; zweiter Poll nach `paired` → 401; `test_a49_auth_endpoints_never_redirect` um `pair/start` und `pair/poll` erweitern. Lauf: `uv run pytest tests/integration/test_pairing.py tests/acceptance/test_matrix_auth_part2.py -q` → FAIL.
- [ ] **Step 4 – Admin-Routen und den Poll-Abschluss implementieren;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Grün:** Gateway-Prüfung.
- [ ] **Step 6 – Commit:** `feat(auth): display pairing with admin-confirmed unit scope`.

### Task 5b.7: Test-QR-Sessions und Renewal aller Principal-Typen

**Files:**
- Create: `myha_gateway/net.py` (`client_ip_class`, später `parse_proxy_cidrs` aus Task 11.2)
- Modify: `myha_gateway/auth/public.py` (Redeem `purpose = guest_test`, `require_session` mit zulässigen Zwecken), `myha_gateway/api/public/me.py`, `myha_gateway/api/public/ws_ticket.py`, `myha_gateway/events/ws.py`
- Test: `tests/unit/test_net.py`, `tests/integration/test_test_sessions.py`, `tests/integration/test_renew.py`

Verhalten:
- Einladung mit `purpose = guest_test` (ohne Aufenthalt; angelegt ab Task 11.5, hier per Testhelfer): Redeem erzeugt eine Session `purpose = test`, `principal_id = None`, `unit_ids = []`, `not_after = now + 10 min`; nativ mit Familie, die ebenfalls nach 10 min endet.
- Zulässig für Testsessions: `GET /me`, `POST /ws-ticket`, `WS /events` (Snapshot mit leerem Dashboard), `GET /me/trust`, `POST /auth/logout`. Alles andere (Dashboard, Aktionen, Befehle, Renew) → 401.
- Erste erfolgreiche WS-Authentifizierung einer Testsession: Audit `setup.guest_test` (Ergebnis `ok`, `normalized_params = {"client_ip_class": …, "audience": …}`; nur die Klasse, nie die Adresse); `instance.setup_state` wechselt per DB-Update (`WHERE setup_state = 'configured'`) auf `verified`; `ctx.instance` wird dafür nicht ersetzt, Admin-Antworten lesen den Zustand aus der DB. `client_ip_class(ip)` in `net.py`: `loopback`; `docker` für `172.30.32.0/23` (HAOS-hassio-Netz) und `172.17.0.0/16`; `private` für RFC 1918, ULA und Link-local; sonst `public`.
- `/me` liefert `principal: {id, type, display_name} | null` und `session: {purpose, audience, not_before, not_after, waiting}` (ergänzen, falls Teilplan 2 Felder fehlen).
- Renew (Teilplan 2, `POST /api/v1/auth/session/renew`) für Bewohner und Displays: neues Cookie mit dem gespeicherten `not_after`, nie größer (Bewohner: Login + `resident_browser_days`, Display: Pairing + `display_days`). Gäste wie in Teilplan 2; eine Verlängerung des Aufenthalts (Task 10.3) setzt `not_after` vorher serverseitig.

- [ ] **Step 1 – Tests (rot):** Testeinladung einlösen → `/me` mit `purpose == "test"`; Dashboard → 401; WS liefert einen leeren Snapshot; genau ein Audit `setup.guest_test`; `setup_state == "verified"`; nach 10 min `/me` → 401; Peer `172.30.32.1` → Klasse `docker`; Renew für Bewohner und Display nie über ihre Obergrenze. Lauf: `uv run pytest tests/integration/test_test_sessions.py tests/integration/test_renew.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(auth): test sessions from setup qr codes and renewal for all principals`.

### Task 5b.8: Web – `/login`, `/pair`, Gerätetest und Wandmodus

**Files:**
- Create: `web/src/guest/routes/Login.tsx`, `web/src/guest/routes/Pair.tsx`, `web/src/guest/routes/DeviceTest.tsx`
- Modify: `web/src/guest/App.tsx` (Routen), `web/src/guest/routes/Dashboard.tsx`, `web/src/api/schema.ts` (Zod für Login- und Pair-Antworten), `web/src/i18n/de.json`, `web/src/i18n/en.json`
- Test: `web/src/guest/routes/Login.test.tsx`, `Pair.test.tsx`, `DeviceTest.test.tsx`, `Dashboard.test.tsx`

Verhalten:
- Layout wie die Einladungsseite des Mockups (zentrierte Karte, Instanzname und Betreiberkontakt aus `GET /api/v1/public/config`). Ein eigenes Login-Design liegt nicht in `docs/reference/design/` (Entscheidung 5).
- **Login:** Felder mit `autocomplete="username"` und `current-password`; Absenden nur per `fetch` (`onSubmit` mit `preventDefault`, `redirect: "manual"`), `client: "browser"`. Jedes 401 zeigt dieselbe Meldung, 429 die Wartezeit aus `Retry-After`; Erfolg → `/`. Das Passwortfeld wird nach jedem Versuch geleert; nichts landet in `localStorage`.
- **Pair:** optionales Gerätelabel → Start → großer Code `123 456` in einer `aria-live`-Region, Countdown bis `expires_at`. Poll alle 3 s, pausiert bei verborgenem Tab und läuft bei Sichtbarkeit weiter. `paired` → `/`; `rejected`/`expired` → Hinweis und „Neu starten“.
- **Dashboard:** `principal.type == "display"` → `GuestPanel` im Wandmodus (64-px-Ziele); `resident` → Schaltfläche „Abmelden“ (`POST /auth/logout` → `/login`); nach jedem `policy.changed` `/me` neu laden und `session/renew` aufrufen, wenn `not_after` gewachsen ist (Clientteil von A51).
- **DeviceTest** (`session.purpose == "test"`): WS mit Ticket öffnen, auf den leeren Snapshot warten, dann „Dieses Gerät erreicht myHA über HTTPS und WebSocket. Sie können die Seite schließen.“ oder den fehlgeschlagenen Schritt (HTTPS, Ticket, WebSocket) zeigen.

- [ ] **Step 1 – Tests (rot):** Login sendet JSON per `fetch` mit `redirect: "manual"`, zeigt für jedes 401 denselben Text, bei 429 die Sekunden und schreibt nie in den Storage (Spy auf `localStorage.setItem`); Pair pollt mit Fake-Timern, pausiert bei `visibilitychange`, Code steht in einer `aria-live`-Region; DeviceTest zeigt Erfolg und Fehlschlag; Dashboard ruft Renew nach `policy.changed` mit größerem `not_after`. Lauf: `cd myha/web && pnpm test src/guest` → FAIL.
- [ ] **Step 2 – Implementieren;** i18n-Schlüssel in beiden Sprachen (Schlüsselgleichheitstest aus Teilplan 2 bleibt grün).
- [ ] **Step 3 – Grün:** Web-Prüfung; `node scripts/check-no-external.mjs myha/web/dist/guest`.
- [ ] **Step 4 – Commit:** `feat(web): resident login, display pairing and device test pages`.

---

## WP9a – Admin-API und Admin-UI Teil 1

Abnahme: A04 (Live-Adminansicht), A20 über die Admin-API, A11 über die Admin-API; manuelle Abnahme und Tastaturpfad der Screens (Vitest hier, E2E in 14.2, real in 13.6).

### Task 9a.1: Restore-Sperre, Units und HA-Kandidaten

**Files:**
- Modify: `myha_gateway/auth/admin.py` (`not_in_restore`, `AdminGuard.csrf_secret`, `identity()` nimmt `HTTPConnection`), `myha_gateway/deps.py` (`restore_secret`), `myha_gateway/api/admin/session.py`, `myha_gateway/api/admin/{principals,pairings,stays,invites,sessions,preview,audit}.py` (Router-Dependency nachrüsten), `myha_gateway/app.py`
- Create: `myha_gateway/api/admin/units.py`, `myha_gateway/api/admin/ha.py`
- Test: `tests/integration/test_admin_units.py`, `tests/acceptance/test_matrix_admin.py` (Routen-Audit; Ersatz für `test_restore_pending_answers_503`)

Verhalten:
- `not_in_restore`: `ctx.restore_mode` → 503 `UPSTREAM_UNAVAILABLE`, `details.reason = "restore_pending"`. Alle Admin-Router außer `session` und `restore` (Tasks 12.3, 12.4) deklarieren sie; die Admin-Router aus Teilplan 2 und aus WP5b werden nachgerüstet.
- Restore-Modus mit Session statt 503: `AdminGuard.csrf_secret` → `ctx.instance.server_secret`, ohne Instanzzeile `ctx.restore_secret` (je Prozess `new_secret()` in `AppContext.create`). Im Restore-Modus antwortet `GET /api/v1/admin/session` 200 mit `setup_state = "restore_pending"`; das ist der Einstieg der UI in den Restore-Screen. Fehlt die Instanzzeile **ohne** Restore-Modus (ein Fehlerzustand), bleibt es bei der heutigen 503 `restore_pending`.
- `GET /api/v1/admin/session` liest `setup_state`, `time_trusted` und `policy_version` in einem kurzen Select aus der DB, nie aus `ctx.instance` (Startstand; Vorgabe aus Teilplan 2); ohne Instanzzeile sind `time_trusted` und `policy_version` `null`.
- `GET /api/v1/admin/units` (`fresh=False`) → `[{id, name, sort, version, bindings, needs_review, blocked}]` nach `sort`; `POST /units {name, sort?}` → `unit_<hex>`; `PATCH /units/{id} {name?, sort?, expected_version}`; `DELETE /units/{id}?expected_version=` → 409, solange Bindungen, Aufenthalte, Zuweisungen oder Unit-Fences sie nennen; sonst löscht es die `dashboard`-Zeile der Unit in derselben Transaktion mit. Jede Unit-Änderung erhöht `policy_version`, weil der Raumname in der Projektion steckt.
- `GET /api/v1/admin/ha/areas` → `[{id, name}]` aus `ctx.ha.registry().areas`.
- `GET /api/v1/admin/ha/entities?area=&kind=` → Kandidaten: nicht deaktivierte Registry-Entities mit `detect_kind(...)` in den fünf Arten und, falls `area` gesetzt, `registry.effective_area(entity_id) == area`; je Kandidat `{entity_id, name, kind, area_id, platform, preview: {actions, target_c_min, target_c_max, target_c_step, hvac_modes}, blocked_reason, bound_to}` über `adapter_for(kind).capabilities(state, entity, ha.temperature_unit)`. HA getrennt → 503 `details.reason = "ha_unavailable"`. Nur Anzeige, nie Rechte.

- [ ] **Step 1 – Tests (rot):** Unit-CRUD, veraltete Version → 409, Löschen mit Bindung oder Unit-Fence → 409, Löschen ohne Referenzen entfernt auch die Dashboard-Zeile; Kandidaten enthalten `light.living_ceiling` mit Aktionen, `light.group_all` mit `blocked_reason == "group_entity"`, `climate.room_204_dual` mit `dual_setpoint`, `switch.living_floor_lamp` unter `area_living` über das Gerät, nie `camera.*` oder `person.*`. `test_restore_pending_answers_503` wird ersetzt durch `test_restore_mode_session_uses_ephemeral_csrf` (Restore-Modus ohne Instanz: `GET /session` 200 mit `setup_state == "restore_pending"`, `POST /session/check` mit diesem Token 204, `GET /units` 503 `restore_pending`) und `test_missing_instance_outside_restore_answers_503`; `GET /session` zeigt eine `policy_version`, die ein Test direkt in der DB erhöht hat; Routen-Audit: jede Admin-API-Route außer `session` und `session/check` hängt an `not_in_restore`. Lauf: `uv run pytest tests/integration/test_admin_units.py tests/acceptance/test_matrix_admin.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(admin): restore gate, units and home assistant candidates`.

### Task 9a.2: Bindungen und Dashboard-Layout

**Files:**
- Create: `myha_gateway/services/bindings.py`, `myha_gateway/api/admin/bindings.py`, `myha_gateway/api/admin/dashboard.py`
- Modify: `myha_gateway/policy/drift.py` (Vergleichsfunktion für die Prüfansicht wiederverwendbar machen), `myha_gateway/ha/client.py` (`search_related`), `myha_simulator/server.py` und `myha_simulator/fixtures.py` (Handler `search/related` mit einer Automation und einem Skript, die `light.living_ceiling` nennen; `_sim`-Schalter für einen Fehler), `myha_gateway/app.py`
- Test: `tests/integration/test_admin_bindings.py`, `tests/integration/test_admin_dashboard.py`, `tests/integration/test_simulator.py`, `tests/integration/test_ha_client.py`

Verhalten:
- `GET /api/v1/admin/units/{id}/bindings` → `[{id, entity_id, kind, label, icon, status, block_reason, capabilities, version}]`.
- `POST /api/v1/admin/units/{id}/bindings {entity_id, label, icon?, confirmed_modes?}`: Fähigkeiten über den Adapter und `with_fingerprint`; `confirmed_modes ⊆ hvac_modes ∩ {off, cool, heat}`, sonst 400; Status `blocked`, wenn der Adapter einen Blockgrund meldet (`group_entity`, `template_entity`, `wrapper_entity`, `dual_setpoint`, `unit_not_celsius`, `no_setpoint`, `invalid_range`), sonst `confirmed`. Gespeichert werden `registry_entity_id`, `unique_id`, `device_id`, effektive `area_id`, `confirmed_capabilities` über `DeviceCapabilities.to_json` (Aktionen, Bereich, Schritt, `hvac_modes`, `confirmed_modes`, `blocked_reason`) und `capability_fingerprint`. HA-Zustand und Fähigkeiten werden vor der Schreibsession gelesen. Entity schon gebunden → 409; HA getrennt oder Temperatureinheit unbekannt → 503 `ha_unavailable`. Policy-Bump, Audit `binding.create`.
- `GET /api/v1/admin/bindings/{id}/review` → `{status, reasons, stored, current}`; `reasons` ⊆ `capabilities_changed`, `area_changed`, `device_replaced`, `entity_missing`, berechnet mit derselben Vergleichsfunktion wie `policy/drift.py`; `current` ist `null`, wenn die Entity fehlt.
- Review- und Bestätigungsantworten tragen `related: {automations: [{id, name}], scripts: [{id, name}], related_unavailable}` für die Entity, aus Core `search/related` mit `item_type: "entity"` (nur IDs und Namen; neue HAClient-Methode `search_related(entity_id)`, vor der Schreibsession aufgerufen). Fehler dieses Aufrufs ergeben leere Listen mit `related_unavailable: true` und blockieren die Bestätigung nie.
- `POST /api/v1/admin/bindings/{id}/confirm {expected_version, confirmed_modes?}`: Neuberechnung aus dem aktuellen HA-Zustand. Meldet der Adapter jetzt einen Blockgrund, wird bzw. bleibt der Status `blocked` (200, Grund in der Antwort); sonst `confirmed` mit neuem Fingerprint, Bereich, `unique_id` und `device_id`. Policy-Bump, Audit `binding.confirm`. Eine `blocked`-Bindung fällt nie von selbst auf `needs_review` zurück; nur diese ausdrückliche Bestätigung ändert sie.
- `PATCH /api/v1/admin/bindings/{id} {label?, icon?, expected_version}` (Label wird projiziert → Policy-Bump); `icon` passt auf `^[a-z0-9-]{1,40}$`. `DELETE /api/v1/admin/bindings/{id}?expected_version=` → Policy-Bump, Karte verschwindet aus dem Layout.
- `GET/PUT /api/v1/admin/dashboard/{unit_id}` mit `{layout: {cards: [{resource_id, size: "s"|"m"|"l"}], theme_default: "light"|"dark"|"contrast"|null}, expected_version}`: Karten nur Bindungen dieser Unit, keine Duplikate, sonst 400; Versions- und Policy-Bump (offene Clients erhalten einen neuen Snapshot). Fehlende Bindungen hängt die Projektion aus Teilplan 2 hinten an.

- [ ] **Step 1 – Tests (rot):** bestätigtes Licht erscheint im Index (`ctx.policy.index(now)`); Gruppenlicht → `blocked`, `decide` → `RESOURCE_UNCONFIRMED`; Klima mit `confirmed_modes=["auto"]` → 400, `["off","cool"]` für die Wohnzimmer-Klimaanlage → 201; doppelte Entity → 409; `_sim/entity/attributes` ändert `supported_features` → Drift (Teilplan 2) setzt `needs_review` → Review nennt `capabilities_changed` → Bestätigen → `confirmed` mit neuem Fingerprint; Bereichswechsel über `_sim/registry/area` → `area_changed`; Bestätigen einer weiterhin gesperrten Bindung bleibt `blocked`; Layout mit fremder Ressource → 400; veraltete Layout-Version → 409; Review von `light.living_ceiling` nennt die Fixture-Automation und das Skript; Simulator-Fehler bei `search/related` → leere Listen, `related_unavailable: true`, Bestätigung gelingt; Simulator-Test für den Handler und HAClient-Test für `search_related`. Lauf: `uv run pytest tests/integration/test_admin_bindings.py tests/integration/test_admin_dashboard.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(admin): resource bindings with review and confirmation, dashboard layout`.

### Task 9a.3: Gruppen und Mitgliedschaften (A20 über die Admin-API)

**Files:**
- Create: `myha_gateway/services/groups.py`, `myha_gateway/api/admin/groups.py`
- Modify: `myha_gateway/app.py`
- Test: `tests/integration/test_admin_groups.py`, `tests/acceptance/test_matrix_events.py`

Verhalten: `GET/POST /api/v1/admin/groups`, `PATCH /groups/{id} {name?, description?, expected_version}`, `DELETE /groups/{id}?expected_version=` (409, solange Zuweisungen die Gruppe nennen; Mitgliedschaften werden mitgelöscht), `GET /groups/{id}/members`, `PUT /groups/{id}/members {members: [{principal_id, valid_from?, valid_to?}], expected_version}` (ersetzt die Menge; nur Bewohner und Displays; `valid_from < valid_to`; Gruppen-Version-Bump). Jede Änderung: Policy-Bump, `invalidate`, `PolicyChanged`, Audit.

- [ ] **Step 1 – Tests (rot):** CRUD und 409-Fälle; Mitgliedschaft mit `valid_from` in der Zukunft gibt jetzt keine Rechte; Gast als Mitglied → 400; `test_a20_membership_removed_via_admin_api_updates_open_connection` (Bewohner mit Gruppengrant hat eine offene WS-Verbindung; `PUT members` ohne ihn → neuer Snapshot ohne die Ressource innerhalb 5 s; Aktion darauf → `NOT_FOUND`). Lauf: `uv run pytest tests/integration/test_admin_groups.py tests/acceptance/test_matrix_events.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(admin): groups and memberships`.

### Task 9a.4: Rollen – Entwurf, Versionen, Diff, Veröffentlichen

**Files:**
- Create: `myha_gateway/services/roles.py`, `myha_gateway/api/admin/roles.py`
- Modify: `myha_gateway/app.py`
- Test: `tests/unit/test_role_diff.py`, `tests/integration/test_admin_roles.py`

Verhalten:
- `GET /api/v1/admin/roles` → `[{id, name, description, latest_version, has_draft, version}]`; `POST /roles {name, description?}` → Vorlage mit leerem Entwurf; `PATCH /roles/{id} {name?, description?, expected_version}`.
- `GET /roles/{id}` → Vorlage, Entwurf, `draft_based_on`, Versionen `[{version_number, published_at, based_on_version}]`; `GET /roles/{id}/versions/{n}` → Regeln.
- `PUT /roles/{id}/draft {rules, based_on, expected_version}`: `RoleRules.model_validate` (`extra="forbid"`); alle Ressourcen-IDs sind Bindungen (sonst 400 mit den unbekannten IDs); `target_c`-Grenzen, Schritte und Ursprünge auf dem 0,1-Raster, `level_pct` und `position_pct` ganzzahlig, Modi ⊆ {off, cool, heat}; setzt `draft_based_on = based_on`; Versions-Bump der Vorlage. Kein Policy-Bump: Entwürfe gewähren nichts. Die Antwort enthält `warnings` für die Regel „Cover“ (Design §4.1), soweit sie aus dem Entwurf allein folgt, nach derselben Logik wie `PolicyIndex.end_code`: Nur wenn die Ressource einen `cover.set_position`-Grant hat, gilt `cover.open` gewährt, aber 100 in keinem seiner `position_pct`-Intervalle → `cover_open_never_offered`, entsprechend `cover.close` und 0 → `cover_close_never_offered`. Ohne `set_position`-Grant gibt es keine Regel und keine Warnung. Ein Deny auf `cover.set_position` ändert daran nichts, denn ein Deny erweitert nie; Fences prüft erst die Live-Vorschau.
- `DELETE /roles/{id}/draft?expected_version=` verwirft den Entwurf.
- `GET /roles/{id}/diff?from=<n>&to=<n|draft>` (Default: neueste veröffentlichte Version gegen Entwurf) → `{changes: [{resource_id, action, type, before, after}], affected: {assignments, stays: [{id, unit_name, status}], open_sessions}}` mit `type` ∈ `added`, `removed`, `widened`, `narrowed`, `changed`. Reine Funktion `diff_rules(before: RoleRules | None, after: RoleRules) -> list[Change]`: je (Ressource, Aktion) und Parameter vergleicht sie die Vereinigung der Intervalle als Decimal-Mengen (umfasst mehr → `widened`, weniger → `narrowed`, Schrittwechsel → `changed`), Enum-Mengen entsprechend; ein neuer Deny ist `narrowed`, ein entfernter `widened`.
- `POST /roles/{id}/publish {expected_version, rollout: false}`: 409, wenn `draft_based_on` nicht die neueste veröffentlichte Version ist (jemand hat inzwischen veröffentlicht) oder die Version abweicht. Neue `RoleVersion(id=f"{template_id}@{n + 1}", grants, denies, published_at, based_on_version)`; veröffentlichte Versionen ändert keine Route mehr. Entwurf geleert, Policy-Bump, Audit `role.publish` mit alter und neuer `policy_version`. `rollout: true` → 400 bis Task 10.6.

- [ ] **Step 1 – Unit-Tests für `diff_rules` (rot):** hinzugefügter und entfernter Grant, 19–23 → 19–24 `widened`, → 20–23 `narrowed`, Schritt 0,5 → 1 `changed`, neuer Deny `narrowed`; das A10-Beispiel mit zwei Intervallen wird nicht zu einer Spanne verschmolzen. Lauf: `uv run pytest tests/unit/test_role_diff.py -q` → FAIL.
- [ ] **Step 2 – `diff_rules` in `services/roles.py` implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Integrationstests der Rollen-API (rot):** Entwurf mit `res_unknown` → 400; Grenze `21.55` → 400; `cover.open` plus `cover.set_position` 0–60 → 200 mit Warnung `cover_open_never_offered`; `cover.open` ohne `set_position`-Grant → keine Warnung; `set_position` 0–100 → keine Warnung; zusätzlicher Deny auf `cover.set_position` → Warnung bleibt; Entwurf speichern und laden; zweimal vom selben Stand veröffentlichen → zweites Mal 409; Veröffentlichen erzeugt v1 und v2 mit `based_on`; Aufenthalte behalten ohne Rollout ihre Version; `GET diff` nennt die betroffenen Aufenthalte; `rollout: true` → 400. Lauf: `uv run pytest tests/integration/test_admin_roles.py -q` → FAIL.
- [ ] **Step 4 – Dienst und Routen implementieren;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Grün:** Gateway-Prüfung.
- [ ] **Step 6 – Commit:** `feat(admin): role drafts, versions, diff and publish`.

### Task 9a.5: Zuweisungen und Fences

**Files:**
- Create: `myha_gateway/services/assignments.py`, `myha_gateway/services/fences.py`, `myha_gateway/api/admin/assignments.py`, `myha_gateway/api/admin/fences.py`
- Modify: `myha_gateway/app.py`
- Test: `tests/integration/test_admin_assignments.py`, `tests/integration/test_admin_fences.py`

Verhalten:
- Zuweisungen: `GET /api/v1/admin/assignments?subject_type=&subject_id=&role_template_id=`, `POST {subject_type: "principal"|"group", subject_id, role_template_id, role_version, unit_ids}`, `PATCH /assignments/{id} {role_version?, unit_ids?, status?: "active"|"disabled_pending_review", expected_version}`, `DELETE`. Subjekt existiert (Bewohner, Display oder Gruppe; nie ein Gast), Version veröffentlicht, `unit_ids` nicht leer und alle vorhanden: Eine Bindung ohne Units gewährt nichts. Policy-Bump je Änderung.
- Fences: `GET /api/v1/admin/fences?scope_type=&scope_id=`, `POST {scope_type, scope_id, resource_ids, action, parameters}`, `PATCH /fences/{id} {resource_ids?, parameters?, expected_version}`, `DELETE`. Validierung beim Schreiben mit demselben `Fence.model_validate` wie der Kontextbau, damit keine gespeicherte Zeile einen Kontext scheitern lässt; `instance` → `scope_id = null`; `unit`, `principal`, `stay`, `session` → ID existiert; Principal-Fences nie auf Gäste (Gäste nur über `scope = stay`); Ressourcen existieren; Rasterregeln wie bei Rollen. Policy-Bump je Änderung.

- [ ] **Step 1 – Tests (rot):** Zuweisung mit leerem `unit_ids` → 400; nicht veröffentlichte Version → 400; Gast als Subjekt → 400; A11 über die API (Gruppengrant 19–23, Principal-Fence ≤ 22 → `decide` mit 22,5 → `FENCE_VIOLATION`); Fence mit `22.05` → 400; Principal-Fence auf einen Gast → 400; jede gespeicherte Fence-Zeile parst. Lauf: `uv run pytest tests/integration/test_admin_assignments.py tests/integration/test_admin_fences.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(admin): assignments and fences validated on write`.

### Task 9a.6: Erklärung je Ressource für Vorschauen

**Files:**
- Create: `myha_gateway/api/admin/explain.py`
- Modify: `myha_gateway/app.py`
- Test: `tests/integration/test_admin_explain.py`

Verhalten: `GET /api/v1/admin/preview/{preview_id}/explain?resource_id=` (`require_csrf`; nur die Vorschau des aufrufenden Admins, wie die Vorschau-Routen aus Teilplan 2) → je angebotener Schreibaktion der Ressource plus `state.read`: `{action, decision: {code, public_code}, grants: [matched ids], denied, fences, capability}`. `decision` kommt für Aktionen ohne Parameter aus `decide(..., execute=False)` mit der Live-`state_view`; für Aktionen mit Parametern (`light.set_level`, `climate.set_temperature`, `climate.set_mode`, `cover.set_position`) ist `decision` `null`, denn ein Code gilt dort nur für einen konkreten Wert: Die erlaubten Werte stehen in `capability`, den Code je Wert liefert `simulate` aus Teilplan 2; `fences` sind ausschließlich `index.fences(ctx, resource_id, action)` als JSON, nie `ctx.fences` (enthält die Unit-Fences aller Units); `capability` ist `effective_capability` oder `null`. Ressource außerhalb des Scopes → 404 `NOT_FOUND` wie in Teilplan 2.

- [ ] **Step 1 – Tests (rot):** Fence einer anderen Unit fehlt in der Erklärung; Grant-IDs der Treffer; Deny → `denied: true` und `DENIED_BY_RULE`; Ressource außerhalb → 404; Vorschau eines anderen Admins → abgelehnt wie in Teilplan 2. Lauf: `uv run pytest tests/integration/test_admin_explain.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(admin): per-resource explain for previews`.

### Task 9a.7: Admin-WebSocket (A04)

**Files:**
- Create: `myha_gateway/events/admin_hub.py`, `myha_gateway/events/admin_ws.py`
- Modify: `myha_gateway/auth/admin.py` (`csrf_ok` prüft auch einen übergebenen Token), `myha_gateway/deps.py` (`admin_hub`), `myha_gateway/app.py` (Route, Hub-Task im Lifespan)
- Test: `tests/acceptance/test_matrix_admin.py` (A04 und Routen-Audit), `tests/integration/test_admin_ws.py`

Verhalten:
- Routen-Audit: `test_every_admin_api_route_requires_the_guard` erlaubt ab hier genau eine WebSocket-Route, `/api/v1/admin/events` mit dem Endpunkt aus `events/admin_ws.py`; jede andere Route bleibt ein `APIRoute`.
- `WS /api/v1/admin/events`: Die `PeerGuardMiddleware` weist fremde Peers wie heute vor dem Handshake ab. Danach genau ein `X-Remote-User-Id`, `accept()`, erste Nachricht innerhalb 5 s `{"type": "auth", "csrf": "<token>"}`, sonst Close 4401. Prüfung: `guard.identity(websocket, fresh=True)` und CSRF-Token von heute oder gestern für diesen Benutzer und den `X-Ingress-Path` des Handshakes; `Sec-Fetch-Site` muss, falls vorhanden, `same-origin` sein. Fehlschlag → Close 4403. Erfolg → `{"type": "ready"}`. Danach alle 5 s `identity(fresh=True)`; jeder Fehlschlag → `{"type": "admin.revoked"}` und Close 4403. Weitere Client-Nachrichten → Close 1008; Frames ≤ 4 KiB.
- `AdminHub`: Solange mindestens ein Admin verbunden ist, liest ein Task alle 2 s in einer kurzen Lesesession `max(audit_event.id)`, `instance.policy_version` und einen Hash über die IDs der offenen Pairing-Anfragen (eine Anfrage, die abläuft, während eine neue kommt, ändert die Zahl nicht, den Hash schon) sowie die mtime von `state_dir/backup-status.json` (fehlt die Datei, zählt das als unverändert). Änderungen → `audit.appended {last_id}`, `policy.changed {policy_version}`, `pairings.changed {}`, `backup.status {}`. Die UI lädt daraufhin neu; der Hub braucht keine Aufrufe aus anderen Diensten. Je Verbindung eine begrenzte Queue (64), Überlauf → Close 1013. Keine Lesesession bleibt über eine Schleifenrunde offen. Im Restore-Modus liest der Hub nichts.

- [ ] **Step 1 – Tests (rot):** ohne Auth-Nachricht → 4401 nach 5 s (Uhr injiziert); falscher CSRF → 4403; `test_a04_admin_ws_closes_after_demotion` (Besitzer über `_sim/auth/users` herabgestuft → `admin.revoked` und Close 4403 innerhalb 5 s plus Toleranz); neue Audit-Zeile → `audit.appended` innerhalb 2 s; neue Pairing-Anfrage → `pairings.changed`, auch wenn gleichzeitig eine andere abläuft; fremder Peer → Handshake abgelehnt; der Routen-Audit kennt die WS-Route. Lauf: `uv run pytest tests/integration/test_admin_ws.py tests/acceptance/test_matrix_admin.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(admin): admin event socket with first-message auth and 5 s role checks (A04)`.

### Task 9a.8: Web – Admin-Shell, API-Client, Session-Bootstrap

**Files:**
- Modify: `web/src/admin/main.tsx`, `web/package.json` (`@radix-ui/react-dialog`, `@radix-ui/react-tabs`; Skript `gen:api:admin`)
- Create: `web/src/admin/App.tsx`, `web/src/admin/session.tsx`, `web/src/admin/api/client.ts`, `web/src/admin/api/events.ts`, `web/src/admin/api/types.ts` (generiert), `web/src/admin/i18n/de.json`, `web/src/admin/i18n/en.json`, `web/src/admin/admin.module.css`
- Delete: `web/src/ui/ThemeSwitch.tsx`, sobald nichts mehr importiert (der `ThemeProvider` aus Teilplan 2 ersetzt ihn)
- Test: `web/src/admin/api/client.test.ts`, `web/src/admin/api/events.test.ts`, `web/src/admin/App.test.tsx`, `web/src/admin/i18n/keys.test.ts`

`gen:api:admin` = `uv --directory ../gateway run myha-gateway openapi --admin > openapi.admin.json && openapi-typescript openapi.admin.json -o src/admin/api/types.ts` (Subcommand aus Teilplan 2; `openapi.admin.json` in `.gitignore`).

Verhalten:
- `HashRouter` mit `#/rooms`, `#/people`, `#/roles/:id?`, `#/preview`, `#/stays`, `#/sessions`, `#/setup`, `#/audit`, `#/publish/:id?`, `#/restore`; Start `#/setup` bei `setup_state == "fresh"`, `#/restore` bei `restore_pending`, sonst `#/rooms`.
- Basis-URL = `new URL(".", document.baseURI)`; Sonderfall: endet `location.pathname` nicht auf `/` und enthält das letzte Pfadsegment keinen Punkt (Ingress-Eintrag ohne Slash, etwa `/api/hassio_ingress/abc`), wird zuerst `/` angehängt, sonst fiele das Segment weg; ein Dateiname wie `/admin.html` bleibt unverändert. Das ist der Ingress-Prefix, nie hart codiert. Alle API-Pfade relativ (`api/v1/admin/…`).
- `client.ts` – `request(method, path, body?)`: JSON, `credentials: "same-origin"`, `redirect: "error"`; Header `X-MyHA-CSRF` bei jeder Anfrage aus der Session im Speicher (nie Storage). 403 → Session genau einmal neu laden (ein gemeinsames Promise für gleichzeitige Fehlschläge) und die Anfrage genau einmal wiederholen; zweites 403 → Zustand `denied` („Kein Admin-Zugang mehr“). 503 nach `details.reason`: `supervisor_version` → sperrender Hinweis mit Ist- und Mindestversion; `supervisor_unreachable` → Banner, neuer Versuch nach 5 s; `restore_pending` → `#/restore`; `ha_unavailable` → Banner. 409 → `ConflictError` mit `current_version` („neu laden und erneut versuchen“). Sonstige Fehler → Hinweis mit der Servermeldung.

```ts
async function request<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const response = await fetch(new URL(path, base()), {
    method, credentials: "same-origin", redirect: "error",
    headers: { "Content-Type": "application/json", "X-MyHA-CSRF": session.csrf() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 403 && !retried) {
    await session.reloadOnce(); // one shared reload for every request that failed meanwhile
    return request<T>(method, path, body, true);
  }
  return decode<T>(response); // 403 again → denied, 503 by reason, 409 → ConflictError
}
```

- `session.tsx`: `SessionProvider` lädt `GET api/v1/admin/session` (Benutzer, CSRF, Versionen, `setup_state`, `time_trusted`, `policy_version`); Kopfzeile wie im Mockup mit Admin-Name, Supervisor-Version, Policy-Version und Setup-Zustand.
- `events.ts`: Admin-WS (`wss:` oder `ws:` nach Seitenprotokoll, Pfad relativ zur Basis), erste Nachricht `{"type": "auth", "csrf"}`, Reconnect mit Backoff 1–10 s; 4403 → Session einmal neu laden und neu verbinden, zweites 4403 → `denied`; `subscribe(type, handler)`.
- Shell: Navigation aus dem Mockup (Icons aus `@tabler/icons-react`), `aria-current="page"`, Skip-Link, DE/EN über `useI18n` aus Teilplan 2 mit eigenen Admin-Tabellen (Admin-Texte landen nie im Gast-Bundle), `ThemeProvider` aus Teilplan 2.

- [ ] **Step 1 – Client-Tests (rot):** `client.test.ts`: Basis-URL für `/api/hassio_ingress/abc/`, `/api/hassio_ingress/abc` und `/admin.html`; CSRF-Header bei GET und POST; 403 → genau ein Session-Reload und eine Wiederholung; zwei parallele 403 → ein Reload; jeder 503-Grund führt in seinen Zustand, `supervisor_version` zeigt den sperrenden Hinweis mit Ist- und Mindestversion (A03 in der UI); 409 liefert `current_version`; Storage wird nie beschrieben. Lauf: `cd myha/web && pnpm test src/admin/api/client` → FAIL.
- [ ] **Step 2 – `client.ts` und `session.tsx` implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Event-Tests (rot):** `events.test.ts`: sendet zuerst `auth`, verbindet mit Backoff neu, zweimal 4403 → `denied`. Lauf: `pnpm test src/admin/api/events` → FAIL.
- [ ] **Step 4 – `events.ts` implementieren;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Shell-Tests (rot), dann Shell:** `App.test.tsx` (Startroute je `setup_state`, Navigation mit `aria-current`, Skip-Link), `keys.test.ts` (DE und EN mit denselben Schlüsseln) → FAIL; dann `App.tsx`, Router, i18n-Tabellen, `pnpm gen:api:admin` und eingecheckte Typen → PASS.
- [ ] **Step 6 – Grün:** Web-Prüfung; danach `grep -rl "Sessions & checkout" myha/web/dist/guest` → keine Treffer (Admin-Texte nicht im Gast-Bundle) und `node scripts/check-no-external.mjs myha/web/dist/admin`.
- [ ] **Step 7 – Commit:** `feat(web): admin shell with ingress-safe routing, csrf handling and admin events`.
- [ ] **Step 8 – Ingress-Proxy für den Dev-Stack:** `tests/e2e/ingress-proxy.mjs` schneidet ab jetzt den Prefix `/api/hassio_ingress/e2e/` ab, reicht WebSocket-Upgrades weiter, setzt `X-Remote-User-Id` und `X-Ingress-Path` und entfernt eingehende Identitätsheader; der Zielport kommt aus `MYHA_ADMIN_PORT` (Default 8099). Prüfung: Proxy gegen `scripts/dev.sh` starten, `curl -s localhost:18098/api/hassio_ingress/e2e/api/v1/admin/session` → 200; E2E-Prüfung bleibt grün. Commit `test(e2e): ingress proxy with prefix stripping and websocket upgrades`. Task 9b.8 Step 5 und Task 14.1 nutzen ihn nur noch.

### Task 9a.9: Web – Rooms & devices

**Files:**
- Create: `web/src/admin/screens/Rooms.tsx`, `web/src/admin/screens/BindingDialog.tsx`, `web/src/admin/screens/LayoutTab.tsx`
- Test: `web/src/admin/screens/Rooms.test.tsx`, `web/src/admin/screens/LayoutTab.test.tsx`

Verhalten (Mockup „Rooms & devices“, E10): Raumliste mit Zählern und Hinweis bei offener Prüfung; Raumdetail mit Radix-Tabs „Geräte“ und „Layout“. Gerätetabelle: Label, Produkt-ID, HA-Entity, Art, bestätigte Fähigkeiten, Status-Chip `Confirmed` | `Needs review` | `Blocked` mit Text (nie nur Farbe) für jeden Block- und Prüfgrund. Dialog „Aus HA-Bereich hinzufügen…“ und „Entity hinzufügen…“ (Radix Dialog): Kandidaten mit Art- und Bereichsfilter, Fähigkeitsvorschau, Label und Symbol, Modusbestätigung nur für `hvac_modes ∩ {off, cool, heat}`; gesperrte Kandidaten sind sichtbar und nur als `blocked` mit Erklärung bindbar. Prüfdialog: gespeichert gegen aktuell, verknüpfte Automationen und Skripte aus `related` mit dem Hinweis „Diese Automationen und Skripte können das Gerät unabhängig von myHA schalten“ (bei `related_unavailable` „Verknüpfungen nicht abrufbar“), „Bestätigen“ mit `expected_version`. Label und Symbol inline ändern, Bindung entfernen (Bestätigungsdialog). Layout-Tab: Kartenliste mit Größe (s/m/l), „nach oben“/„nach unten“-Schaltflächen als Tastaturweg statt Drag-and-drop, Theme-Default, Speichern mit `expected_version`, 409 → Hinweis zum Neuladen. Raum anlegen, umbenennen, löschen.

- [ ] **Step 1 – Tests (rot):** alle Status-Chips und Blockgründe als Text; Hinzufügen sendet `confirmed_modes` nur aus der erlaubten Menge; gesperrter Kandidat hat keine Bestätigen-Schaltfläche; Prüfdialog listet `related` mit dem Hinweis und zeigt bei `related_unavailable` den Ersatztext; Umsortieren per Tastatur (Fokus auf „nach unten“, Enter) ändert die Reihenfolge und speichert; 409 zeigt den Hinweis. Lauf: `pnpm test src/admin/screens/Rooms src/admin/screens/LayoutTab` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): rooms and devices with binding review and layout tab`.

### Task 9a.10: Web – People & groups

**Files:**
- Create: `web/src/admin/screens/People.tsx`, `web/src/admin/screens/PairingDialog.tsx`
- Test: `web/src/admin/screens/People.test.tsx`

Verhalten (E11): Bewohner (Name, Benutzername, Status, aktive Sessions; anlegen mit Passwort ab 10 Zeichen; Dialog „Passwort zurücksetzen“, der nichts zurückzeigt und bei Bewohnern ohne Credential (nach einem Restore) zusätzlich den Benutzernamen verlangt; deaktivieren und aktivieren); Gruppen (anlegen, umbenennen, löschen; Mitgliedereditor mit Gültigkeitsdaten in der Instanzzeitzone, gesendet als UTC); Displays (gepaarte Displays mit Scope und Ablauf aus ihren Sessions, „Gerät widerrufen“; offene Pairing-Anfragen aus `GET pairings`, aktualisiert über `pairings.changed`; Bestätigen-Dialog verlangt den am Tablet abgelesenen Code, neuen oder bestehenden Display-Namen und mindestens eine Unit, ohne Unit bleibt „Bestätigen“ deaktiviert; Ablehnen). Zuweisungen eines Principals oder einer Gruppe nur lesend mit Link zu „Roles & rights“.

- [ ] **Step 1 – Tests (rot):** Bestätigen ohne Unit deaktiviert; Code wird gesendet und ein falscher Code zeigt den Fehler; nach dem Reset erscheint kein Passwort; ohne Credential ist das Namensfeld Pflicht, ein 409 zeigt „Name vergeben“; Gültigkeit geht als UTC-ISO raus; Pairing-Liste lädt nach `pairings.changed` neu. Lauf: `pnpm test src/admin/screens/People` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): people, groups and display pairing approval`.

### Task 9a.11: Web – Roles & rights mit Live-Vorschau

**Files:**
- Create: `web/src/admin/rules.ts`, `web/src/admin/screens/Roles.tsx`, `web/src/admin/screens/RoleMatrix.tsx`, `web/src/admin/screens/RoleCards.tsx`, `web/src/admin/screens/ParamEditor.tsx`, `web/src/admin/screens/Fences.tsx`, `web/src/admin/screens/Assignments.tsx`, `web/src/admin/screens/LivePreview.tsx`
- Test: `web/src/admin/rules.test.ts`, `web/src/admin/screens/Roles.test.tsx`

Verhalten (Mockup „Roles & rights“):
- Rollenliste; Editor mit zwei Ansichten: Matrix (Zeilen = Bindungen der gewählten Units, Spalten Lesen, Ein, Aus, Dimmen, Temperatur, Modus, Öffnen, Stopp, Schließen, Position, Historie-Deny) und Karten mit Chips; „— für diese Art nicht anwendbar“ bei geräte-unmöglichen Zellen.
- Parameter-Editoren: Temperaturintervalle (Min, Max, Schritt, mehrere Intervalle für A10), Bereiche für `level_pct` und `position_pct`, erlaubte Modi; Hinweis „Werte außerhalb werden abgelehnt, nie geklemmt. Gerätebereich …“ aus den Bindungsfähigkeiten; Hinweis, wenn der effektive Schritt wegen des Geräteschritts gröber ist (kgV-Regel); bei Jalousien der Hinweis aus der Regel „Cover“ (Öffnen wirkt nur, wenn 100 %, Schließen nur, wenn 0 % im Positionsbereich liegt) samt den `warnings` aus der Entwurfsantwort; Hinweis „Sichtbar ist eine Ressource, sobald Lesen oder eine Schreibaktion gewährt ist, die das Gerät kann“ (implizites `metadata.read`).
- Denies (Historie-Deny gewinnt); Abschnitt Fences (Scope, Ressource, Aktion, Grenze, Erklärtext „Eine Fence kann nur einschränken“); Abschnitt Zuweisungen (Subjekt, Version, mindestens eine Unit).
- „Entwurf speichern“ mit `expected_version`, „Entwurf verwerfen“, „Prüfen und veröffentlichen“ → `#/publish/:id`.
- Live-Vorschau: Zielauswahl (Gast eines Entwurfsaufenthalts dieser Rolle, Bewohner, Display, Gruppe); legt bei Bedarf eine Vorschau an (API aus Teilplan 2) und rendert `GuestPanel` mit `simulate` und `explain` (Daten aus Task 9a.6, also nur die Fences dieser Ressource); Liste simulierter Taps mit `would_allow`/`would_deny` und Code; Banner „Vorschau als … · Simulation · Aktionen werden nicht ausgeführt.“
- `rules.ts`: `toRules(cells) -> RoleRules` kanonisch (ein Grant je Ressource und Aktion, sortiert), `fromRules(rules) -> cells` (Grants mit mehreren Ressourcen oder Aktionen werden aufgeteilt, Intervalle bleiben getrennt); Rundlauf-Eigenschaft.

- [ ] **Step 1 – Tests für `rules.ts` (rot):** Rundlauf `fromRules(toRules(x))` gleich `x` für Beispielmatrizen und für einen per API angelegten Grant mit zwei Ressourcen; zwei Intervalle (A10) bleiben getrennt; `21.55` wird abgelehnt. Lauf: `pnpm test src/admin/rules` → FAIL.
- [ ] **Step 2 – `rules.ts` implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Editor-Tests (rot):** Matrixzelle per Leertaste umschaltbar; nicht anwendbare Zellen sind beschriftet; eine Entwurfsantwort mit `cover_open_never_offered` zeigt den Hinweis an der Jalousie; der Parameter-Editor sendet getrennte Intervalle; Fences mit Scope und Grenze; Zuweisungen nur mit mindestens einer Unit. Lauf: `pnpm test src/admin/screens/Roles` → FAIL.
- [ ] **Step 4 – Matrix, Karten, Parameter-Editor, Fences und Zuweisungen implementieren;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Vorschau-Tests (rot), dann Live-Vorschau:** die Vorschau ruft nie `/api/v1/actions` (Fetch-Spy), zeigt das Banner, und die Erklärung zeigt nur Fences dieser Ressource → FAIL; `LivePreview.tsx` implementieren → PASS.
- [ ] **Step 6 – Grün:** Web-Prüfung.
- [ ] **Step 7 – Commit:** `feat(web): roles and rights editor with live preview and explain`.

---

## WP10 – Aufenthalte vollständig

Abnahme: A25, A26, A27, A51.

### Task 10.1: Aufenthaltsdetails, Bestätigen, Stornieren, Entwurf bearbeiten

**Files:**
- Modify: `myha_gateway/services/stays.py`, `myha_gateway/api/admin/stays.py`, `myha_gateway/api/admin/sessions.py` (Filter)
- Test: `tests/integration/test_admin_stays.py`

Verhalten:
- `GET /api/v1/admin/stays/{id}` (`require_csrf`, enthält Gerätedaten) → Aufenthalt mit abgeleitetem Status (`draft`, `planned`, `active`, `ended`, `checked_out`, `cancelled`), Einladungen `[{id, device_slot, status: pending|redeemed|expired|revoked, redeem_until}]` und Sessions `[{id, device_label, audience, created_at, last_seen_at, not_after, revoked_at}]`.
- `PATCH /api/v1/admin/stays/{id}` nur für `draft`: `{unit_id?, starts_at?, ends_at?, role_template_id?, role_version?, device_slots?, external_ref?, expected_version}`, Validierung wie beim Anlegen; sonst 409.
- `POST /api/v1/admin/stays/{id}/confirm {expected_version}`: `draft` → `confirmed` ohne Einladungen (verschobene Entwürfe in Task 10.4).
- `POST /api/v1/admin/stays/{id}/cancel {expected_version}`: `draft` oder noch nicht begonnenes `confirmed` → `cancelled`; Einladungen und wartende Sessions samt Refresh-Familien in derselben Transaktion widerrufen (`revoke_stay_sessions` aus Teilplan 2); begonnener Aufenthalt → 409 (dafür gibt es Checkout).
- `GET /api/v1/admin/sessions` erhält die Filter `principal_id` und `principal_type` (`resident`, `display`, `guest`) zusätzlich zu `stay_id`.
- Policy-Bump bei Bestätigen, Stornieren und Ändern; `/api/v1/admin/stays/{stay_id}` kommt auf die Liste sensibler GETs (Task 11.6).

- [ ] **Step 1 – Tests (rot):** abgeleiteter Status an den Grenzen (UTC, Start inklusiv, Ende exklusiv, Ende am Sommerzeitwechsel); PATCH auf `confirmed` → 409; Stornieren vor Beginn widerruft die wartende Session (A24-Fall) und schließt ihre WS-Verbindung innerhalb 5 s; Stornieren nach Beginn → 409; Sessions-Filter. Lauf: `uv run pytest tests/integration/test_admin_stays.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(stays): stay details, confirmation, cancellation and draft editing`.

### Task 10.2: Checkout (A25, A26, A27)

**Files:**
- Modify: `myha_gateway/services/stays.py`, `myha_gateway/api/admin/stays.py`
- Test: `tests/acceptance/test_matrix_stays.py`

Verhalten: `POST /api/v1/admin/stays/{id}/checkout {expected_version}` in einer Schreibtransaktion: `status = checked_out`, `checked_out_at = now`; alle Sessions, Refresh-Familien und nicht eingelösten Einladungen des Aufenthalts widerrufen (Grund `checkout`); Policy-Bump. Nach dem Commit `SessionRevoked(stay_id)` und `PolicyChanged`. Antwort `{revoked_sessions, revoked_families, revoked_invites}` für den Dialog; Audit `stay.checkout`. Checkout eines Entwurfs, eines stornierten oder eines schon ausgecheckten Aufenthalts → 409.

- [ ] **Step 1 – Tests (rot):**
  - `test_a25_checkout_ends_sessions_refresh_families_and_invites`: Browser-Session mit offener WS-Verbindung, native Familie und eine offene Einladung → Checkout → WS geschlossen innerhalb 5 s, `/me` 401 für beide, Refresh 401, Redeem der offenen Einladung 401.
  - `test_a26_no_static_room_qr`: Einladungen entstehen nur über `stays/{id}/invites`, `stays/{id}/move`, `sessions/{id}/replace` und `setup/guest-test` (Routen-Audit über `iter_route_contexts`); es gibt keinen wiederverwendbaren Unit-Code; Redeem ohne Secret im Body (leer oder nur als Query `?i=`) erzeugt keine Session (4xx, kein `Set-Cookie`); eingelöste und offene Einladung nach dem Checkout → 401.
  - `test_a27_next_stay_sees_nothing_of_previous_stay`: Gast von Aufenthalt A erzeugt Befehle; Checkout; Gast von Aufenthalt B in derselben Unit: `GET /api/v1/commands/{id aus A}` → 404, Dashboard ohne Historienfelder, keine öffentliche Route liefert Audit oder Befehle von A.
  Lauf: `uv run pytest tests/acceptance/test_matrix_stays.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(stays): checkout ends every access of a stay (A25-A27)`.

### Task 10.3: Verlängerung (A51)

**Files:**
- Modify: `myha_gateway/services/stays.py`, `myha_gateway/api/admin/stays.py`
- Test: `tests/acceptance/test_matrix_stays.py`

Verhalten: `POST /api/v1/admin/stays/{id}/extend {ends_at, extend_sessions, expected_version}` für bestätigte, nicht ausgecheckte Aufenthalte mit `ends_at` nach dem bisherigen Ende und nach `now` (Verkürzen → 400, dafür gibt es Checkout). `stay.ends_at` wird gesetzt. Mit `extend_sessions = true` erhält jede aktive Session des Aufenthalts `not_after = min(neues Ende, created_at + guest_browser_days)` (Browser) bzw. das neue Ende (native Familie); abgelaufene und widerrufene Sessions bleiben unberührt. Eingelöste Einladungen bleiben eingelöst, offene behalten ihr `redeem_until`. Mit `false` enden die Sessions zum alten Zeitpunkt. Policy-Bump; Antwort `{updated_sessions, needs_new_invite}` (abgelaufene oder widerrufene Geräte, ersetzbar über Task 10.5); Audit `stay.extend`. Clients erfahren das neue Ende über `policy.changed` → `/me` → Renew (Task 5b.8).

- [ ] **Step 1 – Tests (rot):** `test_a51_extend_with_sessions` (aktive Session erhält das neue `not_after`; Renew setzt ein Cookie mit größerem `Max-Age`; eingelöste Einladung bleibt verbraucht; abgelaufenes Gerät steht in `needs_new_invite`); `test_a51_extend_without_sessions`; Verkürzen → 400; Browser-Obergrenze `guest_browser_days` greift. Lauf: `uv run pytest tests/acceptance/test_matrix_stays.py -q -k extend` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(stays): explicit stay extension with or without active devices (A51)`.

### Task 10.4: Zimmerwechsel (A51)

**Files:**
- Create: `myha_gateway/db/migrations/versions/NNNN_stay_move.py`
- Modify: `myha_gateway/db/models.py` (`Stay.moved_from_id: str | None`, FK `stay.id`), `myha_gateway/services/stays.py`, `myha_gateway/api/admin/stays.py`, `myha_gateway/api/admin/invites.py` (409 für verschobene Entwürfe)
- Test: `tests/acceptance/test_matrix_stays.py`, `tests/unit/test_db.py`

Verhalten (Spez §8.6 „neu zu prüfender Zielaufenthalt samt Einladung“, Design §6 „Zimmerwechsel erzeugt neuen `draft`“; entschieden, siehe Entscheidung 2):
- `POST /api/v1/admin/stays/{id}/move {unit_id, redeem_minutes, expected_version}` (`require_csrf`) für bestätigte, nicht beendete Aufenthalte mit anderer Ziel-Unit → in einer Transaktion ein neuer **Entwurf** in der Ziel-Unit (`starts_at = max(now, alt.starts_at)`, `ends_at = alt.ends_at`, gleiche Rollenvorlage und -version, gleiche Geräteplätze und externe Referenz, `moved_from_id = alt.id`) **und** je Geräteplatz eine Einladung über die Einladungslogik aus Teilplan 2 (`redeem_until = min(now + redeem_minutes, ends_at)`, Default 15, höchstens 1440). Antwort genau einmal `{stay, invites: [{invite_id, device_slot, join_url, qr_svg, redeem_until, usable_until}]}`. Einlösen gelingt erst nach der Bestätigung, weil Redeem (Teilplan 2) einen bestätigten Aufenthalt verlangt. Der alte Aufenthalt bleibt bis dahin unverändert; der Entwurf ist in der Vorschau prüfbar (A50-Pfad). Audit `stay.move` ohne Secrets.
- `POST /api/v1/admin/stays/{id}/invites` (Teilplan 2) auf einen Entwurf mit `moved_from_id` → 409: Diese Route bestätigt Entwürfe und würde die Bestätigung des Zimmerwechsels umgehen. Nach der Bestätigung gilt sie wieder normal (abgelaufene Move-Einladungen ersetzt der Admin dann darüber).
- `POST /api/v1/admin/stays/{neu}/confirm {expected_version, carry_over_sessions}` für einen verschobenen Entwurf: 409, wenn der alte Aufenthalt nicht mehr `confirmed` oder schon beendet ist (ausgecheckt, storniert, abgelaufen). Sonst in einer Transaktion: neuer Aufenthalt `confirmed`; alter `checked_out` (`checked_out_at = now`), seine offenen Einladungen widerrufen. `carry_over_sessions = true` → alle noch nicht eingelösten Move-Einladungen des Zielentwurfs werden widerrufen (weitere Geräte bekommen danach Einladungen über die normale Route), und jede aktive Session des alten Aufenthalts erhält `stay_id = neu.id`, **`unit_ids = [neu.unit_id]`**, `not_after = min(not_after, neu.ends_at)`, ihr Gast-Principal `stay_id = neu.id`. `false` (Default) → diese Sessions samt Familien widerrufen (Grund `room_change`); die Gäste lösen die Move-Einladungen ein. Policy-Bump; nach dem Commit `PolicyChanged` (übernommene Verbindungen erhalten einen Snapshot nur mit dem neuen Raum) und `SessionRevoked` für die widerrufenen. Antwort mit Zählern; Audit `stay.move_confirm`.
- Die Rechte am alten Raum enden in beiden Fällen mit der Bestätigung. Die Übernahme ist eine ausdrückliche, protokollierte Entscheidung des Betreibers und geschieht nie automatisch.

- [ ] **Step 1 – Tests (rot):** Migration legt `moved_from_id` an; die Move-Antwort enthält je Geräteplatz genau eine Einladung, ein zweiter Abruf liefert keine Secrets; `test_a51_moved_draft_cannot_bypass_confirm` (Invites-Route auf den Entwurf → 409; Redeem einer Move-Einladung vor der Bestätigung → 401 ohne Session; danach im selben Test bestätigen (ohne Übernahme) und dieselbe Einladung erfolgreich einlösen; in einem zweiten Fall: Bestätigen nach dem Checkout des alten Aufenthalts → 409); `test_a51_carry_over_never_exceeds_device_slots` (Bestätigen mit Übernahme widerruft die offenen Move-Einladungen, übernommene Sessions plus einlösbare Einladungen überschreiten die Geräteplätze nie); `test_a51_room_change_with_carry_over` (offene WS-Verbindung erhält einen Snapshot nur mit dem neuen Raum; Aktion auf eine Ressource des alten Raums → `NOT_FOUND`; `session.unit_ids == [neu.unit_id]`); `test_a51_room_change_without_carry_over` (Sessions widerrufen; alte eingelöste Einladungen bleiben verbraucht; Move-Einladungen funktionieren nach der Bestätigung); `test_move_draft_is_previewable_and_changes_nothing_before_confirm`. Lauf: `uv run pytest tests/acceptance/test_matrix_stays.py tests/unit/test_db.py -q -k "move or room_change or migrat"` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(stays): room change into a reviewed draft with invites and optional carry-over (A51)`.

### Task 10.5: Gerätezugang ersetzen (A51)

**Files:**
- Modify: `myha_gateway/services/stays.py`, `myha_gateway/api/admin/sessions.py`
- Test: `tests/acceptance/test_matrix_stays.py`

Verhalten: `POST /api/v1/admin/sessions/{id}/replace` (`require_csrf`) für Gast-Sessions eines bestätigten, nicht beendeten Aufenthalts: diese Session samt Familie widerrufen (Grund `replaced`), andere Geräte des Aufenthalts bleiben; eine neue Einladung für denselben Aufenthalt und Geräteplatz über die Einladungslogik aus Teilplan 2 (`redeem_until = min(now + 15 min, ends_at)`); Antwort genau einmal `{invite_id, join_url, qr_svg, redeem_until, usable_until}`; Audit `session.replace` ohne Secret. Bewohner- oder Display-Sessions → 400 (Bewohner: Passwort zurücksetzen, Displays: neu pairen).

- [ ] **Step 1 – Tests (rot):** `test_a51_replace_device_keeps_other_devices` (altes Cookie 401, zweites Gerät weiter gültig, neue Einladung lösbar); Secret nur in der Antwort, nicht im Audit; Bewohner-Session → 400. Lauf: `uv run pytest tests/acceptance/test_matrix_stays.py -q -k replace` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(stays): replace one guest device access (A51)`.

### Task 10.6: Rollout beim Veröffentlichen

**Files:**
- Modify: `myha_gateway/services/roles.py`, `myha_gateway/api/admin/roles.py`
- Test: `tests/integration/test_admin_roles.py`

Verhalten (Design §4.1): `POST /api/v1/admin/roles/{id}/publish {expected_version, rollout: true}` setzt in derselben Transaktion wie die Veröffentlichung `role_version = neu` für alle Zuweisungen der Vorlage (`active` und `disabled_pending_review`) und für alle Aufenthalte der Vorlage im Status `draft` oder `confirmed`; ein Policy-Bump; Audit `role.publish` mit `rolled_out: {assignments, stays}`; offene Clients erhalten über `policy.changed` einen neuen Snapshot. Ohne Rollout bleibt alles wie in Task 9a.4.

- [ ] **Step 1 – Tests (rot):** Rollout aktualisiert Zuweisungen und Aufenthalte; ausgecheckte und stornierte Aufenthalte bleiben; offene Gast-Verbindung erhält einen Snapshot mit den neuen Rechten; ohne Rollout gilt die alte Version weiter. Lauf: `uv run pytest tests/integration/test_admin_roles.py -q -k rollout` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(admin): roll out a published role version to assignments and stays`.

### Task 10.7: Aufräumjobs und Audit-Aufbewahrung

**Files:**
- Create: `myha_gateway/services/housekeeping.py`
- Modify: `myha_gateway/app.py` (Hintergrundtask im Admin-Lifespan)
- Test: `tests/integration/test_housekeeping.py`

Verhalten: `run_housekeeping(engine, now, retention_days) -> dict[str, int]` in einer Schreibtransaktion löscht `audit_event` mit `ts < now − audit_retention_days`; `ws_ticket` mit `expires_at < now − 1 h`; `pairing_request` mit `expires_at < now − 24 h`; `invite` mit `redeem_until < now − Aufbewahrung`, aber nur von Aufenthalten, die beendet, ausgecheckt oder storniert sind (Testeinladungen ohne Aufenthalt nach Ablauf), damit die Übersicht eines laufenden Aufenthalts vollständig bleibt; Sessions mit `not_after < now − Aufbewahrung` oder `revoked_at < now − Aufbewahrung` (widerrufene Displays laufen sonst ein Jahr weiter) samt ihren `refresh_token`-, `ws_ticket`- und `command`-Zeilen (in Fremdschlüssel-Reihenfolge). Gast-Principals und Aufenthalte bleiben (klein). Erster Lauf 60 s nach dem Start, dann stündlich; Ergebnis als Zählerzeile im Log; Fehler werden geloggt und beenden nichts; im Restore-Modus übersprungen.

- [ ] **Step 1 – Tests (rot):** jede Kategorie wird an der Grenze gelöscht und knapp davor behalten; abgelaufene Einladungen eines laufenden Aufenthalts bleiben; Löschreihenfolge funktioniert mit `foreign_keys=ON`; Restore-Modus überspringt; nach dem Löschen aller Audit-Zeilen wiederholt sich keine ID (AUTOINCREMENT). Lauf: `uv run pytest tests/integration/test_housekeeping.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(gateway): hourly housekeeping with audit retention`.

---

## WP11 – Setup-Assistent, TLS-Modi, Trustprofil

Abnahme: A44 (Serverteil, real in 13.6); Serverteil von A37 (Pin-Wechsel im Trustprofil).

### Task 11.1: Public-Listener zur Laufzeit starten, stoppen, neu starten

**Files:**
- Create: `myha_gateway/public_listener.py`
- Modify: `myha_gateway/__main__.py` (`_serve` nutzt `PublicListener`, Signatur `_serve(settings, public_server=...)` bleibt), `myha_gateway/deps.py`, `myha_gateway/api/admin/session.py`
- Test: `tests/integration/test_public_listener.py`, `tests/integration/test_startup.py` (bestehende Public-Tests bleiben grün)

`deps.py`: `refresh_public()` wird geteilt in die reine Prüfung `decide_public() -> PublicDecision(ready, origin, tls, reason)` (dieselben Prüfungen wie heute) und `refresh_public()`, das das Ergebnis zuweist und den Grund zurückgibt. Neu: `AppContext.public_listener: PublicListener | None` (setzt `_serve`; in Unit-Tests `None`) und `apply_public(ctx) -> str | None` = `refresh_public()` plus `public_listener.reload()`.

```python
class PublicListener:
    """Runs the public uvicorn server while ctx.public_ready; reload() applies a changed setup."""

    def __init__(self, ctx: AppContext, factory: Callable[[AppContext], uvicorn.Server]) -> None: ...

    async def run(self, admin: uvicorn.Server, stop: asyncio.Event) -> None:
        """Wait for the admin server, then loop: serve (origin, tls) as ctx decided; wait for the
        server to exit, reload() or stop; a failing server restarts with backoff 5 s → 60 s."""

    def reload(self) -> None:
        """Wake the loop: stop the running server when public_ready, public_origin or public_tls
        differ from what it serves, then start a new one if public_ready."""

    def reload_certificate(self) -> bool:
        """E28 (Task 11.3): load_cert_chain on the running server's SSL context; open HTTPS and
        WSS connections stay up. False when no server runs in tls mode."""

    @property
    def running(self) -> bool: ...
```

`create_public_app(ctx)` entsteht je Start neu, damit die origin-gebundene CSP (`connect-src 'self' wss://<host>`, wie heute in `app.py`) und die Proxy-Middleware (Task 11.2) den aktuellen Stand sehen. `GET /api/v1/admin/session` liefert zusätzlich `public_ready` und `public_reason`.

Kein HSTS (Entscheidung): HSTS gilt für den ganzen Host und unabhängig vom Port. Auf HAOS liefert Home Assistant Core meist Klartext-HTTP auf Port 8123 desselben Hosts; ein HSTS-Header von Port 8443 würde dem Betreiber den Zugang zu HA brechen. Das Gateway hat keinen Klartext-Listener, und die Clients nutzen nur `https`-URLs, also bringt HSTS nichts. Im Modus `proxy` gehört der Header dem Proxy des Betreibers.

Hinweis zur Verbindungsebene: Der Admin-Listener läuft bewusst ohne `limit_concurrency`, und die Peer-Prüfung greift heute erst in der ASGI-Schicht (`PeerGuardMiddleware`), nachdem h11 die Anfrage geparst hat. Sobald diese Listener-Steuerung existiert, schließt Task 11.2 fremde Peers schon in `connection_made`.

- [ ] **Step 1 – Tests (rot)** mit injizierter Factory und Fake-Servern wie in Task 4.2: aus beim Start → Origin und Proxy setzen, `apply_public` → Server startet; Origin-Wechsel → alter Server `should_exit`, neuer mit neuer CSP; Listener aus (Restore) → Server stoppt ohne Neustart; `reload()` bei unverändertem Setup startet nichts neu; `test_no_hsts_header_in_tls_mode` (echter Server im Modus `tls`: Antworten von `/healthz` und `/` tragen kein `Strict-Transport-Security`); Fehler → Backoff wie bisher; `stop` → kein Neustart; SIGTERM-Reihenfolge (bestehender Test) bleibt grün. Lauf: `uv run pytest tests/integration/test_public_listener.py tests/integration/test_startup.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(gateway): start, stop and restart the public listener at runtime`.

### Task 11.2: Proxy-Modus durchsetzen

**Files:**
- Create: `myha_gateway/proxy.py` (`ProxyPeerFilter`)
- Modify: `myha_gateway/net.py` (aus Task 5b.7; `parse_proxy_cidrs`), `myha_gateway/app.py` (`create_public_app`), `myha_gateway/deps.py`, `myha_gateway/__main__.py` (`_server()` mit Peer-Protokollklasse)
- Test: `tests/unit/test_net.py`, `tests/integration/test_proxy_mode.py`, `tests/integration/test_proxy_wire.py` (echter uvicorn-Server auf Port 0, rohe Sockets), `tests/integration/test_startup.py`

Verhalten:
- Heute hält `decide_public()` den Modus `proxy` mit dem Grund „proxy mode is not available yet“ zu; ein `shortcut:`-Kommentar verweist auf diesen Task. Der Task öffnet den Modus; Kommentar und Grund entfallen.
- `parse_proxy_cidrs(raw: list[str]) -> tuple[IPv4Network | IPv6Network, ...]` in `net.py`: je Eintrag `ipaddress.ip_network(entry, strict=False)` (Host-Bits werden genullt, `10.0.0.5/24` → `10.0.0.0/24`), 1–16 Einträge, nie `0.0.0.0/0` oder `::/0`, kein Netz, das `172.30.32.0/23` (HAOS-hassio-Netz) oder `172.17.0.0/16` (Docker-Bridge) schneidet: Sonst würden der Supervisor oder der Docker-Gateway als Proxy vertraut. Fehler → `ValueError` mit Grund. `decide_public()`, `ProxyPeerFilter` und `ProxyHeadersMiddleware` nutzen dieselbe normalisierte Liste; ein Fehler hält den Listener mit Grund aus.
- Bei `instance.tls_mode == "proxy"` (nicht beim Dev-Override) ist `ProxyPeerFilter(networks)` die äußerste Schicht: HTTP von einem Peer außerhalb → 403 JSON `UNAUTHENTICATED` mit Safety-Headern, WebSocket → Close 1008 vor dem Handshake. Darin setzt uvicorns `ProxyHeadersMiddleware(trusted_hosts=[str(n) for n in networks])` `scope["client"]` aus `X-Forwarded-For` und das Schema aus `X-Forwarded-Proto`, nur für vertrauenswürdige Peers. So sehen Rate-Limits, Audit-IP-Klassen und der Gastgerätetest den echten Client, ohne dass ein Dienst sich darum kümmern muss.
- Request-Smuggling: Im Modus `proxy` lehnt `ProxyPeerFilter` jede HTTP-Anfrage mit **beiden** Headern `Content-Length` und `Transfer-Encoding` mit 400 `INVALID_PARAMETERS` und `Connection: close` ab; uvicorn schließt danach die Verbindung, eine nachgeschobene Anfrage auf derselben Verbindung wird nie verarbeitet. h11 ist hier nachsichtig; ein Proxy, der beide Header weiterreicht und Upstream-Verbindungen wiederverwendet, wäre sonst ein Smuggling-Weg.
- Peer-Prüfung auf Verbindungsebene: Beide Listener bekommen über `uvicorn.Config(http=...)` eine Unterklasse von uvicorns `H11Protocol`, die in `connection_made` Verbindungen fremder Peers sofort schließt, bevor h11 ein Byte parst (Admin: `trusted_ingress_peer`; Public im Modus `proxy`: die normalisierten CIDRs; sonst kein Filter). Die ASGI-Filter (`PeerGuardMiddleware`, `ProxyPeerFilter`) bleiben als zweite Schicht. Der Admin-Listener bleibt ohne `limit_concurrency`, weil der Ingress-Peer der einzige zugelassene Peer ist; der Docstring von `_server()` sagt das.
- `X-Forwarded-Host` wird nie verwendet; Origin-Prüfungen und die origin-gebundene CSP (`connect-src 'self' wss://<host>`) bleiben an `ctx.public_origin`. Im Modus `tls` und im Dev-Override gilt kein Weiterleitungsheader (wie heute).

- [ ] **Step 1 – Unit-Tests (rot):** `tests/unit/test_net.py`: `10.0.0.5/24` → `10.0.0.0/24`; `0.0.0.0/0`, `::/0`, `172.30.0.0/16`, `172.17.5.0/24`, `10.0.0.0/33`, leere Liste und 17 Einträge → `ValueError`. Lauf: `uv run pytest tests/unit/test_net.py -q` → FAIL.
- [ ] **Step 2 – `parse_proxy_cidrs` implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Integrationstests (rot):** Peer außerhalb → 403 bzw. 1008; Peer innerhalb mit `X-Forwarded-For: 198.51.100.7` → `GET /healthz` meldet `client_ip == "198.51.100.7"`; derselbe Header von außerhalb erreicht nie die App; Instanz mit `10.0.0.5/24` lässt Peer `10.0.0.9` durch (Filter und Middleware nutzen dieselbe normalisierte Liste); Modus `tls` ignoriert den Header; ungültige CIDR-Liste → `decide_public()` liefert einen Grund, der Listener bleibt aus; gültige Proxy-Instanz → `public_ready`. Lauf: `uv run pytest tests/integration/test_proxy_mode.py tests/integration/test_startup.py -q` → FAIL.
- [ ] **Step 4 – Filter, Middleware-Kette und `decide_public()` umsetzen** (zuletzt hinzugefügte Middleware ist außen, wie beim Admin-Listener); Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Wire-Tests (rot):** `tests/integration/test_proxy_wire.py` mit rohem Socket gegen einen echten Server im Modus `proxy`, Peer `127.0.0.1` in den CIDRs: genau das Muster `POST /api/v1/auth/login HTTP/1.1` mit `Content-Length: 4` **und** `Transfer-Encoding: chunked`, dahinter eine zweite, gepipelinte Anfrage `GET /healthz` auf derselben Verbindung → Antwort 400 im Fehlerformat, danach liefert `recv()` `b""` (Verbindung zu), die zweite Anfrage bekommt keine Antwort; ohne den Doppel-Header → 200. Verbindungsebene: Admin-Server mit vertrauenswürdigem Peer `10.255.0.1` (also nicht der Test-Peer) → die TCP-Verbindung wird ohne HTTP-Antwort geschlossen; Public im Modus `proxy` mit CIDR `10.0.0.0/8` → ebenso. Lauf: `uv run pytest tests/integration/test_proxy_wire.py -q` → FAIL.
- [ ] **Step 6 – Doppel-Header-Abwehr und Peer-Protokollklasse umsetzen;** Lauf wie Step 5 → PASS.
- [ ] **Step 7 – Grün:** Gateway-Prüfung.
- [ ] **Step 8 – Commit:** `feat(gateway): proxy mode with trusted peers only, connection-level peer filter and no CL+TE requests`.

### Task 11.3: Zertifikate und Leaf-Pins; Zertifikatswechsel (E28)

**Files:**
- Modify: `myha_gateway/auth/trust.py` (DER-Leser, Kette, ausgelieferte Kette), `myha_gateway/public_listener.py` (Zertifikatswächter, `reload_certificate()`), `myha_gateway/settings.py` (`ssl_dir: Path = Path("/ssl")`, nur über `MYHA_SSL_DIR`, nie über App-Optionen)
- Test: `tests/unit/test_trust_der.py`, `tests/integration/test_cert_reload.py`

Der DER-Leser ist klein, aber sicherheitsrelevant; er ersetzt eine neue Abhängigkeit (`cryptography`):

```python
_SEQUENCE, _EXPLICIT_VERSION, _UTC_TIME, _GENERALIZED_TIME = 0x30, 0xA0, 0x17, 0x18


@dataclass(frozen=True)
class CertFacts:
    pin: str  # base64 SHA-256 of the DER SubjectPublicKeyInfo, as in the trust profile
    not_after: datetime


def _element(der: bytes, pos: int) -> tuple[int, int, int]:
    """Tag, value start and end of the DER element at ``pos``; definite lengths only."""
    if pos + 2 > len(der):
        raise ValueError("truncated DER")
    tag, length = der[pos], der[pos + 1]
    pos += 2
    if length & 0x80:
        count = length & 0x7F
        if not 1 <= count <= 4 or pos + count > len(der):
            raise ValueError("unsupported DER length")
        length = int.from_bytes(der[pos : pos + count], "big")
        pos += count
    if pos + length > len(der):
        raise ValueError("truncated DER")
    return tag, pos, pos + length


def _children(der: bytes, start: int, end: int) -> list[tuple[int, int, int, int]]:
    """Tag, element start, value start and end of each element in ``der[start:end]``."""
    found = []
    while start < end:
        tag, value, stop = _element(der, start)
        found.append((tag, start, value, stop))
        start = stop
    if start != end:
        raise ValueError("DER element overruns its parent")
    return found


def _time(der: bytes, tag: int, start: int, end: int) -> datetime:
    text = der[start:end].decode("ascii")
    if tag == _UTC_TIME:  # YYMMDDHHMMSSZ; 50-99 → 19xx, 00-49 → 20xx (RFC 5280)
        year = int(text[:2])
        text = f"{1900 + year if year >= 50 else 2000 + year}{text[2:]}"
    elif tag != _GENERALIZED_TIME:
        raise ValueError("unexpected time type")
    return datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=UTC)


def cert_facts(der: bytes) -> CertFacts:
    """Pin and expiry of one certificate: Certificate → TBSCertificate → its fields."""
    tag, value, end = _element(der, 0)
    if tag != _SEQUENCE or end != len(der):
        raise ValueError("not a DER certificate")
    tag, tbs_value, tbs_end = _element(der, value)
    if tag != _SEQUENCE:
        raise ValueError("not a DER certificate")
    fields = _children(der, tbs_value, tbs_end)
    if fields and fields[0][0] == _EXPLICIT_VERSION:
        fields = fields[1:]
    if len(fields) < 6:  # serialNumber, signature, issuer, validity, subject, subjectPublicKeyInfo
        raise ValueError("not an X.509 certificate")
    validity = _children(der, fields[3][2], fields[3][3])
    spki_tag, spki_start, _, spki_end = fields[5]
    if len(validity) != 2 or spki_tag != _SEQUENCE:
        raise ValueError("not an X.509 certificate")
    tag, _, start, stop = validity[1]
    digest = hashlib.sha256(der[spki_start:spki_end]).digest()
    return CertFacts(base64.b64encode(digest).decode("ascii"), _time(der, tag, start, stop))
```

Dazu `pem_chain(text) -> list[bytes]` (alle `BEGIN/END CERTIFICATE`-Blöcke über `ssl.PEM_cert_to_DER_cert`, Leaf zuerst wie in `fullchain.pem`) und `served_chain(origin, timeout=5) -> tuple[list[bytes], bool]` (blockierend, über `asyncio.to_thread`): zuerst Handshake mit `ssl.create_default_context()` (Ergebnis „vom System vertraut“), bei `SSLCertVerificationError` erneut mit `CERT_NONE` nur zum Auslesen; Kette über `SSLSocket.get_unverified_chain()` (Python 3.13). Die Kette ohne Prüfung dient nur der Pin-Berechnung, nie einer Vertrauensentscheidung.

Zertifikatswechsel (E28) wörtlich nach Design, ohne Neustart: Der Wächter in `PublicListener` prüft alle 60 s (injizierbar) im Modus `tls` `os.stat` von Zertifikat und Schlüssel (`st_mtime_ns`, `st_size`, `st_ino`). Bei einer Änderung lädt er beide zuerst in einen frischen `SSLContext` (`load_cert_chain` mit der Passwort-Abwehr aus `deps.py`). Fehler → Warnung, der laufende Server behält sein Zertifikat, neuer Versuch im nächsten Takt. Sonst ruft `reload_certificate()` `load_cert_chain` auf dem `SSLContext` des laufenden Servers (`server.config.ssl`): Neue Handshakes sehen das neue Zertifikat, offene HTTPS- und WSS-Verbindungen bleiben bestehen (`shortcut:`-Kommentar: ändert sich eine Datei zwischen Prüfung und Laden, prüft der nächste Takt erneut). Danach wird der primäre Pin aus dem Leaf neu berechnet und in einer Schreibsession in `instance.trust_pins` geschrieben (alter `primary` ersetzt mit `valid_from = now`, ein Ersatzpin gleich dem neuen Primärpin entfällt); `ctx.instance` wird ersetzt, Audit `tls.reloaded` mit dem Pin. Das gilt unter beiden Policies: Auch `pin_optional` veröffentlicht den berechneten Primärpin. Auch nach einem Zertifikatswechsel setzt das Gateway kein HSTS (Begründung in Task 11.1).

- [ ] **Step 1 – DER-Tests (rot):** `tests/unit/test_trust_der.py`: Pins gleich `openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64` für EC-P-256- und RSA-2048-Zertifikate sowie für eine Kette aus Leaf und Zwischen-CA (erzeugt mit der `openssl`-CLI wie `self_signed` in `test_startup.py`, Skip ohne `openssl`); `not_after` gleich `openssl x509 -enddate`; abgeschnittenes oder zufälliges DER → `ValueError`; GeneralizedTime (Jahr ≥ 2050) wird gelesen. Lauf: `uv run pytest tests/unit/test_trust_der.py -q` → FAIL.
- [ ] **Step 2 – `cert_facts`, `pem_chain` und `served_chain` implementieren** (Code oben); Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Wächter-Tests (rot):** `tests/integration/test_cert_reload.py` mit einem echten uvicorn-Server im Modus `tls` auf Port 0 (Zertifikate per `openssl` wie in `test_startup.py`): Zertifikat und Schlüssel durch ein neues Paar ersetzen → innerhalb eines Takts zeigt ein neuer TLS-Handshake das neue Leaf, eine vorher geöffnete WSS-Verbindung bleibt offen, kein Serverneustart, `/me/trust` zeigt den neuen Primärpin und der alte fehlt; kaputter Schlüssel → Handshake zeigt weiter das alte Leaf, Warnung, kein Pin-Wechsel; gleicher Schlüssel mit neuem Zertifikat → Pin unverändert, neues Leaf im Handshake; unter `pin_optional` steht der Primärpin ebenfalls im Profil. Lauf: `uv run pytest tests/integration/test_cert_reload.py -q` → FAIL.
- [ ] **Step 4 – Zertifikatswächter in `PublicListener` implementieren;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Grün:** Gateway-Prüfung; Coverage `myha_gateway/auth/*` ≥ 85 %.
- [ ] **Step 6 – Commit:** `feat(gateway): leaf pins from certificates and reload on certificate change (E28)`.

### Task 11.4: Setup-API – Origin, TLS/Proxy, Instanz, Trustprofil

**Files:**
- Create: `myha_gateway/services/setup.py`, `myha_gateway/api/admin/setup.py`
- Modify: `myha_gateway/app.py`
- Test: `tests/integration/test_admin_setup.py`

Verhalten (alle `require_csrf`; Änderungen an der Instanz schreiben in die DB, ersetzen danach `ctx.instance` und rufen `apply_public(ctx)`; liegen neue Dateien unter den alten Pfaden, lädt `setup/tls` sie über `reload_certificate()` aus Task 11.3):
- `PUT /api/v1/admin/setup/origin {public_origin, confirm_revoke_all, expected_version}`: kanonisch, wie Browser ihn senden, geprüft mit `_origin_problem(origin, allow_http=False)` aus `deps.py` (Kleinbuchstaben, kein Standardport, nichts nach dem Host, kein `@`, kein IPv4-mapped IPv6); die Antwort nennt den Grund, nie einen Origin mit `@`. Der Wechsel eines bestehenden Origins verlangt `confirm_revoke_all = true` und widerruft alle Zugangs-Sessions, Refresh-Familien, offenen Einladungen und offenen Pairing-Anfragen (`status = rejected`), weil Zugangsdaten an den Origin gebunden sind und ein Host- oder Portwechsel neues Enrollment verlangt (Spez §8.4). Ohne Bestätigung → 409 mit den Zählern. Antwort `{public_ready, reason}`.
- `PUT /api/v1/admin/setup/tls {tls_mode, cert_path?, key_path?, proxy_cidrs?, expected_version}`: `tls` → beide Pfade lösen (Symlinks aufgelöst) innerhalb von `settings.ssl_dir` auf, sind reguläre lesbare Dateien, `load_cert_chain` gelingt mit derselben Passwort-Abwehr wie `decide_public()` (ein verschlüsselter Schlüssel wird mit Grund abgelehnt), das Leaf ist nicht abgelaufen; der Primärpin kommt aus dem Leaf der Datei. `proxy` → `parse_proxy_cidrs` aus Task 11.2 (gespeichert wird die normalisierte Liste); der Primärpin kommt nur aus einem Handshake mit dem Origin, dem das System vertraut (`served_chain(...)[1]`); ist die Kette nicht vertrauenswürdig oder der Origin vom Gateway aus nicht erreichbar, bleibt der Primärpin leer und die Kette wird nur angezeigt. `setup_state` wechselt per DB-Update von `fresh` auf `configured`, sobald `public_ready`. Audit.
- `PUT /api/v1/admin/setup/instance {name, operator_contact, timezone, session_limits, audit_retention_days, expected_version}`: Zeitzone über `zoneinfo.ZoneInfo`; Grenzen `guest_browser_days`, `resident_browser_days`, `resident_refresh_days` je 1–365, `display_days` 1–3650, `native_access_minutes` 5–60, Aufbewahrung 1–365. Gilt für neue Sessions; bestehende werden nie verlängert.
- `GET /api/v1/admin/setup/trust` → `{policy, pins, computed_primary, chain: [{pin, not_after, role: "leaf"|"ca"}], system_trusted, warnings}`.
- `PUT /api/v1/admin/setup/trust {policy, backup_pins, expected_version}`: je Ersatzpin Format (Base64 von 32 Byte), ungleich dem Primärpin, nicht der Pin eines CA-Zertifikats der aktuellen Kette (Datei im Modus `tls`, ausgelieferte Kette im Modus `proxy`) → sonst 400 „Pins müssen zu einem Leaf-Schlüssel gehören“; höchstens 2. Warnung (kein Fehler) unter **beiden** Policies, sobald die Pinliste nicht leer ist und keinen Ersatzpin hat (E28: eine Let's-Encrypt-Erneuerung mit neuem Schlüssel sperrt native Clients mit Pins aus; Empfehlung: vorab erzeugter Ersatzschlüssel). `pin_optional` veröffentlicht den berechneten Primärpin trotzdem. Audit `trust.update`.
- `POST /api/v1/admin/setup/trust/recompute` → Primärpin aus der aktuellen Kette nach denselben Regeln (Modus `proxy`: nur aus einem systemvertrauten Handshake).
- `setup_state`-Werte: `fresh`, `configured`, `verified` (Task 5b.7), `restore_pending` (abgeleitet im Restore-Modus).

- [ ] **Step 1 – Tests für Origin und TLS (rot):** Origin mit Pfad, Slash am Ende, Großbuchstaben, Port 443 oder `http` → 400; Origin mit `@` → 400 ohne Echo; Origin-Wechsel ohne Bestätigung → 409 mit Zählern, mit Bestätigung → Sessions widerrufen, offene Pairing-Anfrage `rejected`, Public-Listener mit neuem Origin neu gestartet (Fake); Zertifikatspfad außerhalb `ssl_dir` (`../etc/passwd`, Symlink nach außen) → 400; verschlüsselter Schlüssel → 400 mit Grund, keine Rückfrage auf dem Terminal; Proxy mit `172.30.32.0/24` oder `0.0.0.0/0` → 400; Proxy-CIDR `10.0.0.5/24` wird als `10.0.0.0/24` gespeichert; gültige Konfiguration setzt `setup_state` in der DB auf `configured`. Lauf: `uv run pytest tests/integration/test_admin_setup.py -q` → FAIL.
- [ ] **Step 2 – `setup/origin` und `setup/tls` implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Tests für Instanz und Trustprofil (rot):** ungültige Zeitzone → 400; `native_access_minutes = 90` → 400; Ersatzpin gleich dem Pin der Zwischen-CA → 400; Pinliste ohne Ersatzpin → 200 mit Warnung unter `pin_required` **und** `pin_optional`; das Profil erscheint über `/me/trust`, unter `pin_optional` mit Primärpin; `trust/recompute` im Proxy-Modus nimmt den Pin aus einer systemvertrauten Kette und lässt ihn bei nicht vertrauter Kette leer (Fake für `served_chain`). → FAIL.
- [ ] **Step 4 – `setup/instance`, `setup/trust` und `setup/trust/recompute` implementieren;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Grün:** Gateway-Prüfung.
- [ ] **Step 6 – Commit:** `feat(admin): setup api for origin, tls, proxy, instance and trust profile`.

### Task 11.5: Setup-Prüfungen und Gastgerätetest (A44, Serverteil)

**Files:**
- Modify: `myha_gateway/services/setup.py`, `myha_gateway/api/admin/setup.py`
- Test: `tests/integration/test_setup_checks.py`

`GET /api/v1/admin/setup/checks` → `[{name, status: ok|warn|fail|todo, detail, hint}]`; `POST /api/v1/admin/setup/checks/{name}/run` führt eine aktive Prüfung erneut aus. Aktive Prüfungen (`tls`, `websocket`) bauen Verbindungen auf und laufen nur über `run`; `GET` zeigt ihr letztes Ergebnis aus dem Speicher, alle anderen berechnet es bei jedem Aufruf:

| name | Prüfung |
|---|---|
| `gateway` | Migrationen auf Head, ein Worker, Datenverzeichnisse beschreibbar |
| `home_assistant` | `ha.connected`, Temperatureinheit °C, Core- und Supervisor-Version, Mindestversion erfüllt |
| `admin_identity` | aufrufender Admin hat eine frische Prüfung bestanden; zeigt den Namen |
| `public_origin` | Origin gesetzt, Modus gültig, `ctx.public_ready`, Listener läuft, sonst Grund aus `decide_public()` |
| `tls` | in beiden Modi Handshake mit dem Origin vom Gateway aus (`served_chain`): vom System vertraut ja/nein (nein → Warnung: eine private CA funktioniert nur auf Geräten, die sie kennen), Leaf nicht abgelaufen (Warnung unter 21 Tagen); `tls` zusätzlich: Schlüssel passt zur Datei; `proxy` zusätzlich: die gespeicherten CIDRs bestehen `parse_proxy_cidrs`, sonst `fail`. Hinweis: Ein Test vom Server beweist nichts für das Gast-WLAN. |
| `time` | `{trusted, host_info: ok\|not_permitted\|error, dt_synchronized}`: `trusted` aus `instance.time_trusted` (DB), `host_info` aus `supervisor.host_info()` (`None` → `not_permitted`, Ausnahme → `error`); nicht vertraut → `fail`, `not_permitted` → `ok` mit dem Hinweis, dass nur die Hochwassermarke (E26) wirkt |
| `websocket` | `websockets.connect(wss://…/api/v1/events, origin=public_origin)` wird nach dem Auth-Timeout mit 4401 geschlossen → Upgrade-Pfad funktioniert; nicht erreichbar vom Gateway selbst → Warnung, der Gastgerätetest entscheidet |
| `guest_device` | letztes Audit `setup.guest_test`: `ok` mit Zeit und IP-Klasse, sonst `todo` |
| `client_ip` | IP-Klasse des letzten Gastgerätetests: `docker` → Warnung (Rate-Limits je IP sehen nur eine Adresse; Proxy-Modus empfohlen), sonst `ok` |

`POST /api/v1/admin/setup/guest-test` → Einladung `purpose = guest_test`, ohne Aufenthalt, `device_slot = 0`, `redeem_until = now + 10 min`; Antwort genau einmal `{invite_id, join_url, qr_svg, redeem_until}`; ohne `public_ready` → 409. Den Fortschritt erfährt die UI über `audit.appended` (Admin-WS) und lädt die Prüfungen neu.

- [ ] **Step 1 – Tests der Prüfungen (rot):** jede Prüfung gegen den Simulator: frische Instanz → `todo` oder `fail` mit Gründen; mit Proxy-Konfiguration und Fake-Listener → `ok`; `websocket` ohne Erreichbarkeit → `warn`; `time` mit `_sim/host-info-mode forbidden` → `host_info == "not_permitted"`, mit `ok` → `dt_synchronized == true`; `tls` mit nicht vertrauter Kette (Fake) → `warn` in beiden Modi. Lauf: `uv run pytest tests/integration/test_setup_checks.py -q` → FAIL.
- [ ] **Step 2 – Prüfungen implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Tests des Gastgerätetests (rot):** anlegen → einlösen → WS-Snapshot → `guest_device` `ok`; `client_ip` `ok` bzw. `warn` bei Peer `172.30.32.1`; die Testeinladung ist nur einmal lösbar; ohne `public_ready` → 409. → FAIL.
- [ ] **Step 4 – `setup/guest-test` implementieren;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Grün:** Gateway-Prüfung.
- [ ] **Step 6 – Commit:** `feat(admin): setup checks and guest device test (A44 server part)`.

### Task 11.6: Routen-Audit – sensible GETs, Restore-Sperre, Admin-WS

**Files:**
- Modify: `tests/acceptance/test_matrix_admin.py`; Admin-Routen, deren Dependencies der Audit als falsch meldet; `tests/e2e/helpers/gateway.ts` (Admin-Aufrufe über den Ingress-Proxy senden bei sensiblen GETs den CSRF-Header aus `GET session`, sonst scheitert die E2E-Suite aus Teilplan 2)

Verhalten: `test_every_admin_api_route_requires_the_guard` wird erweitert:
- `SENSITIVE_GETS` (E5: Audit, Sessions, Diagnose, Restore, Vorschau-Dashboards; Einladungs-Secrets stehen nur in POST-Antworten) = `/api/v1/admin/audit`, `/api/v1/admin/sessions`, `/api/v1/admin/pairings`, `/api/v1/admin/stays/{stay_id}`, `/api/v1/admin/preview/{preview_id}/dashboard`, `/api/v1/admin/preview/{preview_id}/explain`. Task 12.3 ergänzt `/api/v1/admin/restore/review`, Task 9b.1 `/api/v1/admin/diagnostics` und `/api/v1/admin/diagnostics/export`. Jeder Eintrag muss existieren und an `require_csrf` hängen. Pfadparameter heißen so, wie Teilplan 2 sie umgesetzt hat; der Test vergleicht die Pfade wörtlich.
- Jede Admin-API-Route außer `session`, `session/check` und den Restore-Routen hängt an `not_in_restore`.
- Die Ausnahme für die einzige WebSocket-Route kommt aus Task 9a.7 und bleibt.
- Mutationen hängen wie bisher an `require_csrf`; die Prüfung läuft nie über eine leere Schleife.

- [ ] **Step 1 – Test erweitern (rot, solange eine Route abweicht):** `uv run pytest tests/acceptance/test_matrix_admin.py -q -k guard`.
- [ ] **Step 2 – Abweichende Routen korrigieren** (Dependency tauschen, nie die Liste kürzen).
- [ ] **Step 3 – Grün:** Gateway-Prüfung und E2E-Prüfung.
- [ ] **Step 4 – Commit:** `test(admin): route audit for sensitive reads, restore gate and the admin socket`.

---

## WP12 – Backup und Restore

Abnahme: A39, A40, A47, A48 (Design §4.6, E22, Spez §15.3). Die Restore-Routen tragen kein `not_in_restore`.

### Task 12.1: Bereinigter Export (A47, A48)

**Files:**
- Create: `myha_gateway/backup/__init__.py`, `myha_gateway/backup/export.py`, `myha_gateway/backup/status.py`
- Modify: `myha_gateway/__main__.py` (Subcommand `backup export|cleanup`; Logging über `setup_logging`), `myha_gateway/app.py` (Lifespan leert `state_dir/tmp/`)
- Test: `tests/unit/test_backup_allowlist.py`, `tests/acceptance/test_matrix_backup.py`

Positivliste (E22) in `backup/export.py`: `EXPORTED` (kopierte Spalten und Zeilenfilter), `OVERRIDDEN` (Spalten mit festem Platzhalter) und `NOT_EXPORTED` (alles andere, ausdrücklich):

| Tabelle | Spalten | Filter / Überschreibung |
|---|---|---|
| `instance` | kopiert: `id, short_id, name, public_origin, tls_mode, tls_cert_path, tls_key_path, proxy_cidrs, trust_policy, timezone, policy_version, security_generation, setup_state, operator_contact, session_limits, audit_retention_days, version` | überschrieben (`OVERRIDDEN`, Platzhalter für NOT-NULL-Spalten; Task 12.4 setzt echte Werte): `server_secret = ''`, `trust_pins = '[]'`, `time_trusted = 0`, `last_seen_clock = exported_at` |
| `unit`, `resource_binding`, `dashboard`, `role_template`, `role_version`, `group` | alle | — |
| `assignment` | alle | `status := 'disabled_pending_review'` |
| `principal` | `id, type, status, display_name, locale, theme, created_at, version` | `type IN ('resident', 'display')` |
| `membership` | alle | nur Mitgliedschaften exportierter Principals |
| `fence` | alle | `scope_type IN ('instance', 'unit')` oder (`principal` und Principal exportiert) |

Nicht exportiert: `credential`, `session`, `refresh_token`, `ws_ticket`, `pairing_request`, `invite`, `stay`, `command`, `audit_event`.

`export(settings) -> ExportResult`, aufgerufen von `myha-gateway backup export` (Exit 0 nur bei Erfolg):
1. `fcntl.flock(LOCK_EX | LOCK_NB)` auf `state_dir/tmp/export.lock`; belegt → Exit 1 „export already running“. Der Lock fällt mit dem Prozess. Danach alles in `state_dir/tmp/` außer dem Lock löschen (Reste eines abgebrochenen Laufs). Auch der Admin-Lifespan leert `tmp/` beim Start (außer im Restore-Modus die Arbeitskopie aus Task 12.3), aber nur, wenn er den Export-Lock nicht blockierend bekommt (`fcntl.flock(LOCK_EX | LOCK_NB)`); hält ein laufender Export den Lock, überspringt er das Aufräumen und loggt eine Zeile (`tmp cleanup skipped: export running`).
2. Liegt `backup_dir/restore.sqlite` schon da: eigener Rest (seine `export_generation` gleich `status.last_export_generation` oder `status.pending_export_generation` und gleiche Instanz-ID) → löschen; sonst wartet ein wiederhergestellter Export auf die Prüfung → Exit 1 „restore pending“, kein Export.
3. Online-Backup der Live-DB über `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True).backup(...)` nach `state_dir/tmp/snapshot.sqlite`.
4. Neue leere DB `state_dir/tmp/export.sqlite` mit der Standardbibliothek: `PRAGMA journal_mode=DELETE` (keine WAL- und SHM-Dateien), Schema über `Base.metadata.create_all` auf einer synchronen Engine ohne die Live-Pragmas, `alembic_version` = Head der Live-DB, `ATTACH` des Snapshots, je Tabelle `INSERT INTO main.t (spalten) SELECT spalten FROM snap.t WHERE filter` plus Überschreibungen, Tabelle `export_meta(key, value)` mit `export_generation = new_id("exp")`, `exported_at`, `schema_version`, `requires_admin_review = '1'`, `instance_id`.
5. `PRAGMA integrity_check` ist `ok`, `PRAGMA foreign_key_check` leer; schließen; `os.fsync` der Datei.
6. Vor dem Umbenennen schreibt `backup/status.py` `pending_export_generation` atomar in `state_dir/backup-status.json`; dann `os.replace` nach `backup_dir/restore.sqlite`, `fsync` des Verzeichnisses, Snapshot löschen; danach `last_export_generation`, `last_export_at`, `last_export_ok`, `last_error` setzen und `pending_export_generation` leeren (dazu `last_cleanup_at`, `last_cleanup_ok` aus Task 12.2). Ein Absturz zwischen Umbenennen und Statuszeile hinterlässt so einen Export, den Task 12.3 als eigenen Rest erkennt.
7. Jeder Fehler: Temporärdateien entfernt, kein `restore.sqlite`, Status `last_export_ok = false` mit Fehlertyp, Exit 1. Kein Rückfall auf irgendeine Kopie der Live-Daten.

- [ ] **Step 1 – Positivlisten-Test (rot):** `tests/unit/test_backup_allowlist.py`: Jede Spalte jeder Tabelle in `Base.metadata` steht genau einmal in `EXPORTED`, `OVERRIDDEN` oder `NOT_EXPORTED`; jede NOT-NULL-Spalte ohne Server-Default einer exportierten Tabelle ist kopiert oder überschrieben; eine neue Spalte ohne Einordnung lässt den Test scheitern. Lauf: `uv run pytest tests/unit/test_backup_allowlist.py -q` → FAIL.
- [ ] **Step 2 – `EXPORTED`, `OVERRIDDEN` und `NOT_EXPORTED` anlegen;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Exporttests (rot):** `tests/acceptance/test_matrix_backup.py`:
  - `test_a47_export_holds_no_live_state_or_secrets`: Seed mit Bewohner samt Passwort, Browser- und nativen Sessions, Refresh-Token, eingelöster und offener Einladung, Aufenthalt, Befehlen, Audit, Stay- und Session-Fence und Gast-Principal; Export; `restore.sqlite` hat `journal_mode = delete`, daneben liegen keine `-wal`/`-shm`; die nicht exportierten Tabellen sind leer; `server_secret` leer, `trust_pins` leer, keine Gäste, keine Stay- oder Session-Fences; alle Zuweisungen `disabled_pending_review`; `requires_admin_review == "1"`; ein Byte-Scan der Datei findet weder Passwort noch irgendeinen Token- oder Secret-Wert noch das `server_secret` (das deckt auch Reste in freien Seiten ab).
  - `test_a48_export_failure_leaves_no_export` (Exception nach dem Snapshot injiziert → kein `restore.sqlite`, `tmp/` leer, Status mit Fehler, Exit 1), `test_parallel_export_refused`, `test_pending_restore_blocks_export`, `test_own_leftover_export_is_replaced`, `test_crash_after_replace_counts_as_own_leftover` (`pending_export_generation` gesetzt, Statuszeile fehlt), `test_tmp_leftovers_are_removed`.
  Lauf: `uv run pytest tests/acceptance/test_matrix_backup.py -q` → FAIL.
- [ ] **Step 4 – `export()`, `backup/status.py` und das CLI-Subcommand implementieren;** Lauf wie Step 3 → PASS.
- [ ] **Step 5 – Grün:** Gateway-Prüfung.
- [ ] **Step 6 – Commit:** `feat(backup): sanitized export into a fresh sqlite file (A47, A48)`.

### Task 12.2: Cleanup-Hook, echte Hook-Skripte, Backup-Status

**Files:**
- Modify: `myha_gateway/backup/export.py` (`cleanup`), `myha/rootfs/opt/myha/backup-export`, `myha/rootfs/opt/myha/backup-cleanup`, `myha/DOCS.md` (Abschnitt „Backups“), `scripts/build-image.sh` (Hook-Smoke)
- Create: `myha_gateway/api/admin/backup.py`
- Test: `tests/acceptance/test_matrix_backup.py`, `tests/integration/test_backup_status.py`

Verhalten: `myha-gateway backup cleanup` löscht `backup_dir/restore.sqlite` nur, wenn es der eigene Export ist (Generation stimmt), und schreibt `last_cleanup_at` und `last_cleanup_ok`; ein Fehler endet mit Exit 1 und bleibt im Status sichtbar. Einen übrig gebliebenen eigenen Export entfernt der Lifespan beim nächsten Start (Task 12.3, Design §14); nur ein fremder oder unbekannter Export führt in den Restore-Modus. Die Hook-Skripte:

```bash
#!/command/with-contenv bashio
# shellcheck shell=bash
# backup_pre: sanitized export for the Supervisor's hot backup (design 4.6).
export MYHA_DATA_DIR=/data
exec /opt/myha/venv/bin/myha-gateway backup export
```

`backup-cleanup` entsprechend mit `backup cleanup`. `GET /api/v1/admin/backup/status` (`fresh=False`) → Inhalt der Statusdatei plus `export_present`; der Admin-Hub (Task 9a.7) meldet Änderungen als `backup.status`.

Hook-Smoke in `scripts/build-image.sh`, s6-Phase (die Hook-Skripte brauchen `with-contenv`): nach dem Start `docker exec "$s6" /opt/myha/backup-export` → Exit 0; `docker exec "$s6" test -f /data/backup/restore.sqlite`; `docker exec "$s6" sh -c 'test ! -e /data/backup/restore.sqlite-wal'`; dann `/opt/myha/backup-cleanup` → Datei weg. Fehler → `fail` mit Logs.

- [ ] **Step 1 – Tests (rot):** Cleanup löscht den eigenen Export und lässt einen fremden liegen; schreibgeschütztes Backup-Verzeichnis → `last_cleanup_ok = false` und Exit 1; `test_a48_cleanup_failure_is_visible` über `GET backup/status`. Lauf: `uv run pytest tests/acceptance/test_matrix_backup.py tests/integration/test_backup_status.py -q` → FAIL.
- [ ] **Step 2 – Cleanup, Status-Route und Hook-Skripte implementieren;** Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Hook-Smoke (rot, dann grün):** Smoke erweitern und zuerst gegen ein Image vom Stand vor Step 2 laufen lassen (`git stash` der Hook-Skripte, `bash scripts/build-image.sh`): Mit den S0-Dummies entsteht kein `restore.sqlite` → FAIL. Dann mit den echten Hooks `bash scripts/build-image.sh` → PASS; CI nutzt weiter `scripts/build-image.sh --smoke-only`.
- [ ] **Step 4 – Grün:** Gateway-Prüfung.
- [ ] **Step 5 – Commit:** `feat(backup): cleanup hook, real hook scripts, backup status and hook smoke test`.
### Task 12.3: Restore-Modus – Prüfen (A48)

**Files:**
- Create: `myha_gateway/backup/restore.py` (Prüfteil), `myha_gateway/api/admin/restore.py` (`GET review`), `myha_gateway/db/migrations/versions/NNNN_instance_singleton.py`
- Modify: `myha_gateway/app.py` (Lifespan: eigener Rest vor dem Restore-Check, `tmp/`-Aufräumen mit Lock-Versuch), `myha_gateway/deps.py`, `myha_gateway/services/timeguard.py` und `myha_gateway/policy/drift.py` (im Restore-Modus keine Schreibzugriffe), `myha_gateway/db/models.py` (Kommentar zum Trigger), `myha_gateway/db/migrations/env.py` (Kommentar), `tests/acceptance/test_matrix_admin.py` (`SENSITIVE_GETS` += `/api/v1/admin/restore/review`)
- Test: `tests/unit/test_db.py`, `tests/integration/test_restore.py`, `tests/acceptance/test_matrix_backup.py`

Verhalten:
- Migration: `CREATE TRIGGER instance_singleton BEFORE INSERT ON instance WHEN (SELECT COUNT(*) FROM instance) >= 1 BEGIN SELECT RAISE(ABORT, 'instance is a singleton'); END` (Downgrade löscht ihn). Ein Batch-Rebuild von `instance` verliert ihn und muss ihn neu anlegen (Kommentar an beiden Stellen).
- `GET /api/v1/admin/restore/review` (`require_csrf`; außerhalb des Restore-Modus 409) prüft `restore.sqlite`: Integrität, `export_meta` vorhanden, `requires_admin_review == "1"`, `schema_version` nicht neuer als der eigene Head (sonst ungültig: „Export einer neueren myHA-Version“). Eine Arbeitskopie `state_dir/tmp/restore-work.sqlite` wird auf Head migriert. Antwort: `{valid, problem, own_leftover, exported_at, export_generation, instance, units, bindings: [{id, label, kind, entity_id, proposed: "confirmed"|"needs_review"|"blocked"|"missing"}], roles, groups, members, fences, principals: [{id, type, display_name, note}], assignments, live_instance_present}`.
- Start mit übrig gebliebenem eigenem Export (Design §14; Task 14.6 gleicht §4.6 an): Im Admin-Lifespan **vor** dem Restore-Check liest das Gateway bei vorhandenem `backup_dir/restore.sqlite` und vorhandener Live-DB beide nur lesend (Standardbibliothek `sqlite3`, `mode=ro`, vor `create_engine`) und dazu `state_dir/backup-status.json`. Stimmen die Instanz-ID des Exports mit der Live-Instanz und seine `export_generation` mit `last_export_generation` oder `pending_export_generation` überein, löscht es den Export, loggt genau eine Zeile `own_leftover_export_removed generation=<exp_…>` (keine Secrets) und startet normal. Jeder andere Fall (andere Instanz, unbekannte Generation, keine Live-DB, unlesbare Datei) führt wie bisher in den Restore-Modus.
- `own_leftover`: Die `export_generation` des Exports ist die `last_export_generation` oder `pending_export_generation` der Statusdatei und die Instanz-ID gleich der Live-Instanz. Dann blieb nur ein eigener Export liegen, den der Start-Check nicht entfernt hat (etwa weil die Statusdatei erst danach geschrieben wurde); die UI empfiehlt „Verwerfen“ (Task 9b.8).
- `proposed` nutzt dieselbe Vergleichsfunktion wie die Bindungsprüfung aus Task 9a.2 (`policy/drift.py`); Task 13.2 macht sie versionsbewusst, beide Stellen erben das. `note` ist „braucht Benutzername und Passwort“ bzw. „muss neu gepaart werden“.
- Im Restore-Modus schreiben Zeitwächter (`services/timeguard.py`), Drift-Erkennung (`policy/drift.py`), Aufräumjob und Admin-Hub nichts in die DB, damit der Dateitausch in Task 12.4 keine offene Verbindung trifft.

- [ ] **Step 1 – Singleton-Trigger:** Test „zweite Instanzzeile → `IntegrityError`“ in `tests/unit/test_db.py` (rot); Migration anlegen; `test_migrated_schema_matches_models` bleibt grün → PASS.
- [ ] **Step 2 – Review-Tests (rot):** `tests/integration/test_restore.py`: gültiger Export → Review mit Vorschlag je Bindung; außerhalb des Restore-Modus → 409; eigener übrig gebliebener Export → `own_leftover == true`; fremder (andere Instanz-ID) → `false`; `test_restore_mode_writes_nothing` (Restore-Modus: ein `state_changed` mit Fähigkeitsänderung und ein Zeitwächter-Takt ändern keine Zeile); `test_own_leftover_export_removed_at_start` (eigene Instanz-ID und `last_export_generation` bzw. `pending_export_generation` → Export gelöscht, eine Logzeile ohne Secrets, kein Restore-Modus, Sessions gültig) und `test_foreign_export_enters_restore_mode` (andere Instanz-ID oder unbekannte Generation → Restore-Modus, Export bleibt); `test_tmp_cleanup_skipped_while_export_runs` (Lock gehalten → `tmp/` unverändert, eine Logzeile). In `tests/acceptance/test_matrix_backup.py`: `test_a48_invalid_export_keeps_restore_locked` (kaputte Datei, fehlende Metadaten, `requires_admin_review = 0`, neueres Schema → `valid = false`, Public-Listener aus). Lauf: `uv run pytest tests/integration/test_restore.py tests/acceptance/test_matrix_backup.py -q` → FAIL.
- [ ] **Step 3 – `restore/review` und die Schreibsperren implementieren;** Lauf wie Step 2 → PASS.
- [ ] **Step 4 – Grün:** Gateway-Prüfung.
- [ ] **Step 5 – Commit:** `feat(backup): restore review with leftover detection and a write freeze in restore mode (A48)`.

### Task 12.4: Restore-Modus – Anwenden und Verwerfen (A39, A40, A48)

**Files:**
- Modify: `myha_gateway/backup/restore.py`, `myha_gateway/api/admin/restore.py`, `myha_gateway/app.py`
- Test: `tests/acceptance/test_matrix_backup.py`, `tests/integration/test_restore.py`

Verhalten:
- `POST /api/v1/admin/restore/apply {bindings: {id: "confirm"|"review"}, assignments: {id: "activate"|"keep_disabled"}, export_generation}` (`require_csrf`; ungültiger Export oder eine `export_generation`, die nicht zur Datei passt → 409) baut eine frische Live-DB `state_dir/tmp/live-new.sqlite`: Migrationen auf Head, positiv gelistete Zeilen aus der Arbeitskopie. Bindungen werden nur `confirmed`, wenn der Admin bestätigt **und** HA noch übereinstimmt, sonst `needs_review`; `blocked` bleibt `blocked`; Zuweisungen nur bei `activate` aktiv. HA-Zustand und Fähigkeiten werden vor der Schreibsession gelesen (Vorgabe aus Teilplan 2).
- Instanz: exportierte Einstellungen; neues `server_secret`; `security_generation = max(Live, Export) + 1` und `policy_version = max(Live, Export) + 1` (ohne Live-Instanz zählt der Export allein), damit keine alte Clientgeneration wiederkehrt; `time_trusted = true`, `last_seen_clock = now`; `trust_pins`: im Modus `tls` der aus dem Zertifikat neu berechnete Primärpin, sonst leer bis zum Setup; `setup_state = "configured"` bei gesetztem Origin, sonst `fresh`.
- Danach Checkpoint und `dispose` der neuen Engine, dann der Tausch: `ctx.engine.dispose()`, Live-`myha.sqlite` mit `-wal` und `-shm` löschen, `os.replace(live-new, db_path)`, `fsync` des Verzeichnisses, `restore.sqlite` und Arbeitskopie löschen. `ctx.instance` neu laden, `ctx.restore_mode = False`, `ctx.policy.invalidate()`, `apply_public(ctx)`, Audit `restore.apply` in der neuen DB. Bewohner existieren danach ohne Credential (Benutzername und Passwort setzt der Admin über Task 5b.2), Displays ohne Sessions.
- `POST /api/v1/admin/restore/discard {confirm: true}` (`require_csrf`): `restore.sqlite` löschen; mit Live-Instanz geht es mit den Live-Daten weiter, ohne Live-Instanz legt `ensure_instance` eine frische Einrichtung an. Danach `restore_mode = False`, `apply_public(ctx)`, Audit. Beide Wege sind sicher: Der Export enthält nie Sicherheitsdaten, und die Live-DB ist der neueste Stand.
- Nach `apply` und `discard` gilt ein anderes CSRF-Secret; die UI lädt die Session neu (Task 9a.8).

- [ ] **Step 1 – Apply-Tests (rot):** `tests/acceptance/test_matrix_backup.py`:
  - `test_a39_restore_does_not_reactivate_codes_sessions_or_passwords`: vor dem Export Bewohner mit Benutzername und Passwort, Browser-Session und nativer Familie, Gast mit eingelöster Einladung und Session, zweite Einladung offen; Export; danach ein Session-Widerruf; App mit `restore.sqlite` neu starten (echter Lifespan) → Restore-Modus, Public-Listener aus, `GET /units` 503 `restore_pending`; `apply` → altes Cookie 401, alter Bearer 401, alter Refresh 401, beide Einladungs-Secrets 401, Login mit altem Namen und Passwort 401 (kein Credential); `security_generation` und `policy_version` größer als Live und Export, `server_secret` anders; nach `PUT principals/{id}/credential {username, password}` klappt der Login.
  - `test_a40_backup_without_restore_keeps_live_state`: Export und Cleanup bei aktiven Sessions → Sessions gültig, `policy_version` unverändert, kein Restore-Modus nach Neustart.
  - `test_a40_restore_never_widens`: Zuweisungen bleiben ohne `activate` deaktiviert; eine Bindung mit geändertem Fingerprint wird `needs_review`; `blocked` bleibt `blocked`.
  - `apply` mit ungültigem Export oder abweichender `export_generation` → 409, Public-Listener bleibt aus; im Modus `tls` steht nach `apply` der Primärpin des Zertifikats im Profil.
  Lauf: `uv run pytest tests/acceptance/test_matrix_backup.py -q` → FAIL.
- [ ] **Step 2 – `restore/apply` implementieren** (Neubau, Dateitausch, Kontext zurücksetzen); Lauf wie Step 1 → PASS.
- [ ] **Step 3 – Verwerfen und flüchtiges CSRF-Secret:** Tests (rot): `discard` ohne Live-Daten → frische Instanz ohne Rollen; `discard` eines eigenen Rests → Live-Daten unverändert, Sessions gültig; ohne Export und ohne Live-DB startet die App im Einrichtungszustand ohne Rollen; `test_restore_without_live_db_uses_ephemeral_csrf` (`GET /session` 200 im Restore-Modus ohne Instanz, `apply` gelingt mit diesem Token, danach wird es mit 403 abgelehnt). Dann `restore/discard` implementieren → PASS.
- [ ] **Step 4 – Grün:** Gateway-Prüfung.
- [ ] **Step 5 – Commit:** `feat(backup): restore apply and discard with a fresh security generation (A39, A40, A48)`.

---

## WP9b – Admin-UI Teil 2

Abnahme: manuelle Abnahme aller Screens und Tastaturpfad (Vitest hier, E2E in 14.1/14.2, real in 13.6); A50 in der UI.

### Task 9b.1: Diagnose-API

**Files:**
- Create: `myha_gateway/services/diagnostics.py`, `myha_gateway/services/metrics.py`, `myha_gateway/api/admin/diagnostics.py`
- Modify: `myha_gateway/services/commands.py` und `myha_gateway/services/preview.py` (Entscheidungsdauer erfassen), `myha_gateway/app.py` (Argon2-Messung beim Start), `tests/acceptance/test_matrix_admin.py` (`SENSITIVE_GETS` += Diagnose-Routen)
- Test: `tests/integration/test_diagnostics.py`

Verhalten:
- `services/metrics.py`: `DecisionTimer` mit einem Ringpuffer der letzten 1000 Dauern von `decide`; `p95_ms()`.
- `GET /api/v1/admin/diagnostics` (`require_csrf`) → `{gateway: {version, arch}, home_assistant: {core, supervisor, minimum_met, connected, temperature_unit, adapter_locked}, public: {ready, reason, mode, origin}, time: {trusted, host_info: ok|not_permitted|error, dt_synchronized}, policy: {version, decide_p95_ms, live_clients, displays_online}, argon2_ms, backup, database: {size_bytes, schema_version}}`. `argon2_ms` ist ein Hash mit den konfigurierten Parametern, einmal beim Start in einem Thread gemessen. Keine IP-Adressen, keine Token.
- `GET /api/v1/admin/diagnostics/export` (`require_csrf`) → dasselbe JSON mit `<host>` statt des Hostnamens im Origin und `Content-Disposition: attachment; filename="myha-diagnostics.json"`; die UI zeigt es vor dem Download (Spez §15.2).
- `POST /api/v1/admin/diagnostics/argon2-benchmark` (`require_csrf`) → `measure_argon2()` in einem Thread (etwa 10 s), ein Lauf zur Zeit (sonst 409); Ergebnis als Tabelle für `docs/security.md`. Das ersetzt das Argon2-Startlog der entfernten S0-Probe.

- [ ] **Step 1 – Tests (rot):** Felder vorhanden; weder Antwort noch Export enthalten IPv4-Adressen, `Bearer`, 43-stellige base64url-Werte oder das `server_secret`; Export mit `Content-Disposition` und `<host>`; p95 aus erfassten Werten; `time.host_info` folgt `_sim/host-info-mode` (`forbidden` → `not_permitted`, `ok` → `ok`); Benchmark läuft im Thread (`/healthz` antwortet währenddessen) und ein zweiter gleichzeitiger Aufruf → 409. Lauf: `uv run pytest tests/integration/test_diagnostics.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(admin): redacted diagnostics, decision p95 and argon2 benchmark`.

### Task 9b.2: Web – Preview as user

**Files:**
- Create: `web/src/admin/screens/Preview.tsx`
- Test: `web/src/admin/screens/Preview.test.tsx`

Verhalten (Mockup „Preview as user“, Design §4.5, A28–A30, A50): Zielauswahl (Gast eines Aufenthalts einschließlich Entwurf, nicht eingelöste Einladung, Bewohner, Display, Gruppe als ausdrücklich markierte hypothetische Person, optional eine konkrete Session); Viewport 390/768/1180 und frei veränderbare Breite; „Auswerten zum Zeitpunkt“ jetzt, vor Check-in, nach Checkout oder frei (Anzeige in der Instanzzeitzone, Versand UTC); Erklärung je Karte (Task 9a.6); Panel „Effektiver Kontext“ (Ziel, Gruppen, Scope, Gültigkeit, Policy-Version) und Hinweis auf verborgene Ressourcen; Liste simulierter Taps mit `would_allow`/`would_deny` und Code; das Banner „Vorschau als … · Simulation · Aktionen werden nicht ausgeführt.“ ist immer sichtbar; bei hypothetischer Zeit stehen Zustände als „aktuell gemessen“ (A29); Ablauf-Countdown der Vorschau, danach neu anlegen. Deep-Link `#/preview?invite=<id>` aus „Stays & invites“.

- [ ] **Step 1 – Tests (rot):** Banner sichtbar; Taps rufen nur `simulate` (Fetch-Spy trifft nie `/api/v1/actions`); Gruppenziel zeigt die Markierung; hypothetische Zeit zeigt „aktuell gemessen“; Deep-Link wählt die Einladung. Lauf: `pnpm test src/admin/screens/Preview` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): preview as user with simulation and explain`.

### Task 9b.3: Web – Stays & invites

**Files:**
- Create: `web/src/admin/screens/Stays.tsx`, `web/src/admin/screens/QrDialog.tsx`, `web/src/admin/screens/ExtendDialog.tsx`, `web/src/admin/screens/MoveDialog.tsx`
- Test: `web/src/admin/screens/Stays.test.tsx`, `web/src/admin/screens/QrDialog.test.tsx`

Verhalten (Mockup „Stays & invites“): Liste (Unit, Zeitraum, Rolle, Geräte „1 von 2 eingelöst“, abgeleiteter Status); neuer Aufenthalt (Unit, Rollenvorlage und veröffentlichte Version, Check-in und Check-out in der Instanzzeitzone, gespeichert in UTC, Geräteanzahl, Einlösefrist 15 min / 1 h / 24 h maximal); „Zuerst prüfen“ öffnet die Vorschau des Entwurfs; „QR ausgeben“ öffnet den QR-Dialog: ein QR je Gerät („Einladung 1 von 2“), genau einmal angezeigt; die Antwort lebt nur im Komponentenzustand und wird beim Schließen und bei „Nächstes Gerät“ verworfen, nie in URL, Storage oder Log; Link maskiert und ohne Kopierknopf (Mockup: „bis zur Einlösung ein Zugangsnachweis“); Einlösen bis, nutzbar bis, Rolle, „Diese Einladung prüfen“ (`#/preview?invite=`), „Fertig“. Einladung widerrufen, Entwurf bearbeiten, bestätigen, stornieren. Verlängern-Dialog: neues Ende und Kontrollkästchen „Aktive Geräte mitverlängern“ mit Erklärtext; Ergebnis listet aktualisierte Geräte und solche, die eine neue Einladung brauchen („Ersatz ausgeben“). Zimmerwechsel-Dialog: Ziel-Unit → Move; die QR-Codes der Move-Antwort zeigt der vorhandene QR-Dialog genau einmal (gleiche Regeln: nur im Komponentenzustand, beim Schließen verworfen) → Vorschau → Bestätigen mit Kontrollkästchen „N aktive Geräte übernehmen“ (Default aus, mit Erklärung, dass der alte Raum endet und die noch nicht eingelösten neuen QR-Codes dann ungültig werden).

- [ ] **Step 1 – Tests (rot):** Nach dem Schließen enthält weder Zustand noch DOM das Secret; Link maskiert, kein Kopierknopf; Verlängern-Ergebnis zeigt beide Gruppen; Zimmerwechsel sendet `carry_over_sessions: true` nur bei gesetztem Häkchen; die QR-Codes der Move-Antwort erscheinen im QR-Dialog, und nach dem Schließen steht das Secret weder im Zustand noch im DOM. Lauf: `pnpm test src/admin/screens/Stays src/admin/screens/QrDialog` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): stays and invites with one-time qr dialog, extension and room change`.

### Task 9b.4: Web – Sessions & checkout

**Files:**
- Create: `web/src/admin/screens/Sessions.tsx`, `web/src/admin/screens/CheckoutDialog.tsx`
- Test: `web/src/admin/screens/Sessions.test.tsx`

Verhalten (Mockup „Sessions & checkout“): je Aufenthalt Geräte (Sessions) und offene Einladungen mit Status; „Gerät widerrufen“, „Einladung widerrufen“, „Ersatz ausgeben“ (Task 10.5, öffnet den QR-Dialog); „Checkout …“ öffnet den Dialog mit den Zählern aus `GET stays/{id}` (aktive Sessions, Refresh-Familien, offene Einladungen) und dem Text „Der nächste Gast sieht nichts aus diesem Aufenthalt. Bereits ausgeführte Befehle lassen sich nicht zurücknehmen.“; Abschnitt Displays (Display-Sessions mit Scope, zuletzt gesehen, Ablauf, widerrufen); Bewohner-Sessions (widerrufen).

- [ ] **Step 1 – Tests (rot):** Checkout-Dialog zeigt die Zähler und sendet `expected_version`; Widerruf aktualisiert die Liste; „Ersatz ausgeben“ öffnet den QR-Dialog. Lauf: `pnpm test src/admin/screens/Sessions` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): sessions, device replacement and checkout`.

### Task 9b.5: Web – Setup assistant

**Files:**
- Create: `web/src/admin/screens/Setup.tsx`, `web/src/admin/screens/TrustProfile.tsx`, `web/src/admin/screens/GuestTest.tsx`
- Test: `web/src/admin/screens/Setup.test.tsx`

Verhalten (Mockup „Setup assistant“): Prüfliste mit Statussymbol und Text (nie nur Farbe), Detail und „Ausführen“; Formular Public Origin mit Bestätigungsdialog „Ein neuer Origin beendet alle Zugänge“ samt Zählern aus der 409-Antwort; TLS-Modus (eigenes HTTPS: Zertifikats- und Schlüsselpfad unter `/ssl`; Reverse Proxy: CIDR-Liste); Instanz-Einstellungen (Name, Kontakt, Zeitzone, Session-Grenzen, Aufbewahrung); Trustprofil (Policy, berechneter Primärpin nur lesbar mit Kette und Ablauf, Ersatzpins mit Fehlermeldungen, die E28-Warnung gut sichtbar bei jeder nicht leeren Pinliste ohne Ersatzpin, unter beiden Policies, „Neu berechnen“); Gastgerätetest: Start → QR-Dialog → wartet auf `audit.appended` → lädt die Prüfungen neu → bestanden mit IP-Klasse oder Warnung. Eine frische Instanz öffnet hier.

- [ ] **Step 1 – Tests (rot):** Warnung bei einer Pinliste ohne Ersatzpin unter `pin_required` und `pin_optional`; Prüfung `time` mit `not_permitted` zeigt den Hinweis auf die Hochwassermarke; Fehler bei einem CA-Pin; Origin-Wechsel zeigt die Zähler; Gastgerätetest mit simuliertem WS-Ereignis. Lauf: `pnpm test src/admin/screens/Setup` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): setup assistant with tls, trust profile and guest device test`.

### Task 9b.6: Web – Audit & diagnostics

**Files:**
- Create: `web/src/admin/screens/Audit.tsx`, `web/src/admin/screens/Diagnostics.tsx`
- Test: `web/src/admin/screens/Audit.test.tsx`

Verhalten (Mockup „Audit & diagnostics“): Tabelle (Zeit, Akteur, effektiver Principal, Operation, Ergebnis, Grund · Ressource, „×N“ bei zusammengefassten Ablehnungen); Filter Alle / Abgelehnt / Admin / Vorschau, Principal-Filter, Cursor-Paginierung („Mehr laden“). Anonyme Ereignisse (über den NULL-freien Schlüssel aus Teilplan 2 zusammengefasst) stehen als „anonym“ da und verschwinden nie stillschweigend unter einem Principal-Filter; der Filter bietet „anonym“ als eigene Option. Erste Seite aktualisiert sich bei `audit.appended`. Diagnose-Panel (Task 9b.1), „Prüfen und exportieren…“ zeigt das JSON vor dem Download (Blob mit Dateiname); Argon2-Messung mit Tabelle; App-Backup-Status (letzter sauberer Export, Cleanup-Fehler hervorgehoben).

- [ ] **Step 1 – Tests (rot):** Zähler bei zusammengefassten Zeilen; Bezeichnung „anonym“; Export-Vorschau vor dem Download; Filterparameter in der Anfrage. Lauf: `pnpm test src/admin/screens/Audit` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): audit log and diagnostics`.

### Task 9b.7: Web – Review & publish

**Files:**
- Create: `web/src/admin/screens/Publish.tsx`
- Test: `web/src/admin/screens/Publish.test.tsx`

Verhalten (Mockup „Review changes“): Diff-Liste mit Typ-Chips (hinzugefügt, entfernt, erweitert, eingeengt, geändert) und Erklärtext, Zusammenfassung vorher/nachher, betroffene Aufenthalte, Sessions und Zuweisungen; Kontrollkästchen „Auch auf bestehende Zuweisungen und Aufenthalte ausrollen (protokolliert)“; „Veröffentlichen“ mit `expected_version` → „Policy-Version ist jetzt vN“ oder bei 409 „Inzwischen wurde eine neuere Version veröffentlicht; neu laden“ (nie überschreiben); Auswahl zweier veröffentlichter Versionen zum Vergleich.

- [ ] **Step 1 – Tests (rot):** `rollout` wird gesendet; 409-Text; leerer Diff zeigt „Keine Unterschiede“. Lauf: `pnpm test src/admin/screens/Publish` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): review and publish with rollout and conflict handling`.

### Task 9b.8: Web – Restore

**Files:**
- Create: `web/src/admin/screens/Restore.tsx`
- Test: `web/src/admin/screens/Restore.test.tsx`

Verhalten: nur erreichbar bei `setup_state == "restore_pending"` (sonst Umleitung); zeigt die Prüfdaten, je Bindung „bestätigen“/„prüfen“ (Vorgabe aus `proposed`), je Zuweisung „aktivieren“/„deaktiviert lassen“ (Vorgabe deaktiviert), Hinweise je Principal; „Anwenden“ mit Bestätigungsdialog („Sitzungen, Einladungen und Passwörter der alten Installation bleiben ungültig…“); „Verwerfen“ erst nach Bestätigung, Text je nachdem, ob Live-Daten vorhanden sind; ungültiger Export → Erklärung und nur „Verwerfen“; `own_leftover` → Hinweis „übrig gebliebener eigener Export nach einem gescheiterten Cleanup“ und „Verwerfen“ als empfohlene Aktion; nach „Anwenden“ einmal die Session neu laden (neues CSRF) → `#/setup`.

- [ ] **Step 1 – Tests (rot):** Vorgaben; Payload von „Anwenden“; `own_leftover` empfiehlt „Verwerfen“; ungültiger Export blendet „Anwenden“ aus; nach „Anwenden“ genau ein Session-Reload. Lauf: `pnpm test src/admin/screens/Restore` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Web-Prüfung.
- [ ] **Step 4 – Commit:** `feat(web): restore review and apply`.
- [ ] **Step 5 – Manuelle Abnahme im Dev-Stack (Fable):** `scripts/dev.sh` und der Ingress-Proxy aus Task 9a.8 Step 8 (`node tests/e2e/ingress-proxy.mjs`); Admin-UI über den Proxy unter `http://localhost:18098/api/hassio_ingress/e2e/`; jeden Screen nur mit der Tastatur durchgehen (Tab, Shift+Tab, Enter, Leertaste, Escape in Dialogen), DE/EN und alle drei Themes; Befunde als Fix-up-Tasks an den Implementierer. Der Nachweis im echten HA folgt in Task 13.6.

---

## WP13 – Paketierung und Installation auf der Referenzinstallation

Abnahme (Gate): App installiert, Admin-SSO real, Licht und Klima im Wohnzimmer real bedient; A40 (Update ohne Rechteaufweitung), A44 und A47 real. Task 13.1 steht vorgezogen am Anfang dieses Teilplans.

### Task 13.2: Versionierte Fähigkeits-Fingerprints (A40)

Ändert ein Update den Inhalt des Fingerprints, darf das nicht jede Bindung in die Prüfung schicken. Echte Abweichungen müssen weiterhin auffallen.

**Files:**
- Modify: `myha_gateway/adapters/base.py`, `myha_gateway/policy/drift.py`, `myha_gateway/backup/restore.py` (nutzt die Vergleichsfunktion aus `drift.py`)
- Test: `tests/unit/test_adapters.py`, `tests/acceptance/test_matrix_drift.py`, `tests/integration/test_restore.py`

```python
FINGERPRINT_VERSION = 1
_PAYLOADS: Mapping[int, Callable[[DeviceCapabilities, Mapping[str, Any]], dict[str, Any]]] = {
    1: _payload_v1,  # today's payload of with_fingerprint, unchanged
}

def parse_fingerprint(stored: str) -> tuple[int, str]:
    """``"<version>:<hex>"``; a bare hex string is version 1, stored before versioning."""

def fingerprint(capabilities: DeviceCapabilities, attributes: Mapping[str, Any], version: int = FINGERPRINT_VERSION) -> str:
    """Version 1 stays bare hex, so every stored v1 value keeps matching; later ones carry a prefix."""
```

Dieselbe versionsbewusste Vergleichsfunktion nutzen die Drift-Erkennung, die Bindungsprüfung (Task 9a.2) und das Restore-Review (Task 12.3). Regel im Modul-Docstring: Eine Änderung am Payload fügt `_payload_v<n+1>` hinzu und erhöht `FINGERPRINT_VERSION`; `_payload_v<n>` bleibt, solange gespeicherte Zeilen ihn tragen können. Drift für eine Bindung mit gespeicherter Version k kleiner als die aktuelle: `fingerprint(caps, attrs, k)` berechnen. Gleich → in derselben Transaktion den aktuellen Fingerprint eintragen, Status unverändert, kein Policy-Bump, Audit `binding.fingerprint_upgraded`. Ungleich → `needs_review` wie bisher. `blocked` wird nie angefasst; Zustände mit `availability == "offline"` und unbekannte Temperatureinheit werden übersprungen wie in Teilplan 2.

- [ ] **Step 1 – Tests (rot):** Golden-Fingerprint v1 unverändert (Wert aus dem bestehenden Test); mit einem nur im Test registrierten Payload v2 (Monkeypatch von `_PAYLOADS` und `FINGERPRINT_VERSION = 2`): unverändertes Gerät → Fingerprint `2:<hex>`, Status bleibt `confirmed`, `policy_version` unverändert; verändertes Gerät → `needs_review`; `blocked` bleibt unberührt; offline übersprungen; `test_a40_fingerprint_upgrade_neither_widens_nor_flags`; das Restore-Review schlägt für eine unveränderte Bindung mit v1-Fingerprint unter v2 `confirmed` vor. Lauf: `uv run pytest tests/unit/test_adapters.py tests/acceptance/test_matrix_drift.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `feat(adapters): versioned capability fingerprints with in-place upgrade (A40)`.

### Task 13.3: pnpm-Download per Hash prüfen

**Files:**
- Modify: `myha/web/package.json` (`packageManager`), `myha/Dockerfile` (`corepack prepare` mit derselben Angabe), `.github/workflows/ci.yml` (Prüfschritt)

Verhalten: `packageManager` lautet `pnpm@10.14.0+sha512.<hex>`, damit corepack den heruntergeladenen pnpm gegen den Hash prüft; das Dockerfile bereitet genau diese Angabe vor. CI bricht ab, wenn das Feld keinen Hash trägt.

- [ ] **Step 1 – CI-Prüfung (rot):** im Web-Job vor `pnpm install` der Schritt `node -e "const p=require('./package.json').packageManager; if(!/^pnpm@\d+\.\d+\.\d+\+sha512\.[0-9a-f]{128}$/.test(p)){console.error('packageManager without sha512 hash: '+p);process.exit(1)}"` (Arbeitsverzeichnis `myha/web`); lokal ausführen → FAIL beim heutigen `pnpm@10.14.0`.
- [ ] **Step 2 – Hash setzen:** `cd myha/web && corepack use pnpm@10.14.0` schreibt das Feld mit Hash. Gegenprobe: `npm view pnpm@10.14.0 dist.integrity | sed 's/^sha512-//' | base64 -d | xxd -p -c 256` ergibt denselben Hex-Wert. Im Dockerfile `corepack prepare pnpm@10.14.0+sha512.<hex> --activate`.
- [ ] **Step 3 – Grün:** Prüfschritt aus Step 1 → PASS, Web-Prüfung, `bash scripts/build-image.sh`.
- [ ] **Step 4 – Commit:** `build(web): pin pnpm by sha512 hash for corepack`.

### Task 13.4: Entwicklerskript – Deinstallieren, Repository entfernen, Ingress-Aufrufe

**Files:**
- Modify: `scripts/install-on-ha.py`, `myha/gateway/tests/unit/test_install_script.py`

Neue Subcommands (mutierende fragen ohne `--yes` nach; Token und Cookies erscheinen nie in der Ausgabe):
- `uninstall` → `POST /addons/<slug>/uninstall`.
- `remove-repo --repo URL` → Repository-Slug per `GET /store/repositories` über die URL suchen, `DELETE /store/repositories/<slug>`.
- `ingress-call METHOD PATH [JSON]` → Ingress-Session wie `ingress-get`; bei anderen Methoden als GET zuerst `GET api/v1/admin/session` und dessen `csrf_token` als `X-MyHA-CSRF` mitsenden; Ausgabe: Status und JSON. So kann Fable die Referenzinstallation über dieselbe Admin-API einrichten wie die UI.
- `ingress-get` und `ingress-call` maskieren in jeder Ausgabe `csrf_token` und die HA-`user.id` aus `GET session` (`<masked>`), wie heute schon das Token.

- [ ] **Step 1 – Tests (rot):** Argumente; CSRF-Header bei POST, keiner bei GET; `csrf_token` und `user.id` erscheinen maskiert; `remove-repo` findet den Slug über die URL und meldet „nicht gefunden“ sauber; Token wird in Fehlermeldungen maskiert. Lauf: `cd myha/gateway && uv run pytest tests/unit/test_install_script.py -q` → FAIL.
- [ ] **Step 2 – Implementieren.**
- [ ] **Step 3 – Grün:** `uv run pytest tests/unit/test_install_script.py -q`, `uv run ruff check ../../scripts`, `uv run mypy --strict ../../scripts/install-on-ha.py`.
- [ ] **Step 4 – Commit:** `chore: uninstall, repository removal and ingress calls in the install script`.

### Task 13.5: Installation auf der Referenzinstallation (Fable, nach Zustimmung)

- [ ] **Step 1 – S0-Stand klären:** Welche der Schritte Task 1.2 Step 4, 1.3 und 1.4 Step 2 aus Teilplan 1 liefen bereits vom Branch `s0-probe`? Ergebnisse stehen in `docs/s0-report.md`. Noch offene Zeilen weist Task 13.6 mit der echten App nach.
- [ ] **Step 2 – Probe-App entfernen** (nur falls installiert): `set -a; source .dev/ha.env; set +a`; `uv run scripts/install-on-ha.py --yes stop`, `uninstall`, `remove-repo --repo 'https://github.com/LL0rd/myha#s0-probe'`; danach `git push origin --delete s0-probe` und lokal `git branch -D s0-probe`.
- [ ] **Step 3 – Installieren (braucht den Betreiber: Host-Port für 8443):** `add-repo` (Default `https://github.com/LL0rd/myha`), `reload`, `install`, `network <Host-Port>`, `start`, jeden Schritt einzeln mit Ergebnisprüfung. `logs --lines 200`: Migrationen, Zeile „Supervisor … (minimum 2026.10.3)“, „public listener off: no public origin configured“, kein Traceback, keine Frame-Size-Fehler beim realen `get_states` (S0-Notiz zur 16-MiB-Grenze) und eine stabile Verbindung des HAClient hinter dem Supervisor-Proxy.
- [ ] **Step 4 – Admin-SSO real (A01–A05 über den echten Ingress):** `ingress-get api/v1/admin/session` → 200 mit dem Besitzer des Tokens als Benutzer (in der Ausgabe maskiert); Dimitri öffnet myHA in der HA-Seitenleiste: Die Admin-UI lädt unter dem Ingress-Prefix, der Setup-Assistent zeigt die Prüfungen, der Admin-WS verbindet (Pairing-Liste aktualisiert sich live). Optional A04 real (braucht den Betreiber): ein Testbenutzer verliert die Admin-Gruppe → nächste Mutation 403, Admin-WS schließt.
- [ ] **Step 5 – Bericht:** `docs/s0-report.md` Zeilen 4 (Supervisor- und Core-Version aus `/info`) und 5 (echtes Build-Log) ergänzen; Commit `docs: myHA installed on the reference installation` (Fable-Trailer).

### Task 13.6: Realer vertikaler Pfad und Nachweise (Fable, nach Zustimmung)

Steps 1–3 und 8 brauchen den Betreiber (offener Punkt 1, Telefon, Tablet, Zustimmung zum Schalten der Klimaanlage). Steps 4–7 laufen ohne offenen Punkt 1 und dürfen vorher stattfinden.

- [ ] **Step 1 – Public Listener (braucht den Betreiber: offener Punkt 1):** TLS-Modus und Zertifikat nach Dimitris Entscheidung über `ingress-call PUT api/v1/admin/setup/tls …` und `…/setup/origin …`; Prüfungen ausführen. A01 real: `curl -H 'X-Remote-User-Id: x' <origin>/api/v1/admin/session` → 404.
- [ ] **Step 2 – Wohnzimmer (braucht den Betreiber):** Unit „Living room“; Bindungen für das Govee-Licht und eine Klimaanlage (bestätigte Modi aus `hvac_modes ∩ {off, cool, heat}`); Rolle mit `light.on/off/set_level` sowie `climate.set_temperature` 19–24 °C in 1-°C-Schritten und `climate.set_mode` {off, cool, heat}; ein Bewohner-Konto des Betreibers mit Zuweisung. Login auf dem Telefon des Betreibers: Licht an, aus, Helligkeit → jeweils `confirmed` und in HA sichtbar; Klima-Sollwert im Bereich → `confirmed`, außerhalb → abgelehnt; Moduswechsel cool ↔ heat mit Beobachtung, ob der Sollwert bleibt oder springt (E7; die Nachprüfung setzt bei einem Wert außerhalb `needs_review`) → S0-Zeile 14. A18 real: liegt der gemeldete Sollwert außerhalb 19–24 °C (nötigenfalls mit Zustimmung in HA auf 26 °C gestellt), lehnt myHA `cool` und `heat` ab. Am Ende Modus und Sollwert wiederherstellen.
- [ ] **Step 3 – Gastpfad und Gast-WLAN (A44; braucht den Betreiber):** Aufenthalt und Einladung; Einlösen mit einem Telefon im Gast-WLAN; Gastgerätetest aus dem Setup-Assistenten → Prüfungen `guest_device` und `client_ip` (IP-Klasse → S0-Zeile 11); Checkout → Seite „Zugang beendet“.
- [ ] **Step 4 – Backup (A47, A48 real):** `backup-new`; `logs` mit den Zeilen von Export und Cleanup; `backup-download <slug> <Scratchpad>/myha-backup.tar` (außerhalb des Repos); das innere App-Archiv enthält `backup/restore.sqlite`, kein `state/`, keine `-wal`/`-shm`; `sqlite3` auf der exportierten Datei: Sicherheitstabellen leer, `server_secret` leer; `GET backup/status` meldet Erfolg, und nach dem Post-Hook liegt kein Export mehr in `/data/backup` → S0-Zeile 12. „Gestoppte App“ (A48): `stop`, `backup-new`, Archiv ohne `restore.sqlite` und ohne `state/`, keine Hook-Zeilen im Log; `start` → kein Restore-Modus. Optional mit eigener Zustimmung: die erste Teilsicherung wiederherstellen → Restore-Modus → Prüfen → Anwenden (A39 real); dabei festhalten, ob der Supervisor `/data/state` beim Restore leert (offener Punkt 8) und wie er auf einen fehlschlagenden `backup_pre` reagiert (offener Punkt 9).
- [ ] **Step 5 – Zeitquelle (E26):** Die Prüfung `time` und das Diagnosefeld `time` zeigen `host_info`. Bei `not_permitted` bleibt es bei `hassio_api: false` (Entscheidung 7: nur die Hochwassermarke); `hassio_api: true` mit `hassio_role: default` nur mit Dimitris Zustimmung (eigener Commit, `rebuild`, erneut prüfen) → S0-Zeile 13.
- [ ] **Step 6 – Update ohne Rechteaufweitung (A40 real):** Status aller Bindungen und die Vorschau eines Aufenthalts notieren; einen neueren Commit von `main` per `update` bzw. `rebuild` einspielen; danach keine `binding.needs_review`-Welle im Audit, kein Restore-Modus, Vorschau unverändert, Sessions weiter gültig.
- [ ] **Step 7 – Argon2:** `argon2_ms` aus der Diagnose und die Tabelle aus `POST diagnostics/argon2-benchmark` → `docs/security.md` und S0-Zeile 15. Liegen die vorläufigen Parameter deutlich neben ≈ 250 ms, passt ein Subagent die Parameter in `auth/passwords.py` an; bestehende Hashes werden beim nächsten Login neu erstellt.
- [ ] **Step 8 – Manuelle Abnahme der Admin-UI im echten HA (braucht den Betreiber):** alle Screens, Tastaturpfad, DE/EN, drei Themes; Screenreader (VoiceOver oder TalkBack) für Login, Dashboard, Rechteeditor und Vorschau → Ergebnis in `docs/acceptance.md` (Gate WP9a/9b, A42 manueller Teil).
- [ ] **Step 9 – Bericht:** `docs/s0-report.md` Zeilen 11–15 ergänzen, `docs/acceptance.md` (Status-Spalte und Nachweis) mit A01–A05, A18, A40, A44, A47, A48 real und den Gate-Nachweisen; keine LAN-IPs, Token, User-IDs oder Entity-IDs der Referenzinstallation. Commit `docs: real vertical path on the reference installation` (Fable-Trailer).

### Task 13.7: Veröffentlichte Images (optional; Fable, nach Zustimmung; braucht den Betreiber)

Nicht Gate-relevant (E16: erst lauffähig, dann bequem).

- [ ] **Step 1:** Dimitri stellt die ghcr-Pakete auf öffentlich; nach Task 14.6 Tag `v0.2.0` pushen → `image.yml` baut amd64 und aarch64; `docker pull ghcr.io/ll0rd/myha-amd64:0.2.0` gelingt.
- [ ] **Step 2:** `image: ghcr.io/ll0rd/myha-{arch}` in `myha/config.yaml`; auf der Referenzinstallation `update` statt lokalem Build prüfen; Commit `feat(addon): use the published images`.

---

## WP14 – E2E, Last, Doku

Abnahme: A41, A42, A45 und A46 dokumentiert; Regression A19–A35, A50, A52 im Browser.

### Task 14.1: E2E – Admin-Journey, Bewohner, Display-Pairing, Gerätetest

**Files:**
- Modify: `tests/e2e/helpers/gateway.ts` (frisches Gateway ohne Seed je Spec; nutzt den Ingress-Proxy aus Task 9a.8 Step 8 mit `MYHA_ADMIN_PORT` des Test-Gateways)
- Create: `tests/e2e/admin.spec.ts`, `tests/e2e/resident.spec.ts`

Specs:
- `admin.spec.ts`: `http://localhost:18098/api/hassio_ingress/e2e/` öffnet den Setup-Assistenten (frische Instanz; der Public-Listener läuft über den Dev-Override); „Rooms & devices“: Raum „Room 205“, `light.room_203_ceiling` und `climate.room_203_trv` (Modi off/heat) binden; „Roles & rights“: Rolle „Guest“ mit Licht ein/aus und Temperatur 19–24/0,5, Entwurf speichern, „Review & publish“ → v1; „Stays & invites“: Aufenthalt heute bis morgen, „Zuerst prüfen“ zeigt das Banner, „QR ausgeben“ für zwei Geräte; die Join-URL liest der Test über `page.waitForResponse` auf `POST …/stays/{id}/invites` aus der Antwort (der Dialog zeigt den Link nur maskiert), dann „Fertig“; zweiter Browserkontext öffnet die URL → Dashboard „Room 205“ → Licht aus → bestätigt; „Sessions & checkout“: Checkout → Gastseite „Zugang beendet“ innerhalb 5 s; „Audit & diagnostics“ zeigt Redeem und Checkout; Gastgerätetest: Start → dritter Kontext löst ein → Prüfung `guest_device` ok.
- `resident.spec.ts`: Bewohner über „People & groups“ anlegen → `/login` im Gastkontext → Dashboard; falsches Passwort zeigt die einheitliche Meldung; Display-Pairing: Der Admin legt zuerst einen Display-Principal mit einer Zuweisung der Rolle auf die Unit an (ohne Zuweisung sähe das Tablet ein leeres Dashboard); Tablet-Kontext `/pair` → Code → Admin bestätigt für diesen Display mit dem abgelesenen Code und der Unit → Tablet zeigt das Wand-Dashboard.

- [ ] **Step 1 – Specs schreiben, rot gegen den Stand ohne die Helfer-Erweiterung:** `cd tests/e2e && pnpm exec playwright test admin.spec.ts resident.spec.ts` → FAIL.
- [ ] **Step 2 – Helfer erweitern.**
- [ ] **Step 3 – Grün:** E2E-Prüfung; CI-Job `e2e` aus Teilplan 2 läuft die neuen Specs mit.
- [ ] **Step 4 – Commit:** `test(e2e): admin journey, resident login and display pairing`.

### Task 14.2: E2E – A41 Größen, A42 Tastatur und axe

**Files:**
- Create: `tests/e2e/a11y.spec.ts`
- Modify: `tests/e2e/package.json` (`@axe-core/playwright`)

Für Gast-Dashboard, Login, Admin-Rolleneditor und Admin-Vorschau bei 320×640, 768×1024, 1024×768, 1180×820 und 2560×1440 sowie mit 200 % Textgröße (Root-Schriftgröße per `page.addStyleTag` auf 200 %) bei 320 und 768 px Breite; Voraussetzung: Schrift- und Abstandsgrößen in `rem`, was der Test damit mitprüft: kein horizontales Scrollen (`scrollWidth <= clientWidth`), jede Hauptaktion nach Scrollen sichtbar und mindestens 44 px groß (Wandmodus 64 px). Tastatur: Tab erreicht Login-Felder und Absenden, Dashboard-Aktionen, Matrixzellen und die Zielauswahl der Vorschau in sinnvoller Reihenfolge; Dialoge halten den Fokus und schließen mit Escape. axe: keine Verstöße der Stufen `serious` oder `critical`. Die Screenreader-Prüfung für Login, Dashboard, Rechteeditor und Vorschau bleibt manuell (Task 13.6 Step 8).

- [ ] **Step 1 – Spec schreiben (rot, solange Layoutfehler bestehen):** `pnpm exec playwright test a11y.spec.ts`.
- [ ] **Step 2 – Gefundene Layout- und Fokusfehler in `web/src` beheben** (je Befund ein kleiner Commit `fix(web): …`).
- [ ] **Step 3 – Grün:** E2E-Prüfung und Web-Prüfung.
- [ ] **Step 4 – Commit:** `test(e2e): responsive layouts, keyboard paths and axe checks (A41, A42)`.

### Task 14.3: Keine Secrets in Logs, Audit und Diagnose (A45)

**Files:**
- Create: `tests/acceptance/test_matrix_logs.py`

`test_a45_logs_audit_and_diagnostics_hold_no_secrets`: Logging über `setup_logging("debug")` mit dem echten Handler, mitgeschnitten per `capfd` (nicht `caplog`). Abläufe: Bewohner-Login richtig und falsch, Redeem im Browser und nativ, Refresh und Wiederverwendung, WS-Ticket und WS-Auth, Pairing Start, Poll und Bestätigung, Test-QR, Einladungen ausgeben, Gerätezugang ersetzen, Diagnose und Export. Alle dabei entstandenen Secret-Werte (Passwörter, Einladungs-Secrets, Access- und Refresh-Token, Poll-Token, Tickets, CSRF-Token, `server_secret`, Cookies) werden gesammelt; keiner erscheint in stdout/stderr, in einer Spalte von `audit_event` oder in den Diagnose-Antworten; die versuchten Benutzernamen fehlgeschlagener Logins fehlen im Audit.

- [ ] **Step 1 – Test schreiben:** `uv run pytest tests/acceptance/test_matrix_logs.py -q` (rot, falls ein Pfad etwas preisgibt).
- [ ] **Step 2 – Gefundene Stellen beheben** (Redaction, Audit-Felder).
- [ ] **Step 3 – Grün:** Gateway-Prüfung.
- [ ] **Step 4 – Commit:** `test: no secrets in logs, audit or diagnostics (A45)`.

### Task 14.4: Lastskript (A46)

**Files:**
- Create: `scripts/load-test.py` (PEP 723: `websockets`, `httpx`)
- Modify: `myha/gateway/myha_simulator/fixtures.py`, `myha/gateway/myha_simulator/__main__.py` (`--synthetic N`: N zusätzliche Lichter und Sensoren im Bereich `area_load`), `myha/gateway/myha_gateway/devseed.py` (`dev-seed load`: Unit mit 100 Bindungen aus `area_load`, Rolle, Aufenthalt, 20 Einladungen; Join-URLs nur auf stdout und nur mit `MYHA_PUBLIC_ENABLED=1`, wie `dev-seed room203`)
- Test: `myha/gateway/tests/integration/test_devseed.py`, `myha/gateway/tests/integration/test_simulator.py`

Verhalten: Gegen einen laufenden Dev-Stack (Simulator mit `--synthetic 485`, also 500 Entities; Gateway; `dev-seed load`) löst das Skript 20 Einladungen nativ ein, öffnet 20 WS-Verbindungen und ändert 10 min lang jede Sekunde über `_sim/state` zufällige gebundene Entities. Es misst Ereignislatenz (Änderung im Simulator bis `resource.updated` beim Client, p50/p95), die Rundlaufzeit von `POST /actions` (p95), `decide_p95_ms` aus der Diagnose und den Speicher des Gateways am Anfang und Ende (`/proc/<pid>/status`, lokal). Ausgabe als JSON. Ergebnis mit Hardwareangabe in `docs/acceptance.md` (A46). Der 72-Stunden-Wandbetrieb (braucht den Betreiber) läuft nur, wenn ein Wandtablet an der Referenzinstallation verfügbar ist (Fable, nach Zustimmung); sonst steht er mit Ziel und begründeter Grenze als offen in der Matrix.

- [ ] **Step 1 – Tests (rot):** `--synthetic 485` liefert 500 Entities; `dev-seed load` ist idempotent und gibt 20 URLs aus. Lauf: `uv run pytest tests/integration/test_devseed.py tests/integration/test_simulator.py -q` → FAIL.
- [ ] **Step 2 – Implementieren;** Skript einmal lokal laufen lassen.
- [ ] **Step 3 – Grün:** Gateway-Prüfung, `uv run ruff check ../../scripts`.
- [ ] **Step 4 – Commit:** `test: load script for 20 clients and 100 resources (A46)`.

### Task 14.5: Doku

**Files:**
- Modify: `docs/setup.md`, `docs/security.md`, `myha/DOCS.md`, `README.md`
- Create: `docs/restore.md`, `docs/device-matrix.md`

Inhalt (englisch, für Betreiber, nichts Betreiberspezifisches):
- `setup.md`: Installation, Rundgang durch den Setup-Assistenten, Verbindungsmodi mit Proxy-CIDRs und Zertifikatswechsel, Bewohner, Display-Pairing, Aufenthalte (Einladungen, Verlängerung, Zimmerwechsel, Ersatz, Checkout), Trustprofil für native Apps mit der E28-Warnung, Anforderungen an das Gast-WLAN (nur DNS und der Gateway-Port).
- `security.md`: Punkte aus „Decided, not implemented yet“ nach „Implemented today“ (Trustprofil, Leaf-Pins, native Token mit Rotation, Login-Rate-Limits und einheitliche Fehler, Pairing, Proxy-Modus, Backup und Restore); Argon2-Tabelle mit den gewählten Parametern; A45-Zusammenfassung; Abschnitt „No HSTS“ mit der Begründung aus Task 11.1 (HSTS gilt hostweit und portunabhängig, HA Core läuft auf demselben Host oft per HTTP auf 8123, das Gateway hat keinen Klartext-Listener, im Modus `proxy` gehört der Header dem Proxy); Meldeweg für Schwachstellen: nur der Verweis auf `SECURITY.md` (braucht den Betreiber: die private Meldung im GitHub-Repo aktiviert nur Dimitri, offener Punkt 11).
- `restore.md`: Inhalt eines Backups (E22-Liste), Hooks und Status, Prüfen, Anwenden und Verwerfen, was verloren geht (Sessions, Einladungen, Passwörter, Pins), Verhalten bei Fehlern (A48), normale Backups und Updates (A40) samt Fingerprint-Upgrade.
- `device-matrix.md`: getestete Browser (Playwright-Chromium-Version, Dimitris Telefonbrowser aus Task 13.6), Mindest-WebView (zod 4 braucht `globalThis`, Chrome ≥ 71); native Zeilen folgen in Teilplan 4.
- `DOCS.md`: Option `log_level`, Ports, echtes Backup-Verhalten, Hinweis Pilotstatus. `README.md`: Status „pilot“.

- [ ] **Step 1:** Texte schreiben; Links prüfen (`grep -n "](" docs/*.md`).
- [ ] **Step 2 – Commit:** `docs: setup, security, restore and device matrix for the web pilot`.

### Task 14.6: Teilplan-3-Abschluss

- [ ] **Step 1:** Gateway-, Web- und E2E-Prüfung grün, CI grün; Coverage `policy` und `auth` ≥ 85 %.
- [ ] **Step 2:** `CHANGELOG.md` mit Abschnitt `[0.2.0]` (Added, Changed, Security); Version 0.2.0 in `myha/config.yaml`, `myha/gateway/pyproject.toml` und `myha_gateway/__init__.py` (plus `tests/unit/test_version.py`); `docs/acceptance.md` mit dem Stand A01–A52, die Zeilen nennen statt der Arbeitspakete die Tasks und Tests dieses Plans; im Design §4.6 den Startablauf an §14 angleichen (ein eigener übrig gebliebener Export wird beim Start entfernt, ein fremder oder unbekannter führt in den Restore-Modus), im Design §6 die Routenliste aus Entscheidung 12 nachtragen, §5 um die neuen Spalten (`session.access_expires_at`, `instance.trust_policy`, die Pairing-Felder aus Task 5b.1, `stay.moved_from_id`, Singleton-Trigger) ergänzen, §7 auf „Radix nur Dialog und Tabs“ setzen, §10 um Login-Brute-Force (Rate Limits, einheitliche Fehler, Argon2id), Display-Pairing (Code nur am Gerät, nie leerer Scope) und Proxy-Spoofing (nur Proxy-CIDRs, keine HAOS- oder Docker-Netze) ergänzen und §14 auf den Ist-Stand bringen.
- [ ] **Step 3 – Commit:** `docs: plan 3 wrap-up`. Fable: Review (`superpowers:requesting-code-review`), danach Gate.

### Task 14.7: Privates Repo – Submodule `vendor/myha`

**Files (im privaten Repo `LL0rd/myha-apps`, lokaler Klon neben diesem Repo):** `.gitmodules`, `vendor/myha`, `README.md`

- [ ] **Step 1:** `git submodule add https://github.com/LL0rd/myha vendor/myha`; `git -C vendor/myha checkout <Commit von main nach Task 14.6>`.
- [ ] **Step 2:** README-Hinweis: Die Web-UI wird ab Teilplan 4 aus `vendor/myha` gebaut (`vite build --mode native`).
- [ ] **Step 3 – Commit und Push:** `chore: vendor myha as a pinned submodule`.

---

## Gate zu Teilplan 4 (Masterplan)

| Bedingung | Nachweis |
|---|---|
| Add-on auf Dimitris HA installiert | Task 13.5 |
| Admin-SSO real | Task 13.5 Step 4, Task 13.6 Step 8 |
| Licht und Klima im Wohnzimmer real bedient | Task 13.6 Step 2; hängt am offenen Punkt 1 (Zertifikat oder Reverse Proxy) und an Dimitris Telefon |
| Restliche M2-Abnahmefälle grün oder dokumentiert | A01–A32, A34, A35, A39–A42, A44, A45, A47–A52 in `docs/acceptance.md`; A33 entfällt (E21); A46 dokumentiert, der 72-Stunden-Lauf hängt am offenen Punkt 14 |

---

## Entscheidungen (Plan-Review, Revision 2)

2. **Zimmerwechsel:** `stays/{id}/move` erzeugt den Zielentwurf und je Geräteplatz eine Einladung (QR genau einmal); Einlösen gelingt erst nach der Bestätigung; die Bestätigung übernimmt bestehende Sessions nur auf ausdrücklichen Wunsch (Default aus) und setzt dann `session.unit_ids` auf die neue Unit. Die Invites-Route lehnt verschobene Entwürfe ab, und die Bestätigung scheitert, wenn der alte Aufenthalt nicht mehr läuft (Task 10.4).
3. **Argon2-Parameter:** vorläufig `t=3, m=64 MiB, p=1` mit Rehash beim Login; die Messung auf der Referenzinstallation (Task 13.6 Step 7) kann sie anpassen. Keine Kalibrierung je Installation.
4. **Bewohner-Passwörter:** Der Admin setzt Benutzername und Passwort; keine Selbständerung; `must_reset` bleibt `false` und wird nicht durchgesetzt.
5. **Login- und Pairing-Seiten** folgen der Einladungsseite des Mockups; `ios-frame.jsx` und ein eigenes Login-Design liegen nicht im Repo.
6. **Origin-Wechsel** widerruft nach Bestätigung alle Zugänge, offenen Einladungen und offenen Pairing-Anfragen (Spez §8.4).
7. **`hassio_api`:** bleibt `false`; ohne `/host/info` wirkt nur die Hochwassermarke (E26). Die Prüfung und das Diagnosefeld `time` zeigen den Stand; eine zusätzliche Supervisor-Berechtigung gibt es nur mit Dimitris Zustimmung (Task 13.6 Step 5).
12. **Routen über Design §6 hinaus**, nachgetragen in Task 14.6: `preview/{id}/explain`; `GET roles/{id}`, `GET roles/{id}/versions/{n}`, `DELETE roles/{id}/draft`; `PATCH`/`DELETE` `units/{id}` und `bindings/{id}`, `GET bindings/{id}/review`; `stays/{id}/move` mit Einladungen, `carry_over_sessions` beim Bestätigen, `stays/{id}` (`GET`, `PATCH`), `stays/{id}/cancel`; Filter `principal_id` und `principal_type` auf `sessions`; die neuen Felder von `GET session` (`policy_version`, `public_ready`, `public_reason`); `me/trust`, `ws-ticket` und `logout` für Testsessions; `restore/discard`; `setup/instance`, `setup/trust/recompute`; `diagnostics/argon2-benchmark`.
13. **Radix:** nur Dialog und Tabs; Select, Checkbox, Switch und Tooltip bleiben native Elemente, solange sie den Tastaturtest bestehen. Task 14.6 passt Design §7 an.

## Offene Punkte (braucht den Betreiber oder reale Beobachtung)

1. **TLS auf der Referenzinstallation:** Liegt ein Zertifikat für einen Hostnamen in `/ssl`, oder steht ein Reverse Proxy davor (E8)? Davon hängen Task 13.6 Steps 1–3 und die Gate-Zeile „Licht und Klima“ ab; Steps 4–7 laufen ohne.
8. **Restore-Verhalten des Supervisors:** Ob ein App-Restore `/data/state` leert oder die ausgeschlossenen Pfade stehen lässt, ist nicht belegt. Tasks 12.3 und 12.4 funktionieren in beiden Fällen; Task 13.6 Step 4 hält das reale Verhalten fest.
9. **Fehlschlagender `backup_pre`:** Ob der Supervisor das Backup abbricht oder ohne Export fortsetzt, ist nicht belegt. Es gibt in beiden Fällen keinen Rückfall auf Live-Daten (`state/` ist immer ausgeschlossen); Task 13.6 Step 4 hält es fest.
10. **Veröffentlichte Images (E16):** ghcr-Pakete öffentlich stellen und ein Release-Tag setzen braucht Dimitris Zustimmung; Task 13.7 ist optional.
11. **Schwachstellenmeldung:** Die private Meldung ist im GitHub-Repo deaktiviert; nur Dimitri kann sie aktivieren. `docs/security.md` verweist nur auf `SECURITY.md` (Task 14.5).
14. **72-Stunden-Wandbetrieb (A46):** braucht ein Wandtablet an der Referenzinstallation; ohne Gerät bleibt der Fall dokumentiert offen.

