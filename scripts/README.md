# scripts

Developer tools, run from the repository root.

- `dev.sh` starts the local stack: the Home Assistant simulator on :8123 (from Task 2.1 on),
  the gateway (admin :8099, public :8443, plain HTTP on 127.0.0.1, data in `.dev/data`) and
  the Vite dev servers for the guest app (http://localhost:5173/) and the admin app
  (http://localhost:5174/admin.html), which proxy `/api` to the gateway. Ctrl-C stops exactly
  the processes it started. Needs `uv`, `pnpm` and `pnpm --dir myha/web install`; run
  `pnpm --dir myha/web build` first if the gateway itself should serve the bundles.
  `MYHA_ADMIN_PORT` and `MYHA_PUBLIC_PORT` move the gateway, the Vite proxies stay on 8099/8443.
- `build-image.sh` builds the amd64 add-on image with Docker and smoke-tests the gateway in it.
- `make-icons.py` redraws `myha/icon.png` and `myha/logo.png`:
  `uv run --with pillow python scripts/make-icons.py`.
- `install-on-ha.py` (planned, Task 1.1) installs and configures the add-on on a Home Assistant
  instance through the Supervisor API. It reads `HA_URL` and `HA_TOKEN` from the environment.

`.dev/` is git-ignored and holds local state: the gateway data of `dev.sh` and `ha.env` with
`HA_URL` and `HA_TOKEN` for `install-on-ha.py` (`set -a; source .dev/ha.env; set +a`).
