# myHA

myHA gives guests, residents and wall displays room-scoped access to your home. You pick a
room, define the allowed actions and values, preview the panel as that person will see it, and
then hand out access. Guests join with a single-use QR invite tied to their stay. Their access
ends with the stay and can be revoked at any time. Every request is checked on the server
against a deny-by-default policy.

> **Pre-alpha.** myHA is under active development and not ready for real guests. Most of what
> this page describes is not implemented yet, and data may be discarded between versions
> without migration. Do not expose myHA to the internet.

## Admin panel

Open **myHA** in the Home Assistant sidebar. The admin panel runs through Home Assistant
ingress and is only available to Home Assistant administrators. It is never served on the
guest port.

## Guest port

| Port | Purpose | Default |
|------|---------|---------|
| `8443/tcp` | Guest and resident access, over HTTPS or behind your reverse proxy | Off |

To turn the guest port on, enter a host port in the **Network** section of the app's
configuration. myHA only answers on this port after guest access has been set up in the admin
panel. For HTTPS, myHA reads the certificate and key from the Home Assistant `/ssl` folder,
read-only.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `log_level` | `info` | Log detail: `debug`, `info`, `warning` or `error`. |

## Backups

myHA supports Home Assistant backups while it runs. Its live state is excluded from backups.
Before each backup, myHA writes an export of its configuration, and the backup contains that
export instead. After a restore, an administrator reviews the export in the admin panel before
myHA grants access again. Sessions, invitations and passwords are never restored, so guests and
residents need new access afterwards.

In this pre-alpha, the backup hooks only write a test file.

## More information

Source code, issues and documentation: <https://github.com/LL0rd/myha>. Design documents are
in the [`docs` folder](https://github.com/LL0rd/myha/tree/main/docs).
