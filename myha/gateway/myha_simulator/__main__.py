"""``myha-simulator --port N [--host 127.0.0.1]``: the fake Home Assistant for dev and E2E."""

import argparse
import contextlib
import logging

from myha_simulator.server import create_app, make_server


def main(argv: list[str] | None = None) -> None:
    """Entry point of the ``myha-simulator`` command."""
    parser = argparse.ArgumentParser(
        prog="myha-simulator",
        description="Fake Home Assistant Core and Supervisor for development and tests. The /_sim "
        "control API has no authentication; binding to 127.0.0.1 (the default) is its only "
        "protection.",
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C after uvicorn's graceful shutdown
        make_server(create_app(), args.host, args.port).run()


if __name__ == "__main__":
    main()
