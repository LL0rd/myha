# myHA – Design-Dokument

**Stand:** 2026-10-22 · **Status:** Entwurf zur Freigabe · **Sprache:** Deutsch (Code, Identifier und Repo-Doku: Englisch)

Dieses Dokument legt fest, *wie* myHA gebaut wird. Es baut auf der Produkt- und Implementierungsspezifikation von ChatGPT auf (`docs/reference/2026-10-22-ha-access-ui-spezifikation-chatgpt.md`, im Folgenden „Spez“) und dem Claude-Design-Mockup (`docs/reference/design/`). Wo die Spez etwas verbindlich vorgibt, wird es hier nicht wiederholt, sondern referenziert. Wo die Spez offen war oder sich mit der realen Umgebung reibt, steht hier die Entscheidung mit Begründung.

## 1. Ziel und Umfang dieser Runde

myHA ist eine Home-Assistant-App (HAOS-Add-on) mit eigener Administration im HA-Ingress, serverseitiger Rechteverwaltung und einem responsiven Bedienpanel für Bewohner, Gäste und Wandtablets. Kernablauf (Spez §0): Raum auswählen → erlaubte Aktionen und Werte festlegen → Ansicht als Benutzer prüfen → Zugang ausgeben → Zugang gezielt oder automatisch beenden.

**Im Umfang (Entscheidung Dimitri, 2026-10-22):** Phasen S0, M1, M2 und M3 der Spez §18.

| Phase | Ergebnis | Verbindliches Ende |
|---|---|---|
| S0 Nachweise | Admin-Identität über Ingress, Supervisor-Mindestversion, zwei Listener, HA-Lese-/Schreibadapter, Backup-Hook-Probe, Transport-Spike Android | Keine unbelegte Annahme trägt das weitere Design |
| M1 Vertikaler Kern | Ein Raum (Wohnzimmer), ein Govee-Licht, eine Klimaanlage; Principal/Gruppe/Policy; Browserlogin; Fremdansicht; Aktion und gefiltertes Live-Update | Verbotene API-Aktion und fremder Zustand nachweislich blockiert; Simulation steuert kein Gerät |
| M2 Webpilot | GUI-Einrichtung, Gruppen/Rollen/Fences, Gerätebindungen, Dashboard-Editor, Gast-QR, Aufenthalte, Checkout, Audit, Backup/Restore | Abnahmefälle A01–A35, A39–A42, A45, A47–A52 automatisiert bzw. dokumentiert |
| M3 Native | Android/Fire-Paket mit lokaler UI, sicherer Speicherung, Scanner, nativer Verbindung, optionalem Pinning; iOS als Gerüst | Android-Build läuft auf Emulator/Gerät; iOS-Gerüst kompiliert nicht hier (kein macOS) und ist als ungetestet markiert |

**Nicht im Umfang:** M4 (Vorlagen-Rollout für viele Einheiten, Lizenzierung, Support-Tooling), Store-Einreichung, Kameras/Schlösser/Skripte und alles aus Spez §18.1. Ein Rollout einer neuen Rollenversion auf bestehende Zuweisungen ist dagegen Teil von M2, weil der Publish-Ablauf ohne ihn unvollständig wäre.

## 2. Geklärte offene Punkte

| Nr. | Punkt | Entscheidung | Begründung |
|---|---|---|---|
| E1 | Name, Slug | Produktname **myHA**, Add-on-Slug `myha`, Python-Paket `myha_gateway` | Vorgabe Dimitri; „HA Access UI“ war nur Arbeitstitel |
| E2 | Repositories | Öffentlich `LL0rd/myha` (Gateway, Web-UI, HA-App-Repository, Doku). Privat `LL0rd/myha-apps` (Capacitor-Projekte, native Plugins, Store-Assets) | Offener Kern, verkaufbare Komfort-Apps (Spez §17, Dimitri) |
| E3 | Lizenz | Apache-2.0 für `myha` (Annahme, Dimitri bestätigt bei Spec-Review); `myha-apps` proprietär | HA-Ökosystem-üblich, erlaubt kommerzielle Nutzung durch den Autor |
| E4 | Supervisor-Minimum | ≥ 2026.10.3 wird erzwungen. Dimitris Instanz steht auf 2026.10.2 (Stable-Release 2026.10.3 vom 2026-10-19 wird automatisch nachgezogen). Admin-UI zeigt bei zu altem Supervisor eine Update-Aufforderung und bleibt gesperrt | Spez §3.4; PR #7308 (`panel_admin`-Durchsetzung) ist in 2026.10.3 enthalten, verifiziert über GitHub |
| E5 | Admin-Identität | Peer-IP `172.30.32.2` am Admin-Listener, genau ein `X-Remote-User-Id`, danach frische Rollenprüfung über `config/auth/list` (Cache ≤ 5 s). Der neue Header `X-Remote-User-Groups` (Supervisor-PR #7398, gemerged 2026-10-21, noch nicht released) wird protokolliert, aber nie als Ersatz der Core-Abfrage verwendet | `config/auth/list` gegen Dimitris Core 2026.10.2 getestet: liefert `id`, `is_owner`, `is_active`, `group_ids`, `system_generated`; S0-Gate damit vorab bestanden |
| E6 | Klima-Semantik | Erster Adapter: Einzel-Sollwert in °C (`climate.set_temperature` → `temperature`), **plus** `climate.set_mode` mit je Ressource admin-bestätigter Modusliste aus `off`, `cool`, `heat`. `auto`, `dry`, `fan_only`, Presets und Doppelsollwerte bleiben gesperrt | Dimitris vier Geräte sind Klimaanlagen (Modi auto/cool/dry/fan_only/heat/off, 8–30 °C, Schritt 1 °C); ohne Moduswechsel wären sie für Gäste nutzlos. Spez §6.4 erlaubt das bei geprüfter Semantik |
| E7 | Temperatur-Fence und Modus | Ein Moduswechsel wird nur erlaubt, wenn der danach aktive Sollwert innerhalb des effektiven Bereichs liegt; sonst `ACTION_NOT_ALLOWED` mit Admin-Grund `MODE_WOULD_BYPASS_FENCE`. Das Gateway sendet dazu den aktuellen `temperature`-Wert erneut mit, wenn HA ihn beim Moduswechsel verwerfen könnte | Spez §6.4 Absatz 3 |
| E8 | TLS/Erreichbarkeit | Zwei Betriebsarten: `tls` (eigener HTTPS-Listener, PEM-Zertifikat und Schlüssel aus `/ssl`, beliebiger Aussteller: Let's Encrypt über die HA-App, eigene CA, privates Zertifikat) und `proxy` (Klartext-Bind, nur konfigurierte Proxy-CIDRs, `X-Forwarded-Proto/Host` nur von diesen). Public Origin ist immer explizit. Setup-Assistent prüft beide. Entscheidung für den Pilot fällt beim Deployment | Dimitri: „frei möglich … Let's Encrypt, eigene CA, privates Zert“ |
| E9 | Cover-Tests | Es gibt keine Cover-Entities in Dimitris HA. Cover werden gegen den HA-Simulator getestet und im Pilot als „nicht nachgewiesen auf realer Hardware“ dokumentiert | Inventar 2026-10-22 |
| E10 | Dashboard-Editor | Minimal in „Rooms & devices“ integriert: Reihenfolge der Karten je Einheit, Beschriftung, Symbol, bevorzugte Kartengröße, Theme-Default. Kein freies Raster, keine Viewport-Overrides in dieser Runde | Spez §11.3 MVP; Mockup hat keinen eigenen Editor-Screen |
| E11 | Personen und Gruppen | Zusätzlicher Admin-Screen „People & groups“ (Bewohner mit Passwort-Reset, Gruppen, Mitgliedschaften, Displays mit Pairing-Freigabe) | Spez §5/§8 verlangen es, Mockup zeigt es nicht |
| E12 | Icons und Fonts | Keine externen Ressourcen: `@tabler/icons-react` gebündelt, Fonts Nunito Sans und JetBrains Mono über `@fontsource` selbst gehostet | Spez §8.2 Punkt 4 (Einstiegsseite lädt nichts Fremdes); Mockup nutzt iconify-CDN, das wird nicht übernommen |
| E13 | Sprache | UI deutsch und englisch (Browsersprache, manuell umschaltbar). Code, Kommentare, README, ADRs englisch; dieses Design-Dokument und der Plan deutsch | Öffentliches Repo; Dimitri kommuniziert deutsch |
| E14 | Native Tokens schon in M2 | Access-/Refresh-Token-Pfad (`client=native`) wird im Gateway bereits in M2 implementiert und getestet, damit M3 nur den Client baut | M3 ist im Umfang; der Serverteil ist klein und testbar |
| E15 | Deployment auf Dimitris HA | Über die Supervisor-API per WS `supervisor/api` (Repository hinzufügen, App installieren, Optionen setzen) mit dem Owner-Token aus der HA-MCP-Konfiguration; alternativ über die HA-GUI. Kein SSH nötig | SSH-Schlüssel ist nicht hinterlegt; der Token liegt lokal in `~/.claude.json` und wird nie ausgegeben |
| E16 | Build-Strategie | Dockerfile im Add-on-Ordner, damit der Supervisor lokal bauen kann (Pilot). Zusätzlich GitHub-Actions-Workflow, der `ghcr.io/ll0rd/myha-{arch}` für amd64 und aarch64 baut; sobald der läuft, bekommt `config.yaml` den `image:`-Eintrag | Erst lauffähig, dann bequem |
| E17 | Python-/Node-Versionen | Gateway Python 3.13 auf HA-Basisimage `ghcr.io/home-assistant/{arch}-base-python:3.13-alpine3.22` (genauer Tag wird in WP0 gegen `docker-base` verifiziert). Lokal `uv` mit Python 3.13. Web mit Node 22 und `pnpm` | Lokal liegt Python 3.14 vor, das Add-on-Image gibt 3.13 vor; Versionsgleichheit vermeidet Überraschungen |
| E18 | Ingress-Peer in Tests | Vertrauenswürdige Peer-IP ist nur über die Umgebungsvariable `MYHA_TRUSTED_INGRESS_PEER` änderbar (Default `172.30.32.2`), niemals über App-Optionen. Tests setzen `127.0.0.1` | Spez §17: kein `ALLOW_ALL`; Tests brauchen trotzdem einen Weg |
| E19 | QR-Erzeugung | Serverseitig als SVG mit `segno` (reines Python), nur im Admin-Ingress ausgeliefert | Spez §8.2 (lokal, kein externer Bilddienst) |
| E20 | Passwort-Hashing | `argon2-cffi` (Argon2id), Parameter in S0 auf der HA-VM gemessen (Ziel ≈ 250 ms) und in `docs/security.md` festgehalten | Spez §8.1 |

## 3. Architektur

```
HA-Admin (Browser) ──HA-Frontend──▶ Supervisor-Ingress ──▶ Admin-Listener :8099 (nur Peer 172.30.32.2)
Gast / Bewohner / Tablet (Browser oder native App) ──HTTPS/WSS──▶ Public-Listener :8443 (tls oder proxy)
                                                                   │
                                            ┌──────────────────────┴──────────────────────┐
                                            │  myha_gateway (ein Prozess, ein Worker)      │
                                            │  AdminApp (FastAPI)   PublicApp (FastAPI)    │
                                            │        └───── gemeinsamer Backend-Kontext ───┘
                                            │  Auth · Policy · Projection · EventBus       │
                                            │  Commands · Audit · Backup · Setup           │
                                            │  HAClient (eine WS-Verbindung) · SQLite      │
                                            └──────────────┬───────────────────────────────┘
                                                           ▼
                                   ws://supervisor/core/websocket · http://supervisor/info
```

- **Ein Prozess, zwei ASGI-Apps.** `uvicorn` startet beide Server im selben Event-Loop (zwei `uvicorn.Server`-Instanzen mit `asyncio.gather`). Ressourcen (DB-Engine, HAClient, EventBus, Settings) werden genau einmal erzeugt und per Dependency Injection geteilt. Kein zweiter Worker.
- **Admin-Listener** bindet `0.0.0.0:8099` im Container (der Port ist nicht am Host veröffentlicht); jede Anfrage durchläuft `IngressGuard` (Peer-IP, Header-Eindeutigkeit, Rollenprüfung). `proxy_headers=False`.
- **Public-Listener** startet erst, wenn `instance.public_origin` gesetzt und die gewählte Betriebsart gültig ist (`tls`: Zertifikat lesbar; `proxy`: CIDRs gesetzt). Vorher antwortet der Port gar nicht.
- **HAClient** hält genau eine WS-Verbindung zum Core, abonniert `state_changed`, lädt Snapshot und Registries, führt `call_service` mit Kontext-ID aus und meldet Verbindungszustand. Nach Reconnect: Snapshot neu laden, Generation erhöhen, Schreibadapter bis zur Revalidierung von `get_config.unit_system` sperren.
- **EventBus** verteilt Upstream-Ereignisse in-process an alle Client-Verbindungen; jede Verbindung projiziert mit *ihrer* Session und der aktuellen Policyversion. Sperrung, Checkout und Publish erzeugen Bus-Ereignisse, auf die Verbindungen mit Snapshot-Neuaufbau oder Schließen reagieren.
- **Policy** ist eine reine Funktion ohne I/O (`decide`), die aus einem vorberechneten `PolicyIndex` (aus DB geladen, bei jeder Änderung neu gebaut) entscheidet. Dieselbe Funktion dient Aktionen, Projektion und Simulation.

### 3.1 Repository-Layout (öffentlich, `LL0rd/myha`)

```
myha/                          ← Git-Root und HA-App-Repository
├── repository.yaml            ← name, url, maintainer
├── README.md · LICENSE (Apache-2.0) · CHANGELOG.md
├── myha/                      ← Add-on-Ordner = Docker-Build-Kontext
│   ├── config.yaml · build.yaml · Dockerfile · DOCS.md · icon.png · logo.png
│   ├── rootfs/etc/s6-overlay/s6-rc.d/myha/…   (run-Skript, finish)
│   ├── rootfs/opt/myha/backup-export · backup-cleanup
│   ├── gateway/               ← Python-Projekt (pyproject.toml, uv.lock)
│   │   ├── myha_gateway/      ← Paket, siehe §4
│   │   ├── alembic/
│   │   └── tests/             ← pytest inkl. Abnahmematrix
│   ├── web/                   ← Vite + React + TypeScript (pnpm)
│   │   ├── index.html         ← Gast/Bewohner/Display-Einstieg
│   │   ├── admin.html         ← Admin-UI unter Ingress-Prefix
│   │   └── src/{ui,guest,admin,api,i18n,native}
│   └── simulator/             ← Fake HA Core + Supervisor für Dev und Tests
├── tests/e2e/                 ← Playwright
├── scripts/                   ← dev.sh, build-image.sh, install-on-ha.py
├── docs/                      ← setup.md, security.md, restore.md, device-matrix.md, adr/, reference/, superpowers/
└── .github/workflows/         ← ci.yml (lint, pytest, web build, e2e), image.yml (buildx → ghcr)
```

Der Add-on-Ordner enthält den gesamten Produktcode, weil der Supervisor nur diesen Ordner als Build-Kontext sieht. `web/` wird im Dockerfile (Multi-Stage, Node-Stage) gebaut und als statische Dateien in das Python-Image kopiert.

### 3.2 Repository-Layout (privat, `LL0rd/myha-apps`)

```
myha-apps/
├── vendor/myha                ← Git-Submodule auf LL0rd/myha (gepinnter Commit)
├── app/                       ← Capacitor-8-Projekt
│   ├── package.json · capacitor.config.ts
│   ├── src/                   ← native Shell: Instanzen, Scanner, Wandmodus, Einstellungen
│   ├── android/               ← Gradle-Projekt, Plugin MyhaTransport (Kotlin/OkHttp)
│   └── ios/                   ← Xcode-Projekt, Plugin MyhaTransport (Swift/URLSession), ungetestet
├── scripts/build-web.sh       ← baut vendor/myha/myha/web im Modus native nach app/www-panel
└── docs/device-matrix.md · store/
```

Die gemeinsame Web-UI wird **nicht** kopiert, sondern aus dem Submodule gebaut (`vite build --mode native`). Der native Build enthält keine Admin-Routen.

## 4. Backend-Entwurf (`myha_gateway`)

| Modul | Verantwortung | Wichtige Schnittstelle |
|---|---|---|
| `settings` | Pydantic-Settings aus Env/`/data/options.json`, Pfade, Defaults | `Settings` |
| `app` | Erzeugt `AdminApp`, `PublicApp`, Lifespan (Migration → Restore-Check → HAClient → Listener) | `create_admin_app()`, `create_public_app()`, `main()` |
| `db` | SQLAlchemy 2 (async, `aiosqlite`), WAL, `foreign_keys=ON`, `secure_delete=ON`, Alembic | `Session`, Modelle |
| `ha.client` | WS-Verbindung, Reconnect mit Backoff, `get_states`, Registries, `subscribe_events`, `call_service`, `get_config` | `HAClient`, `HAState`, `ServiceResult(context_id)` |
| `ha.supervisor` | `GET http://supervisor/info`, Versionsvergleich, `addons/self/info` (Ingress-URL) | `SupervisorInfo` |
| `adapters` | Je Kind (`light`, `switch`, `climate`, `cover`, `sensor`): Fähigkeiten aus HA-Attributen ableiten, Aktion → expliziter HA-Aufruf, Erwartung für Zustandsbestätigung | `Adapter.capabilities(state, registry)`, `Adapter.translate(action, params, state)`, `Adapter.expectation(...)` |
| `policy` | `PolicyIndex` (Grants, Denies, Fences je Principal/Gruppe), `decide`, effektive Parameterbereiche | `decide(ctx, resource_id, action, params, now) -> Decision` |
| `projection` | Typisierte Produktobjekte je Session (Positivliste Felder), Räume ohne sichtbare Ressourcen entfernen, Capabilities nur für erlaubte Schreibaktionen | `project_dashboard(session, snapshot)`, `project_resource(...)` |
| `events` | `EventBus`, Verbindungsregistry, Generation/Sequenz, Snapshot-Neuaufbau, Schließen bei Widerruf | `EventBus.publish`, `ClientConnection` |
| `auth.admin` | `IngressGuard`, Rollen-Cache (5 s), CSRF-Nachweis (HMAC über User-ID + Ingress-Pfad + Server-Secret, Lebensdauer 12 h) | `require_admin()` |
| `auth.public` | Invite-Redeem, Bewohner-Login (Argon2id, Rate Limit, gleichartige Fehler), Display-Pairing, Browser-Cookie `__Host-myha_session`, native Access/Refresh-Familien, WS-Tickets, Renewal | `require_session(purpose="access")` |
| `commands` | Idempotenz, Statusmaschine `accepted → sent → confirmed/failed/unknown`, Bestätigung über beobachtete Zustände (Timeout 10 s), Wiederherstellung als `unknown` nach Neustart | `CommandService.submit` |
| `stays` | Aufenthalte, Einladungen, Checkout, Verlängerung, Zimmerwechsel; alle Widerrufe transaktional | `StayService` |
| `preview` | Previewkontexte (`purpose=preview`, 15 min), Simulation | `PreviewService` |
| `audit` | Schreiben, Aggregation abgelehnter Massenanfragen (pro Session/Aktion/Minute), Aufbewahrung (Default 30 Tage, Job stündlich) | `audit(event)` |
| `backup` | Bereinigter Export (SQLite Online Backup → Positivlisten-Kopie in neue DB), Hooks, Restore-Modus | CLI `myha-gateway backup export|cleanup` |
| `setup` | Prüfungen des Assistenten (Gateway, HA, Admin-Identität, Public Origin/TLS, WSS, Gastgerätetest mit Test-QR) | `SetupService.run(check)` |
| `i18n` | Serverseitige Texte nur für Fehlertitel in E-Mails/QR-Druck; UI-Texte liegen im Web | — |

### 4.1 Policy-Semantik (Konkretisierung von Spez §6)

- **Aktionen** (geschlossene Menge): `metadata.read`, `state.read`, `history.read` (im MVP immer verweigert), `switch.on/off`, `light.on/off/set_level`, `climate.set_temperature/set_mode`, `cover.open/close/stop/set_position`.
- **Parameter-Schemas** (Pydantic, `extra="forbid"`): `level_pct: int 0..100`, `target_c: Decimal-String`, `mode: Enum[off, cool, heat]`, `position_pct: int 0..100`. Zusätzliche Felder, NaN, Unendlich, Strings mit Exponent werden abgelehnt.
- **Grant** = `{resources: [product_id], actions: [action], parameters?: {name: {intervals: [{min,max}], step?, origin?} | {allowed: [enum]}}}`. Ein Grant gilt nur, wenn Ressource *und* Aktion enthalten sind und *alle* Parameter der Anfrage von diesem Grant abgedeckt werden. Mehrere Grants bilden die Vereinigung vollständiger Regeln (A10).
- **Deny** = `{resources, actions}`; trifft ein Deny, ist die Entscheidung negativ (A12).
- **Fence** = `{scope: instance|unit|principal|session, resources?, action, parameters}`; Fences schneiden Intervalle und Enum-Mengen; sie können nie erlauben (A11).
- **Schrittprüfung:** `(value - origin) / step` muss ganzzahlig sein (Decimal-Arithmetik); Gerätegrenzen und Geräteschritt gehen zusätzlich ein. Kein Klemmen, kein Runden.
- **Effektive Capability** (für Projektion und UI): Vereinigung der Grant-Intervalle ∩ Fences ∩ Gerätebereich, abzüglich durch Deny entfernter Aktionen. Die UI erhält daraus `intervals`, `step`, `origin` bzw. `allowed`.
- **Decision-Codes (admin-sichtbar):** `GRANTED`, `NO_GRANT`, `DENIED_BY_RULE`, `FENCE_VIOLATION`, `PARAM_OUT_OF_RANGE`, `PARAM_STEP`, `PARAM_SCHEMA`, `RESOURCE_NOT_IN_SCOPE`, `RESOURCE_UNCONFIRMED`, `CAPABILITY_MISSING`, `ADAPTER_LOCKED`, `SESSION_INACTIVE`, `STAY_NOT_STARTED`, `STAY_ENDED`, `PREVIEW_CONTEXT`, `UNKNOWN_ACTION`, `MODE_WOULD_BYPASS_FENCE`.
- **Öffentliche Abbildung:** `RESOURCE_*`, `UNKNOWN_ACTION` → `NOT_FOUND`; `NO_GRANT`, `DENIED_BY_RULE`, `FENCE_VIOLATION`, `CAPABILITY_MISSING`, `MODE_WOULD_BYPASS_FENCE`, `PREVIEW_CONTEXT` → `ACTION_NOT_ALLOWED`; `PARAM_*` → `INVALID_PARAMETERS`; `SESSION_INACTIVE`, `STAY_*` → `ACCESS_EXPIRED`; `ADAPTER_LOCKED` → `UPSTREAM_UNAVAILABLE`.
- **Policyversion:** jede veröffentlichte Rollenversion, Zuweisung, Fence-, Bindungs- oder Gruppenänderung erhöht `instance.policy_version` monoton in derselben Transaktion; danach `policy.changed` auf dem Bus.

### 4.2 Adapter-Regeln

| Kind | Bestätigung der Bindung | Übersetzung | Erwartung |
|---|---|---|---|
| light | Domain `light`, kein Gruppen-/Template-Hinweis in Registry (Plattform `group`, `template`, `switch_as_x` → Review), `supported_color_modes` vorhanden | `light.turn_on` ohne Zusatzfelder; `light.turn_off`; `set_level` → `light.turn_on {brightness_pct}` bzw. `turn_off` bei 0 (verlangt `light.off`-Recht), positive Werte verlangen `light.on` | `state == on/off`, bei Level `brightness` ≈ Ziel ± 1 % |
| switch | Domain `switch`, Plattform nicht `group`/`template` | `switch.turn_on/off` | `state` |
| climate | `unit_system.temperature == "°C"`, `temperature`-Attribut vorhanden, kein `target_temp_high/low` im aktuellen Zustand, `min_temp/max_temp/target_temp_step` gelesen; Modusliste vom Admin aus `hvac_modes ∩ {off, cool, heat}` bestätigt | `climate.set_temperature {temperature}`; `climate.set_hvac_mode {hvac_mode}` | `temperature` bzw. `state` (= hvac_mode) |
| cover | Domain `cover`, `supported_features` enthält OPEN/CLOSE/STOP bzw. SET_POSITION; Plattform nicht `group`/`template` | `cover.open_cover/close_cover/stop_cover/set_cover_position {position}`; `set_position` 0/100 verlangen zusätzlich `cover.close`/`cover.open`, Zwischenwerte beide (A17) | `current_position` bzw. `state` |
| sensor | Domain `sensor`/`binary_sensor`, `device_class` und `unit_of_measurement` werden als typisierte Felder übernommen | nur Lesen | — |

Ein neu entdecktes Gerät, eine geänderte `unique_id`, ein Bereichswechsel oder geänderte `supported_features` setzen `resource_binding.status = needs_review` und sperren Schreibaktionen bis zur Admin-Bestätigung (A14–A16). Eine reine Umbenennung der `entity_id` bei gleicher Registry-ID wird übernommen und protokolliert.

### 4.3 Authentifizierung und Sessions

| Zugangsart | Ablauf | Artefakt |
|---|---|---|
| Admin | Ingress-Peer + `X-Remote-User-Id` + `config/auth/list` (aktiv und Owner oder `system-admin`) bei jeder Anfrage (Cache 5 s); Mutationen zusätzlich mit `X-MyHA-CSRF` (HMAC an User-ID und Ingress-Pfad gebunden, aus `GET /api/v1/admin/session`) und `Sec-Fetch-Site: same-origin`/fehlend | keine eigene Admin-Session; Live-Admin-WS prüft alle 5 s neu und schließt bei Herabstufung (A04) |
| Gast | `GET /join#i=<secret>` → Seite entfernt Fragment, `POST /api/v1/auth/redeem {invite_secret, device_label?, client: browser|native}` → atomarer Verbrauch (`UPDATE invite SET consumed_at=… WHERE id=… AND consumed_at IS NULL AND revoked_at IS NULL`) in derselben Transaktion wie `session INSERT` (A22) | Browser: Cookie `__Host-myha_session` (Secure, HttpOnly, SameSite=Lax, Path=/), Ablauf = min(Stay-Ende, konfigurierte Obergrenze). Native: Access-Token (15 min) + Refresh-Token (Familie, Rotation, Wiederverwendung sperrt Familie) |
| Bewohner | `POST /api/v1/auth/login {username, password}`; Normalisierung NFKC + casefold; Argon2id; Rate Limit 10/min je IP und 5/min je Benutzername; identische Fehlermeldung | wie Gast; Refresh-Familie max. 30 Tage |
| Display | Tablet: `POST /api/v1/auth/pair/start` → `{pairing_id, code (6 Ziffern), poll_token}`, Anzeige des Codes; Admin bestätigt in „People & groups“ mit Scope; Tablet pollt `POST /api/v1/auth/pair/poll` (Rate Limit, 10 min gültig) | langlebige, betreibergesteuerte Session; jederzeit widerrufbar |
| WS | `POST /api/v1/ws-ticket` (Session + CSRF) → einmaliges Ticket 30 s; Client sendet es als erste Nachricht `{"type":"auth","ticket":…}`; 5 s Auth-Timeout; Origin exakt gegen Public Origin | — |

CSRF für öffentliche Mutationen: Sitzungsgebundenes Token aus `GET /api/v1/me` im Header `X-MyHA-CSRF` plus Origin-Prüfung. Auth-Endpunkte leiten nie weiter (A49). Alle Secrets (Invite, Session, Refresh, Ticket) werden nur als SHA-256-Hash gespeichert; Vergleich zeitkonstant.

### 4.4 Live-Ereignisse und Befehle

- Verbindung erhält zuerst `snapshot` (Generation `conn_<n>`, Sequenz 0), danach `resource.updated`/`resource.removed` mit laufender Sequenz. Jedes Ereignis wird erst unmittelbar vor dem Senden gegen Session und `policy_version` projiziert; nach `policy.changed` folgt ein neuer Snapshot statt Deltas.
- `session.revoked` (bei Widerruf, Checkout, Ablauf) → Verbindung wird serverseitig innerhalb von 5 s geschlossen (A20, A25).
- Befehl: `POST /api/v1/actions` mit `Idempotency-Key` (UUID, Pflicht). Ablauf in einer DB-Transaktion: Session prüfen → `decide` → `command INSERT (status=accepted)` → Commit → HA-Aufruf → `sent` → Beobachtung → `confirmed|failed|unknown` → `command.updated`. Gleicher Schlüssel, gleicher Body → derselbe Befehl ohne zweiten Versand; anderer Body → `409 CONFLICT` (A31). Nach Neustart werden `accepted|sent` zu `unknown` (A32).
- Keine Offline-Warteschlange: Der Client sendet nur bei bestehender Verbindung; der Server lehnt Befehle ab, wenn der HAClient getrennt ist (`UPSTREAM_UNAVAILABLE`).

### 4.5 Fremdansicht

`POST /api/v1/admin/preview {target: {principal_id | invite_id | group_id}, session_id?, at?}` erzeugt eine `session` mit `purpose=preview`, `actor_admin_id`, Ablauf 15 min. `GET /api/v1/admin/preview/{id}/dashboard` ruft `project_dashboard` mit genau diesem Kontext auf; `POST …/simulate {resource_id, action, parameters}` ruft `decide` mit `now = at or server_time` und liefert `would_allow`, `code`, `normalized_parameters`, `matched_rules`. `PublicApp` lehnt Preview-Sessions an allen ausführenden Endpunkten ab (`PREVIEW_CONTEXT`, A30). Hypothetische Zeiten ändern nur die Policybewertung; Zustände werden als „aktuell gemessen“ beschriftet (A29).

### 4.6 Backup und Restore

- `backup_pre` → `myha-gateway backup export`: Online-Backup der Live-DB nach `/data/state/tmp/snapshot.sqlite`, daraus **neue leere DB** `/data/state/tmp/export.sqlite` mit Positivliste (`instance` ohne Secrets, `unit`, `resource_binding`, `role_template`, `role_version`, `group`, `membership`, `assignment` mit `status=disabled_pending_review`, `fence`, `dashboard`, `principal` ohne Credentials), Metadaten `export_generation`, `exported_at`, `schema_version`, `requires_admin_review=1`; `PRAGMA integrity_check`; atomar nach `/data/backup/restore.sqlite` verschieben. Lockdatei verhindert parallele Exporte.
- `backup_post` → `myha-gateway backup cleanup`: Export löschen; Fehler sichtbar im Admin-Status.
- Beim Start: existiert `/data/backup/restore.sqlite`, startet die App im **Restore-Modus**: Public-Listener bleibt zu, Admin sieht den Import-Review, bestätigt Bindungen und Zuweisungen, danach wird eine frische Live-DB mit neuer `security_generation` erzeugt; Sessions, Einladungen, Passwörter und Refresh-Familien existieren danach nicht (A39). Fehlt beides (Live-DB und Export), bleibt die App im Einrichtungszustand.
- `config.yaml`: `backup: hot`, `backup_exclude: ["state", "state/**"]` (relativ zu `/data`; das tatsächliche Archiv wird in WP11 geprüft, A47).

## 5. Datenmodell

Die Tabellen aus Spez §5 werden übernommen; Konkretisierungen:

| Tabelle | Ergänzung/Entscheidung |
|---|---|
| `instance` | Einzeiler: `public_origin`, `tls_mode`, `tls_cert_path`, `tls_key_path`, `proxy_cidrs`, `timezone`, `policy_version`, `security_generation`, `setup_state`, `server_secret` (für HMACs, nur hier) |
| `unit` | `id` (`unit_…`), `name`, `sort`, `template_id?` |
| `resource_binding` | `id` (`res_…`), `unit_id`, `kind`, `entity_id`, `registry_entity_id`, `device_id`, `area_id`, `label`, `icon`, `confirmed_capabilities` (JSON inkl. Modusliste, Bereich, Schritt), `capability_fingerprint`, `status` (`confirmed`, `needs_review`, `blocked`), `sort`, `card_size` |
| `principal` | `type` (`resident`, `guest`, `display`), `status`, `display_name`, `locale`, `theme` |
| `credential` | `principal_id` unique, `username_normalized` unique, `password_hash`, `changed_at`, `must_reset` |
| `group`, `membership` | wie Spez; `membership.valid_from/valid_to` |
| `role_template`, `role_version` | Versionen unveränderlich; `role_version.grants`, `denies` als JSON mit Schema; `published_at`, `based_on_version` |
| `assignment` | `subject_type` (`principal`/`group`), `subject_id`, `role_template_id`, `role_version`, `unit_ids` (Scope), `status` |
| `fence` | `scope_type`, `scope_id`, `resource_ids?`, `action`, `parameters` |
| `stay` | `unit_id`, `starts_at`, `ends_at` (UTC), `status` (`planned`, `active`, `checked_out`, `cancelled`), `external_ref?`, `role_template_id`, `role_version` |
| `invite` | `secret_hash`, `purpose` (`guest_join`), `stay_id`, `unit_ids`, `role_template_id`, `role_version`, `redeem_until`, `consumed_at`, `consumed_session_id`, `revoked_at`, `device_slot` |
| `session` | `token_hash` (Browser/Access), `family_id` (native Refresh), `principal_id`, `audience` (`browser`, `native`), `purpose` (`access`, `preview`, `pairing`), `stay_id?`, `unit_ids`, `not_before`, `not_after`, `last_seen_at`, `revoked_at`, `revoke_reason`, `csrf_secret`, `device_label`, `actor_admin_id?` |
| `refresh_token` | `family_id`, `token_hash`, `issued_at`, `rotated_at`, `used_at` (Wiederverwendung sperrt Familie) |
| `ws_ticket` | `ticket_hash`, `session_id`, `expires_at`, `used_at` |
| `pairing_request` | `code`, `poll_token_hash`, `expires_at`, `approved_session_id?` |
| `dashboard` | je Unit: Kartenreihenfolge, Größen, Theme-Default (JSON, versioniert) |
| `command` | wie Spez; `idempotency_key` unique mit `session_id`; `ha_context_id`; `expectation` JSON |
| `audit_event` | wie Spez; zusätzlich `aggregate_count` für zusammengefasste Ablehnungen |

Alle Zeitpunkte als UTC-ISO-Strings in SQLite; `starts_at` inklusiv, `ends_at` exklusiv. Optimistic Locking über `version`-Spalte auf allen admin-editierbaren Tabellen (`If-Match`/`expected_version` in der API).

## 6. API-Vertrag

Namespace `/api/v1`. Öffentliche Routen nur in `PublicApp`, Admin-Routen nur in `AdminApp`. OpenAPI aus Pydantic → `openapi-typescript` → `web/src/api/types.ts` (Build-Schritt, eingecheckt).

**Öffentlich (Spez §12.1 unverändert)** plus:
- `POST /api/v1/auth/pair/start`, `POST /api/v1/auth/pair/poll` (Display-Pairing).
- `GET /api/v1/public/config` liefert nur Instanzname, Sprachen, Theme-Defaults, Betreiberkontakt.

**Admin (`/api/v1/admin`)**
- `GET session` (CSRF-Token, Admin-Name, Supervisor-/Core-Versionen, Setup-Zustand)
- `units`, `units/{id}/bindings`, `bindings/{id}/confirm`, `bindings/{id}/review`, `ha/areas`, `ha/entities?area=&kind=` (Kandidatenliste aus Registry, nur Anzeige, keine Rechte)
- `principals`, `principals/{id}/credential` (Reset), `groups`, `groups/{id}/members`, `pairings`, `pairings/{id}/approve|reject`
- `roles`, `roles/{id}/versions`, `roles/{id}/draft` (PUT), `roles/{id}/diff` (Draft gegen veröffentlicht, mit betroffenen Aufenthalten/Sessions), `roles/{id}/publish` (mit `expected_version`, optional `rollout=true`)
- `assignments`, `fences`
- `stays`, `stays/{id}/invites` (POST erzeugt n Einladungen, Antwort enthält Secret genau einmal und SVG-QR), `stays/{id}/checkout`, `stays/{id}/extend`, `stays/{id}/move` (Zimmerwechsel erzeugt neuen Stay-Entwurf), `invites/{id}/revoke`
- `sessions`, `sessions/{id}/revoke`, `sessions/{id}/replace` (Widerruf + neue Einladung)
- `preview`, `preview/{id}/dashboard`, `preview/{id}/simulate`
- `dashboard/{unit_id}` (GET/PUT)
- `setup/checks`, `setup/checks/{name}/run`, `setup/origin` (PUT), `setup/tls` (PUT), `setup/guest-test` (erzeugt Test-QR, meldet Einlösung)
- `audit?filter=&cursor=`, `diagnostics`, `diagnostics/export` (Vorschau, dann Download), `backup/status`
- `restore/review`, `restore/apply`
- `WS admin/events` (Setup-Fortschritt, Pairing-Anfragen, Backup-Status, Live-Audit)

Fehlerformat überall `{"error": {"code": "...", "message": "...", "details"?: ...}}`; öffentliche Codes nur die acht aus Spez §12.1; Admin-Antworten dürfen Decision-Codes enthalten.

## 7. Web-Frontend

- **Stack:** Vite 6, React 18, TypeScript strict, React Router, Radix Primitives (Dialog, Select, Checkbox, Switch, Tabs, Tooltip), eigene CSS-Module mit Designtokens aus dem Mockup (`--bg #F7F5F0`, `--accent #C8A24A`, Dark- und Kontrast-Theme, Nunito Sans / JetBrains Mono). Kein Tailwind. Buildziel `es2019`, CSS ohne Container-Query-Pflicht (Grid/Flex/Media Queries), Autoprefixer.
- **Zwei Einstiege:** `admin.html` (Base `./`, läuft unter beliebigem Ingress-Prefix, liest `X-Ingress-Path` nicht, sondern nutzt relative Pfade und `document.baseURI`), `index.html` (Gast/Bewohner/Display, PWA mit Manifest; Service Worker cached nur `/assets/*`).
- **Gemeinsame UI (`src/ui`):** `GuestPanel` (Portierung des Mockups: Layouts `tiles|list|focus`, `wall`, `simulate`, `explain`, Status `online|stale|waiting|expired`), `ResourceCard` je Kind, `CommandStatus` (checking → accepted → confirmed | unknown | failed), `ConnectionPill`, `ThemeProvider`, `useI18n`.
- **Gast-Routen:** `/join` (Fragment lesen, `history.replaceState` ohne Fragment, Redeem), `/login`, `/pair`, `/` (Dashboard mit WS, Stale-Fenster 30 s, danach Zustände entfernen), `/ended`. Sprache: Browser → gespeicherte Wahl; Theme: System → Wahl.
- **Admin-Routen** (Mockup-Screens plus E10/E11): Rooms & devices (inkl. Layout-Tab), People & groups, Roles & rights (Matrix und Karten-Editor, Parameter, Fences, Live-Vorschau mit Explain), Preview as user, Stays & invites (QR-Dialog), Sessions & checkout, Setup assistant, Audit & diagnostics, Review & publish, Restore (nur im Restore-Modus).
- **Zugänglichkeit:** 44 px Touchziele (64 px Wand), sichtbarer Fokus, `aria-live` für Befehlsstatus, Tastaturpfad für Drag-and-drop (Pfeiltasten/Buttons), `prefers-reduced-motion`.
- **Sicherheit:** CSP `default-src 'self'; connect-src 'self' wss:; img-src 'self' data:; frame-ancestors 'none'` auf dem Public-Listener; Admin-Listener `frame-ancestors` nur für HA (Ingress bettet ein) – Header kommt vom Gateway. Beschriftungen immer als Text gerendert.
- **Typen:** generierte API-Typen; Dashboard-Schema als Zod-Schema validiert, bevor gerendert wird (gilt auch für den nativen Build).

## 8. HA-App-Paketierung

`myha/config.yaml` (Entwurf, Versionen folgen in WP0):

```yaml
name: myHA
version: "0.1.0"
slug: myha
description: Room-scoped guest, resident and wall-display access for Home Assistant
url: https://github.com/LL0rd/myha
arch: [aarch64, amd64]
init: false
startup: application
boot: auto
ingress: true
ingress_port: 8099
panel_icon: mdi:key-variant
panel_title: myHA
panel_admin: true
homeassistant_api: true
hassio_api: false
auth_api: false
ports:
  8443/tcp: null
ports_description:
  8443/tcp: Guest and resident access (HTTPS or behind your reverse proxy)
map:
  - type: ssl
    read_only: true
backup: hot
backup_pre: /opt/myha/backup-export
backup_post: /opt/myha/backup-cleanup
backup_exclude:
  - state
  - state/**
options:
  log_level: info
schema:
  log_level: list(debug|info|warning|error)
```

Keine Zugangsdaten in `options`. Das Dockerfile ist mehrstufig (Node-Stage baut `web/`, Python-Stage installiert `gateway/` mit `uv` in ein venv); `build.yaml` referenziert die HA-Python-Basisimages je Architektur. s6-Service startet `myha-gateway serve`. `GET http://supervisor/info` funktioniert laut Supervisor-Bypass-Liste auch ohne `hassio_api` – das ist ein S0-Nachweis; falls er scheitert, wird `hassio_api: true` mit `hassio_role: default` gesetzt und dokumentiert.

## 9. Native Apps (M3, privates Repo)

- **Shell:** Capacitor 8, TypeScript. Screens: Instanzen (mehrere, getrennte Sessions/Trustprofile), Instanz hinzufügen (QR oder Adresse), Panel (Web-UI aus dem Submodule im Modus `native`), Wandmodus-Einstellungen (Wachhalten, Querformat, Nachtthema, PIN für App-Einstellungen).
- **Transport:** Interface `Transport { request(), openSocket() }` im öffentlichen Web-Code; Browser-Implementierung nutzt `fetch`/`WebSocket`, native Implementierung ruft das Plugin `MyhaTransport`. Android: Kotlin, OkHttp mit `CertificatePinner` (SPKI-SHA-256, mindestens ein Ersatzpin), WebSocket über OkHttp, Pools und Sockets werden bei Trustprofil-Änderung geschlossen; keine Redirect-Verfolgung bei Auth-Endpunkten. iOS: Swift, `URLSession` mit `URLSessionWebSocketTask` und `urlSession(_:didReceive:)`-Pinning, Gerüst ohne Build-Nachweis.
- **Tokenablage:** Android Keystore-verschlüsselt (EncryptedSharedPreferences), iOS Keychain; Access-Token nur im Speicher.
- **QR-Scanner:** Web-Decoder `zxing-wasm` mit `getUserMedia` im WebView (keine Play Services nötig); Kameraberechtigung über Capacitor. Fallback manuelle Adresseingabe. Native QR-Payload: Join-URL mit Fragment `i=<secret>&inst=<instance_id>&fp=<spki-sha256>`; Browser ignorieren die Zusatzparameter. Das Secret wird erst nach erfolgreicher TLS-/Pinprüfung gesendet.
- **Gerätematrix:** Pixel-Emulator API 30/35, reales Android-Gerät von Dimitri, Fire HD 10 falls vorhanden; Ergebnisse in `docs/device-matrix.md`.

## 10. Sicherheit – Zuordnung Bedrohung → Mechanismus

| Bedrohung | Mechanismus | Abnahme |
|---|---|---|
| Gefälschte Ingress-Identität | Peer-IP, Header-Eindeutigkeit, Core-Rollenabfrage, getrennte Listener | A01–A05 |
| Fremde Ressource per ID | Scope-Prüfung vor jeder Auflösung, identischer `NOT_FOUND` | A06, A19 |
| Umgehung über Parameter/Services | Geschlossene Aktionsmenge, `extra=forbid`, Adapterübersetzung ohne Durchreichung | A07–A09, A13, A17, A18 |
| Rechteerweiterung durch Vereinigung | Vollständige Regeln, Deny-Vorrang, Fences als Schnitt | A10–A12 |
| Registry-Drift | Fingerprint der Fähigkeiten, `needs_review` sperrt Schreiben | A14–A16 |
| Veraltete Rechte in offenen Verbindungen | Projektion pro Ereignis, Snapshot nach `policy.changed`, Schließen nach Widerruf | A20, A21, A25, A52 |
| QR-Missbrauch | 256-Bit-Secret, Hash, atomarer Verbrauch, Einlösefrist, Fragment-Transport, keine Logs | A22–A26, A45 |
| Simulation führt aus | `purpose=preview` an allen Aktionsrouten abgelehnt | A28–A30 |
| Doppelte oder nachgeholte Befehle | Idempotency-Key, `unknown` nach Neustart, keine Offline-Queue | A31–A34 |
| CSRF/Origin/Replay | Custom-Header-Token je Session, Origin-Prüfung, einmalige WS-Tickets, Refresh-Rotation, keine Redirects | A35, A49 |
| Restore reaktiviert Zugänge | Positivlisten-Export ohne Secrets, neue Sicherheitsgeneration | A39, A47, A48 |
| Informationsabfluss in Logs | Redaction-Filter im Logging (Token, Secrets, Authorization), Audit ohne Payloads | A45 |

## 11. Tests und Qualitätssicherung

- **pytest** (`gateway/tests`): reine Policy-Tests tabellengetrieben (A06–A18 als parametrisierte Fälle), Auth-Tests mit gefälschten Peers/Headers (A01–A05), Integrationstests gegen den **HA-Simulator** (`simulator/`: WS `/api/websocket` mit `auth`, `get_states`, `subscribe_events`, `call_service`, Registries, `get_config`, `config/auth/list`; HTTP `/info` als Supervisor; Fixture mit Licht, Schalter, Klimaanlage 1 °C, Thermostat 0,5 °C, Cover, Sensoren; Fehlerinjektion: Gerät offline, Service-Fehler, Verbindungsabbruch), Ereignis- und Widerrufstests (A19–A21, A25, A52), Idempotenz und Neustart (A31–A34), Backup/Restore (A39, A47, A48), Zeitzonen inkl. Sommerzeitwechsel.
- **Web:** Vitest für Policy-nahe UI-Logik (Explain-Mapping, Stale-Fenster), `tsc --noEmit`, ESLint.
- **Playwright** (`tests/e2e`): Gateway gegen Simulator mit `MYHA_TRUSTED_INGRESS_PEER=127.0.0.1` und einem kleinen Ingress-Proxy, der Identitätsheader setzt: Admin legt Raum, Rolle, Aufenthalt und Einladung an → Gast löst QR ein → erlaubte Aktion sichtbar bestätigt, verbotene nicht vorhanden und per API abgelehnt → Checkout → „Access ended“. Viewports 320, 768, 1180, 2560 und 200 % Text (A41); Tastaturpfad (A42 teilweise automatisiert).
- **Last:** Skript mit 20 WS-Clients und 100 freigegebenen Ressourcen gegen den Simulator, Messung Policy-p95 und Ereignislatenz (Spez §16, A46 als Dokumentation).
- **CI:** GitHub Actions auf jedem Push: Ruff, mypy (strict für `policy`, `auth`, `adapters`), pytest mit Coverage-Schwelle 85 % für Policy/Auth, Web-Build, Playwright (Chromium). Image-Workflow baut amd64/aarch64 mit buildx.
- **Reale HA:** Nach M1 Installation auf Dimitris Instanz; Nachweise A01–A05 real über Ingress, vertikaler Pfad mit Govee-Licht und Klimaanlage im Wohnzimmer; Backup-Archiv prüfen (A47).

## 12. Entwicklungs- und Deploymentablauf

- `scripts/dev.sh` startet Simulator (:8123), Gateway (Admin :8099, Public :8443 im Modus `proxy` ohne TLS auf localhost) und Vite-Dev-Server mit Proxy; `MYHA_DATA_DIR=./.dev/data`.
- `scripts/build-image.sh` baut das amd64-Image lokal wie der Supervisor (gleiches Dockerfile, `BUILD_FROM`-Arg).
- `scripts/install-on-ha.py` nutzt WS `supervisor/api`: `POST /store/repositories {repository}`, `POST /store/addons/<slug>/install`, `POST /addons/<slug>/options`, `POST /addons/<slug>/start`; der Token wird aus `~/.claude.json` gelesen und nie ausgegeben. Vor jedem Lauf fragt das Skript nach Bestätigung.
- Commits auf `main` im öffentlichen Repo; Feature-Arbeit in kurzen Branches, wenn parallel gearbeitet wird. Jedes Arbeitspaket endet mit grünen Tests und Commit.

## 13. Arbeitspakete, Reihenfolge und Orchestrierung

Fable 5.1 schreibt den Implementierungsplan, dispatcht je Arbeitspaket Opus-5.5-Subagenten mit scharf abgegrenztem Auftrag, prüft deren Ergebnis (Tests laufen lassen, Diff lesen) und führt am Ende Code- und Sicherheitsreview sowie die Abnahmematrix aus.

| WP | Inhalt | Phase | Gate |
|---|---|---|---|
| 0 | Repo-Gerüst beider Repos, Tooling (uv, pnpm, Ruff, mypy, ESLint), CI-Skelett, Add-on-Skelett mit Hello-World-Listenern, Basisimage-Tag verifiziert | S0 | CI grün, Image baut |
| 1 | HA-Simulator; `HAClient` mit Reconnect, Snapshot, Registries, `call_service`; Supervisor-`/info` | S0 | Integrationstest gegen Simulator; `/info` ohne `hassio_api` real nachgewiesen |
| 2 | Datenmodell, Migrationen, `PolicyIndex`, `decide`, Adapter-Fähigkeiten und Übersetzung | M1 | A06–A18 grün |
| 3 | `IngressGuard`, Rollenprüfung, CSRF, Admin-App-Skelett, zwei Listener, Startreihenfolge | S0/M1 | A01–A05 gegen Fake-Peer grün |
| 4 | Öffentliche Auth: Invite/Redeem, Cookie/CSRF, Bewohner-Login, Display-Pairing, native Tokens, WS-Tickets, Renewal | M1/M2 | A22–A24, A35, A49 grün |
| 5 | Projektion, EventBus, WS-Events, Befehle mit Idempotenz und Bestätigung, Audit | M1 | A19–A21, A31–A34 grün |
| 6 | Preview und Simulation | M1 | A28–A30 grün |
| 7 | Web: Tokens, `GuestPanel`, Join/Login/Pair/Dashboard, i18n, PWA, CSP | M1 | Playwright: Join → Aktion → Bestätigung |
| 8 | Web-Admin: alle Screens aus §7 | M2 | Manuelle Abnahme je Screen, Tastaturpfad |
| 9 | Aufenthalte, Einladungen, QR, Checkout, Verlängerung, Zimmerwechsel, Rollout | M2 | A25–A27, A50, A51 grün |
| 10 | Setup-Assistent, TLS-Modi, Public Origin, Gastgerätetest | M2 | A44 dokumentiert |
| 11 | Backup-Hooks, Restore-Modus, Archivprüfung | M2 | A39, A47, A48 grün |
| 12 | Paketierung, Dockerfile, s6, Image-Workflow, Installation auf Dimitris HA, realer vertikaler Pfad | M1/M2 | Add-on läuft, Admin-SSO real, Licht und Klima real bedient |
| 13 | E2E-Suite, Lastskript, Doku (setup, security, restore, device-matrix), DOCS.md | M2 | A41, A42, A45, A46 dokumentiert |
| 14 | Natives Repo: Capacitor-App, `MyhaTransport` Android, iOS-Gerüst, Scanner, sichere Ablage, Wandmodus, Pinning-Tests | M3 | A36–A38, A43 auf Android/Emulator; iOS als ungetestet markiert |
| 15 | Fable-Abschlussreview: Code-Review, Security-Review, Abnahmematrix, Restrisiken | alle | Bericht an Dimitri |

## 14. Risiken und bewusst offene Punkte

- **Supervisor 2026.10.3** ist auf Dimitris Instanz noch nicht aktiv; bis dahin bleibt der Admin-Zugang laut E4 gesperrt. Die reale Prüfung in WP12 setzt das Update voraus.
- **`/info` ohne `hassio_api`**: aus dem Supervisor-Quelltext plausibel, aber erst in WP1 real nachgewiesen.
- **Cover** nur im Simulator; **Fahrenheit**, Doppelsollwerte und Presets bleiben gesperrt.
- **iOS** kann hier weder gebaut noch getestet werden; das Gerüst ist Startpunkt, kein Nachweis.
- **Alte Fire-WebViews**: Buildziel `es2019` und CSS-Fallbacks sind eine Annahme, bis ein Fire-Gerät getestet ist.
- **Selbstsignierte Zertifikate im Browser** funktionieren nur, wenn das Gastgerät die CA kennt; der Setup-Assistent warnt, verbietet es aber nicht (Dimitris Wunsch nach Freiheit).
- **Backup-Exclude-Muster** werden erst am realen Archiv verifiziert (A47); bis dahin gilt der Export als unbestätigt.
