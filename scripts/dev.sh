#!/usr/bin/env bash
# Local dev stack: HA simulator (from Task 2.1 on), gateway, guest and admin Vite dev servers.
# Ctrl-C stops exactly the processes this script started; other gateways on this machine stay up.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

for tool in uv pnpm; do
  command -v "$tool" >/dev/null || { echo "dev.sh: $tool not found in PATH" >&2; exit 1; }
done
if [[ ! -d myha/web/node_modules ]]; then
  echo "dev.sh: myha/web/node_modules missing, run: pnpm --dir myha/web install" >&2
  exit 1
fi

pids=()
trap 'kill -TERM "${pids[@]}" 2>/dev/null || true; wait' EXIT
trap 'exit 0' INT TERM

if uv --directory myha/gateway run python -c \
  "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('myha_simulator.__main__') else 1)"; then
  uv --directory myha/gateway run myha-simulator --port 8123 &
  pids+=("$!")
else
  echo "simulator not available yet (Task 2.1), starting gateway and web only"
fi

# MYHA_ADMIN_PORT and MYHA_PUBLIC_PORT pass through from the caller; the Vite proxies stay on 8099/8443.
public_port=${MYHA_PUBLIC_PORT:-8443}
mkdir -p .dev/data
MYHA_DATA_DIR="$ROOT/.dev/data" MYHA_HA_WS_URL=ws://127.0.0.1:8123/api/websocket \
  MYHA_SUPERVISOR_URL=http://127.0.0.1:8123 SUPERVISOR_TOKEN=sim-token \
  MYHA_TRUSTED_INGRESS_PEER=127.0.0.1 MYHA_PUBLIC_ENABLED=1 \
  MYHA_PUBLIC_ORIGIN="http://localhost:$public_port" MYHA_WEB_DIST="$ROOT/myha/web/dist" \
  MYHA_ADMIN_BIND=127.0.0.1 MYHA_PUBLIC_BIND=127.0.0.1 MYHA_LOG_LEVEL=debug \
  uv --directory myha/gateway run myha-gateway serve &
pids+=("$!")

# BROWSER=none overrides --open in dev:admin; without --clearScreen false each Vite wipes the terminal.
# --strictPort: a taken port ends the script instead of moving Vite away from the URLs below.
BROWSER=none pnpm --dir myha/web dev --clearScreen false --strictPort &
pids+=("$!")
BROWSER=none pnpm --dir myha/web dev:admin --clearScreen false --strictPort &
pids+=("$!")

echo "guest    http://localhost:5173/"
echo "admin    http://localhost:5174/admin.html"
echo "gateway  http://localhost:$public_port/healthz"
wait -n  # the first process to exit ends the script; the EXIT trap stops the others
