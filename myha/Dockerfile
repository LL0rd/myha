# syntax=docker/dockerfile:1.7
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23-2026.10.1
# Build tools pinned by tag and digest; Dependabot updates both.
FROM ghcr.io/astral-sh/uv:0.10.12@sha256:72ab0aeb448090480ccabb99fb5f52b0dc3c71923bffb5e2e26517a1c27b7fec AS uv

FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS web
WORKDIR /src/web
# The pnpm version must match packageManager in web/package.json.
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM ${BUILD_FROM} AS runtime
# /usr/local/bin/python3 is the base image's CPython 3.13; uv must never download another one.
# uv is only mounted and runs without a cache: neither ends up in the image (about 110 MB).
# The base image points both extra index variables at the Home Assistant wheel index; emptied,
# so every build input comes from the lock file.
ENV UV_PROJECT_ENVIRONMENT=/opt/myha/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3 UV_NO_CACHE=1 UV_EXTRA_INDEX_URL= PIP_EXTRA_INDEX_URL=
WORKDIR /opt/myha/gateway
COPY gateway/pyproject.toml gateway/uv.lock gateway/.python-version ./
# Only the locked dependencies go into the venv; the gateway itself is never built or installed.
# shortcut: no compiler; --no-build fails loudly if a dependency ever lacks a musllinux wheel.
# Then drop --no-build and add `apk add --virtual .build build-base libffi-dev` to this step.
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    uv sync --locked --no-dev --no-install-project --no-build
COPY gateway/ ./
RUN /opt/myha/venv/bin/python -m compileall -q myha_gateway
COPY --from=web /src/web/dist /opt/myha/web
COPY rootfs/ /
RUN chmod a+x /etc/s6-overlay/s6-rc.d/myha/run /etc/s6-overlay/s6-rc.d/myha/finish /opt/myha/backup-export /opt/myha/backup-cleanup
# The gateway runs from its source: python -m myha_gateway with this directory on PYTHONPATH.
ENV PATH="/opt/myha/venv/bin:${PATH}" PYTHONPATH=/opt/myha/gateway MYHA_WEB_DIST=/opt/myha/web
