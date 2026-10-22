# Setting up myHA

> **Pre-alpha.** myHA is under active development and not ready for real guests. This page
> describes the intended setup. Sections marked *Coming with the web pilot* describe parts of
> the admin panel that do not exist yet.

## Requirements

- Home Assistant OS on an amd64 or aarch64 system. Apps are only available on Home Assistant
  OS.
- Supervisor 2026.10.3 or newer. On older versions, myHA keeps the admin panel locked.
- For guests outside your network: a host name and a certificate, either for myHA itself or for
  a reverse proxy in front of it.

## Installation

1. In Home Assistant, go to **Settings → Apps** and select **Install app**.
2. In the top-right corner, open the **⋮** menu and select **Repositories**.
3. Add `https://github.com/LL0rd/myha` and select **Add**.
4. Open **myHA** in the list of apps, install it and start it.

The admin panel then appears as **myHA** in the sidebar. It runs through Home Assistant ingress,
so only Home Assistant administrators can open it, and it needs no port of its own.

## Public listener

Guests, residents and wall displays connect to the public listener on container port
`8443/tcp`. It is off by default:

- To reach it from your network, enter a host port for `8443/tcp` in the **Network** section of
  the app's **Configuration** tab.
- Even with a host port, myHA answers on this port only after the public address and the
  connection mode below are configured in the admin panel.

## Connection modes

*Coming with the web pilot.* The admin panel will configure one of two modes. In both, the
public origin is always set explicitly: the exact address guests open, such as
`https://guests.example.com:8443`. Invitations and origin checks use only this address.

| Mode | Use it when | What myHA does |
|------|-------------|----------------|
| `tls` | myHA terminates HTTPS itself | Reads a PEM certificate and key from the Home Assistant `/ssl` folder, read-only. Any issuer works: Let's Encrypt through its Home Assistant app, your own CA or a private certificate. Renewed files are picked up without a restart. |
| `proxy` | A reverse proxy in front of myHA terminates HTTPS | Listens without TLS and accepts connections only from the proxy addresses you configure. `X-Forwarded-Proto` and `X-Forwarded-Host` count only from those addresses. |

A third setting, the development override, runs the public listener over plain HTTP without any
of this configuration. It is meant for local development and automated tests only.

Browsers accept a self-signed or private-CA certificate only on devices that already trust that
CA. The native apps accept only certificates from a CA the device trusts, optionally pinned to
the certificate's public key; see [security](security.md).

## Setup assistant

*Coming with the web pilot.* A setup assistant in the admin panel will check, step by step:

- the gateway and its connection to Home Assistant,
- your administrator identity through ingress,
- the public origin, the certificate or the proxy, and secure WebSockets from outside,
- a guest device test: a short-lived test QR code proves that a phone reaches myHA over HTTPS
  and WebSockets,
- whether the client addresses myHA sees are plausible, which rate limits and proxy checks rely
  on,
- the trust profile for the native apps: the primary pin of the certificate's public key and a
  backup pin.

The backup pin must belong to the key of the next certificate. If pins are required and no
backup pin exists, a certificate renewal with a new key locks out the native apps, so the
assistant warns about this case.

## Guest access

The admin panel for stays does not exist yet. Until it comes with the web pilot, stays and
invitations are created through the admin API, which is reachable only through Home Assistant
ingress.

1. Create a stay with `POST /api/v1/admin/stays`. A stay names one room, a published role, a
   start, an end and the number of device slots, from 1 to 50. A new stay is a draft.
2. Issue invitations with `POST /api/v1/admin/stays/{stay_id}/invites`. Each invitation takes
   one free device slot, and the first invitation confirms the stay.
3. Hand each guest one join link or its QR code. The answer that issues an invitation is the
   only place where its secret appears, so copy or print it right away.

An invitation works once. It can be redeemed for up to 1440 minutes (24 hours), 15 minutes by
default, and never after the stay ends. The secret travels in the part of the link after `#`,
which browsers never send to a server. A revoked invitation or session frees its device slot,
and so do an expired invitation that nobody redeemed and a session that has ended.

The guest opens the link and lands on the join page. It removes the secret from the address bar
and redeems it. Before the stay starts, the guest waits with no controls. During the stay, the
dashboard shows the room with the actions the role allows, and changes from Home Assistant
appear live. When the stay ends or an admin revokes the session, the page switches to the
"Access ended" screen.

Guests need the public listener:

- The public origin must be set, because every join link uses it. Without it, issuing
  invitations fails.
- The session cookie is a `__Host-` cookie, so browsers keep it only over HTTPS. The one
  exception is `localhost`, which browsers treat as secure for local development.

### Development loop

`scripts/dev.sh` starts the simulator, the gateway and the web development server, with the
public listener on `http://localhost:8443`. The command below then creates room 203 of the
simulator, a guest role and a stay, and prints two join links:

```sh
MYHA_DATA_DIR="$PWD/.dev/data" MYHA_HA_WS_URL=ws://127.0.0.1:8123/api/websocket \
  MYHA_SUPERVISOR_URL=http://127.0.0.1:8123 SUPERVISOR_TOKEN=sim-token \
  MYHA_PUBLIC_ENABLED=1 MYHA_PUBLIC_ORIGIN=http://localhost:8443 \
  uv run --project myha/gateway myha-gateway dev-seed room203
```

Run it from the repository root, with the settings of `scripts/dev.sh`, so that it writes to the
same database. The command refuses to run without `MYHA_PUBLIC_ENABLED=1`, which is for
development and tests only. A second run changes
nothing and prints no link, because the secrets of the first run are stored nowhere.

The end-to-end suite starts its own simulator, gateway and ingress stand-in, seeds the same
room and walks the guest journey in Chromium. Build the web app first:

```sh
(cd myha/web && pnpm install && pnpm build)
cd tests/e2e && pnpm install && pnpm exec playwright install chromium
pnpm exec playwright test
```
