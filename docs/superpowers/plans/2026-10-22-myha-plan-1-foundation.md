# myHA Teilplan 1 – Fundament (WP0–WP4)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Beide Repositories, Tooling und CI stehen; ein Add-on-Gerüst läuft auf Dimitris HA und liefert den S0-Bericht; HA-Simulator, HAClient, Datenmodell, Policy-Engine (A06–A18) und Ingress-Guard (A01–A05) sind getestet.

**Architecture:** Siehe Design §3–§4. Von außen nach innen: lauffähiges Add-on-Gerüst mit zwei Listenern → reale Probe (parallel) → Simulator und HAClient → Domäne und Policy → Admin-Identität.

**Tech Stack:** uv, Python 3.13, FastAPI, uvicorn, Pydantic 2, pydantic-settings, SQLAlchemy 2 async + aiosqlite, Alembic, httpx, websockets, argon2-cffi, segno, pytest, pytest-asyncio, Ruff, mypy; pnpm, Vite 6, React 18, TypeScript; Docker buildx; GitHub Actions.

**Design-Referenz:** `docs/superpowers/specs/2026-10-22-myha-design.md` – bei Widerspruch zwischen Plan und Design gilt das Design; Abweichungen an Fable melden.

**Revision 2** (nach Plan-Review): Dev-/Test-Overrides in den Settings, getrennte Web-Bundles, vollständiges Simulator-Fixture und `_sim`-API, Drift-Erkennung für A16, erweitertes `Expectation`, Supervisor-Skript über REST-Proxy für Logs/Downloads, Probe-Modus als App-Option, Docker-Smoke mit `--entrypoint`, CI-Korrekturen, `frame-ancestors 'self'`.

---

## Dateistruktur (Ergebnis dieses Teilplans)

```
myha/                                   (Repo-Root, GitHub LL0rd/myha)
├── repository.yaml · README.md · LICENSE · CHANGELOG.md · .gitignore
├── .github/workflows/ci.yml · image.yml
├── scripts/dev.sh · build-image.sh · install-on-ha.py · check-no-external.mjs
├── docs/s0-report.md · setup.md (Stub) · security.md (Stub)
└── myha/                               (Add-on-Ordner, Build-Kontext)
    ├── config.yaml · build.yaml · Dockerfile · .dockerignore · DOCS.md · icon.png · logo.png
    ├── rootfs/etc/s6-overlay/s6-rc.d/myha/{type,run,finish}
    ├── rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/myha
    ├── rootfs/opt/myha/backup-export · backup-cleanup
    ├── web/ (Vite-Gerüst: vite.guest.config.ts, vite.admin.config.ts, index.html, admin.html, src/guest/main.tsx, src/admin/main.tsx, src/ui/tokens.css)
    └── gateway/
        ├── pyproject.toml · .python-version · uv.lock · alembic.ini · README.md
        ├── myha_gateway/
        │   ├── __init__.py · __main__.py · settings.py · app.py · deps.py · errors.py · logsetup.py · ids.py · static.py
        │   ├── db/__init__.py · db/models.py · db/migrations/env.py · db/migrations/versions/0001_initial.py
        │   ├── ha/__init__.py · ha/client.py · ha/messages.py · ha/supervisor.py
        │   ├── adapters/__init__.py · base.py · light.py · switch.py · climate.py · cover.py · sensor.py · registry.py
        │   ├── policy/__init__.py · codes.py · model.py · context.py · index.py · decide.py
        │   ├── auth/__init__.py · admin.py
        │   └── api/__init__.py · api/admin/__init__.py · api/admin/probe.py · api/public/__init__.py · api/public/health.py
        ├── myha_simulator/__init__.py · server.py · fixtures.py · __main__.py
        └── tests/conftest.py · helpers.py · unit/ · integration/ · acceptance/
```

Jede Datei hat eine Verantwortung: `decide.py` entscheidet nur, `index.py` baut nur den Index, `context.py` baut nur Kontexte aus geladenen Zeilen, Adapter übersetzen nur, `client.py` spricht nur mit HA.

---

## WP0 – Repos, Tooling, Add-on-Gerüst

### Task 0.1: GitHub-Repos anlegen und Grundgerüst committen

**Files:**
- Create: `README.md`, `LICENSE` (Apache-2.0, Copyright 2026 Dimitri Roschkowski), `CHANGELOG.md`, `repository.yaml`
- Modify: `.gitignore` (ergänzen: `.env`, `*.sqlite`, `*.sqlite-*`, `.dev/`, `node_modules/`, `dist/`, `myha/web/openapi.json`; `uv.lock` und `pnpm-lock.yaml` **nicht** ignorieren)

- [ ] **Step 1: Repos anlegen** (Dimitri hat zugestimmt)

```bash
cd /home/dimitri/Projekte/myHA
gh repo create LL0rd/myha --public --description "Room-scoped guest, resident and wall-display access for Home Assistant" --source . --remote origin
mkdir -p /home/dimitri/Projekte/myha-apps && cd /home/dimitri/Projekte/myha-apps && git init -q -b main
printf '# myHA native apps\n\nProprietary. Companion apps for https://github.com/LL0rd/myha.\n' > README.md && git add -A && git commit -qm "chore: initial"
gh repo create LL0rd/myha-apps --private --description "myHA native companion apps (proprietary)" --source . --remote origin --push
```

- [ ] **Step 2: `repository.yaml`**

```yaml
name: myHA Apps
url: https://github.com/LL0rd/myha
maintainer: Dimitri Roschkowski <info@d-r.dev>
```

- [ ] **Step 3:** README (englisch, kurz): Zweck, Status „pre-alpha“, Installation über HA-App-Repository, Verweis auf `docs/`. LICENSE mit vollständigem Apache-2.0-Text. CHANGELOG mit `## Unreleased`.
- [ ] **Step 4: Commit und Push:** `git add -A && git commit -m "chore: repository metadata, license and readme" && git push -u origin main`.

### Task 0.2: Gateway-Projekt mit uv

**Files:**
- Create: `myha/gateway/pyproject.toml`, `.python-version` (`3.13`), `myha_gateway/__init__.py` (`__version__ = "0.1.0"`), leere Pakete `myha_gateway/{policy,auth,adapters,ha,api,api/admin,api/public,db}/__init__.py`, `myha_simulator/__init__.py`, `tests/__init__.py`, `tests/unit/test_version.py`

- [ ] **Step 1: pyproject**

```toml
[project]
name = "myha-gateway"
version = "0.1.0"
description = "myHA gateway: policy-enforcing access layer for Home Assistant"
requires-python = ">=3.13,<3.14"
license = "Apache-2.0"
dependencies = [
  "fastapi>=0.118", "uvicorn[standard]>=0.37", "pydantic>=2.11", "pydantic-settings>=2.10",
  "sqlalchemy[asyncio]>=2.0.43", "aiosqlite>=0.21", "alembic>=1.16", "httpx>=0.28",
  "websockets>=15", "argon2-cffi>=25", "segno>=1.6", "pyyaml>=6",
]

[project.scripts]
myha-gateway = "myha_gateway.__main__:main"
myha-simulator = "myha_simulator.__main__:main"

[dependency-groups]
dev = ["pytest>=8.4", "pytest-asyncio>=1.1", "pytest-cov>=6", "anyio>=4", "ruff>=0.13", "mypy>=1.18", "types-PyYAML"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py313"
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "ASYNC"]
ignore = ["S101"]
[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S105", "S106", "S311"]

[tool.mypy]
python_version = "3.13"
strict = true
files = ["myha_gateway/policy", "myha_gateway/auth", "myha_gateway/adapters"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["myha_gateway", "myha_simulator"]
```

- [ ] **Step 2: Test:** `tests/unit/test_version.py`: `from myha_gateway import __version__; assert __version__ == "0.1.0"`.
- [ ] **Step 3:** `cd myha/gateway && uv sync && uv run pytest -q` → `1 passed`; `uv run mypy` → `Success: no issues found` (leere Pakete vorhanden); `uv run ruff check`.
- [ ] **Step 4: Commit** `chore(gateway): bootstrap uv project`.

### Task 0.3: Settings, Logging-Redaction, IDs

**Files:**
- Create: `myha_gateway/settings.py`, `myha_gateway/logsetup.py`, `myha_gateway/ids.py`
- Test: `tests/unit/test_settings.py`, `tests/unit/test_logsetup.py`, `tests/unit/test_ids.py`

- [ ] **Step 1: Settings-Felder (alle mit Env-Prefix `MYHA_`, außer `SUPERVISOR_TOKEN`):**

| Feld | Default | Zweck |
|---|---|---|
| `data_dir: Path` | `/data` | `state_dir = data_dir/"state"`, `backup_dir = data_dir/"backup"` als `computed_field` |
| `web_dist: Path` | `/opt/myha/web` | enthält `guest/` und `admin/` |
| `admin_port`, `public_port` | 8099, 8443 | |
| `admin_bind`, `public_bind` | `0.0.0.0` | beide Listener binden im Container immer auf alle Interfaces |
| `trusted_ingress_peer: str` | `172.30.32.2` | E18 |
| `supervisor_url` | `http://supervisor` | |
| `supervisor_token: SecretStr \| None` | Env `SUPERVISOR_TOKEN` (`validation_alias`) | |
| `ha_ws_url` | `ws://supervisor/core/websocket` | |
| `min_supervisor_version` | `2026.10.3` | E4 |
| `log_level` | `info` | aus `data_dir/options.json` überschreibbar |
| `probe_mode: bool` | `False` | aus `options.json`; S0: Public-Listener an, Probe-Logs |
| `public_enabled: bool` | `False` | **Dev/Test-Override:** erzwingt Public-Listener auch ohne Instanzkonfiguration; dann gilt `tls_mode=proxy` ohne vertrauenswürdige Proxies (reines HTTP) und `public_origin` aus `MYHA_PUBLIC_ORIGIN` |
| `public_origin: str \| None` | `None` | nur zusammen mit `public_enabled`; sonst kommt der Origin aus `instance.public_origin` |

Regel in `Settings.model_validator`: `public_enabled and not public_origin` → `ValueError` („MYHA_PUBLIC_ORIGIN required with MYHA_PUBLIC_ENABLED“), außer `probe_mode` ist gesetzt (dann `public_origin = f"http://probe.invalid:{public_port}"`, weil die Probe nur `/healthz` nutzt).

- [ ] **Step 2: Tests zuerst** (`test_settings.py`: Defaults; `options.json` setzt `log_level` und `probe_mode`; `public_enabled` ohne Origin → Fehler; mit `probe_mode` ok. `test_logsetup.py`: `RedactionFilter` ersetzt `Bearer <x>`, `(secret|token|password|authorization)[=:]\s*\S+`, und 43+-stellige base64url-Strings durch `<redacted>`. `test_ids.py`: `new_id("res")` → `res_` + 12 hex; `new_secret()` ≥ 43 Zeichen; `hash_secret` 64 hex; `constant_time_equal`).
- [ ] **Step 3:** rot → implementieren → grün. Commit `feat(gateway): settings with dev overrides, log redaction and id helpers`.

### Task 0.4: Zwei ASGI-Apps, ein Prozess, Probe-Endpunkte

**Files:**
- Create: `myha_gateway/app.py`, `deps.py`, `errors.py`, `__main__.py`, `api/public/health.py`, `api/admin/probe.py`
- Test: `tests/integration/test_listeners.py`

- [ ] **Step 1: Tests**

```python
import httpx, pytest
from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.deps import AppContext

@pytest.fixture
async def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_PUBLIC_ENABLED", "1")
    monkeypatch.setenv("MYHA_PUBLIC_ORIGIN", "http://testserver")
    return await AppContext.create()

async def test_public_health_reports_client_ip(ctx):
    app = create_public_app(ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 4444)), base_url="http://testserver") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["client_ip"] == "203.0.113.9"

async def test_public_listener_has_no_admin_routes(ctx):          # Vorgriff auf A01
    app = create_public_app(ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("172.30.32.2", 1)), base_url="http://testserver") as c:
        r = await c.get("/api/v1/admin/probe", headers={"X-Remote-User-Id": "owner1"})
    assert r.status_code == 404

async def test_admin_probe_requires_trusted_peer(ctx):
    app = create_admin_app(ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("10.0.0.5", 1)), base_url="http://a") as c:
        r = await c.get("/api/v1/admin/probe")
    assert r.status_code == 403 and r.json()["error"]["code"] == "UNAUTHENTICATED"

async def test_admin_probe_echoes_identity_headers_from_trusted_peer(ctx):
    app = create_admin_app(ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("172.30.32.2", 1)), base_url="http://a") as c:
        r = await c.get("/api/v1/admin/probe", headers={"X-Remote-User-Id": "u1", "X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert r.status_code == 200 and r.json()["peer"] == "172.30.32.2" and r.json()["headers"]["x-remote-user-id"] == "u1"
```

- [ ] **Step 2:** rot.
- [ ] **Step 3: Implementieren.** `AppContext` (dataclass: `settings`, später `engine`, `ha`, `bus`, `policy`; `create()` classmethod; Property `public_ready`). `errors.py`: `ApiError(status, code, message, details=None)` + Handler → `{"error": {...}}`; FastAPI-Validation-Fehler → `400 INVALID_PARAMETERS` (öffentlich) bzw. mit Details (Admin). `create_public_app(ctx)`: FastAPI ohne Docs, Router `health` (`GET /healthz` → `{"status","client_ip","x_forwarded_for"}`). `create_admin_app(ctx)`: `PeerGuardMiddleware` (403 bei falschem Peer; wird in WP4 durch `require_admin` ergänzt), Router `probe` (`GET /api/v1/admin/probe` → `{"peer","headers":{nur x-remote-user-*, x-ingress-path, x-forwarded-*, x-real-ip}}`). `__main__.py` (argparse): `serve` startet `uvicorn.Server` für Admin und, wenn `ctx.public_ready`, für Public (`proxy_headers=False`, `server_header=False`, `ws_max_size=65536`), beide via `asyncio.gather`; `public_ready` = `settings.public_enabled or settings.probe_mode` (ab WP4 zusätzlich Instanzkonfiguration). Ein Hintergrundtask prüft alle 2 s, ob `public_ready` später wahr wird, und startet den Public-Server nach. `probe` (CLI) gibt `GET {supervisor_url}/info` (Status + Version), Env-Variablen**namen** und Pfadprüfungen aus.
- [ ] **Step 4:** grün; lokal `MYHA_DATA_DIR=.dev/data MYHA_PUBLIC_ENABLED=1 MYHA_PUBLIC_ORIGIN=http://localhost:8443 uv run myha-gateway serve` → `curl -s localhost:8443/healthz`. Commit `feat(gateway): admin and public listeners with probe endpoints`.

### Task 0.5: Web-Gerüst (Vite, zwei getrennte Bundles)

**Files:**
- Create: `myha/web/package.json`, `vite.guest.config.ts`, `vite.admin.config.ts`, `vite.shared.ts`, `tsconfig.json`, `eslint.config.js`, `index.html`, `admin.html`, `src/guest/main.tsx`, `src/admin/main.tsx`, `src/ui/tokens.css`, `src/vite-env.d.ts`, `src/ui/tokens.test.ts`, `vitest.config.ts`

- [ ] **Step 1:** `pnpm init`; deps `react@18 react-dom@18 react-router-dom@6 zod`; dev `vite@6 @vitejs/plugin-react typescript@5 vitest jsdom @testing-library/react @testing-library/user-event eslint typescript-eslint @fontsource/nunito-sans @fontsource/jetbrains-mono @tabler/icons-react openapi-typescript`. Skripte: `build` = `vite build --config vite.guest.config.ts && vite build --config vite.admin.config.ts`, `dev` (guest), `dev:admin`, `test`, `lint`, `typecheck`, `gen:api` (ab Plan 2). `vite.shared.ts`: `base: "./"`, `build.target: "es2019"`, Dev-Proxy `/api` → `http://127.0.0.1:8443`. Guest-Config: `root` = Projekt, `input: index.html`, `outDir: dist/guest`, `emptyOutDir: true`. Admin-Config: `input: admin.html`, `outDir: dist/admin`, Dev-Server-Port 5174 mit Proxy `/api` → `http://127.0.0.1:8099` (Dev-Ingress kommt mit dem E2E-Proxy in Plan 2).
- [ ] **Step 2: `tokens.css`** mit den CSS-Variablen aus dem Mockup (`docs/reference/design/ha-access-ui.dc.html`, Zeilen 14–16: `:root`, `[data-theme="dark"]`, `[data-theme="contrast"]`), Fonts per `@fontsource`-Import in beiden `main.tsx`.
- [ ] **Step 3:** Beide Einstiege rendern „myHA“ + Theme-Umschalter. `tokens.test.ts` prüft über `?raw`-Import, dass alle drei Themes `--accent` definieren.
- [ ] **Step 4:** `pnpm build` → `dist/guest/index.html`, `dist/admin/admin.html`; `pnpm test`, `pnpm lint`, `pnpm typecheck` grün. Commit `chore(web): vite scaffold with separate guest and admin bundles and design tokens`.

### Task 0.6: Gateway liefert die Web-Bundles aus

**Files:**
- Create: `myha_gateway/static.py`
- Modify: `myha_gateway/app.py`
- Test: `tests/integration/test_static.py`

- [ ] **Step 1: Test:** `MYHA_WEB_DIST=<tmp>` mit `guest/index.html`, `guest/assets/g.js`, `guest/manifest.webmanifest`, `guest/sw.js`, `admin/admin.html`, `admin/assets/a.js`. Public: `GET /` und `GET /join` → guest/index.html (SPA-Fallback **nur für Pfade ohne `/api/`-Prefix und ohne Dateiendung**; `GET /api/v1/nope` → JSON 404); `/assets/g.js` → Datei; `/sw.js`, `/manifest.webmanifest` → aus `guest/`; `/assets/a.js` → 404; `/admin.html` → 404. Admin: `GET /` → admin/admin.html; `/assets/a.js` → Datei; `/assets/g.js` → 404. Header: HTML `Cache-Control: no-store`, `/assets/*` `public, max-age=31536000, immutable`; Public-CSP `default-src 'self'; connect-src 'self' wss: ws:; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'` (`ws:` nur, wenn `public_origin` mit `http://` beginnt); Admin-CSP `default-src 'self'; connect-src 'self' wss: ws:; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'`; beide `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
- [ ] **Step 2–4:** rot, implementieren (`StaticFiles` + Fallback-Route), grün, Commit `feat(gateway): serve separate guest and admin bundles with csp and cache headers`.

### Task 0.7: Add-on-Paketierung

**Files:**
- Create: `myha/config.yaml`, `build.yaml`, `Dockerfile`, `.dockerignore`, `DOCS.md`, `icon.png`, `logo.png`, `rootfs/etc/s6-overlay/s6-rc.d/myha/{type,run,finish}`, `rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/myha`, `rootfs/opt/myha/backup-export`, `rootfs/opt/myha/backup-cleanup`, `scripts/build-image.sh`

- [ ] **Step 1: `config.yaml`** wie Design §8, ergänzt um

```yaml
options:
  log_level: info
schema:
  log_level: list(debug|info|warning|error)
  probe_mode: bool?
```

`probe_mode` ist optional (kein Default in `options`), nur für S0 und wird nach WP1 wieder entfernt.

- [ ] **Step 2: `build.yaml`**

```yaml
build_from:
  aarch64: ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.23
  amd64: ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23
labels:
  org.opencontainers.image.title: myHA
  org.opencontainers.image.source: https://github.com/LL0rd/myha
  org.opencontainers.image.licenses: Apache-2.0
```

- [ ] **Step 3: Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.7
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23
FROM --platform=$BUILDPLATFORM node:22-alpine AS web
WORKDIR /src/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM ${BUILD_FROM} AS runtime
ENV UV_PROJECT_ENVIRONMENT=/opt/myha/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
WORKDIR /opt/myha/gateway
COPY gateway/pyproject.toml gateway/uv.lock gateway/.python-version ./
RUN apk add --no-cache --virtual .build build-base libffi-dev \
 && uv sync --frozen --no-dev --no-install-project \
 && apk del .build
COPY gateway/ ./
RUN uv sync --frozen --no-dev
COPY --from=web /src/web/dist /opt/myha/web
COPY rootfs/ /
RUN chmod a+x /etc/s6-overlay/s6-rc.d/myha/run /etc/s6-overlay/s6-rc.d/myha/finish /opt/myha/backup-export /opt/myha/backup-cleanup
ENV PATH="/opt/myha/venv/bin:${PATH}" MYHA_WEB_DIST=/opt/myha/web
```

`.dockerignore`: `**/node_modules`, `**/dist`, `**/.venv`, `**/__pycache__`, `**/.pytest_cache`, `**/.dev`. Prüfen, ob `/usr/local/bin/python3` im Basisimage der richtige Interpreter ist (`docker run --rm --entrypoint python3 ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23 -c "import sys;print(sys.executable)"`); sonst `UV_PYTHON` anpassen. Falls `uv sync` Wheels nicht findet (musllinux), bleibt der Quellbau mit `build-base`; Ergebnis in `docs/s0-report.md`.

- [ ] **Step 4: s6-Run-Skript**

```bash
#!/command/with-contenv bashio
# shellcheck shell=bash
export MYHA_DATA_DIR=/data
export MYHA_LOG_LEVEL="$(bashio::config 'log_level' 'info')"
if bashio::config.true 'probe_mode'; then
  export MYHA_PROBE_MODE=1
  bashio::log.warning "probe_mode is enabled: public listener exposed for the S0 probe"
fi
bashio::log.info "Starting myHA gateway"
exec /opt/myha/venv/bin/myha-gateway serve
```

`finish`: beendet den Container bei Exit ≠ 0 (`/run/s6/basedir/bin/halt`). Hook-Dummies: `backup-export` schreibt `/data/backup/PROBE-export.txt` (Zeitstempel) und loggt mit `bashio::log.info`; `backup-cleanup` löscht sie. Startet das Gateway im `probe_mode`, legt es `/data/state/probe-live.marker` an (Nachweis für den Backup-Ausschluss) und loggt die Argon2-Messung (Task 1.4).

- [ ] **Step 5: `scripts/build-image.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker buildx build --load --platform linux/amd64 \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23 \
  -t myha-local:dev myha/
tmp=$(mktemp -d)
cid=$(docker run -d --rm --entrypoint /opt/myha/venv/bin/myha-gateway \
  -e MYHA_DATA_DIR=/data -e MYHA_PUBLIC_ENABLED=1 -e MYHA_PUBLIC_ORIGIN=http://127.0.0.1:18443 \
  -v "$tmp:/data" -p 18443:8443 myha-local:dev serve)
trap 'docker stop "$cid" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:18443/healthz && exit 0; sleep 1; done
docker logs "$cid"; exit 1
```

Der `--entrypoint` umgeht `/init` (s6) absichtlich; der s6-Pfad wird in WP1 auf dem echten HA geprüft.

- [ ] **Step 6:** `DOCS.md` (englisch): Zweck, Ports, Ingress, Backup-Hinweis, Pre-Alpha-Warnung. Icons: `scripts/make-icons.py` (Pillow aus der Dev-Gruppe oder statisch committete PNGs 128×128 und 250×100, Schlüsselsymbol auf `#C8A24A`).
- [ ] **Step 7:** `scripts/build-image.sh` grün; Commit `feat(addon): add-on packaging with multi-stage image and s6 service`.

### Task 0.8: CI-Skelett

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/image.yml`

- [ ] **Step 1: `ci.yml`:** Trigger push/PR. Jobs: `gateway` (`astral-sh/setup-uv@v6`, `working-directory: myha/gateway`, `uv sync`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q --cov=myha_gateway --cov-report=term`); `web` (`pnpm/action-setup@v4` mit `package_json_file: myha/web/package.json`, `actions/setup-node@v4` Node 22 mit `cache: pnpm` und `cache-dependency-path: myha/web/pnpm-lock.yaml`, `working-directory: myha/web`, `pnpm install --frozen-lockfile`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`); `image` (`docker/setup-buildx-action@v3`, Build `myha/` für amd64 ohne Push).
- [ ] **Step 2: `image.yml`:** Trigger Tag `v*` und `workflow_dispatch`; Matrix `amd64`/`aarch64` liest `BUILD_FROM` aus `build.yaml` (`yq`), baut `linux/amd64` bzw. `linux/arm64` und pusht `ghcr.io/ll0rd/myha-{arch}:<version>` (`permissions: packages: write`, Login mit `GITHUB_TOKEN`). Noch nicht in `config.yaml` als `image:` eintragen (E16).
- [ ] **Step 3:** Push, `gh run watch`, Fehler beheben. Commit `ci: lint, test, build and image workflows`.

### Task 0.9: Dev-Skript

**Files:**
- Create: `scripts/dev.sh`

- [ ] **Step 1:** Startet Simulator (`uv run myha-simulator --port 8123`, sobald Task 2.1 existiert; vorher übersprungen), Gateway mit `MYHA_DATA_DIR=.dev/data MYHA_HA_WS_URL=ws://127.0.0.1:8123/api/websocket MYHA_SUPERVISOR_URL=http://127.0.0.1:8123 SUPERVISOR_TOKEN=sim-token MYHA_TRUSTED_INGRESS_PEER=127.0.0.1 MYHA_PUBLIC_ENABLED=1 MYHA_PUBLIC_ORIGIN=http://localhost:8443 MYHA_WEB_DIST=myha/web/dist`, und `pnpm dev`; `trap` beendet alle Prozesse.
- [ ] **Step 2:** Commit `chore: dev script`.

---

## WP1 – S0-Real-Probe (parallel zu WP2–WP4; mutierende Schritte führt Fable nach Dimitris Zustimmung aus)

### Task 1.1: Supervisor-Skript

**Files:**
- Create: `scripts/install-on-ha.py` (PEP-723-Header: `# /// script\n# dependencies = ["websockets>=15", "httpx>=0.28"]\n# ///`; Aufruf `uv run scripts/install-on-ha.py <cmd>`)

- [ ] **Step 1: Verhalten.** Liest `~/.claude.json`, findet den MCP-Eintrag `homeassistant` (`url`, `headers.Authorization`); leitet daraus `ws://<host>/api/websocket` und `http://<host>` ab; Token nie ausgeben. WS-Hilfsfunktion:

```python
async def sup(ws, i, endpoint, method="get", data=None, timeout=None):
    msg = {"id": i, "type": "supervisor/api", "endpoint": endpoint, "method": method}
    if data is not None: msg["data"] = data
    if timeout is not None: msg["timeout"] = timeout      # None = kein Timeout (für install/rebuild)
    await ws.send(json.dumps(msg))
    while True:
        r = json.loads(await ws.recv())
        if r.get("id") == i: return r
```

Subcommands: `status` (`/supervisor/info` Version, `/store/repositories`, Installationsstatus des Add-ons mit Slug-Suffix `_myha`), `add-repo` (`POST /store/repositories {"repository": "https://github.com/LL0rd/myha"}`), `reload` (`POST /store/reload`), `install` (`POST /store/addons/<slug>/install`, `timeout=None`), `rebuild` (`POST /addons/<slug>/rebuild`, `timeout=None`), `update` (`POST /store/addons/<slug>/update`), `start`/`stop`/`restart`, `options <json>` (`POST /addons/<slug>/options {"options": {...}}`), `network <hostport>` (`POST /addons/<slug>/options {"network": {"8443/tcp": <hostport>}}`), `logs` (**REST-Proxy** `GET http://<host>/api/hassio/addons/<slug>/logs` mit Bearer, Text; falls 401: Hinweis, Logs in der HA-GUI zu lesen), `backup-new` (`POST /backups/new/partial {"name": "myha-probe", "addons": [slug]}` → `data.slug`), `backup-download <backup-slug> <out.tar>` (REST-Proxy `GET /api/hassio/backups/<slug>/download`), `ingress-get <path>` (`POST /ingress/session` → `data.session`; dann REST `GET http://<host>/api/hassio_ingress/<ingress_token>/<path>` mit Cookie `ingress_session=<session>`; `ingress_token` aus `GET /addons/<slug>/info` → `data.ingress_entry`). Mutierende Befehle fragen `[y/N]`, außer `--yes` ist gesetzt (nur Fable verwendet `--yes`).
- [ ] **Step 2:** `uv run scripts/install-on-ha.py status` zeigt die Supervisor-Version. Commit `chore: supervisor install and probe script`.

### Task 1.2: Probe-Endpunkte erweitern (Subagent) und auf dem HA installieren (Fable)

**Files:**
- Modify: `myha_gateway/api/admin/probe.py`, `myha_gateway/api/public/health.py`, `myha_gateway/__main__.py`
- Create: `myha_gateway/ha/supervisor.py`, `tests/unit/test_supervisor.py`

- [ ] **Step 1: Test für `supervisor.py`** (`httpx.MockTransport`): `GET {supervisor_url}/info` → `{"result":"ok","data":{"supervisor":"2026.10.3","channel":"stable"}}` → `SupervisorInfo(version="2026.10.3", channel="stable")`; `is_at_least("2026.10.3")` wahr, `"2026.10.2"` falsch; `host_info()` → `dt_synchronized` oder `None` bei 401/403; `self_info()` → `ingress_entry`; HTTP 401 auf `/info` → `SupervisorUnavailable`.
- [ ] **Step 2:** Implementieren (Bearer aus `settings.supervisor_token`; Versionsvergleich über `tuple(int)`).
- [ ] **Step 3:** `GET /api/v1/admin/probe` liefert zusätzlich `supervisor_info`, `host_info`, `self_info` (Erfolg oder Fehlertext), `env_names`, `cwd_listing_of_data` (nur Namen). Public `GET /healthz` liefert `client_ip`, `x_forwarded_for`, `x_real_ip`. Im `probe_mode` loggt das Gateway beim Start: Supervisor-Version, `/host/info`-Ergebnis, Argon2-Messung (Task 1.4), legt `/data/state/probe-live.marker` an.
- [ ] **Step 4 (Fable, nach Dimitris Zustimmung):** Push; `uv run scripts/install-on-ha.py --yes add-repo && reload && install && options '{"log_level":"debug","probe_mode":true}' && network 8443 && start`; `ingress-get api/v1/admin/probe`; von diesem Rechner `curl http://<HA-LAN-IP>:8443/healthz` und, falls IPv6 vorhanden, `curl -6`; `logs`.
- [ ] **Step 5:** `docs/s0-report.md` (ohne LAN-IPs, Tokens, User-IDs): Supervisor-Version, Peer-IP, vorhandene Identitätsheader (inkl. `X-Remote-User-Groups` ja/nein), `/info` ohne `hassio_api` ja/nein, `/host/info` Ergebnis (falls 401/403: Test mit `hassio_api: true`, `hassio_role: default` wiederholen und E26 festhalten), Client-IP am Public-Port (echte LAN-IP oder Docker-Gateway), IPv6, s6-Warnungen im Log, Containerstart in Sekunden, Image-Größe. Commit `docs: S0 probe results`.

### Task 1.3: Backup-Hooks real prüfen (Fable)

- [ ] **Step 1:** `uv run scripts/install-on-ha.py --yes backup-new` → Backup-Slug; `logs` zeigt `backup-export` und `backup-cleanup`.
- [ ] **Step 2:** `backup-download <slug> /tmp/myha-probe.tar`; `tar -tvf` und das innere Add-on-Archiv auflisten: `backup/PROBE-export.txt` muss enthalten sein, `state/probe-live.marker` **nicht**.
- [ ] **Step 3:** Ergebnis und ggf. korrigierte `backup_exclude`-Muster in `docs/s0-report.md` und `config.yaml`. Commit `docs: S0 backup hook results`.

### Task 1.4: Argon2-Parameter und Klimaanlagen-Verhalten

- [ ] **Step 1 (Subagent):** `myha_gateway/auth/passwords.py`: `measure_argon2()` misst `PasswordHasher(time_cost=t, memory_cost=m, parallelism=1)` für t∈{2,3,4}, m∈{19456, 47104, 65536} und gibt ms je Kombination zurück; im `probe_mode` beim Start geloggt. Test: Funktion liefert neun Werte > 0.
- [ ] **Step 2 (Fable, nur mit Dimitris Zustimmung):** An **einer** Klimaanlage im Wohnzimmer über die HA-REST-API: Ausgangswerte `hvac_mode` und `temperature` notieren; `climate.set_hvac_mode` auf den jeweils anderen Modus (`cool` ↔ `heat`) und zurück; nach jedem Schritt `temperature` lesen; **am Ende Modus und Sollwert wiederherstellen**. Frage: bleibt der Sollwert erhalten oder springt er je Modus? Ohne Zustimmung: Punkt offen lassen.
- [ ] **Step 3:** Ergebnisse in `docs/security.md` (Argon2-Wahl) und `docs/s0-report.md` („Klimaanlage Moduswechsel“); Design E7/§14 bei Bedarf anpassen. Commit `docs: S0 argon2 timing and climate mode behaviour`.

### Task 1.5: Android-Transport-Spike (privates Repo, parallelisierbar)

**Files (in `/home/dimitri/Projekte/myha-apps`):**
- Create: `spikes/transport-android/` (Gradle 8.x mit Wrapper, Kotlin JVM-Projekt, Foojay-Toolchain-Resolver, JDK 17 Toolchain, OkHttp 4.12, JUnit 5), `spikes/tls-testserver/server.py` (PEP 723: `websockets`; HTTPS `/ping` + WS-Echo mit zwei umschaltbaren Zertifikaten), `spikes/tls-testserver/make-certs.sh`, `spikes/README.md`

- [ ] **Step 1:** Zertifikate: eigene Test-CA, zwei Serverschlüssel (A, B) für `localhost`; SPKI-Pins (`openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64`).
- [ ] **Step 2:** JUnit-Tests (starten den Python-Server je Test über `ProcessBuilder`): richtiger Pin → `/ping` 200 und WS-Echo; falscher Pin → beide `SSLPeerUnverifiedException`; falscher Hostname → beide scheitern; Pinwechsel A→B: alter Client wird geschlossen (`connectionPool.evictAll()` + WS `close`), neuer Client mit Pin B verbindet, Pin A wird abgelehnt; Redirect (`302` auf anderen Host) wird nicht gefolgt (`followRedirects(false)`). Test-CA nur über einen eigenen `X509TrustManager` für den Test; kein `trust all`.
- [ ] **Step 3:** `./gradlew test` grün; Ergebnisse in `spikes/README.md` und zusammengefasst in `myha/docs/s0-report.md`. Commits in beiden Repos.

### Task 1.6: S0-Bericht abschließen (Fable)

- [ ] **Step 1:** `docs/s0-report.md` vervollständigen (Tabelle Annahme → Ergebnis → Konsequenz); Design §14 aktualisieren; `probe_mode` aus `config.yaml`/Run-Skript/Settings entfernen und den Probe-Endpunkt löschen (Commit `chore: remove S0 probe mode`).
- [ ] **Step 2:** Commit `docs: complete S0 report`. Kein Gate für WP2–WP4; Gate für Teilplan 2 (Masterplan).

---

## WP2 – HA-Simulator und HAClient

### Task 2.1: Simulator-Fixtures, WS-Protokoll, Steuer-API

**Files:**
- Create: `myha_simulator/fixtures.py`, `myha_simulator/server.py`, `myha_simulator/__main__.py`, `tests/conftest.py` (Fixture `sim` startet den Simulator in-process auf freiem Port und liefert Basis-URLs und einen `SimControl`-Client für die `_sim`-API)
- Test: `tests/integration/test_simulator.py`

- [ ] **Step 1: Fixture (`fixtures.py`)** – jede Entity mit `unique_id`, `device_id`, `area_id`, `platform`:

| entity_id | area | platform | Attribute |
|---|---|---|---|
| `light.room_203_ceiling` | `area_203` | `govee` | `supported_color_modes ["color_temp","rgb"]`, `brightness 178`, on |
| `light.room_203_bedside` | `area_203` | `shelly` | `supported_color_modes ["onoff"]`, on |
| `climate.room_203_trv` | `area_203` | `zha` | `hvac_modes [heat, off]`, `min_temp 7, max_temp 30, target_temp_step 0.5, temperature 21.5, current_temperature 21.0`, `heat` |
| `cover.room_203_blind` | `area_203` | `zha` | `supported_features 15, current_position 100`, `open` |
| `sensor.room_203_humidity` | `area_203` | `zha` | `46`, `%`, `device_class humidity` |
| `light.living_ceiling` | `area_living` | `govee` | `["color_temp","rgb"]`, `brightness 140`, on |
| `switch.living_floor_lamp` | `area_living` | `shelly` | off |
| `climate.living_ac` | `area_living` | `midea` | `hvac_modes [auto,cool,dry,fan_only,heat,off]`, `8/30/1`, `temperature 22, current 24`, `cool` |
| `cover.kitchen_blind` | `area_kitchen` | `zha` | `supported_features 11`, `closed` |
| `light.hallway` | `area_hall` | `shelly` | `["onoff"]`, on |
| `light.group_all` | – | `group` | `["onoff"]` (muss Review auslösen) |
| `climate.room_204_dual` | `area_204` | `generic` | `target_temp_high 24, target_temp_low 20` (gesperrt) |
| `sensor.outdoor_temp` | – | `openweathermap` | `12.3`, `°C` |
| `camera.lobby` | `area_hall` | `unifiprotect` | darf nie projiziert werden |
| `person.owner` | – | `person` | darf nie projiziert werden |

Areas: `area_203` „Room 203“, `area_204` „Room 204“, `area_living` „Living room“, `area_kitchen` „Kitchen“, `area_hall` „Hallway“. Users (`config/auth/list`): `owner1` („Owner“, `is_owner`, `system-admin`), `user1` („Regular user“, `system-users`), `sup` („Supervisor“, `system-admin`, `system_generated`). Config: `unit_system.temperature "°C"`, `time_zone "Europe/Sofia"`, `version "2026.10.2"`. Tokens: `sim-token` (admin), `sim-user-token` (nicht admin).

- [ ] **Step 2: Server** (FastAPI, `websockets`-kompatibel über Starlette-WS): `WS /api/websocket` mit `auth_required → auth → auth_ok|auth_invalid`; Befehle `get_states`, `get_config`, `subscribe_events` (nur `state_changed`), `unsubscribe_events`, `call_service` (light/switch/climate/cover realistisch, `state_changed` an Abonnenten, `result` mit `context`), `config/entity_registry/list`, `config/device_registry/list`, `config/area_registry/list`, `config/auth/list` (nur Admin-Token; Verhalten je `auth_list_mode`), `ping`→`pong`; unbekannt → `{"success": false, "error": {"code": "unknown_command"}}`. HTTP: `GET /info` (Bearer; Version aus `supervisor_version`, Default `2026.10.3`), `GET /host/info` (Default 403; `host_info_mode` → `ok` liefert `dt_synchronized true`), `GET /addons/self/info` (`ingress_entry "/api/hassio_ingress/simtoken"`).
  **Steuer-API** (`/_sim/...`, nur lokal):

| Endpunkt | Wirkung |
|---|---|
| `POST /_sim/reset` | Fixture-Zustand und alle Modi zurücksetzen |
| `POST /_sim/state/{entity_id}` `{state?, attributes?}` | setzt Zustand, löst `state_changed` aus |
| `POST /_sim/offline/{entity_id}` / `POST /_sim/online/{entity_id}` | `unavailable` setzen/aufheben |
| `POST /_sim/fail-next-service {"error": "service_not_found"}` | nächster `call_service` scheitert |
| `POST /_sim/drop-connections` | schließt alle WS-Verbindungen |
| `POST /_sim/clock-skew {"seconds": n}` | verschiebt `time_fired` |
| `GET /_sim/service-calls` / `POST /_sim/service-calls/reset` | Liste aller `call_service` (domain, service, entity_id, data, context_id) |
| `POST /_sim/auth/users [..]` | ersetzt die Nutzerliste |
| `POST /_sim/auth/list-mode {"mode": "ok"\|"error"\|"schema_missing_groups"}` | Verhalten von `config/auth/list` |
| `POST /_sim/supervisor/version {"version": "2026.10.2"}` | Antwort von `/info` |
| `POST /_sim/host-info-mode {"mode": "ok"\|"forbidden"}` | |
| `POST /_sim/climate/mode-setpoint {"entity_id","mode","temperature"}` | beim Wechsel in `mode` springt `temperature` auf den Wert (E7-Nachprüfung) |
| `POST /_sim/registry/rename {"entity_id","new_entity_id"}` | Umbenennung bei gleicher `unique_id` |
| `POST /_sim/registry/replace {"entity_id","new_unique_id"}` | Geräteersatz |
| `POST /_sim/registry/area {"entity_id","area_id"}` | Bereichswechsel |
| `POST /_sim/entity/attributes {"entity_id","attributes"}` | z. B. `supported_features` ändern (Drift) |

- [ ] **Step 3: Tests:** Auth ok/ungültig; `get_states` enthält 15 Entities; `call_service light.turn_on` ändert Zustand und sendet Event mit `context.id` und landet im Service-Call-Log; `config/auth/list` mit `sim-user-token` → Fehler; `list-mode schema_missing_groups` liefert Nutzer ohne `group_ids`; `fail-next-service` wirkt genau einmal; `drop-connections` schließt; `mode-setpoint` springt beim Moduswechsel; `registry/rename` behält `unique_id`.
- [ ] **Step 4:** Commit `feat(simulator): fake Home Assistant core and supervisor with control api`.

### Task 2.2: HAClient

**Files:**
- Create: `myha_gateway/ha/messages.py`, `myha_gateway/ha/client.py`
- Test: `tests/integration/test_ha_client.py`

- [ ] **Step 1: Schnittstelle**

```python
class HAClient:
    def __init__(self, ws_url: str, token: str, *, on_event: Callable[[StateChanged], Awaitable[None]], on_status: Callable[[ConnectionStatus], Awaitable[None]], clock: Callable[[], datetime] = utcnow) -> None: ...
    async def start(self) -> None     # verbindet, authentifiziert, lädt Snapshot + Registries + config, abonniert state_changed, startet Reconnect-Loop (Backoff 1 s → 30 s)
    async def stop(self) -> None
    connected: bool; generation: int; adapter_locked: bool; temperature_unit: str | None
    def state(self, entity_id: str) -> HAState | None
    def all_states(self) -> dict[str, HAState]
    def registry(self) -> Registry                       # entities (unique_id, device_id, area_id, platform), devices, areas
    async def call_service(self, domain: str, service: str, entity_id: str, data: dict[str, Any]) -> ServiceResult   # HAServiceError bei success=false
    async def auth_list(self) -> list[HAUser]            # config/auth/list ohne Cache; HAProtocolError bei fehlenden Feldern
    def last_event_skew_seconds(self) -> float | None    # nur Diagnose
```

- [ ] **Step 2: Tests (rot):** Snapshot enthält `light.living_ceiling`; Event-Callback nach `_sim/state`; `call_service` liefert `context_id`; nach `drop-connections` Reconnect < 3 s, `generation` +1, `adapter_locked` bis `get_config` wahr, danach falsch; `on_status` meldet `disconnected`/`connected`; `auth_list` liefert 3 Nutzer, mit `schema_missing_groups` → `HAProtocolError`; `fail-next-service` → `HAServiceError`; Registry nach `registry/rename` aktualisiert (Client lädt Registries bei `entity_registry_updated`-Event oder alle 5 min neu – im Simulator wird `entity_registry_updated` nach Registry-Änderungen gesendet und `subscribe_events` dafür unterstützt).
- [ ] **Step 3:** Implementieren (`websockets`, Request-IDs monoton, `Future` je ID, `ping` alle 20 s, `max_size` 16 MiB – in S0 gegen reale `get_states`-Größe prüfen, Token nie loggen).
- [ ] **Step 4:** Commit `feat(gateway): resilient Home Assistant websocket client`.

---

## WP3 – Datenmodell, Adapter, Policy

### Task 3.1: Datenbank, Modelle, Migration

**Files:**
- Create: `myha_gateway/db/__init__.py`, `db/models.py`, `alembic.ini`, `db/migrations/env.py`, `db/migrations/versions/0001_initial.py`
- Test: `tests/unit/test_db.py`

- [ ] **Step 1: Tests:** Pragmas `journal_mode=wal`, `foreign_keys=1`, `secure_delete=1`; `run_migrations(engine)` erzeugt alle Tabellen aus Design §5; FK durchgesetzt; `instance`-Singleton beim ersten Start mit `id`, `short_id` (8 Zeichen), `server_secret`, `policy_version=1`, `security_generation=1`, `session_limits`-Defaults (`guest_browser_days 30, resident_browser_days 30, resident_refresh_days 30, display_days 365, native_access_minutes 15`), `audit_retention_days 30`, `time_trusted=1`, `last_seen_clock`.
- [ ] **Step 2: Modelle** exakt nach Design §5 inklusive `instance.last_seen_clock`, `command.body_hash`, `resource_binding.unique_id`; Unique-Indizes: `credential.username_normalized`, `session.token_hash`, `invite.secret_hash`, `(command.session_id, command.idempotency_key)`, `refresh_token.token_hash`, `ws_ticket.ticket_hash`, `pairing_request.poll_token_hash`. JSON-Spalten als `JSON`; Zeitpunkte als ISO-8601-`Z`-Strings über `TypeDecorator` ↔ `datetime(UTC)`; `version` (Integer, Default 1) auf admin-editierbaren Tabellen. Schreibtransaktionen beginnen mit `BEGIN IMMEDIATE` (Engine-Event), damit parallele Redeems nicht in `database is locked` laufen.
- [ ] **Step 3:** Alembic async-Env (`run_sync`), Migration 0001 per Autogenerate, manuell geprüft.
- [ ] **Step 4:** Commit `feat(gateway): sqlite schema, pragmas and initial migration`.

### Task 3.2: Adapter – Fähigkeiten, Übersetzung, StateView, Expectation

**Files:**
- Create: `myha_gateway/adapters/base.py`, `registry.py`, `light.py`, `switch.py`, `climate.py`, `cover.py`, `sensor.py`
- Test: `tests/unit/test_adapters.py`

- [ ] **Step 1: Typen (`base.py`)**

```python
class Action(StrEnum):
    METADATA_READ="metadata.read"; STATE_READ="state.read"; HISTORY_READ="history.read"
    SWITCH_ON="switch.on"; SWITCH_OFF="switch.off"
    LIGHT_ON="light.on"; LIGHT_OFF="light.off"; LIGHT_SET_LEVEL="light.set_level"
    CLIMATE_SET_TEMPERATURE="climate.set_temperature"; CLIMATE_SET_MODE="climate.set_mode"
    COVER_OPEN="cover.open"; COVER_CLOSE="cover.close"; COVER_STOP="cover.stop"; COVER_SET_POSITION="cover.set_position"
WRITE_ACTIONS: frozenset[Action]  # alle außer den drei *_READ

@dataclass(frozen=True)
class DeviceCapabilities:
    kind: Literal["light","switch","climate","cover","sensor"]
    actions: frozenset[Action]                      # physisch mögliche Schreibaktionen
    target_c_min: Decimal | None = None; target_c_max: Decimal | None = None; target_c_step: Decimal | None = None
    hvac_modes: frozenset[str] = frozenset()        # roh vom Gerät
    confirmed_modes: frozenset[str] = frozenset()   # Admin-bestätigt ⊆ {off,cool,heat}
    blocked_reason: str | None = None               # "group_entity" | "template_entity" | "dual_setpoint" | "unit_not_celsius" | "no_setpoint"
    fingerprint: str = ""                           # sha256 über kind, actions, Bereiche, hvac_modes, supported_features, color_modes

@dataclass(frozen=True)
class StateView:
    availability: Literal["online","offline","unknown"]; adapter_locked: bool
    power: str | None = None; level_pct: int | None = None
    target_c: Decimal | None = None; current_c: Decimal | None = None; hvac_mode: str | None = None
    position_pct: int | None = None; value: str | None = None; unit: str | None = None
    observed_at: datetime | None = None

@dataclass(frozen=True)
class HACall: domain: str; service: str; entity_id: str; data: dict[str, Any]

@dataclass(frozen=True)
class Expectation:
    """Alle gesetzten Felder müssen zutreffen; target_c_range wird vom Aufrufer (Policy-Prüfbereich) gesetzt."""
    power: str | None = None; level_pct: int | None = None; level_tolerance: int = 1
    hvac_mode: str | None = None; target_c: Decimal | None = None
    target_c_range: tuple[Decimal, Decimal] | None = None
    position_pct: int | None = None; position_tolerance: int = 1
    cover_state: str | None = None
    def matches(self, view: StateView) -> bool: ...
    def violated_range(self, view: StateView) -> bool: ...   # True, wenn target_c_range gesetzt und target_c außerhalb/None

class Adapter(Protocol):
    kind: str
    def capabilities(self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None) -> DeviceCapabilities: ...
    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView: ...
    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall: ...
    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation: ...
    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]: ...
```

- [ ] **Step 2: Tests (rot):** Light: `["color_temp","rgb"]` → {on, off, set_level} (HA-Semantik: jeder Color-Mode außer `onoff`/`unknown` hat Helligkeit); `["onoff"]` → {on, off}; platform `group` → `blocked_reason="group_entity"`, `template` → `template_entity`; `translate(set_level, 40)` → `light.turn_on {brightness_pct: 40}`; `set_level 0` → `light.turn_off`; `required_actions(set_level, 0)` = {set_level, off}, `(…, 40)` = {set_level, on}; `expectation(set_level, 40)` → `power="on", level_pct=40`. Climate: AC → `8/30/1`, 6 `hvac_modes`, `confirmed_modes` leer; fehlender `target_temp_step` → `0.5`; dual → `dual_setpoint`; `"°F"` → `unit_not_celsius`; fehlendes `temperature`-Attribut → `no_setpoint`; `translate(set_temperature, "21.5")` → `{temperature: 21.5}`; `translate(set_mode, "cool")` → `climate.set_hvac_mode {hvac_mode: "cool"}`; `expectation(set_mode, "cool")` → `hvac_mode="cool"`; `state_view` → `target_c=Decimal("22")`, `hvac_mode="cool"`. Cover: 15 → inkl. set_position; 11 → ohne; `required_actions(set_position, 0)` = {set_position, close}, `100` → {set_position, open}, `40` → {set_position, open, close}. Sensor: `value "46"`, `unit "%"`. Switch: {on, off}. `registry.detect_kind`; `fingerprint` ändert sich bei anderen `supported_features`. `Expectation.violated_range` wahr bei `target_c=28` und Range (19, 24) sowie bei `target_c=None`.
- [ ] **Step 3–4:** Implementieren; `unavailable`/`unknown` → `availability`. mypy strict grün. Commit `feat(gateway): device adapters with capabilities, translation, state views and expectations`.

### Task 3.3: Policy-Modell und Codes

**Files:**
- Create: `myha_gateway/policy/codes.py`, `policy/model.py`
- Test: `tests/unit/test_policy_model.py`

- [ ] **Step 1: `codes.py`:** `DecisionCode(StrEnum)` in der Reihenfolge aus Design §4.1 (inkl. `RESOURCE_NEEDS_REVIEW`); `PUBLIC_CODE: dict[DecisionCode, str]`; `PublicError(StrEnum)` mit den acht öffentlichen Codes.
- [ ] **Step 2: `model.py`** (Pydantic, `extra="forbid"`): `Interval(min, max, step, origin=0)` (Validatoren), `RangeRule(intervals)`, `EnumRule(allowed)`, `Grant(resources, actions, parameters={})`, `Deny(resources, actions)`, `Fence(scope_type, scope_id, resource_ids|None, action, parameters)`, `RoleRules(grants, denies)`; Request-Schemas `LevelParams(level_pct 0..100)`, `TargetCParams(target_c: str` Regex `^-?\d{1,2}(\.\d)?$` → Decimal`)`, `ModeParams(mode: Literal["off","cool","heat"])`, `PositionParams(position_pct 0..100)`, `NoParams`; `PARAM_SCHEMA: dict[Action, type[BaseModel]]`.
- [ ] **Step 3: Tests:** gültige/ungültige `target_c` (`"21.5"` ok; `"21.55"`, `"NaN"`, `"1e1"`, `"abc"`, `""` → Fehler); `extra`-Feld → Fehler; `Interval(min=24, max=19)` → Fehler; `PUBLIC_CODE[NO_GRANT] == "ACTION_NOT_ALLOWED"`, `PUBLIC_CODE[RESOURCE_NEEDS_REVIEW] == "UPSTREAM_UNAVAILABLE"`.
- [ ] **Step 4:** Commit `feat(policy): rule models, parameter schemas and decision codes`.

### Task 3.4: Effektiver Kontext und PolicyIndex

**Files:**
- Create: `myha_gateway/policy/context.py`, `policy/index.py`
- Test: `tests/unit/test_policy_index.py`

- [ ] **Step 1: Typen**

```python
@dataclass(frozen=True)
class RoleBinding: role_template_id: str; role_version: int; unit_ids: frozenset[str]
@dataclass(frozen=True)
class EffectiveContext:
    principal_ref: str                          # principal_id | "hypothetical:guest:<stay_id>" | "hypothetical:group:<group_id>"
    role_bindings: tuple[RoleBinding, ...]; fences: tuple[Fence, ...]
    not_before: datetime | None; not_after: datetime | None
    stay_id: str | None; purpose: Literal["access","preview","test"]; policy_version: int
    session_active: bool = True; time_trusted: bool = True
@dataclass(frozen=True)
class ResourceRef: id: str; unit_id: str; kind: str; status: Literal["confirmed","needs_review","blocked"]; capabilities: DeviceCapabilities
@dataclass(frozen=True)
class EffectiveCapability: intervals: tuple[Interval, ...] = (); allowed: frozenset[str] = frozenset(); matched: tuple[str, ...] = ()

class PolicyIndex:  # unveränderlich
    def __init__(self, role_versions: Mapping[tuple[str,int], RoleRules], resources: Mapping[str, ResourceRef], units: Mapping[str, frozenset[str]]) -> None: ...
    def rules_for(self, ctx) -> list[tuple[RoleBinding, RoleRules]]
    def resource(self, resource_id) -> ResourceRef | None
    def in_scope(self, ctx, resource_id) -> bool
    def effective_capability(self, ctx, resource_id, action) -> EffectiveCapability | None   # None = nicht gewährt oder verweigert
```

`context.py`: `build_context_for_session(session, principal, assignments, memberships, stay, fences, instance, now)` nach der Quellentabelle in Design §4.1 (reine Funktion über geladene Zeilen; das Laden kommt in Teilplan 2 mit `policy/loader.py`); `build_context_for_preview(target, rows..., at)` für die vier Zielarten; `time_trusted` kommt aus `instance.time_trusted`.

- [ ] **Step 2: Tests:** Bewohner mit Gruppen (direkt + Gruppe; abgelaufene Mitgliedschaft zählt nicht); Gast → genau eine Bindung aus dem Stay und Fenster `[starts_at, min(ends_at, not_after))`; Vorschau `stay_id` → hypothetischer Ref; `effective_capability`: Vereinigung (18–20, 22–24 bleiben getrennt), Fence `≤ 22` schneidet, Gerätebereich schneidet, Deny → `None`, Modus-Schnitt `{off, cool, heat} ∩ {off, cool}` → `{off, cool}`; Session-Fences gelten nur für diese Session.
- [ ] **Step 3–4:** Implementieren (Decimal), Commit `feat(policy): effective context and immutable policy index`.

### Task 3.5: `decide()` und Abnahmefälle A06–A18

**Files:**
- Create: `myha_gateway/policy/decide.py`, `tests/helpers.py` (Index-Builder `room203_index()` aus Simulator-Fixture-Daten ohne laufenden Simulator)
- Test: `tests/acceptance/test_matrix_policy.py`, `tests/unit/test_decide.py`

- [ ] **Step 1: Signatur**

```python
@dataclass(frozen=True)
class Decision:
    allowed: bool; code: DecisionCode; public_code: str
    normalized_params: dict[str, Any]; matched: tuple[str, ...]; message: str
    check_range: tuple[Decimal, Decimal] | None = None   # Prüfbereich für set_mode cool/heat (E7), für die Expectation

def decide(ctx: EffectiveContext, index: PolicyIndex, resource_id: str, action: Action | str, params: Mapping[str, Any], now: datetime, state_view: StateView | None, *, execute: bool = False) -> Decision: ...
```

Reihenfolge exakt Design §4.1; `RESOURCE_UNCONFIRMED` für `blocked`/nie bestätigt (alle Aktionen), `RESOURCE_NEEDS_REVIEW` nur für `WRITE_ACTIONS`; `required_actions` des Adapters für zusammengesetzte Aktionen; `metadata.read` implizit, wenn irgendeine Aktion oder `state.read` gewährt ist; Regel „Modus“ und Regel „Zeit“ wie Design.

- [ ] **Step 2: Testtabelle (rot)** mit `room203_index()` (Ressourcen `res_203_light` = `light.room_203_ceiling`, `res_203_bedside`, `res_203_trv`, `res_203_blind`, `res_203_hum`, `res_lr_ceiling`, `res_lr_ac` …; Rolle `room_guest` v1 wie Spez §7 plus `light.set_level` auf dem Hauptlicht):

| Test | Aufbau | Erwartung |
|---|---|---|
| `test_a06_foreign_resource_is_not_found` | Gast 203 fragt `res_lr_ceiling` und `res_unknown` | beide `RESOURCE_NOT_IN_SCOPE` → `NOT_FOUND`, identische Message |
| `test_a07_off_only` | Grant nur `light.off` | `light.on`, `set_level 40` → `NO_GRANT`; `light.off` → `GRANTED` |
| `test_a08_on_only` | Grant nur `light.on` | `light.off`, `set_level 0` → `NO_GRANT`; `{"level_pct": 40, "flash": "short"}` → `PARAM_SCHEMA` |
| `test_a09_temperature_range_and_step` | Grant 19–24/0.5 | `21.5` GRANTED; `24.1` PARAM_OUT_OF_RANGE; `21.3` PARAM_STEP; `"NaN"` PARAM_SCHEMA; extra `hvac_mode` → PARAM_SCHEMA |
| `test_a10_two_grants_do_not_merge` | 18–20 und 22–24 | `21` PARAM_OUT_OF_RANGE; `19`, `23` GRANTED |
| `test_a11_fence_narrows_wide_grant` | Gruppe 19–23, Fence principal ≤ 22 | `22.5` FENCE_VIOLATION; `22` GRANTED |
| `test_a12_deny_wins` | Grant + Deny `light.on` | DENIED_BY_RULE |
| `test_a13_unknown_action_and_wildcards` | `light.toggle`, `*`, `light.*`, Ressource `*` | UNKNOWN_ACTION bzw. RESOURCE_NOT_IN_SCOPE → NOT_FOUND |
| `test_a14_new_device_not_granted` | Entity ohne Binding | NOT_FOUND |
| `test_a15_group_entity_blocked` | Binding `blocked` (`group_entity`) | `RESOURCE_UNCONFIRMED` → NOT_FOUND, auch für `state.read` |
| `test_a16_needs_review_blocks_writes_only` | Binding `needs_review` | `light.on` → `RESOURCE_NEEDS_REVIEW` → UPSTREAM_UNAVAILABLE; `state.read` GRANTED |
| `test_a16b_adapter_locked` | `state_view.adapter_locked` | Schreiben → ADAPTER_LOCKED; Lesen GRANTED |
| `test_a17_cover_position_requires_both_directions` | Grant `cover.open` + `set_position` | `100` GRANTED; `0`, `40` NO_GRANT |
| `test_a18_mode_cannot_bypass_fence` | set_temperature 19–24, set_mode {off,cool,heat}, Gerät target 28 | `cool` → MODE_WOULD_BYPASS_FENCE; target 22 → GRANTED mit `check_range=(19,24)`; `off` GRANTED; target `None` → MODE_WOULD_BYPASS_FENCE; nur set_mode-Grant ohne Temperaturgrant/Fence → GRANTED bei 28; nur set_mode-Grant mit Instanz-Fence ≤ 24 → Prüfbereich = Fence ∩ Gerät → 28 → MODE_WOULD_BYPASS_FENCE |
| `test_stay_window` | vor `starts_at` / nach `ends_at` | STAY_NOT_STARTED / STAY_ENDED → ACCESS_EXPIRED |
| `test_time_untrusted_blocks_stays_only` | `time_trusted=False` | Gast TIME_UNTRUSTED; Bewohner GRANTED |
| `test_preview_context_rejected_on_execute` | `purpose=preview`, `execute=True` | PREVIEW_CONTEXT; mit `execute=False` normal bewertet |
| `test_dst_boundary` | Stay-Ende 2026-03-29 03:00 Europe/Sofia | exklusive Grenze korrekt in UTC |
| `test_decide_p95_under_20ms` | 1000 Aufrufe | p95 < 20 ms |

- [ ] **Step 3–4:** Implementieren, grün, Commit `feat(policy): pure decision function with acceptance matrix A06-A18`.

---

## WP4 – Admin-Identität und Listener-Finalisierung

### Task 4.1: IngressGuard mit Rollenprüfung und CSRF

**Files:**
- Create: `myha_gateway/auth/admin.py`
- Modify: `myha_gateway/app.py` (ersetzt `PeerGuardMiddleware`), `api/admin/probe.py` → zusätzlich `GET /api/v1/admin/session`
- Test: `tests/acceptance/test_matrix_admin.py`, `tests/unit/test_admin_csrf.py` (Tests nutzen die `sim`-Fixture und eine `admin_client`-Fixture mit Peer `172.30.32.2` und Header `X-Remote-User-Id: owner1`)

- [ ] **Step 1: Verhalten.** `require_admin(fresh: bool)` als Dependency: (1) Peer == `trusted_ingress_peer` sonst `403 UNAUTHENTICATED`; (2) genau ein `X-Remote-User-Id`-Header (`request.headers.getlist`), sonst 403; (3) Supervisor-Version ≥ Minimum (Ergebnis 60 s gecacht; zu alt oder nicht erreichbar → `503 UPSTREAM_UNAVAILABLE`, `details.reason="supervisor_version"|"supervisor_unreachable"`); (4) `ha.auth_list()` → Nutzer exakt per `id`; `is_active and (is_owner or "system-admin" in group_ids)`; `fresh=False`: Ergebnis 5 s gecacht (Schlüssel user_id); `fresh=True`: immer neu; `HAProtocolError`/Verbindungsfehler/Nutzer unbekannt → 403 ohne Rückfall (A05). `AdminIdentity(user_id, name, display_name, groups_header)`. Mutationen (`POST/PUT/PATCH/DELETE`) und sensible GETs (Design E5) verwenden `fresh=True` und `require_csrf`: `X-MyHA-CSRF == hmac_sha256(server_secret, f"{user_id}|{ingress_path}|{YYYY-MM-DD}")` für heute oder gestern (UTC), `Sec-Fetch-Site` falls vorhanden `same-origin`. `GET /api/v1/admin/session` → `{user: {id, name}, csrf_token, supervisor_version, core_version, setup_state, time_trusted}`.
- [ ] **Step 2: Tests (rot):** `test_a01_public_listener_ignores_identity_headers`, `test_a02_wrong_peer_rejected`, `test_a03_missing_duplicate_headers_and_old_supervisor` (kein Header; zwei Header; `_sim/supervisor/version 2026.10.2` → 503 mit Hinweis), `test_a04_demoted_admin_blocked_on_next_mutation` (nach `POST /_sim/auth/users` mit `owner1` → `system-users`: GET innerhalb 5 s noch 200, POST mit gültigem CSRF sofort 403), `test_a05_auth_list_failure_no_fallback` (`list-mode error` → 403; `schema_missing_groups` → 403; auch nach vorherigem Erfolg bei `fresh=True`), CSRF: fehlend/falsch/vorgestern → 403; `Sec-Fetch-Site: cross-site` → 403; `same-origin` ok; fehlend ok.
- [ ] **Step 3–4:** Implementieren (Cache mit `time.monotonic`), grün, Commit `feat(auth): ingress guard with fresh admin role checks and csrf (A01-A05)`.

### Task 4.2: Startreihenfolge und Public-Listener-Schalter

**Files:**
- Modify: `myha_gateway/app.py`, `__main__.py`, `deps.py`
- Test: `tests/integration/test_startup.py`

- [ ] **Step 1: Verhalten.** Lifespan: Migrationen → Restore-Check (`backup_dir/restore.sqlite` vorhanden → `ctx.restore_mode=True`; Logik in Teilplan 3) → `instance` laden → HAClient starten (Fehler blockieren den Admin-Listener nicht) → Admin-Listener → Public-Listener, wenn `public_ready`: `settings.public_enabled or settings.probe_mode or (instance.public_origin and instance.tls_mode in ("tls","proxy") and gültig)` und nicht `restore_mode`. Effektiver Origin: `settings.public_origin` bei Override, sonst `instance.public_origin`; `ctx.public_origin` ist die einzige Quelle für Origin-Prüfungen und `join_url`. Bind immer `0.0.0.0` (Container); im Override-Modus reines HTTP.
- [ ] **Step 2: Tests:** ohne Origin kein Public-Server; mit Override offen; `restore.sqlite` → `restore_mode`, Public geschlossen; `ctx.public_origin` entspricht dem Override.
- [ ] **Step 3:** Commit `feat(gateway): startup sequence and conditional public listener`.

### Task 4.3: Teilplan-1-Abschluss

- [ ] **Step 1:** `uv run pytest -q --cov=myha_gateway` ≥ 85 % für `policy` und `auth`; Ruff, mypy, `pnpm build`, CI grün.
- [ ] **Step 2:** `CHANGELOG.md`; `docs/setup.md` Stub; `docs/security.md` Stub (Argon2-Ergebnis aus WP1, sobald vorhanden).
- [ ] **Step 3:** Commit `docs: changelog and setup stub for plan 1`. Fable: Review (`superpowers:requesting-code-review`), dann Gate zu Teilplan 2 (S0-Bericht liegt vor oder WP1 ist als offen markiert).
