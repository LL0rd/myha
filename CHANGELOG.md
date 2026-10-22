# Changelog

All notable changes to myHA are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

The foundation (gateway, web scaffold, app packaging and the core of the policy engine) and the
vertical guest core (invitations, guest sessions, live dashboard, actions and admin preview).
myHA is pre-alpha and not ready for real guests.

### Added

- Gateway with two listeners in one process: the admin listener on the ingress port 8099 and
  the public listener on 8443, which runs only when explicitly enabled. Both return errors in
  one JSON shape, never redirect and publish no API documentation.
- Settings from `MYHA_*` environment variables and the app options, and a log filter that
  redacts tokens, secrets, passwords and cookies.
- Separate guest and admin web bundles (Vite, React, TypeScript). The gateway serves them with
  a strict Content Security Policy, `nosniff`, `no-referrer` and per-file cache rules; the
  public listener never serves admin code.
- App packaging: multi-stage image on the Home Assistant Python base image, s6 service, ingress
  panel for administrators, placeholder backup hooks and live state excluded from backups.
- CI for linting, type checks, tests, the web build and an image smoke test, and a release
  workflow that builds images for amd64 and aarch64.
- Developer tools: a local development stack, an image build with smoke tests and a script that
  installs and configures the app through the Supervisor API.
- Home Assistant simulator (fake Core and Supervisor) for development and tests.
- Home Assistant WebSocket client with reconnect and backoff, registries, event subscriptions
  and service calls. Its keepalive uses Home Assistant's own `ping`, so a stuck Core is noticed
  even while the Supervisor proxy still answers.
- SQLite database with WAL and `synchronous=FULL`, so a committed revocation, checkout or
  invite redemption survives a power loss; foreign keys and secure delete are on.
- Device adapters for lights, switches, climate devices, covers and sensors: capabilities from
  Home Assistant attributes, one explicit service call per action, state views and the state
  expected after an action.
- Policy model: a closed set of actions, strict parameter schemas, grants, denies and fences,
  and decision codes in a fixed evaluation order; the effective access context and an
  immutable policy index.
- Admin access through Home Assistant ingress only: trusted ingress peer, exactly one identity
  header, a minimum Supervisor version, a fresh role check with Home Assistant for every change
  and a CSRF proof (acceptance cases A01-A05).
- Policy decisions: one pure function checks each request in a fixed order, and the first
  failing check denies (acceptance cases A06-A18). Allowed values lie on a grid whose step is
  the least common multiple of the rule's step, the device's step and the request format's step
  (0.1 °C, 1 %). Range limits move inwards onto that grid, so the panel never offers a value
  the device cannot take.
- Startup sequence: restore check, database migrations and the instance row come first. The
  public listener starts only when its configuration is complete, with a certificate and key
  that load or with reverse proxy addresses, and never during a restore. If it fails, it
  restarts after a delay that doubles from 5 to at most 60 seconds, while the admin panel stays
  up. A database that cannot be opened stops the app with a logged reason and exit code 3.
- Stays and one-time invitations through the admin API: an invitation's join link and QR code,
  rendered locally, appear only in the answer that issues it. Each invitation takes one device
  slot of its stay. Admins can list and revoke guest sessions and revoke invitations.
- Public guest authentication: invitation redemption with exactly one winner per invitation,
  guest sessions in host-bound `__Host-` cookies, a CSRF token for changes, one-time WebSocket
  tickets and `/api/v1/me` (acceptance cases A22-A24, A35 in part).
- Rate limits for the public routes, and on the public listener a 10-second deadline for each
  request and a 64 KiB body cap.
- Time plausibility guard: a clock that runs behind its high-water mark or jumps locks the
  access of guests with a stay window until the time is plausible again. A host clock that the
  Supervisor reports as synchronized is authoritative.
- Policy loader with a versioned index cache and registry drift detection: changed device
  abilities, a changed area or a replaced device mark the binding for review (A14-A16).
- Dashboard projection and endpoint: only allow-listed fields, only rooms with visible
  resources, and only values the policy would allow.
- Live events over WebSocket, projected per connection, with a new snapshot after every policy
  change and a close within 5 seconds after revocation (A19, A20, A25, A34, A52).
- Idempotent actions with confirmation by the observed device state, recovery as `unknown`
  after a restart and an audit row for every command (A31, A32).
- Admin preview and simulation for a principal, a stay, an invitation or a group; simulation
  never calls a device (A28, A29, A50).
- Admin audit listing with filters and keyset paging; denials fold per minute.
- Guest web app (PWA) with join, dashboard and ended pages, German and English, and the themes
  system, light, dark and contrast.
- Playwright end-to-end suite for the guest journey from invitation to revocation, with a CI
  job.
- Developer tools: `myha-gateway dev-seed room203` seeds a development database, and the
  simulator can add and remove entities and change the temperature unit at runtime.

### Changed

- `audit_event` has an `anon_key` column with a partial index, which folds anonymous denials.
  Migration 0001 was edited in place, because there is no release yet.
- Test coverage now measures code that runs in greenlet and thread switches.

### Security

- A guard refuses Home Assistant and Supervisor calls, read sessions and nested write sessions
  inside a database write session.
- The audit writer refuses parameter names that look like secrets, such as `code`, `otp` or
  `pwd`.
- The public listener aborts TLS clients that stay silent after the server has closed the
  connection.

### Removed

- The pre-release environment probe: the `probe_mode` option, the admin probe route and the
  `myha-gateway probe` command. A `probe_mode` value left in the options of an existing
  installation is ignored. `/healthz` reports only `status` and `client_ip`.

[Unreleased]: https://github.com/LL0rd/myha/commits/main
