# Security

> **Pre-alpha.** myHA is not ready for real guests. This page lists what is implemented today and
> grows with the implementation.

## Threat model

Section 10 of the
[design specification](superpowers/specs/2026-10-22-myha-design.md#10-sicherheit--zuordnung-bedrohung--mechanismus)
(German) maps each threat to its mechanisms and acceptance tests. In short, myHA defends
against:

- forged administrator identities on the ingress path,
- guests reaching rooms, devices or actions outside their grant, including through crafted
  parameters or raw Home Assistant service calls,
- stale permissions in open connections after a change, a checkout or a revocation,
- misuse of invitation QR codes, cross-site requests, replayed tickets and redirects,
- restored backups that would reactivate access,
- secrets in logs,
- native apps connecting to the wrong TLS endpoint.

## Implemented today

- **Admin access through ingress only.** The admin listener has no published port and answers
  only the Supervisor's ingress proxy. Its peer address can be changed only through an
  environment variable for tests, never through an app option. Every admin API request needs
  exactly one `X-Remote-User-Id` header and a Supervisor of at least version 2026.10.3.
- **Fresh role checks.** myHA asks Home Assistant whether the user is an active owner or
  administrator. Changes and sensitive reads ask on every request; other reads reuse a
  confirmed role for at most 5 seconds. If Home Assistant cannot answer, access is denied.
- **CSRF proof for admin changes.** The `X-MyHA-CSRF` header carries an HMAC-SHA256 of user,
  ingress path and day, keyed with a per-instance secret. `Sec-Fetch-Site` must be
  `same-origin` when the browser sends it. Behind the peer check, every failed identity or CSRF
  check gets the same answer.
- **No redirects.** Neither listener redirects, not even for a missing trailing slash.
- **Durable storage.** SQLite runs with `synchronous=FULL`, so a committed revocation or checkout
  survives a power loss, and with `secure_delete`, so deleted rows are overwritten. Database
  errors never echo bound values.
- **Log redaction.** Tokens, secrets, passwords, cookies, authorization headers and long random
  strings are replaced with `<redacted>` before a record reaches the log.
- **Closed action set.** Only a fixed list of actions exists. Parameters must match strict
  schemas that reject extra fields, NaN, infinity and exponents. Each action becomes one
  explicit Home Assistant service call; nothing is passed through.
- **Fail-closed policy order.** Decision checks run in a fixed order, and the first failing check
  denies. A device limit the rule format cannot represent offers nothing.
- **Browser hardening.** A strict Content Security Policy without external sources,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`
  for API answers and entry pages. The public listener never serves admin code.

## Decided, not implemented yet

- **Leaf pins for the native apps.** Pins are SHA-256 hashes of the public key (SPKI) of the leaf
  certificate, never of a CA key: a CA pin would match every certificate that CA issues. A
  transport spike confirmed the behaviour; the trust profile comes with the native apps.
- **No redirects in the native apps.** The native transport takes paths relative to the public
  origin and never follows redirects, because a redirect to another host name or an IP address
  would escape the pin.

## Known gaps

- **Request limits on the public listener are partial.** It caps an incomplete header block at
  16 KiB and concurrent connections at 200, but a complete oversized header block that arrives
  in one read, a slowly sent header block (slowloris) and oversized JSON bodies stay unlimited
  until Plan 2 Task 5a.1.

## Argon2id parameters

Resident passwords will be hashed with Argon2id (`argon2-cffi`). The parameters are chosen so
that one hash takes about 250 ms. The values below come from the pre-release measurement on an
amd64 Home Assistant OS virtual machine, as the median of three runs with parallelism 1.

| time_cost | memory_cost (KiB) | Median (ms) |
|-----------|-------------------|-------------|
| 2 | 19456 | pending |
| 2 | 47104 | pending |
| 2 | 65536 | pending |
| 3 | 19456 | pending |
| 3 | 47104 | pending |
| 3 | 65536 | pending |
| 4 | 19456 | pending |
| 4 | 47104 | pending |
| 4 | 65536 | pending |

**Chosen parameters:** pending.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: on the repository's **Security** tab,
select **Report a vulnerability**. Do not open public issues for security problems.
