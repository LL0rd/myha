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
