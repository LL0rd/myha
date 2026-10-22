# scripts

Developer tools, run from the repository root.

- `dev.sh` starts the local stack: the Home Assistant simulator on :8123 (from Task 2.1 on),
  the gateway (admin :8099, public :8443, plain HTTP on 127.0.0.1, data in `.dev/data`) and
  the Vite dev servers for the guest app (http://localhost:5173/) and the admin app
  (http://localhost:5174/admin.html), which proxy `/api` to the gateway. Ctrl-C stops exactly
  the processes it started. Needs `uv`, `pnpm` and `pnpm --dir myha/web install`; run
  `pnpm --dir myha/web build` first if the gateway itself should serve the bundles.
  `MYHA_ADMIN_PORT` and `MYHA_PUBLIC_PORT` move the gateway, the Vite proxies stay on 8099/8443.
- `build-image.sh` builds the amd64 add-on image `myha-local:dev` with Docker and smoke-tests it
  twice: the gateway on its own, then the s6 path through `/init`. The first test also checks
  that the image contains the gateway but not the simulator.
  `scripts/build-image.sh --smoke-only <image-tag>` skips the build and only smoke-tests an
  existing image, for example the one CI just built.
  The first test publishes the gateway on `127.0.0.1:18443`; `SMOKE_PORT` picks another host
  port, so that several smoke tests can run at the same time.
- `make-icons.py` redraws `myha/icon.png` and `myha/logo.png`:
  `uv run --with pillow==13.0.0 python scripts/make-icons.py`.
- `install-on-ha.py` installs and configures the add-on on a Home Assistant
  instance through the Supervisor API. It reads `HA_URL` and `HA_TOKEN` from the environment.

`.dev/` is git-ignored and holds local state: the gateway data of `dev.sh` and `ha.env` with
`HA_URL` and `HA_TOKEN` for `install-on-ha.py` (`set -a; source .dev/ha.env; set +a`).

Dependabot writes `myha/gateway/uv.lock` with uv 0.11, while CI and the Dockerfile pin uv 0.10.x;
raise both pins with the first uv pull request.
