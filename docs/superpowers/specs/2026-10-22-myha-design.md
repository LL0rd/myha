# myHA – Design-Dokument

**Stand:** 2026-10-22 (Revision 2 nach Spec-Review) · **Status:** Entwurf zur Freigabe · **Sprache:** Deutsch (Code, Identifier und Repo-Doku: Englisch)

Dieses Dokument legt fest, *wie* myHA gebaut wird. Es baut auf der Produkt- und Implementierungsspezifikation von ChatGPT auf (`docs/reference/2026-10-22-ha-access-ui-spezifikation-chatgpt.md`, im Folgenden „Spez“) und dem Claude-Design-Mockup (`docs/reference/design/`). Wo die Spez etwas verbindlich vorgibt, wird es hier nicht wiederholt, sondern referenziert. Wo die Spez offen war oder sich mit der realen Umgebung reibt, steht hier die Entscheidung mit Begründung.

## 1. Ziel und Umfang dieser Runde

myHA ist eine Home-Assistant-App (HAOS-Add-on) mit eigener Administration im HA-Ingress, serverseitiger Rechteverwaltung und einem responsiven Bedienpanel für Bewohner, Gäste und Wandtablets. Kernablauf (Spez §0): Raum auswählen → erlaubte Aktionen und Werte festlegen → Ansicht als Benutzer prüfen → Zugang ausgeben → Zugang gezielt oder automatisch beenden.

**Produktrahmen (Klarstellung Dimitri, 2026-10-22):** myHA ist ein Produkt für beliebige Betreiber (Haushalte, Ferienwohnungen, kleine Beherbergungsbetriebe, Spez §1), kein persönliches Werkzeug. Der Gateway-Kern ist öffentlich und von jedem HAOS-Nutzer über das App-Repository installierbar; die nativen Begleitapps für iOS, Android und Fire OS sind für den **öffentlichen Store-Vertrieb** (App Store, Google Play, Amazon Appstore) bestimmt. Dimitris Installation ist ausschließlich die **Referenzinstallation Nr. 1** für Nachweise gegen echte Hardware; nichts an Code, Fixtures, Defaults oder Dokumentation darf auf sie zugeschnitten sein.

**Im Umfang (Entscheidung Dimitri, 2026-10-22):** Phasen S0, M1, M2 und M3 der Spez §18.

| Phase | Ergebnis | Verbindliches Ende |
|---|---|---|
| S0 Nachweise | Minimal-App real auf Dimitris HA: Ingress-Identität und Peer, Supervisor-`/info` ohne `hassio_api`, Client-IP am Public-Port, Backup-Hooks mit Archivprüfung; Android-Transport-Spike (OkHttp HTTPS/WSS mit Pin); HA-Lese-/Schreibadapter gegen Simulator | Keine unbelegte Annahme trägt das weitere Design; Ergebnisse in `docs/s0-report.md` |
| M1 Vertikaler Kern | Ein Raum (Wohnzimmer), ein Govee-Licht, eine Klimaanlage; Principal/Gruppe/Policy; Gast-Redeem im Browser (Bewohner-Login folgt in M2); Fremdansicht; Aktion und gefiltertes Live-Update | Verbotene API-Aktion und fremder Zustand nachweislich blockiert; Simulation steuert kein Gerät |
| M2 Webpilot | GUI-Einrichtung, Gruppen/Rollen/Fences, Gerätebindungen, Dashboard-Editor, Gast-QR, Aufenthalte, Checkout, Audit, Backup/Restore | Abnahmefälle A01–A32, A34, A35, A39–A42, A44, A45, A47–A52 automatisiert bzw. dokumentiert; A33 entfällt (E21) |
| M3 Native | Android-, Fire-OS- und iOS-App mit lokaler UI, sicherer Speicherung, Scanner, nativer Verbindung, optionalem Pinning und store-fertigen Release-Pipelines | Android-Build läuft auf Emulator/Gerät; iOS-Build und Simulator-Tests laufen in CI auf GitHub-macOS-Runnern; Gerätetests auf echter Hardware und Store-Einreichungen setzen die Entwicklerkonten des Herausgebers voraus und werden als eigene Checkliste geführt |

**Nicht im Umfang:** M4 (Vorlagen-Rollout für viele Einheiten, Lizenzierung, Support-Tooling), Store-Einreichung, Raumpresets (E21), Konfigurationsexport (E23) und alles aus Spez §18.1. Ein Rollout einer neuen Rollenversion auf bestehende Zuweisungen und Aufenthalte ist dagegen Teil von M2, weil der Publish-Ablauf ohne ihn unvollständig wäre.

## 2. Geklärte offene Punkte

| Nr. | Punkt | Entscheidung | Begründung |
|---|---|---|---|
| E1 | Name, Slug | Produktname **myHA**, Add-on-Slug `myha`, Python-Paket `myha_gateway` | Vorgabe Dimitri; „HA Access UI“ war nur Arbeitstitel |
| E2 | Repositories | Öffentlich `LL0rd/myha` (Gateway, Web-UI, HA-App-Repository, Doku). Privat `LL0rd/myha-apps` (Capacitor-Projekte, native Plugins, Store-Assets) | Offener Kern, verkaufbare Komfort-Apps (Spez §17, Dimitri) |
| E3 | Lizenz | Apache-2.0 für `myha` (Annahme, Dimitri bestätigt bei Spec-Review); `myha-apps` proprietär | HA-Ökosystem-üblich, erlaubt kommerzielle Nutzung durch den Autor |
| E4 | Supervisor-Minimum | ≥ 2026.10.3 wird erzwungen. Dimitris Instanz steht auf 2026.10.2 (Stable-Release 2026.10.3 vom 2026-10-19 wird automatisch nachgezogen). Admin-UI zeigt bei zu altem Supervisor eine Update-Aufforderung und bleibt gesperrt | Spez §3.4; PR #7308 (`panel_admin`-Durchsetzung) ist in 2026.10.3 enthalten, verifiziert über GitHub |
| E5 | Admin-Identität | Peer-IP `172.30.32.2` am Admin-Listener, genau ein `X-Remote-User-Id`, danach Rollenprüfung über `config/auth/list` (aktiv, nicht systemgeneriert, Owner oder Gruppe `system-admin`). **Mutationen, sensible Ausgaben (Einladungs-Secrets, Audit, Sessions, Diagnose, Restore, Preview-Dashboards) und jeder Verbindungsaufbau prüfen ohne Cache**; reine Lese-GETs der Admin-UI dürfen ein Ergebnis bis 5 s wiederverwenden; Admin-WS prüft alle 5 s neu und schließt bei Herabstufung. Der neue Header `X-Remote-User-Groups` (Supervisor-PR #7398, gemerged 2026-10-21, noch nicht released) wird protokolliert, aber nie als Ersatz der Core-Abfrage verwendet | Spez §3.4 Schritt 5 (frische Prüfung vor jeder Mutation; 5 s nur für Abonnements), A04. `config/auth/list` gegen Dimitris Core 2026.10.2 getestet |
| E6 | Klima-Semantik | Erster Adapter: Einzel-Sollwert in °C (`climate.set_temperature` → `temperature`), **plus** `climate.set_mode` mit je Ressource admin-bestätigter Modusliste aus `off`, `cool`, `heat`. `auto`, `dry`, `fan_only`, Presets und Doppelsollwerte bleiben gesperrt | In Ferienwohnungen und Hotelzimmern sind Split-Klimaanlagen mit Kühl- und Heizmodus der Regelfall; ein Gastzugang ohne Moduswahl wäre dort nutzlos. Spez §6.4 erlaubt das bei geprüfter Semantik. Die Referenzinstallation (vier Klimaanlagen, Modi auto/cool/dry/fan_only/heat/off, 8–30 °C, Schritt 1 °C) dient der Validierung |
| E7 | Modus und Temperaturgrenzen | `climate.set_mode` wird ausschließlich als `climate.set_hvac_mode {hvac_mode}` übersetzt; kein Mitsenden eines Sollwerts. Die Absicherung erfolgt in `decide` (§4.1, Regel „Modus“): Ein Wechsel nach `cool`/`heat` ist nur erlaubt, wenn der aktuell gemeldete Sollwert innerhalb des Prüfbereichs liegt; `off` ist frei, sobald es gewährt ist. Unbekannter Sollwert zählt als außerhalb. Zusätzlich prüft die Erwartung nach dem Wechsel, ob der danach gemeldete Sollwert im Prüfbereich liegt; wenn nicht: Befehl `failed`, Audit-Eintrag, Bindung `needs_review` (Geräte mit Sollwert je Modus). WP1 und WP13 beobachten das Verhalten an Dimitris Klimaanlage | Spez §6.4 Absatz 3, A18; eine reine Entscheidungsfunktion mit Zustandseingang ist in Aktion und Simulation identisch |
| E8 | TLS/Erreichbarkeit | Zwei Betriebsarten: `tls` (eigener HTTPS-Listener, PEM-Zertifikat und Schlüssel aus `/ssl`, beliebiger Aussteller: Let's Encrypt über die HA-App, eigene CA, privates Zertifikat) und `proxy` (Klartext-Bind, nur konfigurierte Proxy-CIDRs, `X-Forwarded-Proto/Host` nur von diesen). Public Origin ist immer explizit. Setup-Assistent prüft beide. Entscheidung für den Pilot fällt beim Deployment | Dimitri: „frei möglich … Let's Encrypt, eigene CA, privates Zert“ |
| E9 | Cover-Tests | Es gibt keine Cover-Entities in Dimitris HA. Cover werden gegen den HA-Simulator getestet und im Pilot als „nicht nachgewiesen auf realer Hardware“ dokumentiert | Inventar 2026-10-22 |
| E10 | Dashboard-Editor | Minimal in „Rooms & devices“ integriert: Reihenfolge der Karten je Einheit, Beschriftung, Symbol, bevorzugte Kartengröße, Theme-Default. Kein freies Raster, keine Viewport-Overrides in dieser Runde. Einzige Quelle für Reihenfolge und Größe ist `dashboard.layout` je Einheit; die Bindung trägt nur Label und Symbol | Spez §11.3 MVP; Mockup hat keinen eigenen Editor-Screen |
| E11 | Personen und Gruppen | Zusätzlicher Admin-Screen „People & groups“ (Bewohner mit Passwort-Reset, Gruppen, Mitgliedschaften, Displays mit Pairing-Freigabe) | Spez §5/§8 verlangen es, Mockup zeigt es nicht |
| E12 | Icons und Fonts | Keine externen Ressourcen: `@tabler/icons-react` gebündelt, Fonts Nunito Sans und JetBrains Mono über `@fontsource` selbst gehostet | Spez §8.2 Punkt 4 (Einstiegsseite lädt nichts Fremdes); Mockup nutzt iconify-CDN, das wird nicht übernommen |
| E13 | Sprache | UI deutsch und englisch (Browsersprache, manuell umschaltbar). Code, Kommentare, README, ADRs englisch; dieses Design-Dokument und der Plan deutsch | Öffentliches Repo; Dimitri kommuniziert deutsch |
| E14 | Native Tokens schon in M2 | Access-/Refresh-Token-Pfad (`client=native`) inklusive Trustprofil-Endpunkt wird im Gateway bereits in M2 implementiert und getestet (§4.3 „Nativer Vertrag“), damit M3 nur den Client baut | M3 ist im Umfang; der Serverteil ist klein und testbar |
| E15 | Deployment auf der Referenzinstallation | Generisches Entwicklerskript über die Supervisor-API per WS `supervisor/api` (Repository hinzufügen, App installieren, Optionen setzen), konfiguriert über `HA_URL` und `HA_TOKEN`; Betreiber installieren regulär über die HA-GUI. Dimitris Zugangsdaten liegen nur lokal und ungetrackt | Kein SSH nötig; nichts Betreiberspezifisches im öffentlichen Repo |
| E16 | Build-Strategie | Dockerfile im Add-on-Ordner, damit der Supervisor lokal bauen kann (Pilot). Zusätzlich GitHub-Actions-Workflow, der `ghcr.io/ll0rd/myha-{arch}` für amd64 und aarch64 baut; sobald der läuft, bekommt `config.yaml` den `image:`-Eintrag | Erst lauffähig, dann bequem |
| E17 | Python-/Node-Versionen | Gateway Python 3.13 auf HA-Basisimage `ghcr.io/home-assistant/{arch}-base-python:3.13-alpine3.23-2026.10.1` (unveränderlicher Release-Tag wie bei den offiziellen Apps; Python 3.13.17; ohne Compiler im Image, alle Abhängigkeiten als musllinux-Wheels). s6-Dienst im `user-bundles.d`-Layout mit `dependencies.d/base`. Lokal `uv` 0.10 mit Python 3.13. Web mit Node 22 und `pnpm` 10.14 | Lokal liegt Python 3.14 vor, das Add-on-Image gibt 3.13 vor; Versionsgleichheit vermeidet Überraschungen; das alte `s6-rc.d/user`-Layout ist in s6-overlay 3.2.3 deprecated |
| E18 | Ingress-Peer in Tests | Vertrauenswürdige Peer-IP ist nur über die Umgebungsvariable `MYHA_TRUSTED_INGRESS_PEER` änderbar (Default `172.30.32.2`), niemals über App-Optionen. Tests setzen `127.0.0.1` | Spez §17: kein `ALLOW_ALL`; Tests brauchen trotzdem einen Weg |
| E19 | QR-Erzeugung | Serverseitig als SVG mit `segno` (reines Python), nur im Admin-Ingress ausgeliefert | Spez §8.2 (lokal, kein externer Bilddienst) |
| E20 | Passwort-Hashing | `argon2-cffi` (Argon2id), Parameter in S0 auf der HA-VM gemessen (Ziel ≈ 250 ms) und in `docs/security.md` festgehalten | Spez §8.1 |
| E21 | Raumpresets | Nicht in dieser Runde: keine Preset-Aktion, kein Datenmodell, keine UI. Abnahmefall A33 entfällt und wird im Bericht als „nicht anwendbar“ geführt | Spez §6.5 beschreibt Presets, §18.1 nennt „komfortable Raumpresetvorlagen“ als Ausbau nach dem Pilot; YAGNI |
| E22 | Backup-Positivliste | Export enthält zusätzlich zu Spez §15.3: `instance` ohne Secrets und ohne Trustprofil-Schlüssel, `group`, `membership`, `fence` mit Scope `instance`/`unit`/`principal`, `principal` nur Typ `resident`/`display` ohne Credentials, `assignment` als `disabled_pending_review`. Nicht exportiert: Gast-Principals, Session-Fences, Aufenthalte, Einladungen, Sessions, Befehle, Audit | Ohne Gruppen und Fences wäre ein Restore für Familienhaushalte wertlos; Gäste und Aufenthalte sind nach Restore ohnehin neu auszugeben |
| E23 | Konfigurationsexport | `POST /admin/config/export` (Spez §12.2) entfällt in dieser Runde; der bereinigte Backup-Export deckt den Bedarf | YAGNI für den Pilot |
| E24 | Admin-CSRF | Statt Origin-Prüfung: Pflicht-Header `X-MyHA-CSRF` (HMAC an User-ID, Ingress-Pfad und Tag gebunden, aus `GET /api/v1/admin/session`) plus `Sec-Fetch-Site`, das, wenn vorhanden, `same-origin` sein muss. Ein fehlender `Sec-Fetch-Site`-Header wird nur akzeptiert, weil der Pflicht-Custom-Header einen CORS-Preflight erzwingt, den der Admin-Listener nie beantwortet | Unter Ingress kennt das Gateway den HA-Origin nicht zuverlässig; Abweichung von Spez §3.4 Schritt 6 |
| E25 | Cookie-Name | `__Host-myha_<instance_short_id>_session` (8-stelliges Instanzkürzel), damit mehrere Instanzen auf demselben Host mit verschiedenen Ports getrennte Cookies haben | Spez §8.4 „individueller Instanzname“; Browser trennen Cookies nicht nach Port |
| E26 | Vertrauenswürdige Serverzeit | Gespeicherte Hochwassermarke: Das Gateway persistiert minütlich den letzten gesehenen Zeitstempel; liegt die Wanduhr beim Start oder im Betrieb davor, oder springt sie gegenüber der monotonen Uhr um mehr als 60 s, gilt `time_untrusted`. Zusätzlich prüft WP1, ob `GET /host/info` (`dt_synchronized`) mit `hassio_api: true` und `hassio_role: default` erreichbar ist; wenn ja, wird es als zweite Quelle genutzt. Bei `time_untrusted` werden Kontexte mit Aufenthaltsfenster gesperrt (`TIME_UNTRUSTED`), Admin sieht den Grund; Entsperrung automatisch bei Plausibilität | Spez §6.2; Core und Gateway teilen auf HAOS dieselbe Systemuhr, ein Vergleich mit `time_fired` wäre nur ein Zustellverzug |
| E28 | Zertifikatswechsel und Pins | Im Modus `tls` pollt das Gateway `mtime` von Zertifikat und Schlüssel (60 s) und lädt den `SSLContext` neu; der primäre Pin wird dabei neu berechnet und ins Trustprofil geschrieben. `policy: pin_required` → der native Client verweigert Verbindungen ohne passenden Pin; `pin_optional` → vorhandene Pins werden durchgesetzt, eine leere Pinliste bedeutet reine Systemvertrauensprüfung. Der Setup-Assistent warnt bei `pin_required` ohne Ersatzpin ausdrücklich, dass eine Let's-Encrypt-Erneuerung mit neuem Schlüssel native Clients aussperrt, und empfiehlt `pin_optional` oder einen vorab erzeugten Ersatzschlüssel | Let's Encrypt erneuert etwa alle 60 Tage, Certbot erzeugt standardmäßig neue Schlüssel (nicht extern verifiziert, konservativ angenommen) |
| E27 | Native Zertifikatsprüfung | Die native App akzeptiert nur systemvertrauenswürdige Ketten plus optionale SPKI-Pins. Eine private CA funktioniert in M3 nur im Browser auf Geräten, die diese CA kennen; ein nativer Enrollment-Modus für private CAs kommt später | Spez §13.3 letzter Absatz, A38 |

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
- **Admin-Listener** bindet `0.0.0.0:8099` im Container (der Port ist nicht am Host veröffentlicht); jede Anfrage durchläuft die Peer-Prüfung (`PeerGuardMiddleware`), jede Admin-API-Route zusätzlich `require_admin` (`AdminGuard`: Header-Eindeutigkeit, Supervisor-Version, Rollenprüfung nach E5). `proxy_headers=False`.
- **Public-Listener** startet erst, wenn `instance.public_origin` gesetzt und die gewählte Betriebsart gültig ist (`tls`: Zertifikat lesbar; `proxy`: CIDRs gesetzt). Vorher antwortet der Port gar nicht.
- **HAClient** hält genau eine WS-Verbindung zum Core, abonniert `state_changed`, lädt Snapshot und Registries, führt `call_service` mit Kontext-ID aus, meldet Verbindungszustand und prüft die Zeitplausibilität (E26). Nach Reconnect: Snapshot neu laden, Generation erhöhen, Schreibadapter bis zur Revalidierung von `get_config.unit_system` sperren (`adapter_locked`).
- **EventBus** verteilt Upstream-Ereignisse in-process an alle Client-Verbindungen; jede Verbindung projiziert mit *ihrer* Session und der aktuellen Policyversion. Sperrung, Checkout und Publish erzeugen Bus-Ereignisse, auf die Verbindungen mit Snapshot-Neuaufbau oder Schließen reagieren.
- **Policy** ist eine reine Funktion ohne I/O (`decide`), die aus einem vorberechneten `PolicyIndex` (aus DB geladen, bei jeder Änderung neu gebaut) und einem übergebenen Zustandsausschnitt entscheidet. Dieselbe Funktion dient Aktionen, Projektion und Simulation.

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
│   │   └── src/{ui,guest,admin,api,i18n,transport}
│   │   └── myha_simulator/    ← Fake HA Core + Supervisor für Dev und Tests (gleiches uv-Projekt)
├── tests/e2e/                 ← Playwright
├── scripts/                   ← dev.sh, build-image.sh, install-on-ha.py
├── docs/                      ← setup.md, security.md, restore.md, device-matrix.md, s0-report.md, adr/, reference/, superpowers/
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
├── spikes/transport-android/  ← S0-Spike: OkHttp HTTPS+WSS mit Pin gegen lokalen TLS-Testserver
├── scripts/build-web.sh       ← baut vendor/myha/myha/web im Modus native nach app/www-panel
└── docs/device-matrix.md · store/
```

Die gemeinsame Web-UI wird **nicht** kopiert, sondern aus dem Submodule gebaut (`vite build --mode native`). Der native Build enthält keine Admin-Routen.

## 4. Backend-Entwurf (`myha_gateway`)

| Modul | Verantwortung | Wichtige Schnittstelle |
|---|---|---|
| `settings` | Pydantic-Settings aus Env/`/data/options.json`, Pfade, Defaults | `Settings` |
| `app` | Erzeugt `AdminApp`, `PublicApp`, Lifespan (Restore-Check → Migration → Instanzzeile → HAClient → Listener) | `create_admin_app()`, `create_public_app()`, `main()` |
| `db` | SQLAlchemy 2 (async, `aiosqlite`), WAL mit `synchronous=FULL` (ein Stromausfall darf keinen Widerruf, Checkout, Redeem oder Refresh-Wechsel zurückrollen; ein fsync je Schreib-Commit), `foreign_keys=ON`, `secure_delete=ON`, `busy_timeout` 5 s, Schreibtransaktionen `BEGIN IMMEDIATE`, Alembic (App und CLI migrieren mit derselben Engine, atomar) | `Session`, Modelle |
| `ha.client` | WS-Verbindung, Reconnect mit Backoff, `get_states`, Registries, `subscribe_events`, `call_service`, `get_config`, Zeitplausibilität, `adapter_locked` | `HAClient`, `HAState`, `ServiceResult(context_id)` |
| `ha.supervisor` | `GET http://supervisor/info`, Versionsvergleich, `addons/self/info` (Ingress-URL) | `SupervisorInfo` |
| `adapters` | Je Kind (`light`, `switch`, `climate`, `cover`, `sensor`): Fähigkeiten aus HA-Attributen ableiten, Aktion → expliziter HA-Aufruf, Erwartung für Zustandsbestätigung, `StateView` aus HA-Zustand | `Adapter.capabilities(state, registry)`, `Adapter.translate(action, params)`, `Adapter.expectation(...)`, `Adapter.state_view(state)` |
| `policy` | `PolicyIndex`, effektiver Kontext, `decide`, effektive Parameterbereiche | `decide(ctx, resource, action, params, now, state_view) -> Decision` |
| `projection` | Typisierte Produktobjekte je Kontext (Positivliste Felder), Räume ohne sichtbare Ressourcen entfernen, Capabilities nur für erlaubte Schreibaktionen | `project_dashboard(ctx, snapshot)`, `project_resource(...)` |
| `events` | `EventBus`, Verbindungsregistry, Generation/Sequenz, Snapshot-Neuaufbau, Schließen bei Widerruf | `EventBus.publish`, `ClientConnection` |
| `auth.admin` | `AdminGuard` (Peer, Header, Supervisor-Version), Rollenprüfung (E5), CSRF-Nachweis (E24) | `require_admin(fresh: bool)` |
| `auth.public` | Invite-Redeem, Bewohner-Login (Argon2id, Rate Limit, gleichartige Fehler), Display-Pairing, Browser-Cookie, native Access/Refresh-Familien, WS-Tickets, Renewal, Trustprofil | `require_session(purpose="access")` |
| `commands` | Idempotenz, Statusmaschine `accepted → sent → confirmed/failed/unknown`, Bestätigung über beobachtete Zustände (Timeout 10 s), Wiederherstellung als `unknown` nach Neustart | `CommandService.submit` |
| `stays` | Aufenthalte, Einladungen, Checkout, Verlängerung, Zimmerwechsel, Rollout; alle Widerrufe transaktional | `StayService` |
| `preview` | Previewkontexte (`purpose=preview`, 15 min), Simulation | `PreviewService` |
| `audit` | Schreiben, Aggregation abgelehnter Massenanfragen (pro Session/Aktion/Minute), Aufbewahrung (Default 30 Tage, Job stündlich) | `audit(event)` |
| `backup` | Bereinigter Export (SQLite Online Backup → Positivlisten-Kopie in neue DB), Hooks, Restore-Modus | CLI `myha-gateway backup export|cleanup` |
| `setup` | Prüfungen des Assistenten (Gateway, HA, Admin-Identität, Public Origin/TLS, WSS, Gastgerätetest mit Test-QR, Client-IP-Plausibilität), Trustprofil (Pins) | `SetupService.run(check)` |

### 4.1 Policy-Semantik (Konkretisierung von Spez §6)

**Effektiver Kontext.** Jede Entscheidung und jede Projektion arbeitet auf einem `EffectiveContext`:

```
EffectiveContext(
  principal_ref,           # echter Principal oder hypothetical(kind=guest|group_member)
  role_bindings: [(role_template_id, role_version, unit_ids)],
  fences: [...],           # Scope instance, unit, stay, principal, session
  not_before, not_after,   # Gültigkeitsfenster (UTC)
  stay_id | None,
  purpose: access | preview,
  policy_version)
```

Quellen je Zugangsart:

| Zugangsart | Principal | `role_bindings` | Fenster | Fences |
|---|---|---|---|---|
| Bewohner | `principal.type=resident` | alle `assignment` des Principals und seiner Gruppen (Mitgliedschaft zum Zeitpunkt `now` gültig) | Session-Ablauf | instance, unit, principal, session |
| Display | `principal.type=display` | wie Bewohner | Session-Ablauf (betreibergesteuert) | wie Bewohner |
| Gast | `principal.type=guest`, **wird beim Redeem je Einladung (= je Gerät) erzeugt**, trägt `stay_id` und `invite_id` | genau eine Bindung aus dem **Aufenthalt**: `(stay.role_template_id, stay.role_version, [stay.unit_id])`; Einladungen tragen keine Rolle | `[stay.starts_at, min(stay.ends_at, session.not_after))`; vor `starts_at` existiert die Session als `waiting` ohne Geräterechte | instance, unit, session (Principal-Fences für Gäste nur über den Aufenthalt: `fence.scope=stay`) |
| Vorschau | je Ziel (siehe §4.5) | je Ziel | je Ziel, `now` optional hypothetisch | wie das Ziel |

Ein **Rollout** beim Publish (`rollout=true`) setzt `assignment.role_version` **und** `stay.role_version` aller Aufenthalte mit Status `draft`/`confirmed` dieser Vorlage auf die neue Version; ohne Rollout behalten bestehende Zuweisungen und Aufenthalte ihre Version, neue Aufenthalte erhalten die zuletzt veröffentlichte Version als Vorgabe.

- **Aktionen** (geschlossene Menge): `metadata.read`, `state.read`, `history.read` (im MVP immer verweigert), `switch.on/off`, `light.on/off/set_level`, `climate.set_temperature/set_mode`, `cover.open/close/stop/set_position`.
- **Parameter-Schemas** (Pydantic, `extra="forbid"`): `level_pct: int 0..100`, `target_c: Decimal-String` (max. eine Nachkommastelle, kein Exponent), `mode: Enum[off, cool, heat]`, `position_pct: int 0..100`. Zusätzliche Felder, NaN, Unendlich werden abgelehnt (`PARAM_SCHEMA`).
- **Grant** = `{resources: [product_id], actions: [action], parameters?: {name: {intervals: [{min, max, step, origin}]} | {allowed: [enum]}}}`. Schritt und Nullpunkt gehören zum Intervall. Ein Grant deckt eine Anfrage, wenn Ressource *und* Aktion enthalten sind und *alle* Parameter der Anfrage von diesem Grant abgedeckt werden. Mehrere Grants bilden die Vereinigung vollständiger Regeln (A10).
- **Deny** = `{resources, actions}`; trifft ein Deny, ist die Entscheidung negativ (A12).
- **Fence** = `{scope: instance|unit|stay|principal|session, resources?, action, parameters}`; Fences schneiden Intervalle (nur `min`/`max`; Fences tragen keinen Schritt, der effektive Schritt folgt der kgV-Regel in §4.1) und Enum-Mengen; sie können nie erlauben (A11).
- **Schrittprüfung:** `(value - origin) / step` muss ganzzahlig sein (Decimal-Arithmetik); Gerätegrenzen und Geräteschritt gehen zusätzlich ein. Der effektive Schritt eines Parameters ist das kleinste gemeinsame Vielfache aus Grant-Schritt, Geräteschritt und dem Schritt des Anfrageschemas (0,1 °C bzw. 1 %); Intervallgrenzen werden nach innen auf dieses Raster gezogen, sodass die UI nie einen nicht einstellbaren Wert anbietet. Kein Klemmen, kein Runden.
- **Effektive Capability** (für Projektion und UI): Vereinigung der Grant-Intervalle ∩ Fences ∩ Gerätebereich, abzüglich durch Deny entfernter Aktionen. Die UI erhält daraus `intervals` (je mit `step`, `origin`) bzw. `allowed`.
- **Regel „Modus“ (E7):** Für `climate.set_mode` gilt zusätzlich: (1) `mode` muss in der Grant-Menge ∩ bestätigter Modusliste liegen; (2) `off` ist damit erlaubt; (3) `cool`/`heat` werden gegen den **Prüfbereich** geprüft: ist `climate.set_temperature` gewährt, ist das der effektive `target_c`-Bereich; sonst Fences ∩ Gerätebereich; existiert weder Grant noch Fence, entfällt die Prüfung. `state_view.target_c` muss im Prüfbereich liegen; unbekannt (`None`) gilt als außerhalb. Bei mehreren getrennten effektiven Bereichen (z. B. 18–20 und 22–24) verlangt die Vorprüfung in `decide` die Mitgliedschaft in **einem** dieser Bereiche; die Erwartung nach dem Wechsel (`check_range`) nutzt die Hülle (18–24). Beide Prüfungen arbeiten mit den **ungerasterten** Grenzen (Grant ∩ Fences ∩ Gerätegrenzen, ohne Schritt), denn der Sollwert eines Geräts ist nicht an das Bedienraster gebunden: ein Gerät, das unter einer Fence ≤ 21,3 den Wert 21,2 wiederherstellt, ist konform. Verstoß → `MODE_WOULD_BYPASS_FENCE` (öffentlich `ACTION_NOT_ALLOWED`). Die Simulation nutzt dieselbe `state_view` wie die reale Ansicht.
- **Regel „Cover“ (analog E7):** `cover.open` ist nur erlaubt, wenn 100 % im effektiven `position_pct`-Bereich der Ressource liegt (Grant ∩ Fences), `cover.close` nur, wenn 0 % darin liegt; sonst `FENCE_VIOLATION` (eine Fence schließt den Wert aus) bzw. `PARAM_OUT_OF_RANGE` (der Grant schließt ihn aus). Ohne Positionsregel entfällt die Prüfung. Für beide Regeln (E7 und „Cover“) gilt: **Ein Deny erweitert nie** – ein Deny auf `climate.set_temperature` bzw. `cover.set_position` nimmt dem Gast die Aktion, die Grenzen der zugehörigen Grants und Fences bleiben für die Prüfung bestehen; die Projektion bietet `cover.open`/`cover.close` nicht an, wenn die Prüfung sie statisch ablehnt. Für Lichter gibt es keine entsprechende Regel: `light.on` stellt die vom Gerät gemerkte Helligkeit wieder her; eine `level_pct`-Fence begrenzt, was ein Gast **setzen** darf, nicht, was das Gerät wiederherstellt (bewusst akzeptiert).
- **Implizites `metadata.read`:** Eine Ressource ist sichtbar, wenn `state.read` oder eine Schreibaktion gewährt ist, die das Gerät physisch kann (auch unter `needs_review`, dann nur lesend); `history.read` und geräte-unmögliche Aktionen implizieren keine Sichtbarkeit.
- **Regel „Zeit“ (E26):** Kontexte mit Aufenthaltsfenster werden bei `instance.time_trusted = false` mit `TIME_UNTRUSTED` abgelehnt (öffentlich `UPSTREAM_UNAVAILABLE`); Kontexte ohne Fenster (Bewohner, Displays) sind nicht betroffen.
- **`state_view`** (vom Adapter aus dem aktuellen HA-Zustand gebildet, ohne I/O in `decide`): `availability`, `adapter_locked`, kindspezifische Werte (`power`, `level_pct`, `target_c`, `current_c`, `hvac_mode`, `position_pct`, `value`, `device_class`).
- **Decision-Codes (admin-sichtbar), geprüft in dieser Reihenfolge, erster Treffer gewinnt:** `PREVIEW_CONTEXT` (nur an ausführenden Endpunkten) → `SESSION_INACTIVE` → `TIME_UNTRUSTED` → `STAY_NOT_STARTED` → `STAY_ENDED` → `RESOURCE_NOT_IN_SCOPE` → `RESOURCE_UNCONFIRMED` (Status `blocked` oder nie bestätigt; verbirgt die Ressource vollständig) → `UNKNOWN_ACTION` → `PARAM_SCHEMA` → `DENIED_BY_RULE` → `NO_GRANT` (kein Grant nennt Ressource und Aktion) → `PARAM_OUT_OF_RANGE` (Aktion gewährt, Wert in keinem Intervall) → `PARAM_STEP` → `FENCE_VIOLATION` → `CAPABILITY_MISSING` (Gerät kann die Aktion nicht) → `RESOURCE_NEEDS_REVIEW` (Status `needs_review`, nur Schreibaktionen; Lesen bleibt erlaubt) → `MODE_WOULD_BYPASS_FENCE` → `ADAPTER_LOCKED` → `GRANTED`.
- **Öffentliche Abbildung:** `RESOURCE_NOT_IN_SCOPE`, `RESOURCE_UNCONFIRMED`, `UNKNOWN_ACTION` → `NOT_FOUND`; `NO_GRANT`, `DENIED_BY_RULE`, `FENCE_VIOLATION`, `CAPABILITY_MISSING`, `MODE_WOULD_BYPASS_FENCE`, `PREVIEW_CONTEXT` → `ACTION_NOT_ALLOWED`; `PARAM_*` → `INVALID_PARAMETERS`; `SESSION_INACTIVE`, `STAY_*` → `ACCESS_EXPIRED`; `ADAPTER_LOCKED`, `RESOURCE_NEEDS_REVIEW`, `TIME_UNTRUSTED` → `UPSTREAM_UNAVAILABLE`.
- **Drift-Erkennung (A14–A16):** Zwei Auslöser: jedes `state_changed` einer gebundenen Entity (Fähigkeits-Fingerprint aus den neuen Attributen gegen `capability_fingerprint`) und jedes Registry-Ereignis bzw. jeder Indexbau (`unique_id`, effektiver Bereich = `entity.area_id` oder sonst `device.area_id`, Existenz). Jede Abweichung setzt `status=needs_review` (persistiert, `policy_version` erhöht, `policy.changed`). Die reine Umbenennung der `entity_id` bei gleicher `unique_id` wird übernommen und protokolliert.
- **Policyversion:** jede veröffentlichte Rollenversion, Zuweisung, Fence-, Bindungs-, Aufenthalts- oder Gruppenänderung erhöht `instance.policy_version` monoton in derselben Transaktion; danach `policy.changed` auf dem Bus.

### 4.2 Adapter-Regeln

| Kind | Bestätigung der Bindung | Übersetzung | Erwartung |
|---|---|---|---|
| light | Domain `light`, kein Gruppen-/Template-Hinweis in Registry (Plattform `group`, `template`, `switch_as_x` → Bindung `blocked`; Blockgründe der Adapter: `group_entity`, `template_entity`, `wrapper_entity`, `dual_setpoint`, `unit_not_celsius`, `no_setpoint`, `invalid_range`), `supported_color_modes` vorhanden | `light.turn_on` ohne Zusatzfelder; `light.turn_off`; `set_level` → `light.turn_on {brightness_pct}` bzw. `turn_off` bei 0 (verlangt `light.off`-Recht), positive Werte verlangen `light.on` | `state == on/off`, bei Level `brightness` ≈ Ziel ± 1 % |
| switch | Domain `switch`, Plattform nicht `group`/`template` | `switch.turn_on/off` | `state` |
| climate | `unit_system.temperature == "°C"`, `temperature`-Attribut vorhanden, kein `target_temp_high/low` im aktuellen Zustand, `min_temp/max_temp/target_temp_step` gelesen; Modusliste vom Admin aus `hvac_modes ∩ {off, cool, heat}` bestätigt | `climate.set_temperature {temperature}`; `climate.set_hvac_mode {hvac_mode}` (E7) | `temperature` bzw. `state == hvac_mode`; bei `cool`/`heat` zusätzlich `temperature` im Prüfbereich, sonst `failed` + `needs_review` (E7) |
| cover | Domain `cover`, `supported_features` enthält OPEN/CLOSE/STOP bzw. SET_POSITION; Plattform nicht `group`/`template` | `cover.open_cover/close_cover/stop_cover/set_cover_position {position}`; `set_position` 0/100 verlangen zusätzlich `cover.close`/`cover.open`, Zwischenwerte beide (A17) | `current_position` bzw. `state` |
| sensor | Domain `sensor`/`binary_sensor`, `device_class` und `unit_of_measurement` werden als typisierte Felder übernommen | nur Lesen | — |

Ein neu entdecktes Gerät, eine geänderte `unique_id`, ein Bereichswechsel oder geänderte `supported_features` setzen `resource_binding.status = needs_review` und sperren Schreibaktionen bis zur Admin-Bestätigung (A14–A16). Eine reine Umbenennung der `entity_id` bei gleicher Registry-ID wird übernommen und protokolliert.

### 4.3 Authentifizierung und Sessions

| Zugangsart | Ablauf | Artefakt |
|---|---|---|
| Admin | Ingress-Peer + `X-Remote-User-Id` + `config/auth/list` (aktiv, nicht systemgeneriert, Owner oder `system-admin`) nach E5; Mutationen zusätzlich mit CSRF nach E24 | keine eigene Admin-Session; Live-Admin-WS prüft alle 5 s neu und schließt bei Herabstufung (A04) |
| Gast | `GET /join#i=<secret>` → Seite entfernt Fragment, `POST /api/v1/auth/redeem {invite_secret, device_label?, client: browser|native}` → in einer Transaktion: atomarer Verbrauch (`UPDATE invite SET consumed_at=… WHERE id=… AND consumed_at IS NULL AND revoked_at IS NULL`, A22), Gast-Principal anlegen, Session anlegen | Browser: Cookie nach E25 (Secure, HttpOnly, SameSite=Lax, Path=/), Ablauf = min(Stay-Ende, `session_limits.guest_browser_days`). Native: Access-Token (`native_access_minutes`, Default 15) + Refresh-Token (Familie, Rotation, Wiederverwendung sperrt Familie), Familie endet spätestens am Stay-Ende |
| Bewohner | `POST /api/v1/auth/login {username, password}`; Normalisierung NFKC + casefold; Argon2id; Rate Limit 10/min je IP und 5/min je Benutzername; identische Fehlermeldung | Browser-Cookie max. `session_limits.resident_browser_days` (Default 30); native Refresh-Familie max. `session_limits.resident_refresh_days` (Default 30) |
| Test-QR | `invite.purpose=guest_test` (aus dem Setup-Assistenten) erzeugt beim Redeem eine Session mit `purpose=test`, 10 min, ohne Rollenbindung und ohne Aufenthalt; erlaubt sind nur `/me`, `ws-ticket` und ein leerer `snapshot` | beweist Erreichbarkeit, TLS und WSS vom Gastgerät (A44) |
| Display | Tablet: `POST /api/v1/auth/pair/start` → `{pairing_id, code (6 Ziffern), poll_token}`, Anzeige des Codes; Admin bestätigt in „People & groups“ mit Scope; Tablet pollt `POST /api/v1/auth/pair/poll` (Rate Limit, 10 min gültig) | langlebige, betreibergesteuerte Session (`session_limits.display_days`, Default 365, sichtbarer Ablauf); jederzeit widerrufbar |
| WS | `POST /api/v1/ws-ticket` (Session, bei Browser-Audience CSRF) → einmaliges Ticket 30 s; Client sendet es als erste Nachricht `{"type":"auth","ticket":…}`; 5 s Auth-Timeout; Browser-Audience: `Origin` exakt gegen Public Origin | — |

CSRF für öffentliche Mutationen der Browser-Audience: Sitzungsgebundenes Token aus `GET /api/v1/me` im Header `X-MyHA-CSRF` plus `Origin`-Prüfung gegen Public Origin. Auth-Endpunkte leiten nie weiter (A49). Alle Secrets (Invite, Session, Refresh, Ticket, Poll-Token) werden nur als SHA-256-Hash gespeichert; Vergleich zeitkonstant.

**Nativer Vertrag (E14):**
- Access-Token im Header `Authorization: Bearer <token>`; Sessions mit `audience=native` setzen kein Cookie, verlangen kein CSRF-Token und ignorieren `Origin` (ein Bearer-Token wird vom Browser nie automatisch mitgeschickt; der Header erzwingt bei Fremdorigins einen Preflight ohne CORS-Antwort).
- `POST /api/v1/auth/refresh {refresh_token}` rotiert die Familie; Antwort enthält neues Access-/Refresh-Paar und das aktuelle Trustprofil.
- `GET /api/v1/me/trust` liefert `{instance_id, public_origin, pins: [{spki_sha256, role: primary|backup, valid_from}], policy: pin_required|pin_optional}` (Semantik nach E28); nur authentifiziert erreichbar. Clients übernehmen Pin-Änderungen ausschließlich hieraus oder durch neues Enrollment (Spez §13.3 Punkt 5).
- Der Einladungs-QR trägt im Fragment zusätzlich `inst=<instance_id>` und `fp=<primärer SPKI-Pin>`; Browser ignorieren die Zusatzparameter.
- Pins pflegt der Admin im Setup-Assistenten („Trust profile“): im Modus `tls` wird der primäre Pin aus dem konfigurierten Zertifikat berechnet und der Ersatzpin manuell eingetragen (z. B. aus dem nächsten Schlüssel); im Modus `proxy` berechnet der Assistent den primären Pin aus der Kette, die der Proxy aktuell ausliefert, und der Ersatzpin wird manuell eingetragen. Pins sind immer SPKI-Pins des Leaf-Zertifikats: ein Pin auf einen CA- oder Zwischenschlüssel würde jedes Zertifikat dieser CA akzeptieren (S0 Zeile 16), deshalb weist der Assistent beim Ersatzpin darauf hin, dass er zum künftigen Leaf-Schlüssel gehören muss. Entfernte Pins verschwinden aus dem Trustprofil; der Client schließt daraufhin Pools und Sockets und prüft neu (A37).

### 4.4 Live-Ereignisse und Befehle

- Verbindung erhält zuerst `snapshot` (Generation `conn_<n>`, Sequenz 0), danach `resource.updated`/`resource.removed` mit laufender Sequenz. Jedes Ereignis wird erst unmittelbar vor dem Senden gegen Kontext und `policy_version` projiziert; nach `policy.changed` folgt ein neuer Snapshot statt Deltas.
- `session.revoked` (bei Widerruf, Checkout, Ablauf) → Verbindung wird serverseitig innerhalb von 5 s geschlossen (A20, A25, A52).
- Befehl: `POST /api/v1/actions` mit `Idempotency-Key` (UUID, Pflicht). Ablauf: Session prüfen → `decide` → `command INSERT (status=accepted)` → Commit → **unmittelbar vor `call_service` erneut im Speicher prüfen: Session nicht widerrufen, `policy_version` unverändert, HAClient verbunden; sonst `failed` mit Grund** → HA-Aufruf → `sent` → Beobachtung → `confirmed|failed|unknown` → `command.updated`. Gleicher Schlüssel, gleicher Body → derselbe Befehl ohne zweiten Versand; anderer Body → `409 CONFLICT` (A31). Nach Neustart werden `accepted|sent` zu `unknown` (A32).
- Keine Offline-Warteschlange: Der Client sendet nur bei bestehender Verbindung; der Server lehnt Befehle ab, wenn der HAClient getrennt ist (`UPSTREAM_UNAVAILABLE`).

### 4.5 Fremdansicht

`POST /api/v1/admin/preview {target, at?}` mit `target` aus genau einer der Formen:

| Ziel | Kontext |
|---|---|
| `{principal_id, session_id?}` | echter Principal; mit `session_id` zusätzlich deren Scope-Obergrenze und Fenster; bei Gästen der zugehörige Aufenthalt |
| `{stay_id}` | hypothetischer Gast dieses Aufenthalts (auch `draft`), Rolle/Einheit/Fenster aus dem Aufenthalt, Stay-Fences |
| `{invite_id}` | wie `{stay_id}` der Einladung; zusätzlich deren `redeem_until` in der Anzeige (A50) |
| `{group_id}` | ausdrücklich markierte hypothetische Person, deren einzige Bindungen die Zuweisungen dieser Gruppe sind |

Die Antwort ist eine `session` mit `purpose=preview`, `actor_admin_id`, `preview_target` (JSON), `principal_id` nur bei echtem Principal, Ablauf 15 min. `GET /api/v1/admin/preview/{id}/dashboard` ruft `project_dashboard` mit genau diesem Kontext auf; `POST …/simulate {resource_id, action, parameters}` ruft `decide` mit `now = at or server_time` und derselben `state_view` wie die Live-Ansicht und liefert `would_allow`, `code`, `normalized_parameters`, `matched_rules`. `PublicApp` lehnt Preview-Sessions an allen ausführenden Endpunkten ab (`PREVIEW_CONTEXT`, A30). Hypothetische Zeiten ändern nur die Policybewertung; Zustände werden als „aktuell gemessen“ beschriftet (A29).

### 4.6 Backup und Restore

- `backup_pre` → `myha-gateway backup export`: Online-Backup der Live-DB nach `/data/state/tmp/snapshot.sqlite`, daraus **neue leere DB** `/data/state/tmp/export.sqlite` mit Positivliste nach E22, Metadaten `export_generation`, `exported_at`, `schema_version`, `requires_admin_review=1`; `PRAGMA integrity_check`; atomar nach `/data/backup/restore.sqlite` verschieben. Lockdatei verhindert parallele Exporte.
- `backup_post` → `myha-gateway backup cleanup`: Export löschen; Fehler sichtbar im Admin-Status.
- Beim Start: existiert `/data/backup/restore.sqlite`, startet die App im **Restore-Modus**: Public-Listener bleibt zu, Admin sieht den Import-Review, bestätigt Bindungen und Zuweisungen, danach wird eine frische Live-DB mit neuer `security_generation` erzeugt; Sessions, Einladungen, Passwörter und Refresh-Familien existieren danach nicht (A39). Fehlt beides (Live-DB und Export), bleibt die App im Einrichtungszustand.
- `config.yaml`: `backup: hot`, `backup_exclude: ["state", "state/**"]` (relativ zu `/data`; das tatsächliche Archiv wird in WP1 und WP12 geprüft, A47).

## 5. Datenmodell

Die Tabellen aus Spez §5 werden übernommen; Konkretisierungen:

| Tabelle | Ergänzung/Entscheidung |
|---|---|
| `instance` | Einzeiler: `id`, `short_id` (8 Zeichen, für Cookie-Name), `name`, `public_origin`, `tls_mode`, `tls_cert_path`, `tls_key_path`, `proxy_cidrs`, `trust_pins` (JSON), `timezone`, `policy_version`, `security_generation`, `setup_state`, `operator_contact`, `session_limits` (JSON: `guest_browser_days`, `resident_browser_days`, `resident_refresh_days`, `display_days`, `native_access_minutes`), `audit_retention_days`, `time_trusted`, `server_secret` (für HMACs, nur hier) |
| `unit` | `id` (`unit_…`), `name`, `sort` |
| `resource_binding` | `id` (`res_…`), `unit_id`, `kind`, `entity_id`, `registry_entity_id`, `device_id`, `area_id`, `label`, `icon`, `confirmed_capabilities` (JSON inkl. Modusliste, Bereich, Schritt), `capability_fingerprint`, `status` (`confirmed`, `needs_review`, `blocked`) |
| `principal` | `type` (`resident`, `guest`, `display`), `status` (`active` \| `disabled`; nur `active` hält Sessions aktiv, jeder andere Wert sperrt), `display_name`, `locale`, `theme`; bei `guest`: `stay_id`, `invite_id` |
| `credential` | `principal_id` unique, `username_normalized` unique, `password_hash`, `changed_at`, `must_reset` |
| `group`, `membership` | wie Spez; `membership.valid_from/valid_to` |
| `role_template`, `role_version` | Versionen unveränderlich; `role_version.grants`, `denies` als JSON mit Schema; `published_at`, `based_on_version`; Draft als `role_template.draft` (JSON, `draft_based_on`) |
| `assignment` | `subject_type` (`principal`/`group`), `subject_id`, `role_template_id`, `role_version`, `unit_ids` (Scope), `status` (`active`, `disabled_pending_review`) |
| `fence` | `scope_type` (`instance`, `unit`, `stay`, `principal`, `session`), `scope_id`, `resource_ids?`, `action`, `parameters` |
| `stay` | `unit_id`, `starts_at`, `ends_at` (UTC), `status` (`draft`, `confirmed`, `checked_out`, `cancelled`; „geplant“/„aktiv“ werden aus `confirmed` und `now` abgeleitet), `external_ref?`, `role_template_id`, `role_version`, `device_slots` |
| `invite` | `secret_hash`, `purpose` (`guest_join`, `guest_test`), `stay_id`, `device_slot`, `redeem_until`, `consumed_at`, `consumed_session_id`, `revoked_at`; **keine Rollenfelder** (Rolle kommt vom Aufenthalt) |
| `session` | `token_hash` (Browser/Access), `family_id` (native Refresh), `principal_id?`, `audience` (`browser`, `native`), `purpose` (`access`, `preview`, `pairing`, `test`), `stay_id?`, `unit_ids`, `not_before`, `not_after`, `last_seen_at`, `revoked_at`, `revoke_reason`, `csrf_secret`, `device_label`, `actor_admin_id?`, `preview_target?` (JSON) |
| `refresh_token` | `family_id`, `token_hash`, `issued_at`, `rotated_at`, `used_at` (Wiederverwendung sperrt Familie) |
| `ws_ticket` | `ticket_hash`, `session_id`, `expires_at`, `used_at` |
| `pairing_request` | `code`, `poll_token_hash`, `expires_at`, `approved_session_id?` |
| `dashboard` | je Unit: `layout` (JSON: Kartenreihenfolge, Größen, Theme-Default), `version` – einzige Quelle für Reihenfolge und Größe (E10) |
| `command` | wie Spez; `idempotency_key` unique mit `session_id`; `ha_context_id`; `expectation` JSON |
| `audit_event` | wie Spez; zusätzlich `aggregate_count` für zusammengefasste Ablehnungen |

Alle Zeitpunkte als UTC-ISO-Strings in SQLite; `starts_at` inklusiv, `ends_at` exklusiv. Optimistic Locking über `version`-Spalte auf allen admin-editierbaren Tabellen (`expected_version` in der API).

## 6. API-Vertrag

Namespace `/api/v1`. Öffentliche Routen nur in `PublicApp`, Admin-Routen nur in `AdminApp`. OpenAPI aus Pydantic → `openapi-typescript` → `web/src/api/types.ts` (Build-Schritt, eingecheckt).

**Öffentlich (Spez §12.1 unverändert)** plus:
- `POST /api/v1/auth/pair/start`, `POST /api/v1/auth/pair/poll` (Display-Pairing).
- `GET /api/v1/me/trust` (Trustprofil, §4.3).
- `GET /api/v1/public/config` liefert nur Instanzname, Sprachen, Theme-Defaults, Betreiberkontakt.

**Admin (`/api/v1/admin`)**
- `GET session` (CSRF-Token, Admin-Name, Supervisor-/Core-Versionen, Setup-Zustand, Zeitstatus)
- `units`, `units/{id}/bindings`, `bindings/{id}/confirm`, `bindings/{id}/review`, `ha/areas`, `ha/entities?area=&kind=` (Kandidatenliste aus Registry, nur Anzeige, keine Rechte)
- `principals`, `principals/{id}/credential` (Reset), `groups`, `groups/{id}/members`, `pairings`, `pairings/{id}/approve|reject`
- `roles`, `roles/{id}/versions`, `roles/{id}/draft` (PUT), `roles/{id}/diff` (Draft gegen veröffentlicht, mit betroffenen Aufenthalten/Sessions), `roles/{id}/publish` (mit `expected_version`, optional `rollout=true`)
- `assignments`, `fences`
- `stays` (POST erzeugt `draft`), `stays/{id}/confirm`, `stays/{id}/invites` (POST erzeugt n Einladungen, Antwort enthält Secret und SVG-QR genau einmal; setzt `draft` → `confirmed`), `stays/{id}/checkout`, `stays/{id}/extend`, `stays/{id}/move` (Zimmerwechsel erzeugt neuen `draft`), `invites/{id}/revoke`
- `sessions`, `sessions/{id}/revoke`, `sessions/{id}/replace` (Widerruf + neue Einladung)
- `preview`, `preview/{id}/dashboard`, `preview/{id}/simulate`
- `dashboard/{unit_id}` (GET/PUT)
- `session` (GET: `{user, csrf_token, supervisor_version, core_version, setup_state, time_trusted}`, Lesecache 5 s), `session/check` (POST, frische Rollenprüfung + CSRF, 204; dient der UI als „bin ich noch Admin“-Prüfung)
- `setup/checks`, `setup/checks/{name}/run`, `setup/origin` (PUT), `setup/tls` (PUT), `setup/trust` (GET/PUT Pins), `setup/guest-test` (erzeugt Test-QR mit `purpose=guest_test`, meldet Einlösung)
- `audit?filter=&cursor=`, `diagnostics`, `diagnostics/export` (Vorschau, dann Download), `backup/status`
- `restore/review`, `restore/apply`
- `WS admin/events` (Setup-Fortschritt, Pairing-Anfragen, Backup-Status, Live-Audit)

Fehlerformat überall `{"error": {"code": "...", "message": "...", "details"?: ...}}`, auch für unbehandelte Serverfehler (`500` → `UPSTREAM_UNAVAILABLE`, Nachricht „Internal error“, nie Stacktraces) und für `405` (→ `ACTION_NOT_ALLOWED`); öffentliche Codes nur die acht aus Spez §12.1; Admin-Antworten dürfen Decision-Codes enthalten. Beide Apps laufen mit `redirect_slashes=False`: Auth- und API-Endpunkte leiten nie weiter. A26 wird so geprüft: Es gibt keinen statischen Zimmer-QR; die Einstiegsseite ohne gültiges Secret gewährt nichts.

## 7. Web-Frontend

- **Stack:** Vite 6, React 18, TypeScript strict, React Router, Radix Primitives (Dialog, Select, Checkbox, Switch, Tabs, Tooltip), eigene CSS-Module mit Designtokens aus dem Mockup (`--bg #F7F5F0`, `--accent #C8A24A`, Dark- und Kontrast-Theme, Nunito Sans / JetBrains Mono). Kein Tailwind. Buildziel `es2019`, CSS ohne Container-Query-Pflicht (Grid/Flex/Media Queries), Autoprefixer.
- **Zwei Einstiege:** `admin.html` (Base `./`, läuft unter beliebigem Ingress-Prefix, nutzt relative Pfade und `document.baseURI`), `index.html` (Gast/Bewohner/Display, PWA mit Manifest; Service Worker cached nur `/assets/*`).
- **Transport-Abstraktion (`src/transport`):** Interface `Transport { request(), openSocket() }`; Browser-Implementierung mit `fetch`/`WebSocket` und Cookie/CSRF; native Implementierung (Modus `native`) ruft das Capacitor-Plugin `MyhaTransport` und sendet Bearer-Token.
- **Gemeinsame UI (`src/ui`):** `GuestPanel` (Portierung des Mockups: Layouts `tiles|list|focus`, `wall`, `simulate`, `explain`, Status `online|stale|waiting|expired`), `ResourceCard` je Kind, `CommandStatus` (checking → accepted → confirmed | unknown | failed), `ConnectionPill`, `ThemeProvider`, `useI18n`.
- **Gast-Routen:** `/join` (Fragment lesen, `history.replaceState` ohne Fragment, Redeem), `/login`, `/pair`, `/` (Dashboard mit WS, Stale-Fenster 30 s, danach Zustände entfernen), `/ended`. Sprache: Browser → gespeicherte Wahl; Theme: System → Wahl.
- **Admin-Routen** (Mockup-Screens plus E10/E11): Rooms & devices (inkl. Layout-Tab), People & groups, Roles & rights (Matrix und Karten-Editor, Parameter, Fences, Live-Vorschau mit Explain), Preview as user, Stays & invites (QR-Dialog), Sessions & checkout, Setup assistant (inkl. Trust profile), Audit & diagnostics, Review & publish, Restore (nur im Restore-Modus).
- **Zugänglichkeit:** 44 px Touchziele (64 px Wand), sichtbarer Fokus, `aria-live` für Befehlsstatus, Tastaturpfad für Drag-and-drop (Pfeiltasten/Buttons), `prefers-reduced-motion`.
- **Sicherheit:** CSP `default-src 'self'; connect-src 'self' wss:; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'` auf dem Public-Listener (`connect-src` zusätzlich `ws:`, solange der Public Origin `http://` ist, also nur im Dev-/Proxy-Betrieb); Admin-Listener gleiche Direktiven mit `frame-ancestors 'self'` (der Ingress-Proxy liefert die Seite unter dem HA-Origin aus) und immer `ws:` (der HA-Origin hinter dem Ingress darf im LAN unverschlüsselt sein). Beide Listener setzen `X-Content-Type-Options: nosniff` und `Referrer-Policy: no-referrer`; API-Antworten tragen `Cache-Control: no-store`, HTML `no-store`, gehashte Assets `immutable`. Die Bundle-Auslieferung ist eine eigene, explizite Implementierung (kein `StaticFiles(html=True)`): nur reguläre Dateien innerhalb des jeweiligen Bundles, Pfade bis 255 Zeichen, keine Dotfiles (auch `/.well-known` bleibt gesperrt, bis eine eigene Route es braucht), keine Symlinks nach außen, keine Verzeichnislisten, keine Redirects; Guest-Bundle mit `base "/"`, Admin-Bundle mit `base "./"`. Guest- und Admin-Bundle werden getrennt gebaut (`dist/guest`, `dist/admin`), damit der Public-Listener keine Admin-Chunks ausliefert. Beschriftungen immer als Text gerendert.
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

Keine Zugangsdaten in `options`. Das Dockerfile ist mehrstufig (Node-Stage baut `web/`, Python-Stage installiert `gateway/` mit `uv` in ein venv); `build.yaml` referenziert die HA-Python-Basisimages je Architektur. s6-Service startet `myha-gateway serve`. `GET http://supervisor/info` steht laut Supervisor-Middleware (`api_bypass` enthält `/info`) jedem App-Token offen; der reale Nachweis gehört zu S0. Falls er scheitert, wird `hassio_api: true` mit `hassio_role: default` gesetzt und dokumentiert.

## 9. Native Apps (M3, privates Repo)

- **Shell:** Capacitor 8 (8.5.x), TypeScript. Screens: Instanzen (mehrere, getrennte Sessions/Trustprofile), Instanz hinzufügen (QR oder Adresse), Panel (Web-UI aus dem Submodule im Modus `native`), Wandmodus-Einstellungen (Wachhalten, Querformat, Nachtthema, PIN für App-Einstellungen).
- **Transport:** Plugin `MyhaTransport` hinter dem `Transport`-Interface aus §7. Android: Kotlin, OkHttp mit `CertificatePinner` (SPKI-SHA-256 aus dem Trustprofil, primär und Ersatz), WebSocket über OkHttp, Pools und Sockets werden bei Trustprofil-Änderung geschlossen (der Transport führt offene WebSockets, schließt sie mit 1012 und verwirft danach eintreffende Frames; laufende Aufrufe enden unter dem alten Profil, deshalb führt WP15 eine Call-Registry); keine Redirect-Verfolgung auf allen Pfaden, und das Plugin nimmt nur Pfade relativ zum Public Origin an, keine freien URLs (OkHttp pinnt nur Hostnamen mit Eintrag, ein Redirect auf einen anderen Namen oder ein IP-Literal liefe ungepinnt; S0 Zeile 16). iOS: Swift, `URLSession` mit `URLSessionWebSocketTask` und `urlSession(_:didReceive:)`-Pinning, gebaut und im Simulator getestet auf GitHub-macOS-Runnern. Nur systemvertrauenswürdige Ketten (E27).
- **Tokenablage:** Android Keystore-verschlüsselt (EncryptedSharedPreferences), iOS Keychain; Access-Token nur im Speicher.
- **QR-Scanner:** Web-Decoder `zxing-wasm` mit `getUserMedia` im WebView (keine Play Services nötig); Kameraberechtigung über Capacitor. Fallback manuelle Adresseingabe. Das Secret wird erst nach erfolgreicher TLS-/Pinprüfung gesendet; danach lädt der Client `GET /api/v1/me/trust` und speichert das Trustprofil je Instanz.
- **Gerätematrix:** Pixel-Emulator API 30/35, reales Android-Gerät, Fire HD 10 falls vorhanden; Ergebnisse in `docs/device-matrix.md`.
- **Store-Vertrieb (Spez §13.5):** Ziel sind veröffentlichte Apps in App Store, Google Play und Amazon Appstore. Teilplan 4 liefert dafür store-fertige Pipelines: signierte Release-Builds über CI (Android/Fire auf Linux-Runnern, iOS auf GitHub-macOS-Runnern mit `xcodebuild`, Signaturmaterial ausschließlich als CI-Secrets), Store-Metadaten und Screenshots, Datenschutzerklärung, Review-Demo (Demo-Instanz mit Testzugang für Store-Prüfer), Privacy-Manifest (iOS) und Data-Safety-Angaben (Play). Die Einreichung selbst erfolgt manuell über die Entwicklerkonten des Herausgebers; Voraussetzungen (Apple Developer Program, Google Play Console, Amazon Developer) werden in Teilplan 4 als Vorbedingungen geführt.

## 10. Sicherheit – Zuordnung Bedrohung → Mechanismus

| Bedrohung | Mechanismus | Abnahme |
|---|---|---|
| Gefälschte Ingress-Identität | Peer-IP, Header-Eindeutigkeit, Core-Rollenabfrage ohne Cache bei Mutationen, getrennte Listener | A01–A05 |
| Fremde Ressource per ID | Scope-Prüfung vor jeder Auflösung, identischer `NOT_FOUND` | A06, A19 |
| Umgehung über Parameter/Services | Geschlossene Aktionsmenge, `extra=forbid`, Adapterübersetzung ohne Durchreichung | A07–A09, A13, A17, A18 |
| Rechteerweiterung durch Vereinigung | Vollständige Regeln, Deny-Vorrang, Fences als Schnitt | A10–A12 |
| Registry-Drift | Fingerprint der Fähigkeiten, `needs_review` sperrt Schreiben | A14–A16 |
| Veraltete Rechte in offenen Verbindungen | Projektion pro Ereignis, Snapshot nach `policy.changed`, Schließen nach Widerruf, Prüfung vor `call_service` | A20, A21, A25, A52 |
| QR-Missbrauch | 256-Bit-Secret, Hash, atomarer Verbrauch, Einlösefrist, Fragment-Transport, keine Logs | A22–A26, A45 |
| Simulation führt aus | `purpose=preview` an allen Aktionsrouten abgelehnt | A28–A30 |
| Doppelte oder nachgeholte Befehle | Idempotency-Key, `unknown` nach Neustart, keine Offline-Queue | A31, A32, A34 |
| CSRF/Origin/Replay | Custom-Header-Token je Session, Origin-Prüfung (Browser), einmalige WS-Tickets, Refresh-Rotation, keine Redirects | A35, A49 |
| Restore reaktiviert Zugänge | Positivlisten-Export ohne Secrets, neue Sicherheitsgeneration | A39, A40, A47, A48 |
| Informationsabfluss in Logs | Redaction-Filter im Logging (Token, Secrets, Authorization), Audit ohne Payloads | A45 |
| Falscher TLS-Endpunkt (nativ) | Systemkette plus Pins aus dem Trustprofil, Pools schließen bei Änderung | A36–A38 |

## 11. Tests und Qualitätssicherung

- **pytest** (`gateway/tests`): reine Policy-Tests tabellengetrieben (A06–A18 als parametrisierte Fälle inkl. Modusregel), Auth-Tests mit gefälschten Peers/Headers (A01–A05), Integrationstests gegen den **HA-Simulator** (`simulator/`: WS `/api/websocket` mit `auth`, `get_states`, `subscribe_events`, `call_service`, Registries, `get_config`, `config/auth/list`; HTTP `/info` als Supervisor; Fixture mit Licht, Schalter, Klimaanlage 1 °C, Thermostat 0,5 °C, Cover, Sensoren; Fehlerinjektion: Gerät offline, Service-Fehler, Verbindungsabbruch, Zeitversatz), Ereignis- und Widerrufstests (A19–A21, A25, A52), Idempotenz und Neustart (A31, A32, A34), Backup/Restore (A39, A40, A47, A48), Zeitzonen inkl. Sommerzeitwechsel, nativer Tokenpfad (Rotation, Wiederverwendung, Trustprofil).
- **Web:** Vitest für Policy-nahe UI-Logik (Explain-Mapping, Stale-Fenster), `tsc --noEmit`, ESLint.
- **Playwright** (`tests/e2e`): Gateway gegen Simulator mit `MYHA_TRUSTED_INGRESS_PEER=127.0.0.1` und einem kleinen Ingress-Proxy, der Identitätsheader setzt: Admin legt Raum, Rolle, Aufenthalt und Einladung an → Gast löst QR ein → erlaubte Aktion sichtbar bestätigt, verbotene nicht vorhanden und per API abgelehnt → Checkout → „Access ended“. Viewports 320, 768, 1180, 2560 und 200 % Text (A41); Tastaturpfad (A42 teilweise automatisiert).
- **Last:** Skript mit 20 WS-Clients und 100 freigegebenen Ressourcen gegen den Simulator, Messung Policy-p95 und Ereignislatenz (Spez §16, A46 als Dokumentation).
- **CI:** GitHub Actions auf jedem Push: Ruff, mypy (strict für `policy`, `auth`, `adapters`), pytest mit Coverage-Schwelle 85 % für Policy/Auth, Web-Build, Playwright (Chromium). Image-Workflow baut amd64/aarch64 mit buildx.
- **Reale HA:** S0 mit der Minimal-App (siehe WP1); nach M1 Installation der echten App auf Dimitris Instanz; Nachweise A01–A05 real über Ingress, vertikaler Pfad mit Govee-Licht und Klimaanlage im Wohnzimmer; Backup-Archiv prüfen (A47).

## 12. Entwicklungs- und Deploymentablauf

- `scripts/dev.sh` startet Simulator (:8123), Gateway (Admin :8099, Public :8443 im Modus `proxy` ohne TLS auf localhost) und Vite-Dev-Server mit Proxy; `MYHA_DATA_DIR=./.dev/data`.
- `scripts/build-image.sh` baut das amd64-Image lokal wie der Supervisor (gleiches Dockerfile, `BUILD_FROM`-Arg).
- `scripts/install-on-ha.py` (generisches Entwicklerwerkzeug, konfiguriert über `HA_URL` und `HA_TOKEN`) nutzt WS `supervisor/api`: `POST /store/repositories {repository}`, `POST /store/addons/<slug>/install`, `POST /addons/<slug>/options`, `POST /addons/<slug>/start`; der Token wird nie ausgegeben, Zugangsdaten der Referenzinstallation liegen nur in der ungetrackten Datei `.dev/ha.env`. Vor jedem mutierenden Lauf fragt das Skript nach Bestätigung.
- Commits auf `main` im öffentlichen Repo; Feature-Arbeit in kurzen Branches, wenn parallel gearbeitet wird. Jedes Arbeitspaket endet mit grünen Tests und Commit.

## 13. Arbeitspakete, Reihenfolge und Orchestrierung

Fable 5.1 schreibt den Implementierungsplan, dispatcht je Arbeitspaket Opus-5.5-Subagenten mit scharf abgegrenztem Auftrag, prüft deren Ergebnis (Tests laufen lassen, Diff lesen) und führt am Ende Code- und Sicherheitsreview sowie die Abnahmematrix aus. Die Pakete 5 und 9 sind bewusst geteilt; der Plan darf weiter teilen.

| WP | Inhalt | Phase | Gate |
|---|---|---|---|
| 0 | Repo-Gerüst beider Repos (GitHub anlegen, Submodule), Tooling (uv, pnpm, Ruff, mypy, ESLint), CI-Skelett, Add-on-Skelett mit zwei Hello-World-Listenern und `IngressGuard`-Rohfassung (Peer, Header-Logging), Backup-Hook-Dummies | S0 | CI grün, Image baut lokal |
| 1 | **S0-Real-Probe:** Minimal-App auf Dimitris HA installieren (`install-on-ha.py`), protokollieren: `GET /info` ohne `hassio_api`, Ingress-Peer und Identitätsheader, Client-IP am Public-Port (IPv4/IPv6 unter HAOS-Portmapping), `backup_pre/post` mit Archivprüfung auf ausgeschlossenen Inhalt, `/host/info` mit `hassio_role: default` (E26), Verhalten der Klimaanlage beim Moduswechsel (E7), Argon2-Parameter messen. Parallel im privaten Repo: Android-Spike OkHttp HTTPS+WSS mit Pin gegen lokalen TLS-Testserver (richtiger/falscher Pin, Hostname, Rotation) | S0 | `docs/s0-report.md` mit Ergebnissen; offene Annahmen in §14 aktualisiert |
| 2 | HA-Simulator; `HAClient` mit Reconnect, Snapshot, Registries, `call_service`, Zeitplausibilität; Supervisor-`/info` | S0 | Integrationstest gegen Simulator |
| 3 | Datenmodell, Migrationen, `PolicyIndex`, effektiver Kontext, `decide`, Adapter-Fähigkeiten, Übersetzung, `state_view` | M1 | A06–A18 grün |
| 4 | `IngressGuard` vollständig, Rollenprüfung nach E5, CSRF nach E24, Admin-App-Skelett, zwei Listener, Startreihenfolge | M1 | A01–A05 gegen Fake-Peer grün |
| 5a | Öffentliche Auth Teil 1: Einladung minimal (Stay `draft`/`confirmed`, Invite anlegen), Redeem, Gast-Principal, Cookie/CSRF, WS-Tickets, `/me`, Zeitplausibilität (E26) | M1 | A22–A24, A35 (CSRF, Origin, Ticket, widerrufene Session) grün |
| 5b | Öffentliche Auth Teil 2: Bewohner-Login, Display-Pairing, native Tokens, Refresh-Rotation, Trustprofil, Renewal, Test-QR | M2 | A35 (rotierter Refresh-Token), A49 und Token-Tests grün |
| 6 | Projektion, EventBus, WS-Events, Befehle mit Idempotenz, Vorprüfung vor `call_service`, Bestätigung, Audit | M1 | A19–A21, A31, A32, A34, A52 grün |
| 7 | Preview und Simulation mit allen vier Zielarten | M1 | A28–A30, A50 grün |
| 8 | Web: Tokens, `GuestPanel`, Transport, Join/Login/Pair/Dashboard, i18n, PWA, CSP | M1 | Playwright: Join → Aktion → Bestätigung |
| 9a | Web-Admin Teil 1: Shell/Navigation, Rooms & devices (mit Layout-Tab), People & groups, Roles & rights mit Live-Vorschau | M2 | Manuelle Abnahme, Tastaturpfad |
| 9b | Web-Admin Teil 2: Preview as user, Stays & invites, Sessions & checkout, Setup assistant, Audit & diagnostics, Review & publish, Restore | M2 | Manuelle Abnahme, Tastaturpfad |
| 10 | Aufenthalte vollständig: Checkout, Verlängerung, Zimmerwechsel, Ersatz, Rollout, Aufräumjobs | M2 | A25–A27, A51 grün |
| 11 | Setup-Assistent, TLS-Modi, Public Origin, Trustprofil, Gastgerätetest | M2 | A44 dokumentiert |
| 12 | Backup-Hooks, Restore-Modus, Archivprüfung | M2 | A39, A40, A47, A48 grün |
| 13 | Paketierung, Dockerfile, s6, Image-Workflow, Installation der echten App auf Dimitris HA, realer vertikaler Pfad | M1/M2 | Add-on läuft, Admin-SSO real, Licht und Klima real bedient |
| 14 | E2E-Suite, Lastskript, Doku (setup, security, restore, device-matrix), DOCS.md | M2 | A41, A42, A45, A46 dokumentiert |
| 15 | Natives Repo: Capacitor-App, `MyhaTransport` Android und iOS, Scanner, sichere Ablage, Wandmodus, Pinning-Tests, Release-Pipelines (Android/Fire auf Linux-Runnern, iOS auf macOS-Runnern), Store-Vorbereitung | M3 | A36–A38, A43 auf Android/Emulator; iOS-Pinning-Tests im Simulator auf dem macOS-Runner; Gerätetests und Einreichung als Checkliste mit Vorbedingung Entwicklerkonten |
| 16 | Fable-Abschlussreview: Code-Review, Security-Review, Abnahmematrix, Restrisiken | alle | Bericht an Dimitri |

## 14. Risiken und bewusst offene Punkte

- **Supervisor ≥ 2026.10.3** ist Voraussetzung (E4); auf Instanzen mit älterem Supervisor bleibt der Admin-Zugang gesperrt, die Admin-UI zeigt den Hinweis. Die Referenzinstallation erfüllt die Version seit 2026-10-22 (S0 Zeile 2).
- **Nativer Transport (S0 Zeile 16):** Pinning ist bisher nur auf der JVM (JSSE) belegt; A36–A38 auf Android (Conscrypt) sowie HTTP/2-Coalescing, TLS-Resumption und Proxys bleiben für WP15 offen. Laufende Aufrufe während einer Trustprofil-Rotation enden unter dem alten Profil, bis WP15 die Call-Registry umsetzt.
- **`/info` ohne `hassio_api`**, **Client-IP am Public-Port** und **Backup-Exclude-Muster** werden erst in WP1 real nachgewiesen.
- **Cover** nur im Simulator; **Fahrenheit**, Doppelsollwerte und Presets bleiben gesperrt.
- **iOS** wird nur in CI (GitHub-macOS-Runner, Simulator) gebaut und getestet; Nachweise auf echter Hardware und die Store-Einreichung hängen von Apple-Entwicklerkonto, Signaturmaterial und einem Testgerät ab.
- **Alte Fire-WebViews**: Buildziel `es2019` und CSS-Fallbacks sind eine Annahme, bis ein Fire-Gerät getestet ist.
- **Selbstsignierte Zertifikate im Browser** funktionieren nur, wenn das Gastgerät die CA kennt; der Setup-Assistent warnt, verbietet es aber nicht (Dimitris Wunsch nach Freiheit). Nativ gilt E27.
- **Zeitplausibilität (E26)** erkennt Rücksprünge und Sprünge der Systemuhr, aber keinen langsamen gemeinsamen Drift; `dt_synchronized` schließt die Lücke nur, wenn WP1 den Zugriff bestätigt.
- **Klimaanlagen mit Sollwert je Modus** (E7) könnten nach einem Moduswechsel einen Wert außerhalb der Grenzen melden; die Nachprüfung macht das sichtbar und sperrt die Bindung, verhindert aber nicht die erste physische Wirkung.
- **Ergebnis des Abschlussreviews zu Teilplan 1 (2026-10-22):** kein kritischer Befund; alle wichtigen Befunde als Fix-ups umgesetzt oder den Tasks zugeordnet (Teilplan 2 Task 5a.1: Header-Deadline gegen Slowloris, Body-Deckel mit 413, Contextvar-Wächter gegen Netz-I/O in Schreibsessions, Audit-Spalte für den Faltschlüssel anonymer Ereignisse; Teilplan 3: Rest-Export beim Start erkennen statt Restore-Modus, Proxy-Peer-Guard, HSTS bewusst entscheiden, `search/related` bei der Bindungsbestätigung).
- **Gemeinsamer Ingress-Origin:** Alle Ingress-Apps und das HA-Frontend teilen den HA-Origin; ein XSS dort liest den Admin-CSRF-Token und handelt als Admin. myHA kann das nicht verhindern (Plattformmodell); das CSRF-Token gilt bis zu zwei UTC-Tage und ist nicht widerrufbar, nutzbar nur zusammen mit der Ingress-Session.
- **Supervisor-Netz als Vertrauensanker:** Eine App mit Host-Netz oder `NET_ADMIN` könnte die Peer-Adresse `172.30.32.2` vortäuschen; solche Apps haben ohnehin gleichwertige Macht über HA. Die Mindestversion 2026.10.3 (E4) gilt zusätzlich wegen Supervisor-PR #7377 (case-insensitive Filterung der Identitäts-Header); myHAs Regel „genau ein Header“ fängt ältere Versionen ab.
- **Token mit vollen Core-Rechten:** Über `homeassistant_api` hat das Gateway Admin-Rechte im Core; eine Codeausführung im Gateway bedeutet die Übernahme von HA. Gegenmaßnahmen sind allein eine kleine Angriffsfläche und strenge Parser.
- **Semantische Nebenwirkungen:** Eine gebundene Entity kann Automationen auslösen („Licht an → Tür auf“), ein `switch` kann alles kapseln. Teilplan 3 zeigt bei der Bindungsbestätigung die referenzierenden Automationen und Skripte (`search/related`).
- **Noch unbelegt (S0 offen):** Client-IP am Public-Port unter HAOS (Zeile 11: ohne echte Quelle landen alle Gäste in einem Rate-Limit-Topf), Backup-Ausschluss (Zeile 12: ohne ihn liegen `server_secret` und Hashes in HA-Backups, A39 fällt), `get_states` über 16 MiB trennt die Core-Verbindung dauerhaft, `/info` ohne `hassio_api` samt `homeassistant`-Feld, Protokoll-Pings am Supervisor-Proxy, Stop-Budget unter HAOS.
- **Fingerprint-Empfindlichkeit:** Geräte, die Bereiche je Modus ändern oder Feature-Bits umschalten, landen bei jedem Wechsel in `needs_review`; an der Referenz-Klimaanlage in WP13 prüfen.
- **WAL-Reste:** `secure_delete` erfasst ältere Seitenstände in der WAL-Datei erst nach einem Checkpoint; lokaler Forensik-Rest, begrenzt durch das 0700-Verzeichnis und den Backup-Ausschluss.
