# S0 report – compatibility evidence

Status: in progress (WP1). This file records what was actually verified against real systems before the design
is treated as settled (design §1, phase S0). Entries marked **open** still need the reference installation
(a HAOS 18.x amd64 VM with Supervisor 2026.10.x) or a real device.

| # | Assumption (design) | Evidence | Result | Consequence |
|---|---|---|---|---|
| 1 | Core exposes `config/auth/list` to the Supervisor token and returns `id`, `is_owner`, `is_active`, `group_ids`, `system_generated` (E5) | WebSocket probe against Core 2026.10.2 with an admin token on 2026-10-22 | ✅ confirmed | Admin SSO path stays as designed |
| 2 | Supervisor ≥ 2026.10.3 exists and contains the `panel_admin` enforcement (E4) | GitHub releases and PR #7308 checked on 2026-10-22; the reference installation reports Supervisor 2026.10.3 (stable) via the developer script on 2026-10-22 | ✅ confirmed (released 2026-10-19, rolled out automatically) | Minimum version enforced by the gateway; the probe can run against the reference installation |
| 3 | Ingress forwards from `172.30.32.2` and sets `X-Remote-User-Id/-Name/-Display-Name` (E5) | Official app documentation; Supervisor source (`supervisor/api/ingress.py`) | ✅ documented; `X-Remote-User-Groups` merged upstream on 2026-10-21, not yet released | Peer check and header handling as designed; groups header logged only |
| 4 | `GET http://supervisor/info` works without `hassio_api` (§8) | Supervisor middleware source: `api_bypass` contains `/info` | ✅ in source | **open:** confirm on the reference installation (Task 1.2), including Core's version in `homeassistant`, the source of `core_version` in `GET /api/v1/admin/session` |
| 5 | The Supervisor builds apps with BuildKit (`# syntax`, `--mount=from`, `--platform=$BUILDPLATFORM`) | Supervisor source `supervisor/apps/build.py` (PR #5974, 2025-07) uses `docker buildx build --pull` | ✅ in source | Multi-stage Dockerfile kept; **open:** first real build log (Task 1.2) |
| 6 | `build.yaml` is accepted by the current Supervisor | Supervisor source: accepted with a deprecation warning | ✅ with warning | Keep for the pilot like the official apps; migrate to `BUILD_ARCH` build args later |
| 7 | Python base image `…-base-python:3.13-alpine3.23` provides CPython 3.13 at `/usr/local/bin/python3` | Local image inspection on 2026-10-22 (`3.13.17`) | ✅ confirmed | `UV_PYTHON` points there; immutable tag `3.13-alpine3.23-2026.10.1` pinned |
| 8 | All runtime dependencies have musl wheels for amd64 and aarch64 (no compiler in the image) | `uv.lock` wheel list; `uv pip install --dry-run --python-platform aarch64-unknown-linux-musl --only-binary :all:` | ✅ confirmed (35 packages) | No `build-base` in the image; `--no-build` guards future packages. **open:** real aarch64 build |
| 9 | s6-overlay v3 service layout in the base image | Local run through `/init`: service starts; `s6-rc.d/user` layout prints a deprecation warning (s6-overlay 3.2.3.2) | ✅ works | Switched to `user-bundles.d` plus `dependencies.d/base`; smoke test asserts a warning-free start |
| 10 | Graceful stop: uvicorn exits 143 after SIGTERM | Local run: `finish` sees `256` by signal 15 | ✅ confirmed | `finish` treats signal exits as a normal stop |
| 11 | The real client IP reaches the published port 8443 on HAOS (IPv4/IPv6) | — | **open** (Task 1.2) | Needed for per-IP rate limits and proxy CIDR checks |
| 12 | `backup_pre`/`backup_post` run on a hot backup and `backup_exclude` keeps `/data/state` out of the archive (A47) | — | **open** (Task 1.3) | Restore design depends on it |
| 13 | `GET /host/info` (`dt_synchronized`) reachable with `hassio_api: true` + `hassio_role: default` (E26) | — | **open** (Task 1.2) | Second time source only if available |
| 14 | Split air conditioners keep or change the setpoint on `hvac_mode` change (E7) | — | **open** (Task 1.4, needs operator consent) | Decides whether the post-change check must mark bindings for review |
| 15 | Argon2id parameters reaching ≈250 ms on the reference hardware (E20) | — | **open** (Task 1.4) | Recorded in `docs/security.md` |
| 16 | Android OkHttp HTTPS + WSS pinning behaves identically on both paths (A36–A37) | JVM spike in the private repo (`spikes/`, commit `681a269`) on 2026-10-22: OkHttp 4.12.0 against a local TLS server with a test CA and keys A/B, ten tests, also green with OkHttp 5.5.1 | ✅ confirmed on the JVM, A38 included; **open:** the same cases on Android (Conscrypt) in WP15 | One client and pool per trust profile; paths only, no redirects; rotation closes WebSockets with 1012 and drops late frames |

## Notes

- Dependency versions resolved on 2026-10-22: FastAPI 0.159, Starlette 1.9, Pydantic 2.14, SQLAlchemy 2.1, uvicorn 0.54, websockets 17 (legacy module names are deprecated; the client uses the top-level API).
- The local image is 205 MB (base 128 MB, venv 77 MB, web 1.8 MB); uv is only mounted during the build and not part of the image.
- The developer script `scripts/install-on-ha.py status` (read-only) was verified against the reference installation: Supervisor version, repository list and app state are reported; the app is not yet installed.
- The reference installation is used exclusively for these proofs; nothing in the product is tailored to it.
- Android transport spike (Task 1.5): HTTPS and WSS fail identically, with the same exception and message, on a wrong pin, a wrong host name or an untrusted chain, and the server sees no request; a matching pin never replaces chain validation.
- OkHttp checks pins once per TLS handshake and only for host names it has an entry for, so an IP literal or any other name of the same server is not pinned. The native transport therefore takes paths only and never follows redirects. A pin on a CA key matches every certificate that CA issues, so the trust profile carries leaf SPKI pins.
- On rotation, OkHttp's `evictAll()` closes only idle connections, and a graceful WebSocket `close()` keeps delivering frames until the peer answers (hard cancel after 60 s). The spike transport closes tracked WebSockets with 1012 and drops late frames; calls already running during a rotation still finish under the old profile, which WP15 has to handle.
- Spike tooling: Gradle 9.8.2 (runs on JDK 26), Kotlin 2.4.21, JDK 17 toolchain, JUnit 5.14.4; `./gradlew test -Pokhttp=5.5.1` runs the same suite against OkHttp 5.
