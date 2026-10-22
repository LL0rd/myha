# myHA – Masterplan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** myHA als HAOS-App mit Gateway, Admin-UI, Gast-PWA und nativer Begleitapp gemäß `docs/superpowers/specs/2026-10-22-myha-design.md` (im Folgenden „Design“) bauen.

**Architecture:** Ein Python-Prozess mit zwei ASGI-Apps (Admin über Ingress, Public über :8443), reine Policy-Funktion, eine HA-WebSocket-Verbindung, SQLite. React/Vite-Frontend mit zwei Einstiegen, geteilt mit der nativen Capacitor-Shell im privaten Repo.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2 (async, aiosqlite), Alembic, httpx, websockets, argon2-cffi, segno; React 18, TypeScript, Vite 6, Radix Primitives, Zod; Playwright; Capacitor 8; GitHub Actions.

---

## Aufteilung in Teilpläne

Das Design umfasst vier Phasen und 17 Arbeitspakete. Ein einzelner Plan mit vollständigem Code wäre nicht wartbar; deshalb gibt es Teilpläne, die jeweils lauffähige, getestete Software liefern. Die Teilpläne 1 und 2 werden sofort geschrieben, weil sie alle Schnittstellen festlegen. Teilplan 3 wird nach Abschluss von Teilplan 1 (S0-Ergebnisse) geschrieben, Teilplan 4 nach Teilplan 2 (stabile Web-UI und Transport-Schnittstelle).

| Teilplan | Datei | Arbeitspakete (Design §13) | Liefert |
|---|---|---|---|
| 1 Fundament | `2026-10-22-myha-plan-1-foundation.md` | WP0, WP1, WP2, WP3, WP4 | Repos, Tooling, CI, Add-on-Gerüst auf Dimitris HA, S0-Bericht, Simulator, HAClient, Datenmodell, Policy mit A06–A18, Ingress-Guard mit A01–A05 |
| 2 Vertikaler Kern | `2026-10-22-myha-plan-2-vertical-core.md` | WP5a, WP6, WP7, WP8 | Gast-Redeem, Sessions, Projektion, Live-Events, Befehle, Preview, Gast-Web-UI; E2E Join → Aktion |
| 3 Webpilot | `2026-10-22-myha-plan-3-webpilot.md` (folgt) | WP5b, WP9a, WP9b, WP10, WP11, WP12, WP13, WP14 | Admin-UI komplett, Aufenthalte, Setup, Backup/Restore, Paketierung, Installation auf dem echten HA, Doku |
| 4 Native | `2026-10-22-myha-plan-4-native.md` (folgt) | WP15 | Android-, Fire-OS- und iOS-App für den öffentlichen Store-Vertrieb: Capacitor-Shell, native Transport-Plugins, Scanner, sichere Ablage, Wandmodus, store-fertige Release-Pipelines (Android/Fire signiert auf Linux-Runnern, iOS auf GitHub-macOS-Runnern), Store-Metadaten, Datenschutzerklärung, Review-Demo. Vorbedingungen: Entwicklerkonten bei Apple, Google und Amazon |
| Abschluss | dieser Masterplan, Abschnitt „WP16“ | WP16 | Fable-Review und Bericht |

## Produktrahmen

myHA ist ein Produkt für beliebige Betreiber und wird öffentlich vertrieben: der Kern als offenes HA-App-Repository, die Begleitapps über App Store, Google Play und Amazon Appstore (Design §1 „Produktrahmen“, §9). Daraus folgt für jeden Task: keine betreiberspezifischen Defaults, Pfade, Namen oder Zugangsdaten im Code; alle Texte in DE und EN; Dokumentation richtet sich an Betreiber, nicht an eine Person. Dimitris Home Assistant ist die Referenzinstallation Nr. 1 für Nachweise gegen echte Hardware, sonst nichts.

## Orchestrierung

- **Fable 5.1** (Hauptsession): schreibt und pflegt die Pläne, dispatcht pro Task einen frischen **Opus-5.5-Subagenten** (`model: "opus"`) mit dem Task-Text, dem Pfad zum Design und zu diesem Plan sowie dem aktuellen Repo-Zustand; prüft nach jedem Task Tests und Diff; führt nach jedem Arbeitspaket einen Review-Subagenten (Spec-Treue, dann Codequalität) aus; führt WP16 selbst durch.
- **Subagenten** arbeiten strikt TDD (Test zuerst, rot, grün, Commit) und committen kleinteilig auf `main` des jeweiligen Repos. Sie dürfen keine Secrets ausgeben (Supervisor-Token, HA-Token, Einladungs-Secrets) und keine Scope-Erweiterung vornehmen; Unklarheiten melden sie an Fable zurück statt zu raten.
- **Reihenfolge:** Teilplan 1 vollständig vor Teilplan 2. Innerhalb eines Teilplans gilt die Task-Reihenfolge; es läuft immer nur **ein** Implementierer-Subagent je Repository. Die Code-Tasks von WP1 folgen direkt auf WP0 und werden als Branch `s0-probe` eingefroren; nur Fables Schritte auf der Referenzinstallation und der Android-Spike im privaten Repo laufen parallel zu WP2–WP4.
- **Dimitris Produktiv-HA:** Jeder mutierende Schritt auf dem echten HA (Repository hinzufügen, App installieren oder neu bauen, Port öffnen, Backup erzeugen, Klimaanlage schalten) wird **von Fable selbst** ausgeführt, nachdem Dimitri im Chat ausdrücklich zugestimmt hat; Subagenten bereiten nur Skripte und Code vor. Ist das HA nicht erreichbar oder der Supervisor zu alt, wird WP1 als „offen“ markiert und blockiert WP2–WP4 nicht.
- **Veröffentlichung:** Das Repo ist öffentlich. Keine LAN-IPs, Ingress-Tokens, HA-User-IDs oder Klarnamen Dritter in Berichten, Fixtures oder Tests.

## Konventionen (gelten für alle Teilpläne)

- **Repos:** `/home/dimitri/Projekte/myHA` = GitHub `LL0rd/myha` (öffentlich, Apache-2.0). `/home/dimitri/Projekte/myha-apps` = GitHub `LL0rd/myha-apps` (privat). Git-Identität aus der globalen Konfiguration; Commit-Messages englisch, Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), Abschluss mit `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Python:** `uv` im Verzeichnis `myha/gateway`; Python 3.13 (`.python-version`); `uv run pytest -q`; Ruff (`ruff check`, `ruff format`); mypy für `myha_gateway/policy`, `myha_gateway/auth`, `myha_gateway/adapters` im Modus `strict`. Kein `print`, Logging über `logging` mit Redaction-Filter.
- **Web:** `pnpm` im Verzeichnis `myha/web`; `pnpm build`, `pnpm test` (Vitest), `pnpm lint`, `pnpm typecheck`.
- **E2E:** `tests/e2e` mit eigenem `package.json`; `pnpm exec playwright test`.
- **Tests sind Pflicht** für neue Logik; Abnahmefälle der Spez §19 heißen im Code `test_a06_*` usw. und liegen in `myha/gateway/tests/acceptance/`.
- **Keine Geheimnisse im Repo:** `.env`, `.dev/`, Token-Dateien sind ignoriert. `scripts/install-on-ha.py` ist generisch und liest `HA_URL` und `HA_TOKEN` aus der Umgebung; Fable setzt sie lokal aus der ungetrackten Datei `.dev/ha.env`. Kein Skript gibt Tokens aus.
- **Commit-Trailer:** Jeder Commit endet mit dem `Co-Authored-By`-Trailer des Modells, das ihn erstellt hat (Opus-Subagenten: `Claude Opus 5.5 <noreply@anthropic.com>`, Fable: `Claude Fable 5.1 <noreply@anthropic.com>`).
- **Dokumentation:** README und `docs/*.md` englisch; `docs/s0-report.md` enthält die S0-Ergebnisse.
- **Migrationen:** Bis zur ersten echten Installation darf `0001_initial` in place geändert werden (lokale `.dev/data` dann löschen); ab der ersten Installation auf einer realen HA-Instanz (Teilplan 3, WP13) ist jede Schemaänderung eine neue Alembic-Revision, nie eine Änderung an einer ausgelieferten.
- **Secrets und IDs:** Produkt-IDs `unit_<12 hex>`, `res_<12 hex>`, `stay_<12 hex>`, `inv_<12 hex>`, `sess_<12 hex>`, `cmd_<12 hex>`; Secrets 32 Byte aus `secrets.token_bytes`, base64url ohne Padding; gespeichert nur als SHA-256-Hex.

## Gates zwischen den Teilplänen

| Übergang | Bedingung |
|---|---|
| Plan 1 → Plan 2 | CI grün; `docs/s0-report.md` liegt vor; A01–A18 grün; Design §14 aktualisiert, falls S0 Annahmen kippt |
| Plan 2 → Plan 3 | E2E „Join → Aktion → Bestätigung → Widerruf“ grün gegen Simulator; A19–A24, A28–A32, A34, A35 (Teil), A50, A52 grün |
| Plan 3 → Plan 4 | Add-on auf Dimitris HA installiert, Admin-SSO real, Licht und Klima im Wohnzimmer real bedient; restliche M2-Abnahmefälle grün oder dokumentiert |
| Plan 4 → WP16 | Android-Build läuft im Emulator; A36–A38, A43 dokumentiert |

## WP16 – Abschlussreview (Fable)

- [ ] `superpowers:requesting-code-review` für das Gesamtrepo mit Fokus auf Design §10 (Bedrohungstabelle) ausführen; Findings beheben lassen.
- [ ] `/security-review`-Skill auf beide Repos anwenden; Findings beheben lassen.
- [ ] Abnahmematrix A01–A52 als Tabelle in `docs/acceptance.md` mit Status (automatisiert grün / dokumentiert / entfällt / offen) erzeugen.
- [ ] Restrisiken aus Design §14 mit Ist-Stand aktualisieren.
- [ ] Bericht an Dimitri: was läuft, was auf dem echten HA geprüft wurde, was offen bleibt, nächste Schritte für M4.
