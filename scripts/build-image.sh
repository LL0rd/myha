#!/usr/bin/env bash
# Build the amd64 add-on image, then smoke-test it twice: the gateway on its own, then through s6.
set -euo pipefail
cd "$(dirname "$0")/.."
base=$(sed -n 's/^  amd64: *//p' myha/build.yaml)  # the pinned base image the Supervisor uses
docker buildx build --load --pull --platform linux/amd64 \
  --build-arg BUILD_FROM="${base:?no amd64 base image in myha/build.yaml}" -t myha-local:dev myha/

# Print the container's logs, then stop with a message.
fail() { docker logs "$1" 2>&1 || true; echo "smoke test failed: $2" >&2; exit 1; }

# 1. Gateway alone: --entrypoint bypasses /init (s6). Created before the start so the trap
# removes it even if the start fails; no --rm, so a crashed container keeps its logs.
gw=$(docker create --entrypoint /opt/myha/venv/bin/myha-gateway \
  -e MYHA_DATA_DIR=/data -e MYHA_PUBLIC_ENABLED=1 -e MYHA_PUBLIC_ORIGIN=http://127.0.0.1:18443 \
  --tmpfs /data -p 127.0.0.1:18443:8443 myha-local:dev serve)
trap 'docker rm -f "$gw" >/dev/null 2>&1 || true' EXIT
docker start "$gw" >/dev/null
for _ in $(seq 1 30); do
  curl -fs http://127.0.0.1:18443/healthz >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:18443/healthz || fail "$gw" "no /healthz within 30 s"
echo
headers=$(curl -fsSI http://127.0.0.1:18443/ | tr -d '\r') || fail "$gw" "HEAD / failed"
grep -iE "^(content-security-policy|cache-control):" <<<"$headers" || true
grep -qiE "^content-security-policy: (.*; )?default-src 'self'(;|$)" <<<"$headers" \
  || fail "$gw" "CSP without default-src 'self'"
grep -qix "cache-control: no-store" <<<"$headers" || fail "$gw" "entry page without no-store"
docker exec "$gw" test -f /opt/myha/web/admin/admin.html || fail "$gw" "admin bundle missing"

# 2. Through /init: s6 starts the service, the gateway comes up, docker stop ends with exit 0.
# Waits for the gateway itself: s6 reports "successfully started" as soon as run is spawned.
s6=$(docker create -e SUPERVISOR_TOKEN=dummy --tmpfs /data myha-local:dev)
trap 'docker rm -f "$gw" "$s6" >/dev/null 2>&1 || true' EXIT
docker start "$s6" >/dev/null
for _ in $(seq 1 30); do
  log=$(docker logs "$s6" 2>&1)
  grep -q "Uvicorn running on" <<<"$log" && break
  sleep 1
done
grep -q "service myha successfully started" <<<"$log" || fail "$s6" "s6 did not start myha"
grep -q "Uvicorn running on" <<<"$log" || fail "$s6" "no gateway under s6 within 30 s"
docker stop "$s6" >/dev/null
code=$(docker inspect -f '{{.State.ExitCode}}' "$s6")
[ "$code" = 0 ] || fail "$s6" "exit code $code after docker stop"
log=$(docker logs "$s6" 2>&1)
if grep "is deprecated" <<<"$log"; then fail "$s6" "s6 reports a deprecated layout"; fi
echo "s6: service myha started, gateway up, docker stop gave exit code 0, no deprecation warning"
