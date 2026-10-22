#!/usr/bin/env bash
# Build the amd64 add-on image and smoke-test the gateway in it.
set -euo pipefail
cd "$(dirname "$0")/.."
docker buildx build --load --platform linux/amd64 \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23 \
  -t myha-local:dev myha/
# --entrypoint bypasses /init (s6) on purpose; the s6 path is verified on a real Home Assistant (WP1).
# No --rm: a container that dies on startup must keep its logs for the output below.
cid=$(docker run -d --entrypoint /opt/myha/venv/bin/myha-gateway \
  -e MYHA_DATA_DIR=/data -e MYHA_PUBLIC_ENABLED=1 -e MYHA_PUBLIC_ORIGIN=http://127.0.0.1:18443 \
  --tmpfs /data -p 127.0.0.1:18443:8443 myha-local:dev serve)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 30); do
  if curl -fs http://127.0.0.1:18443/healthz >/dev/null; then
    curl -fsS http://127.0.0.1:18443/healthz; echo
    curl -fsSI http://127.0.0.1:18443/ | grep -iE "^(content-security-policy|cache-control):"
    exit 0
  fi
  sleep 1
done
docker logs "$cid"; exit 1
