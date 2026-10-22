# Changelog

All notable changes to myHA are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

The foundation: gateway, web scaffold, app packaging and the core of the policy engine.
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

[Unreleased]: https://github.com/LL0rd/myha/commits/main
