# myHA Teilplan 1 – Fundament (WP0–WP4)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Beide Repositories, Tooling und CI stehen; ein Add-on-Gerüst läuft auf Dimitris HA und liefert den S0-Bericht; HA-Simulator, HAClient, Datenmodell, Policy-Engine (A06–A18) und Ingress-Guard (A01–A05) sind getestet.

**Architecture:** Siehe Design §3–§4. Dieser Teilplan baut von außen nach innen: erst das lauffähige Add-on-Gerüst mit zwei Listenern, dann die reale Probe, dann Simulator und HAClient, dann Domäne und Policy, zuletzt die Admin-Identität.

**Tech Stack:** uv, Python 3.13, FastAPI, uvicorn, Pydantic 2, pydantic-settings, SQLAlchemy 2 async + aiosqlite, Alembic, httpx, websockets, argon2-cffi, segno, pytest, pytest-asyncio, Ruff, mypy; pnpm, Vite 6, React 18, TypeScript; Docker buildx; GitHub Actions.

**Design-Referenz:** `docs/superpowers/specs/2026-10-22-myha-design.md` – bei Widerspruch zwischen Plan und Design gilt das Design; Abweichungen an Fable melden.

---

## Dateistruktur (Ergebnis dieses Teilplans)

```
myha/                                   (Repo-Root, GitHub LL0rd/myha)
├── repository.yaml · README.md · LICENSE · CHANGELOG.md · .gitignore
├── .github/workflows/ci.yml · image.yml
├── scripts/dev.sh · build-image.sh · install-on-ha.py · probe-ha.py
├── docs/s0-report.md · setup.md (Stub)
├── tests/e2e/ (nur package.json + Platzhalter in diesem Teilplan)
└── myha/                               (Add-on-Ordner, Build-Kontext)
    ├── config.yaml · build.yaml · Dockerfile · DOCS.md · icon.png · logo.png
    ├── rootfs/etc/s6-overlay/s6-rc.d/myha/{type,run,finish}
    ├── rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/myha
    ├── rootfs/opt/myha/backup-export · backup-cleanup
    ├── web/ (Vite-Gerüst mit index.html, admin.html, src/guest/main.tsx, src/admin/main.tsx)
    └── gateway/
        ├── pyproject.toml · .python-version · uv.lock · alembic.ini · README.md
        ├── myha_gateway/
        │   ├── __init__.py · __main__.py · settings.py · app.py · deps.py · errors.py · logsetup.py · ids.py
        │   ├── db/__init__.py · db/models.py · db/migrations/env.py · db/migrations/versions/0001_initial.py
        │   ├── ha/client.py · ha/messages.py · ha/supervisor.py
        │   ├── adapters/base.py · light.py · switch.py · climate.py · cover.py · sensor.py · registry.py
        │   ├── policy/codes.py · model.py · context.py · index.py · decide.py
        │   ├── auth/admin.py
        │   └── api/admin/probe.py · api/public/health.py
        ├── myha_simulator/__init__.py · server.py · fixtures.py · __main__.py
        └── tests/conftest.py · unit/ · integration/ · acceptance/
```

Jede Datei hat eine Verantwortung: `decide.py` entscheidet nur, `index.py` baut nur den Index, `context.py` baut nur Kontexte aus DB-Zeilen, Adapter übersetzen nur, `client.py` spricht nur mit HA.

---

## WP0 – Repos, Tooling, Add-on-Gerüst

### Task 0.1: GitHub-Repos anlegen und Grundgerüst committen

**Files:**
- Create: `README.md`, `LICENSE` (Apache-2.0, Copyright 2026 Dimitri Roschkowski), `CHANGELOG.md`, `repository.yaml`
- Modify: `.gitignore` (ergänzen: `.env`, `*.sqlite*`, `uv.lock` **nicht** ignorieren)

- [ ] **Step 1: Repos anlegen** (Dimitri hat zugestimmt)

```bash
cd /home/dimitri/Projekte/myHA
gh repo create LL0rd/myha --public --description "Room-scoped guest, resident and wall-display access for Home Assistant" --source . --remote origin
mkdir -p /home/dimitri/Projekte/myha-apps && cd /home/dimitri/Projekte/myha-apps && git init -q -b main
gh repo create LL0rd/myha-apps --private --description "myHA native companion apps (proprietary)" --source . --remote origin
```

- [ ] **Step 2: `repository.yaml`**

```yaml
name: myHA Apps
url: https://github.com/LL0rd/myha
maintainer: Dimitri Roschkowski <info@d-r.dev>
```

- [ ] **Step 3: README (englisch, kurz): Zweck, Status „pre-alpha“, Installation über HA-App-Repository, Verweis auf `docs/`. LICENSE mit vollständigem Apache-2.0-Text. CHANGELOG mit `## Unreleased`.**

- [ ] **Step 4: Commit und Push**

```bash
git add -A && git commit -m "chore: repository metadata, license and readme" && git push -u origin main
```

### Task 0.2: Gateway-Projekt mit uv

**Files:**
- Create: `myha/gateway/pyproject.toml`, `myha/gateway/.python-version` (`3.13`), `myha/gateway/myha_gateway/__init__.py` (`__version__ = "0.1.0"`), `myha/gateway/tests/__init__.py`, `myha/gateway/tests/unit/test_version.py`

- [ ] **Step 1: pyproject**

```toml
[project]
name = "myha-gateway"
version = "0.1.0"
description = "myHA gateway: policy-enforcing access layer for Home Assistant"
requires-python = ">=3.13,<3.14"
license = "Apache-2.0"
dependencies = [
  "fastapi>=0.118",
  "uvicorn[standard]>=0.37",
  "pydantic>=2.11",
  "pydantic-settings>=2.10",
  "sqlalchemy[asyncio]>=2.0.43",
  "aiosqlite>=0.21",
  "alembic>=1.16",
  "httpx>=0.28",
  "websockets>=15",
  "argon2-cffi>=25",
  "segno>=1.6",
  "pyyaml>=6",
]

[project.scripts]
myha-gateway = "myha_gateway.__main__:main"
myha-simulator = "myha_simulator.__main__:main"

[dependency-groups]
dev = [
  "pytest>=8.4", "pytest-asyncio>=1.1", "pytest-cov>=6", "anyio>=4",
  "ruff>=0.13", "mypy>=1.18", "types-PyYAML",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py313"
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "ASYNC"]
ignore = ["S101"]

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

- [ ] **Step 2: Test schreiben** (`tests/unit/test_version.py`): `from myha_gateway import __version__; assert __version__ == "0.1.0"`.
- [ ] **Step 3: `uv sync` ausführen, Test rot/grün prüfen:** `cd myha/gateway && uv sync && uv run pytest -q` → `1 passed`.
- [ ] **Step 4: Commit** `chore(gateway): bootstrap uv project`.

### Task 0.3: Settings, Logging-Redaction, IDs

**Files:**
- Create: `myha_gateway/settings.py`, `myha_gateway/logsetup.py`, `myha_gateway/ids.py`
- Test: `tests/unit/test_settings.py`, `tests/unit/test_logsetup.py`, `tests/unit/test_ids.py`

- [ ] **Step 1: Tests zuerst**

```python
# tests/unit/test_settings.py
from myha_gateway.settings import Settings
def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.trusted_ingress_peer == "172.30.32.2"
    assert s.admin_port == 8099 and s.public_port == 8443
    assert s.state_dir == tmp_path / "state" and s.backup_dir == tmp_path / "backup"
    assert s.supervisor_url == "http://supervisor"
def test_options_json_overrides_log_level(tmp_path, monkeypatch):
    (tmp_path / "options.json").write_text('{"log_level": "debug"}')
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    assert Settings().log_level == "debug"

# tests/unit/test_logsetup.py
import logging
from myha_gateway.logsetup import RedactionFilter
def test_redacts_bearer_and_secrets(caplog):
    f = RedactionFilter()
    rec = logging.LogRecord("x", logging.INFO, "", 0, "Authorization: Bearer abc.def invite_secret=QmFzZTY0 token=xyz", None, None)
    assert f.filter(rec)
    assert "abc.def" not in rec.getMessage() and "QmFzZTY0" not in rec.getMessage() and "xyz" not in rec.getMessage()

# tests/unit/test_ids.py
from myha_gateway.ids import new_id, new_secret, hash_secret, constant_time_equal
def test_id_format():
    assert new_id("res").startswith("res_") and len(new_id("res")) == 16
def test_secret_hash_roundtrip():
    s = new_secret()
    assert len(s) >= 43 and hash_secret(s) == hash_secret(s) and len(hash_secret(s)) == 64
    assert constant_time_equal(hash_secret(s), hash_secret(s)) and not constant_time_equal(hash_secret(s), hash_secret(new_secret()))
```

- [ ] **Step 2: Rot prüfen:** `uv run pytest tests/unit -q` → ImportError.
- [ ] **Step 3: Implementieren.** `Settings(BaseSettings)` mit `env_prefix="MYHA_"`; Felder: `data_dir: Path = Path("/data")`, `state_dir`/`backup_dir` als `computed_field`, `admin_port=8099`, `public_port=8443`, `trusted_ingress_peer="172.30.32.2"`, `supervisor_url="http://supervisor"`, `supervisor_token: SecretStr | None` (aus Env `SUPERVISOR_TOKEN`, Alias), `ha_ws_url="ws://supervisor/core/websocket"`, `log_level="info"`, `min_supervisor_version="2026.10.3"`; `options.json` unter `data_dir` wird, falls vorhanden, als zusätzliche Quelle geladen (nur `log_level`). `RedactionFilter` ersetzt per Regex `Bearer\s+\S+`, `(secret|token|password|authorization)[=:]\s*\S+` (case-insensitive) durch `<redacted>`; `setup_logging(level)` hängt ihn an den Root-Logger. `ids.py`: `new_id(prefix) -> f"{prefix}_{secrets.token_hex(6)}"`, `new_secret() -> base64url(token_bytes(32)) ohne Padding`, `hash_secret(s) -> sha256 hex`, `constant_time_equal = hmac.compare_digest`.
- [ ] **Step 4: Grün prüfen, Ruff, Commit** `feat(gateway): settings, log redaction and id helpers`.

### Task 0.4: Zwei ASGI-Apps, ein Prozess, Probe-Endpunkte

**Files:**
- Create: `myha_gateway/app.py`, `myha_gateway/deps.py`, `myha_gateway/errors.py`, `myha_gateway/__main__.py`, `myha_gateway/api/public/health.py`, `myha_gateway/api/admin/probe.py`
- Test: `tests/integration/test_listeners.py`

- [ ] **Step 1: Test**

```python
import httpx, pytest
from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.deps import AppContext

@pytest.fixture
async def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    return await AppContext.create()

async def test_public_health_reports_client_ip(ctx):
    app = create_public_app(ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 4444)), base_url="http://p") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok", "client_ip": "203.0.113.9"}

async def test_admin_probe_requires_trusted_peer(ctx):
    app = create_admin_app(ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("10.0.0.5", 1)), base_url="http://a") as c:
        r = await c.get("/api/v1/admin/probe")
    assert r.status_code == 403 and r.json()["error"]["code"] == "UNAUTHENTICATED"

async def test_admin_probe_echoes_identity_headers_from_trusted_peer(ctx):
    app = create_admin_app(ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("172.30.32.2", 1)), base_url="http://a") as c:
        r = await c.get("/api/v1/admin/probe", headers={"X-Remote-User-Id": "u1", "X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["peer"] == "172.30.32.2" and body["headers"]["x-remote-user-id"] == "u1"
```

- [ ] **Step 2: Rot prüfen.**
- [ ] **Step 3: Implementieren.** `AppContext` (dataclass mit `settings`, später `engine`, `ha`, `bus`; `create()` classmethod). `errors.py`: `class ApiError(Exception)` mit `status`, `code`, `message`; Exception-Handler, der `{"error": {"code", "message"}}` rendert. `create_public_app(ctx)` → FastAPI ohne Docs-URLs, Router `health` (`GET /healthz` → `request.client.host`). `create_admin_app(ctx)` → FastAPI, Middleware `PeerGuardMiddleware`, die bei `request.client.host != settings.trusted_ingress_peer` `403 UNAUTHENTICATED` liefert (die vollständige Rollenprüfung folgt in WP4); Router `probe` (`GET /api/v1/admin/probe` → `{"peer", "headers": {nur X-Remote-User-*, X-Ingress-Path, X-Forwarded-*}}`). `__main__.py`: Click-freies `argparse` mit Subcommand `serve` (startet beide `uvicorn.Server` via `asyncio.gather`, `proxy_headers=False`, Admin auf `0.0.0.0:admin_port`, Public nur wenn `settings.public_enabled` – in diesem WP Env `MYHA_PUBLIC_ENABLED=1` – auf `0.0.0.0:public_port`), `probe` (gibt Supervisor-`/info` und Umgebungsvariablen redigiert aus; wird in Task 1.x genutzt).
- [ ] **Step 4: Grün prüfen; `uv run myha-gateway serve` lokal starten mit `MYHA_DATA_DIR=.dev/data MYHA_PUBLIC_ENABLED=1`; `curl -s localhost:8443/healthz` zeigt `127.0.0.1`. Commit** `feat(gateway): admin and public listeners with probe endpoints`.

### Task 0.5: Web-Gerüst (Vite, zwei Einstiege)

**Files:**
- Create: `myha/web/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, `admin.html`, `src/guest/main.tsx`, `src/admin/main.tsx`, `src/ui/tokens.css`, `.eslintrc.cjs` oder `eslint.config.js`, `src/vite-env.d.ts`

- [ ] **Step 1:** `pnpm init`, Abhängigkeiten: `react@18`, `react-dom@18`, `react-router-dom@6`; dev: `vite@6`, `@vitejs/plugin-react`, `typescript@5`, `vitest`, `@testing-library/react`, `jsdom`, `eslint`, `typescript-eslint`, `@fontsource/nunito-sans`, `@fontsource/jetbrains-mono`, `@tabler/icons-react`. `vite.config.ts`: `base: "./"`, `build.rollupOptions.input = { guest: "index.html", admin: "admin.html" }`, `build.target: "es2019"`, `outDir: "dist"`, Dev-Proxy `/api` → `http://127.0.0.1:8443`.
- [ ] **Step 2: `tokens.css`** mit den CSS-Variablen aus dem Mockup (`docs/reference/design/ha-access-ui.dc.html`, Zeilen 14–16: light, `[data-theme="dark"]`, `[data-theme="contrast"]`), Fonts per `@fontsource`-Import in `main.tsx`.
- [ ] **Step 3:** Beide Einstiege rendern „myHA“ + Theme-Umschalter (nur zum Nachweis). Test `src/ui/tokens.test.ts` prüft, dass `tokens.css` die drei Themes enthält (String-Test über `?raw`-Import).
- [ ] **Step 4:** `pnpm build` erzeugt `dist/index.html` und `dist/admin.html`; `pnpm test`, `pnpm lint`, `pnpm typecheck` grün. Commit `chore(web): vite scaffold with guest and admin entries and design tokens`.

### Task 0.6: Gateway liefert das Web-Build aus

**Files:**
- Modify: `myha_gateway/app.py`
- Create: `myha_gateway/static.py`
- Test: `tests/integration/test_static.py`

- [ ] **Step 1: Test:** Mit `MYHA_WEB_DIST=<tmp mit index.html, admin.html, assets/x.js>`: Public-App liefert `GET /` → index.html, `GET /join` → index.html (SPA-Fallback), `GET /assets/x.js` → Datei, `GET /admin.html` → 404 (Admin-Bundle nie öffentlich). Admin-App liefert `GET /` → admin.html, `GET /assets/x.js` → Datei. Beide setzen `Cache-Control: no-store` für HTML und `max-age=31536000, immutable` für `/assets/*`. Public setzt CSP-Header aus Design §7, Admin setzt `Content-Security-Policy: frame-ancestors 'self' https:` (Ingress-Einbettung) und `X-Content-Type-Options: nosniff`.
- [ ] **Step 2–4:** rot, implementieren (`StaticFiles` + Fallback-Route; Settings-Feld `web_dist: Path = Path("/opt/myha/web")`), grün, Commit `feat(gateway): serve web bundles with csp and cache headers`.

### Task 0.7: Add-on-Paketierung (config.yaml, Dockerfile, s6, Hook-Dummies)

**Files:**
- Create: `myha/config.yaml` (Inhalt Design §8), `myha/build.yaml`, `myha/Dockerfile`, `myha/DOCS.md`, `myha/icon.png`, `myha/logo.png` (einfaches SVG → PNG 128×128 / 250×100 mit Schlüsselsymbol in `#C8A24A`, per Python `segno`-frei z. B. mit Pillow im Skript `scripts/make-icons.py` oder statisch generiert), `myha/rootfs/etc/s6-overlay/s6-rc.d/myha/type` (`longrun`), `.../run`, `.../finish`, `myha/rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/myha` (leer), `myha/rootfs/opt/myha/backup-export`, `myha/rootfs/opt/myha/backup-cleanup`, `scripts/build-image.sh`

- [ ] **Step 1: `build.yaml`**

```yaml
build_from:
  aarch64: ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.23
  amd64: ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23
labels:
  org.opencontainers.image.title: myHA
  org.opencontainers.image.source: https://github.com/LL0rd/myha
  org.opencontainers.image.licenses: Apache-2.0
```

- [ ] **Step 2: Dockerfile (mehrstufig)**

```dockerfile
# syntax=docker/dockerfile:1.7
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23
FROM node:22-alpine AS web
WORKDIR /src/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM ${BUILD_FROM} AS runtime
ENV UV_PROJECT_ENVIRONMENT=/opt/myha/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
WORKDIR /opt/myha/gateway
COPY gateway/pyproject.toml gateway/uv.lock gateway/.python-version ./
RUN apk add --no-cache build-base libffi-dev && uv sync --frozen --no-dev --no-install-project && apk del build-base libffi-dev
COPY gateway/ ./
RUN uv sync --frozen --no-dev
COPY --from=web /src/web/dist /opt/myha/web
COPY rootfs/ /
RUN chmod a+x /etc/s6-overlay/s6-rc.d/myha/run /etc/s6-overlay/s6-rc.d/myha/finish /opt/myha/backup-export /opt/myha/backup-cleanup
ENV PATH="/opt/myha/venv/bin:${PATH}" MYHA_WEB_DIST=/opt/myha/web
```

Falls `uv sync` auf Alpine Wheels nicht findet (pydantic-core, uvloop, argon2-cffi haben musllinux-Wheels; Fallback ist der Quellbau mit `build-base`), das Ergebnis in `docs/s0-report.md` festhalten.

- [ ] **Step 3: s6-Run-Skript**

```bash
#!/command/with-contenv bashio
# shellcheck shell=bash
export MYHA_DATA_DIR=/data
export MYHA_LOG_LEVEL="$(bashio::config 'log_level' 'info')"
bashio::log.info "Starting myHA gateway ${MYHA_VERSION:-dev}"
exec /opt/myha/venv/bin/myha-gateway serve
```

`finish`: Standard-s6-Finish, das bei Exit-Code ≠ 0 den Container beendet (`/run/s6/basedir/bin/halt`). Hook-Dummies: `backup-export` schreibt `/data/backup/PROBE-export.txt` mit Zeitstempel und loggt; `backup-cleanup` löscht die Datei. Beide `#!/command/with-contenv bashio`.

- [ ] **Step 4: `scripts/build-image.sh`:** `docker buildx build --platform linux/amd64 --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23 -t myha-local:dev myha/` und anschließend `docker run --rm -e MYHA_DATA_DIR=/tmp/d -e MYHA_PUBLIC_ENABLED=1 -p 18443:8443 myha-local:dev myha-gateway serve` mit `curl localhost:18443/healthz` als Smoke-Test (Skript beendet den Container danach).
- [ ] **Step 5: `DOCS.md`** (englisch): Was die App tut, Ports, Ingress, Backup-Hinweis, Pre-Alpha-Warnung.
- [ ] **Step 6:** Image lokal bauen und Smoke-Test grün; Commit `feat(addon): add-on packaging with multi-stage image and s6 service`.

### Task 0.8: CI-Skelett

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/image.yml`

- [ ] **Step 1: `ci.yml`:** Trigger push/PR; Jobs `gateway` (ubuntu-latest, `astral-sh/setup-uv@v6`, `uv sync`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q --cov=myha_gateway --cov-report=term`), `web` (`pnpm/action-setup@v4`, Node 22, `pnpm install --frozen-lockfile`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`), `image` (buildx amd64 Build ohne Push als Smoke).
- [ ] **Step 2: `image.yml`:** Trigger Tag `v*` und manuell; baut `linux/amd64,linux/arm64` mit Matrix über `build.yaml` und pusht nach `ghcr.io/ll0rd/myha-{arch}:<version>` (Login mit `GITHUB_TOKEN`, `packages: write`). Noch nicht als `image:` in `config.yaml` eintragen (Design E16).
- [ ] **Step 3:** Push, CI-Lauf abwarten (`gh run watch`), Fehler beheben, Commit `ci: lint, test, build and image workflows`.

### Task 0.9: Dev-Skript

**Files:**
- Create: `scripts/dev.sh`

- [ ] **Step 1:** Startet (nach Task 2.x funktionsfähig) `uv run myha-simulator` (:8123), Gateway mit `MYHA_HA_WS_URL=ws://127.0.0.1:8123/api/websocket MYHA_SUPERVISOR_URL=http://127.0.0.1:8123 MYHA_TRUSTED_INGRESS_PEER=127.0.0.1 MYHA_PUBLIC_ENABLED=1 MYHA_DATA_DIR=.dev/data` und `pnpm dev`; `trap` beendet alle Prozesse. Bis der Simulator existiert, startet das Skript nur Gateway und Vite.
- [ ] **Step 2:** Commit `chore: dev script`.

---

## WP1 – S0-Real-Probe

> Voraussetzung: Dimitris Supervisor muss 2026.10.3 erreicht haben, sonst installiert sich die App zwar, der Admin-Guard meldet aber „Supervisor zu alt“. Die Probe läuft trotzdem (Header werden vor der Versionsprüfung protokolliert).

### Task 1.1: Installationsskript über die Supervisor-API

**Files:**
- Create: `scripts/install-on-ha.py`

- [ ] **Step 1:** Skript liest `~/.claude.json`, findet den MCP-Eintrag `homeassistant` (`url`, `headers.Authorization`), verbindet per `websockets` zu `ws://<host>/api/websocket`, authentifiziert, und führt `supervisor/api`-Befehle aus:

```python
async def sup(ws, i, endpoint, method="get", data=None, timeout=300):
    await ws.send(json.dumps({"id": i, "type": "supervisor/api", "endpoint": endpoint, "method": method, "data": data or {}, "timeout": timeout}))
    while True:
        r = json.loads(await ws.recv())
        if r.get("id") == i:
            return r
```

Subcommands: `status` (zeigt `/supervisor/info` Version, `/store/repositories`, `/addons` Installationsstatus von `*_myha`), `add-repo` (`POST /store/repositories {"repository": "https://github.com/LL0rd/myha"}`), `install` (`POST /store/addons/<slug>/install`, Slug ermitteln über `GET /store/addons` → Eintrag mit `slug` endend auf `_myha`), `start`, `stop`, `restart`, `logs` (`GET /addons/<slug>/logs`, Text), `options` (`POST /addons/<slug>/options {"options": {...}}`), `network` (`POST /addons/<slug>/options {"network": {"8443/tcp": 8443}}`). Jeder mutierende Befehl fragt `Fortfahren? [y/N]`. Der Token wird nie geloggt.

- [ ] **Step 2:** `python3 -I scripts/install-on-ha.py status` zeigt die Supervisor-Version. Commit `chore: supervisor install script`.

### Task 1.2: Probe-Endpunkte erweitern und auf dem HA installieren

**Files:**
- Modify: `myha_gateway/api/admin/probe.py`, `myha_gateway/__main__.py`
- Create: `myha_gateway/ha/supervisor.py`, `tests/unit/test_supervisor.py`

- [ ] **Step 1: Test für `supervisor.py`:** mit `httpx.MockTransport`: `GET http://supervisor/info` liefert `{"result":"ok","data":{"supervisor":"2026.10.3","channel":"stable"}}` → `SupervisorInfo(version="2026.10.3")`; `is_at_least("2026.10.3")` wahr, `"2026.10.2"` falsch; `GET /host/info` → `dt_synchronized` optional; HTTP 401 → `SupervisorUnavailable`.
- [ ] **Step 2:** Implementieren (`httpx.AsyncClient`, Header `Authorization: Bearer <SUPERVISOR_TOKEN>`); Versionsvergleich über `tuple(int)` der Teile `YYYY.MM.P`.
- [ ] **Step 3:** `GET /api/v1/admin/probe` liefert zusätzlich `supervisor_info` (Version oder Fehlertext), `host_info` (oder Fehlertext), `env` (nur Namen, keine Werte), `client`. Public `GET /healthz` liefert zusätzlich `x_forwarded_for` (roh, nur Anzeige).
- [ ] **Step 4:** Push nach GitHub; `scripts/install-on-ha.py add-repo && install && start`; in der HA-GUI die Sidebar-Kachel öffnen und `/api/v1/admin/probe` über die Ingress-URL aufrufen (`https://<ha>/api/hassio_ingress/<token>/api/v1/admin/probe`); Port 8443 über `network` freigeben; von einem Gerät im LAN `curl http://<LAN-IP>:8443/healthz`.
- [ ] **Step 5:** Ergebnisse in `docs/s0-report.md` festhalten: Supervisor-Version, Peer-IP (erwartet 172.30.32.2), vorhandene Identitätsheader (inkl. ob `X-Remote-User-Groups` kommt), `/info` erreichbar ohne `hassio_api` (ja/nein), `/host/info` erreichbar (erwartet nein; dann Test mit `hassio_api: true`, `hassio_role: default` wiederholen und Entscheidung E26 festhalten), Client-IP am Public-Port (echte LAN-IP oder Docker-Gateway-IP?), IPv6 ja/nein, Startzeit des Containers, Image-Größe. Commit `docs: S0 probe results (ingress, supervisor, client ip)`.

### Task 1.3: Backup-Hooks real prüfen

- [ ] **Step 1:** Über die HA-GUI ein Backup nur dieser App erstellen (oder `supervisor/api` `POST /backups/new/partial {"name":"myha-probe","addons":["<slug>"]}`).
- [ ] **Step 2:** Add-on-Logs prüfen: `backup-export` und `backup-cleanup` wurden ausgeführt (Zeitstempel); App-Backup-Archiv herunterladen (`GET /backups/<slug>/download` über das Skript) und prüfen: enthält `backup/PROBE-export.txt`? enthält **kein** `state/`? (Vorher im Container `mkdir -p /data/state && echo x > /data/state/live.sqlite` über ein temporäres Probe-Kommando anlegen.)
- [ ] **Step 3:** Ergebnis und ggf. korrigierte `backup_exclude`-Muster in `docs/s0-report.md` und `config.yaml`. Commit `docs: S0 backup hook results`.

### Task 1.4: Argon2-Parameter und Klimaanlagen-Verhalten messen

- [ ] **Step 1:** Temporärer CLI-Befehl `myha-gateway probe argon2` misst `argon2.PasswordHasher(time_cost=t, memory_cost=m, parallelism=1)` für t∈{2,3,4}, m∈{19456, 47104, 65536} im Container (über `supervisor/api` `POST /addons/<slug>/stdin`? Nicht verfügbar – stattdessen beim Start einmalig messen, wenn `MYHA_PROBE_ARGON2=1` als Option gesetzt ist, und ins Log schreiben). Ergebnis (Ziel ≈ 250 ms) in `docs/security.md` (neu) festhalten.
- [ ] **Step 2:** Mit der HA-MCP-/REST-Schnittstelle an **einer** Klimaanlage im Wohnzimmer beobachten (nur lesend plus zwei Service-Aufrufe mit Dimitris Zustimmung im Chat, sonst überspringen und offen lassen): aktuellen `temperature`-Wert notieren, `climate.set_hvac_mode` cool → heat → cool ausführen, nach jedem Schritt `temperature` lesen. Bleibt der Sollwert erhalten oder springt er je Modus? Ergebnis in `docs/s0-report.md`, Abschnitt „Klimaanlage Moduswechsel“; Design E7/§14 bei Bedarf anpassen.
- [ ] **Step 3:** Commit `docs: S0 argon2 timing and climate mode behaviour`.

### Task 1.5: Android-Transport-Spike (privates Repo, parallelisierbar)

**Files (in `/home/dimitri/Projekte/myha-apps`):**
- Create: `spikes/transport-android/` (Gradle-Projekt, Kotlin, JDK 17 über Gradle-Toolchain mit Foojay-Resolver, OkHttp 4.12), `spikes/tls-testserver/server.py` (Python, `ssl` mit selbst erzeugtem Zertifikat, HTTP `/ping` und WebSocket-Echo über `websockets`), `spikes/README.md`

- [ ] **Step 1:** Testserver mit zwei Schlüsselpaaren erzeugen (`openssl`), SPKI-Pins berechnen (`openssl x509 -pubkey | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64`).
- [ ] **Step 2:** Spike als JUnit-Test (läuft auf dem Host ohne Emulator, OkHttp ist reines JVM): Fälle `richtiger Pin → /ping 200 und WS-Echo ok`, `falscher Pin → beide scheitern mit SSLPeerUnverifiedException`, `falscher Hostname → beide scheitern`, `Pinwechsel zur Laufzeit → bestehender WS wird geschlossen, neuer Client mit neuem Pin verbindet`, `Redirect auf anderen Host wird nicht gefolgt (followRedirects=false)`. Systemvertrauen: Testserver-CA nur für den Test in einen eigenen `TrustManager` laden; **kein** `trust all`.
- [ ] **Step 3:** `./gradlew test` grün; Ergebnisse (OkHttp-Version, JDK, Verhalten) in `spikes/README.md` und zusammengefasst in `myha/docs/s0-report.md`. Commit in beiden Repos.

### Task 1.6: S0-Bericht abschließen

- [ ] **Step 1:** `docs/s0-report.md` vervollständigen (Tabelle Annahme → Ergebnis → Konsequenz) und Design §14 aktualisieren (Fable prüft und passt E26/E7/`config.yaml` an, falls nötig).
- [ ] **Step 2:** Commit `docs: complete S0 report`. **Gate:** Fable liest den Bericht, bevor WP2 startet.

---

## WP2 – HA-Simulator und HAClient

### Task 2.1: Simulator-Fixtures und WS-Protokoll

**Files:**
- Create: `myha_simulator/fixtures.py`, `myha_simulator/server.py`, `myha_simulator/__main__.py`
- Test: `tests/integration/test_simulator.py`

- [ ] **Step 1: Fixture-Inhalt** (`fixtures.py`, als Python-Dicts):
  - Areas: `area_living` „Living room“, `area_kitchen` „Kitchen“, `area_203` „Room 203“.
  - Entities (mit `unique_id`, `device_id`, `area_id`, `platform`): `light.living_ceiling` (platform `govee`, `supported_color_modes: ["color_temp","rgb"]`, brightness 180), `light.hallway` (on/off only, `supported_color_modes: ["onoff"]`), `light.group_all` (platform `group` → muss als Review markiert werden), `switch.living_floor_lamp`, `climate.living_ac` (platform `midea`, `hvac_modes: [auto,cool,dry,fan_only,heat,off]`, `min_temp 8, max_temp 30, target_temp_step 1, temperature 22, current_temperature 24, hvac_mode cool`), `climate.room_203_trv` (`hvac_modes: [heat, off]`, `min 7, max 30, step 0.5, temperature 21.5`), `climate.room_204_dual` (mit `target_temp_high/low` → gesperrt), `cover.room_203_blind` (`supported_features` OPEN|CLOSE|STOP|SET_POSITION = 15, `current_position 100`), `cover.kitchen_blind` (OPEN|CLOSE|STOP = 11), `sensor.room_203_humidity` (46, `%`, `device_class humidity`), `sensor.outdoor_temp` (°C), `camera.lobby` (darf nie erscheinen), `person.dimitri`.
  - Users für `config/auth/list`: `{"id":"owner1","name":"dr","is_owner":true,"is_active":true,"group_ids":["system-admin"],"system_generated":false}`, `{"id":"user1","name":"Alla","is_owner":false,"is_active":true,"group_ids":["system-users"]}`, `{"id":"sup","name":"Supervisor","group_ids":["system-admin"],"system_generated":true}`.
  - Config: `{"unit_system":{"temperature":"°C"},"time_zone":"Europe/Sofia","version":"2026.10.2"}`.
- [ ] **Step 2: Server** (FastAPI): `WS /api/websocket` implementiert `auth_required → auth → auth_ok/auth_invalid` (Token `sim-token`), Befehle `get_states`, `get_config`, `subscribe_events` (nur `state_changed`), `unsubscribe_events`, `call_service` (Domänen light/switch/climate/cover; verändert den Zustand realistisch, sendet `state_changed` an Abonnenten, liefert `result` mit `context`), `config/entity_registry/list`, `config/device_registry/list`, `config/area_registry/list`, `config/auth/list` (nur mit Admin-Token), `supervisor/api` → `unknown_command`. Unbekannte Befehle → `{"success": false, "error": {"code": "unknown_command"}}`. HTTP: `GET /info` (Supervisor-Fake, Bearer-Prüfung) → `{"result":"ok","data":{"supervisor":"2026.10.3"}}`; `GET /host/info` → 403 (konfigurierbar); `GET /addons/self/info` → `ingress_url`. Steuer-API für Tests: `POST /_sim/state/{entity_id}` (Zustand setzen, Event auslösen), `POST /_sim/offline/{entity_id}` (`unavailable`), `POST /_sim/fail-next-service` (nächster `call_service` liefert Fehler), `POST /_sim/drop-connections` (schließt alle WS), `POST /_sim/clock-skew {seconds}` (verschiebt `time_fired`), `POST /_sim/reset`.
- [ ] **Step 3: Tests:** Verbindung + Auth; `get_states` enthält 13 Entities; `call_service light.turn_on` ändert Zustand und sendet Event an Abonnenten mit `context.id`; `config/auth/list` ohne Admin → Fehler; `fail-next-service` wirkt genau einmal; `drop-connections` schließt die Verbindung.
- [ ] **Step 4:** grün, Commit `feat(simulator): fake Home Assistant core and supervisor for tests`.

### Task 2.2: HAClient

**Files:**
- Create: `myha_gateway/ha/messages.py`, `myha_gateway/ha/client.py`
- Test: `tests/integration/test_ha_client.py` (nutzt Simulator-Fixture aus `conftest.py`, der den Simulator in-process auf freiem Port startet)

- [ ] **Step 1: Schnittstelle**

```python
class HAClient:
    def __init__(self, ws_url: str, token: str, *, on_event: Callable[[StateChanged], Awaitable[None]], on_status: Callable[[ConnectionStatus], Awaitable[None]], clock: Callable[[], datetime] = utcnow) -> None: ...
    async def start(self) -> None            # verbindet, authentifiziert, lädt Snapshot + Registries + config, abonniert state_changed, startet Reconnect-Loop
    async def stop(self) -> None
    @property
    def connected(self) -> bool
    @property
    def generation(self) -> int              # +1 je erfolgreichem (Re-)Connect
    @property
    def adapter_locked(self) -> bool         # True bis get_config nach (Re-)Connect validiert ist
    @property
    def temperature_unit(self) -> str | None
    def state(self, entity_id: str) -> HAState | None
    def all_states(self) -> dict[str, HAState]
    def registry(self) -> Registry           # entities, devices, areas (Dataclasses)
    async def call_service(self, domain: str, service: str, entity_id: str, data: dict[str, Any]) -> ServiceResult  # Fehler → HAServiceError
    async def auth_list(self) -> list[HAUser]  # config/auth/list, ohne Cache
    def time_skew_seconds(self) -> float      # letzter time_fired minus eigene Uhr
```

- [ ] **Step 2: Tests (rot):** Snapshot nach `start()` enthält `light.living_ceiling`; Event-Callback nach `_sim/state`; `call_service` liefert `context_id` und der Zustand ändert sich; nach `_sim/drop-connections` reconnectet der Client innerhalb von 3 s, `generation` steigt, `adapter_locked` ist bis zum neuen `get_config` wahr und danach falsch; `on_status` meldet `disconnected` und `connected`; `auth_list` liefert 3 Nutzer; `call_service` auf `fail-next-service` wirft `HAServiceError`; `time_skew_seconds` spiegelt `clock-skew`.
- [ ] **Step 3: Implementieren** mit `websockets` (Client), Request-IDs monoton, `asyncio.Future` je ID, Backoff 1 s → 30 s, Heartbeat über `ping` alle 20 s, Nachrichten-Limit 4 MiB. Nie das Token loggen.
- [ ] **Step 4:** grün, Commit `feat(gateway): resilient Home Assistant websocket client`.

---

## WP3 – Datenmodell, Adapter, Policy

### Task 3.1: Datenbank, Modelle, Migration

**Files:**
- Create: `myha_gateway/db/__init__.py`, `myha_gateway/db/models.py`, `alembic.ini`, `myha_gateway/db/migrations/env.py`, `myha_gateway/db/migrations/versions/0001_initial.py`
- Test: `tests/unit/test_db.py`

- [ ] **Step 1: Tests:** `create_engine(path)` setzt `journal_mode=WAL`, `foreign_keys=ON`, `secure_delete=ON` (per `PRAGMA` abfragen); `run_migrations(engine)` erzeugt alle Tabellen aus Design §5; Fremdschlüssel werden durchgesetzt (Insert `membership` mit unbekannter Gruppe → `IntegrityError`); `instance`-Singleton wird beim ersten Start mit `short_id`, `server_secret`, `policy_version=1`, `security_generation=1`, `session_limits`-Defaults angelegt.
- [ ] **Step 2: Modelle** exakt nach Design §5 (alle Tabellen, Spalten, Unique-Indizes: `credential.username_normalized`, `session.token_hash`, `invite.secret_hash`, `(command.session_id, command.idempotency_key)`, `refresh_token.token_hash`, `ws_ticket.ticket_hash`). JSON-Spalten als `JSON`-Typ; Zeitpunkte als ISO-8601-Strings mit `Z`, über `TypeDecorator` zu `datetime(tz=UTC)`. `version`-Spalte (Integer, Default 1) auf admin-editierbaren Tabellen.
- [ ] **Step 3:** Alembic async-Env (`run_sync`), Migration 0001 per `alembic revision --autogenerate` erzeugt und manuell geprüft.
- [ ] **Step 4:** grün, Commit `feat(gateway): sqlite schema, pragmas and initial migration`.

### Task 3.2: Adapter – Fähigkeiten, Übersetzung, StateView

**Files:**
- Create: `myha_gateway/adapters/base.py`, `registry.py`, `light.py`, `switch.py`, `climate.py`, `cover.py`, `sensor.py`
- Test: `tests/unit/test_adapters.py`

- [ ] **Step 1: Typen in `base.py`**

```python
class Action(StrEnum):
    METADATA_READ="metadata.read"; STATE_READ="state.read"; HISTORY_READ="history.read"
    SWITCH_ON="switch.on"; SWITCH_OFF="switch.off"
    LIGHT_ON="light.on"; LIGHT_OFF="light.off"; LIGHT_SET_LEVEL="light.set_level"
    CLIMATE_SET_TEMPERATURE="climate.set_temperature"; CLIMATE_SET_MODE="climate.set_mode"
    COVER_OPEN="cover.open"; COVER_CLOSE="cover.close"; COVER_STOP="cover.stop"; COVER_SET_POSITION="cover.set_position"

@dataclass(frozen=True)
class DeviceCapabilities:   # aus HA abgeleitet, vom Admin bestätigt
    kind: Literal["light","switch","climate","cover","sensor"]
    actions: frozenset[Action]              # physisch mögliche Schreibaktionen
    target_c_min: Decimal | None = None; target_c_max: Decimal | None = None; target_c_step: Decimal | None = None
    hvac_modes: frozenset[str] = frozenset() # vom Gerät gemeldet (roh)
    confirmed_modes: frozenset[str] = frozenset()  # Admin-bestätigt ⊆ {off,cool,heat}
    blocked_reason: str | None = None       # z. B. "dual_setpoint", "group_entity", "unit_not_celsius"
    fingerprint: str = ""                   # sha256 über relevante Attribute

@dataclass(frozen=True)
class StateView:
    availability: Literal["online","offline","unknown"]
    adapter_locked: bool
    power: str | None = None; level_pct: int | None = None
    target_c: Decimal | None = None; current_c: Decimal | None = None; hvac_mode: str | None = None
    position_pct: int | None = None
    value: str | None = None; unit: str | None = None
    observed_at: datetime | None = None

@dataclass(frozen=True)
class HACall:
    domain: str; service: str; entity_id: str; data: dict[str, Any]

@dataclass(frozen=True)
class Expectation:   # Bestätigung über beobachteten Zustand
    attribute: str; expected: Any; tolerance: Decimal | None = None

class Adapter(Protocol):
    kind: str
    def capabilities(self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None) -> DeviceCapabilities: ...
    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView: ...
    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall: ...
    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation: ...
    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]: ...  # z. B. set_level 0 → {LIGHT_SET_LEVEL, LIGHT_OFF}
```

- [ ] **Step 2: Tests (rot), je Adapter:** Light: `supported_color_modes` mit brightness → actions {on, off, set_level}; `onoff` → {on, off}; platform `group` → `blocked_reason="group_entity"`; `translate(set_level, 40)` → `light.turn_on {brightness_pct: 40}`; `translate(set_level, 0)` → `light.turn_off`; `required_actions(set_level, 0)` = {set_level, off}, `(set_level, 40)` = {set_level, on}. Climate: AC-Fixture → `target_c_min 8, max 30, step 1`, `hvac_modes` 6, `confirmed_modes` leer bis Admin bestätigt; dual → `blocked_reason="dual_setpoint"`; `temperature_unit="°F"` → `blocked_reason="unit_not_celsius"`; `translate(set_temperature, "21.5")` → `climate.set_temperature {temperature: 21.5}`; `translate(set_mode, "cool")` → `climate.set_hvac_mode {hvac_mode: "cool"}`; `state_view` liefert `target_c=Decimal("22")`, `hvac_mode="cool"`. Cover: features 15 → {open, close, stop, set_position}; 11 → ohne set_position; `required_actions(set_position, 0)` = {set_position, close}, `(…, 100)` = {set_position, open}, `(…, 40)` = {set_position, open, close}. Sensor: nur Lesen, `value "46"`, `unit "%"`. Switch: {on, off}. `registry.detect_kind(entity_id)` ordnet Domains zu; `fingerprint` ändert sich bei geänderten `supported_features`.
- [ ] **Step 3:** Implementieren. `unavailable`/`unknown` → `availability` entsprechend.
- [ ] **Step 4:** grün, mypy strict grün, Commit `feat(gateway): device adapters with capabilities, translation and state views`.

### Task 3.3: Policy-Modell und Codes

**Files:**
- Create: `myha_gateway/policy/codes.py`, `myha_gateway/policy/model.py`
- Test: `tests/unit/test_policy_model.py`

- [ ] **Step 1: `codes.py`:** `class DecisionCode(StrEnum)` mit allen Codes aus Design §4.1 in Prüfreihenfolge; `PUBLIC_CODE: dict[DecisionCode, str]` nach Design; `class PublicError(StrEnum)` mit den acht öffentlichen Codes.
- [ ] **Step 2: `model.py`** (Pydantic, `extra="forbid"`): `Interval(min: Decimal, max: Decimal, step: Decimal, origin: Decimal = 0)` mit Validator `min <= max`, `step > 0`; `RangeRule(intervals: list[Interval])`; `EnumRule(allowed: list[str])`; `Grant(resources: list[str], actions: list[Action], parameters: dict[str, RangeRule | EnumRule] = {})`; `Deny(resources, actions)`; `Fence(scope_type, scope_id, resource_ids: list[str] | None, action, parameters)`; `RoleRules(grants: list[Grant], denies: list[Deny])`; Parameter-Request-Schemas: `LevelParams(level_pct: int, ge 0 le 100)`, `TargetCParams(target_c: str)` mit Validator (Regex `^-?\d{1,2}(\.\d)?$`, → Decimal), `ModeParams(mode: Literal["off","cool","heat"])`, `PositionParams(position_pct: int)`, `NoParams()`; `PARAM_SCHEMA: dict[Action, type[BaseModel]]`.
- [ ] **Step 3: Tests:** `TargetCParams(target_c="21.5")` ok, `"21.55"`, `"NaN"`, `"1e1"`, `"abc"` → `ValidationError`; `Grant(..., extra="x")` → Fehler; `Interval(min=24, max=19)` → Fehler; `PUBLIC_CODE[DecisionCode.NO_GRANT] == "ACTION_NOT_ALLOWED"`.
- [ ] **Step 4:** Commit `feat(policy): rule models, parameter schemas and decision codes`.

### Task 3.4: Effektiver Kontext und PolicyIndex

**Files:**
- Create: `myha_gateway/policy/context.py`, `myha_gateway/policy/index.py`
- Test: `tests/unit/test_policy_index.py`

- [ ] **Step 1: Typen**

```python
@dataclass(frozen=True)
class RoleBinding: role_template_id: str; role_version: int; unit_ids: frozenset[str]
@dataclass(frozen=True)
class EffectiveContext:
    principal_ref: str                      # principal_id oder "hypothetical:guest:<stay_id>" / "hypothetical:group:<group_id>"
    role_bindings: tuple[RoleBinding, ...]
    fences: tuple[Fence, ...]
    not_before: datetime | None; not_after: datetime | None
    stay_id: str | None; purpose: Literal["access","preview","test"]; policy_version: int
    session_active: bool = True; time_trusted: bool = True

@dataclass(frozen=True)
class ResourceRef:  # aus resource_binding
    id: str; unit_id: str; kind: str; status: Literal["confirmed","needs_review","blocked"]; capabilities: DeviceCapabilities

class PolicyIndex:
    """Unveränderlich; aus DB gebaut. Enthält role_versions {(tid, ver): RoleRules}, resources {id: ResourceRef}, units {id: set(res_ids)}."""
    def rules_for(self, ctx: EffectiveContext) -> list[tuple[RoleBinding, RoleRules]]: ...
    def resource(self, resource_id: str) -> ResourceRef | None: ...
    def effective_capability(self, ctx, resource_id, action) -> EffectiveCapability | None  # None = Aktion nicht erlaubt
```

`context.py`: `build_context_for_session(session_row, principal_row, assignments, memberships, stay_row, fences, instance) -> EffectiveContext` nach der Quellentabelle in Design §4.1 (reine Funktion über geladene Zeilen; das Laden selbst liegt in einem kleinen Repository-Modul `policy/loader.py`, das in Teilplan 2 ergänzt wird). `build_context_for_preview(target, ...)` für die vier Zielarten.

- [ ] **Step 2: Tests:** Bewohner in Gruppe → Bindungen aus direkter und Gruppenzuweisung; abgelaufene Mitgliedschaft (`valid_to < now`) zählt nicht; Gast → genau eine Bindung aus dem Stay, Fenster `[starts_at, min(ends_at, not_after))`; Vorschau `stay_id` → `principal_ref` hypothetisch; `effective_capability` vereinigt Intervalle zweier Grants (18–20 und 22–24 bleiben zwei Intervalle), schneidet mit Fence `≤ 22` (→ 18–20 und 22–22) und Gerätebereich; Deny entfernt die Aktion (`None`); Modus: Grant `allowed [off, cool, heat]` ∩ bestätigt `{off, cool}` → `{off, cool}`.
- [ ] **Step 3:** Implementieren; Intervallarithmetik mit `Decimal`.
- [ ] **Step 4:** Commit `feat(policy): effective context and immutable policy index`.

### Task 3.5: `decide()` und Abnahmefälle A06–A18

**Files:**
- Create: `myha_gateway/policy/decide.py`
- Test: `tests/acceptance/test_matrix_policy.py`, `tests/unit/test_decide.py`

- [ ] **Step 1: Signatur**

```python
@dataclass(frozen=True)
class Decision:
    allowed: bool; code: DecisionCode; public_code: str
    normalized_params: dict[str, Any]; matched: tuple[str, ...]   # z. B. ("grant:role_room_guest@2#1", "fence:f_mia")
    message: str  # admin-lesbar

def decide(ctx: EffectiveContext, index: PolicyIndex, resource_id: str, action: Action | str, params: Mapping[str, Any], now: datetime, state_view: StateView | None) -> Decision: ...
```

Reihenfolge exakt wie Design §4.1 („Decision-Codes … erster Treffer gewinnt“), inklusive Regel „Modus“ (Prüfbereich-Logik) und Regel „Zeit“. `required_actions` des Adapters wird für zusammengesetzte Aktionen (set_level 0, set_position) geprüft: jede erforderliche Aktion braucht einen Grant und darf nicht verweigert sein. `metadata.read` wird implizit gewährt, wenn irgendeine Aktion oder `state.read` auf der Ressource gewährt ist (Spez §6.2: minimale Beschriftung).

- [ ] **Step 2: Testtabelle (rot), eine Fixture `room203` mit den Ressourcen aus dem Simulator-Fixture und Rollen wie Spez §7:**

| Test | Aufbau | Erwartung |
|---|---|---|
| `test_a06_foreign_resource_is_not_found` | Gast Room 203 fragt `res_lr_ceiling` (anderer Raum) und `res_unknown` | beide `RESOURCE_NOT_IN_SCOPE` → public `NOT_FOUND`, identische Message |
| `test_a07_off_only` | Grant nur `light.off` | `light.on` → `NO_GRANT`; `light.set_level 40` → `NO_GRANT`; `light.off` → `GRANTED` |
| `test_a08_on_only` | Grant nur `light.on` | `light.off` → `NO_GRANT`; `set_level 0` → `NO_GRANT` (erfordert off); Params `{"level_pct": 40, "flash": "short"}` → `PARAM_SCHEMA` |
| `test_a09_temperature_range_and_step` | Grant 19–24 step 0.5 | `21.5` GRANTED; `24.1` PARAM_OUT_OF_RANGE; `21.3` PARAM_STEP; `"NaN"` PARAM_SCHEMA; Params mit `hvac_mode` → PARAM_SCHEMA |
| `test_a10_two_grants_do_not_merge` | Grants 18–20 und 22–24 | `21` → PARAM_OUT_OF_RANGE; `19` und `23` GRANTED |
| `test_a11_fence_narrows_wide_grant` | Gruppe 19–23, Fence principal ≤ 22 | `22.5` → FENCE_VIOLATION; `22` GRANTED |
| `test_a12_deny_wins` | Grant und Deny `light.on` | DENIED_BY_RULE |
| `test_a13_unknown_action_and_wildcards` | `"light.toggle"`, `"*"`, `"light.*"`, Ressource `"*"` | UNKNOWN_ACTION bzw. RESOURCE_NOT_IN_SCOPE → NOT_FOUND |
| `test_a14_new_device_not_granted` | Index kennt neue Entity ohne Binding | NOT_FOUND |
| `test_a15_group_entity_blocked` | Binding mit `blocked_reason="group_entity"`, Status `blocked` | `RESOURCE_UNCONFIRMED` → NOT_FOUND; auch `state.read` |
| `test_a16_adapter_locked` | `state_view.adapter_locked=True` | Schreiben → ADAPTER_LOCKED → UPSTREAM_UNAVAILABLE; `state.read` weiter GRANTED |
| `test_a17_cover_position_requires_both_directions` | Grant nur `cover.open` + `set_position` | `set_position 100` GRANTED; `0` NO_GRANT; `40` NO_GRANT |
| `test_a18_mode_cannot_bypass_fence` | Grant set_temperature 19–24, set_mode {off,cool,heat}, Gerät meldet target 28 | `cool` → MODE_WOULD_BYPASS_FENCE; target 22 → GRANTED; `off` immer GRANTED; target `None` → MODE_WOULD_BYPASS_FENCE; nur set_mode-Grant ohne Temperaturgrant und ohne Fence → GRANTED auch bei 28 |
| `test_stay_window` | Gast vor `starts_at`, nach `ends_at` | STAY_NOT_STARTED / STAY_ENDED → ACCESS_EXPIRED |
| `test_time_untrusted_blocks_stays_only` | `time_trusted=False` | Gast → TIME_UNTRUSTED; Bewohner GRANTED |
| `test_preview_context_rejected_on_execute` | `purpose=preview`, `execute=True` | PREVIEW_CONTEXT |
| `test_dst_boundary` | Stay endet 2026-03-29 03:00 Europe/Sofia (Umstellung) | Grenze exklusiv korrekt in UTC |

- [ ] **Step 3:** Implementieren; `decide` hat einen Parameter `execute: bool = False` (nur ausführende Endpunkte setzen `True`).
- [ ] **Step 4:** `uv run pytest tests/acceptance -q` grün; Laufzeitmessung `test_decide_p95_under_20ms` (1000 Aufrufe, p95 < 20 ms) grün. Commit `feat(policy): pure decision function with acceptance matrix A06-A18`.

---

## WP4 – Admin-Identität und Listener-Finalisierung

### Task 4.1: IngressGuard mit Rollenprüfung und CSRF

**Files:**
- Create: `myha_gateway/auth/admin.py`
- Modify: `myha_gateway/app.py` (ersetzt `PeerGuardMiddleware`), `myha_gateway/api/admin/probe.py` → neue Route `GET /api/v1/admin/session`
- Test: `tests/acceptance/test_matrix_admin.py`, `tests/unit/test_admin_csrf.py`

- [ ] **Step 1: Verhalten.** `require_admin(fresh: bool)` als FastAPI-Dependency: (1) Peer = `trusted_ingress_peer`, sonst `403 UNAUTHENTICATED`; (2) genau ein `X-Remote-User-Id` (mehrere oder keiner → 403); (3) Supervisor-Version ≥ Minimum (Ergebnis 60 s gecacht; Fehler → `503 UPSTREAM_UNAVAILABLE` mit `details.reason="supervisor_version"`); (4) `ha.auth_list()`; Nutzer per `id` exakt suchen; `is_active and (is_owner or "system-admin" in group_ids)`; Ergebnis bei `fresh=False` 5 s gecacht, bei `fresh=True` immer neu; Fehler oder Schemaabweichung (fehlende Felder) → 403 ohne Rückfall (A05). Rückgabe `AdminIdentity(user_id, name, display_name, groups_header)`. Mutationen (`POST/PUT/PATCH/DELETE`) nutzen `fresh=True` und zusätzlich `require_csrf`: Header `X-MyHA-CSRF == hmac_sha256(server_secret, f"{user_id}|{ingress_path}|{date_utc}")` (auch Vortag akzeptiert, 12-h-Überlappung), `Sec-Fetch-Site` falls vorhanden `same-origin`. `GET /api/v1/admin/session` liefert `{user, csrf_token, supervisor_version, core_version, setup_state, time_trusted}`.
- [ ] **Step 2: Tests (rot):** `test_a01_public_listener_ignores_identity_headers` (Public-App, `X-Remote-User-Id: owner1`, Route `/api/v1/admin/session` existiert nicht → 404; kein Admin-Router registriert), `test_a02_wrong_peer_rejected` (Peer 172.30.33.5 → 403), `test_a03_missing_duplicate_headers_and_old_supervisor` (kein Header → 403; zwei Header → 403; Supervisor 2026.10.2 → 503 mit Hinweis), `test_a04_demoted_admin_blocked_on_next_mutation` (erst Owner, dann Simulator-User auf `system-users` ändern via `_sim`-Endpunkt `POST /_sim/auth/users`; GET innerhalb 5 s noch ok, POST sofort 403), `test_a05_auth_list_failure_no_fallback` (Simulator liefert Fehler bzw. Schema ohne `group_ids` → 403), CSRF: falscher/alter/fehlender Token → 403, `Sec-Fetch-Site: cross-site` → 403.
- [ ] **Step 3:** Implementieren; `HAClient.auth_list` nutzen; Cache als kleines Dict mit Monotonic-Zeit.
- [ ] **Step 4:** grün, Commit `feat(auth): ingress guard with fresh admin role checks and csrf (A01-A05)`.

### Task 4.2: Startreihenfolge und Public-Listener-Schalter

**Files:**
- Modify: `myha_gateway/app.py`, `myha_gateway/__main__.py`, `myha_gateway/deps.py`
- Test: `tests/integration/test_startup.py`

- [ ] **Step 1: Verhalten.** Lifespan: Migrationen → (Restore-Check: existiert `backup_dir/restore.sqlite` → `ctx.restore_mode=True`; vollständige Logik in Teilplan 3) → `instance` laden → HAClient starten (Fehler blockiert nicht den Admin-Listener, Status wird gemeldet) → Admin-Listener → Public-Listener nur, wenn `instance.public_origin` gesetzt, `tls_mode` gültig und nicht `restore_mode` (Env `MYHA_PUBLIC_ENABLED=1` erzwingt ihn für Dev/Tests im `proxy`-Modus auf localhost). `AppContext.public_ready` Property; `__main__ serve` startet den Public-Server, sobald `public_ready` wahr wird (Task im Hintergrund, pollt alle 2 s).
- [ ] **Step 2: Tests:** ohne Origin kein Public-Server (Port geschlossen); mit `MYHA_PUBLIC_ENABLED=1` offen; `restore.sqlite` vorhanden → `ctx.restore_mode` und Public geschlossen.
- [ ] **Step 3:** grün, Commit `feat(gateway): startup sequence and conditional public listener`.

### Task 4.3: Teilplan-1-Abschluss

- [ ] **Step 1:** `uv run pytest -q --cov=myha_gateway` ≥ 85 % für `policy` und `auth`; `ruff`, `mypy`, `pnpm build` grün; CI grün.
- [ ] **Step 2:** `CHANGELOG.md` unter „Unreleased“ ergänzen; `docs/setup.md` Stub mit Installationsschritten (Repository hinzufügen, installieren, Sidebar).
- [ ] **Step 3:** Commit `docs: changelog and setup stub for plan 1`. Fable führt Review nach `superpowers:requesting-code-review` aus und entscheidet über den Übergang zu Teilplan 2.
