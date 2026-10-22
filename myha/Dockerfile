# syntax=docker/dockerfile:1.7
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23-2026.10.1
FROM --platform=$BUILDPLATFORM node:25-alpine AS web
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
ENV UV_PROJECT_ENVIRONMENT=/opt/myha/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3 UV_NO_CACHE=1
WORKDIR /opt/myha/gateway
COPY gateway/pyproject.toml gateway/uv.lock gateway/.python-version ./
# shortcut: no compiler; --no-build fails loudly if a dependency ever lacks a musllinux wheel.
# Then drop --no-build and add `apk add --virtual .build build-base libffi-dev` to this step.
RUN --mount=from=ghcr.io/astral-sh/uv:0.10,source=/uv,target=/bin/uv \
    uv sync --frozen --no-dev --no-install-project --no-build
COPY gateway/ ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.10,source=/uv,target=/bin/uv \
    uv sync --frozen --no-dev
COPY --from=web /src/web/dist /opt/myha/web
COPY rootfs/ /
RUN chmod a+x /etc/s6-overlay/s6-rc.d/myha/run /etc/s6-overlay/s6-rc.d/myha/finish /opt/myha/backup-export /opt/myha/backup-cleanup
ENV PATH="/opt/myha/venv/bin:${PATH}" MYHA_WEB_DIST=/opt/myha/web
