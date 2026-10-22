# myHA

myHA is a Home Assistant app (HAOS add-on) that gives guests, residents and wall displays
room-scoped access to a home. Administration runs inside Home Assistant: operators pick a room,
define the allowed actions and values, preview the panel as that user will see it, and then
hand out access. Guests join by scanning a single-use QR invite tied to a stay. Their access
ends automatically with the stay and can be revoked at any time. Every request is checked on
the server against a deny-by-default policy, so the user interface never decides what someone
is allowed to do.

## Status

Pre-alpha. myHA is under active development and cannot be installed yet.

## Installation (planned)

Once a first release is available:

1. In Home Assistant OS, go to **Settings → Apps → App Store**.
2. Open the **⋮** menu, select **Repositories** and add `https://github.com/LL0rd/myha`.
3. Install **myHA** from the store and start it.

## Documentation

Design documents and implementation plans (currently in German) are in [`docs/`](docs/).
Start with the [design specification](docs/superpowers/specs/2026-10-22-myha-design.md).

## License

myHA is licensed under the [Apache License 2.0](LICENSE).
