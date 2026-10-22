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
- Home Assistant WebSocket client with reconnect and backoff, keepalive, registries, event
  subscriptions and service calls.
- SQLite database with WAL, a full sync on every commit, foreign keys and secure delete;
  migrations run at startup.
- Device adapters for lights, switches, climate devices, covers and sensors: capabilities from
  Home Assistant attributes, one explicit service call per action, state views and the state
  expected after an action.
- Policy model: a closed set of actions, strict parameter schemas, grants, denies and fences,
  and decision codes in a fixed evaluation order; the effective access context and an
  immutable policy index.
- Admin access through Home Assistant ingress only: trusted ingress peer, exactly one identity
  header, a minimum Supervisor version, a fresh role check with Home Assistant for every change
  and a CSRF proof.
<!-- TODO part 2: policy decisions (decide) with the policy acceptance cases (task 3.5) -->
<!-- TODO part 2: startup sequence and public listener switch (task 4.2) -->

[Unreleased]: https://github.com/LL0rd/myha/commits/main
