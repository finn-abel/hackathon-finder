"""Step 10: serve the local dashboard.

    uv run dashboard.py              # serve and open a browser
    uv run dashboard.py --port 8080
    uv run dashboard.py --no-open

A plain static server over the project directory. The page is one HTML file
with no build step and no dependencies; it fetches results.json at load, so
re-running `judge.py` and refreshing is enough to see new results.

It binds to localhost only — results.json is your own scan, not something to
put on a network.
"""

from __future__ import annotations

import argparse
import http.server
import socket
import webbrowser
from functools import partial

from core.config import PROJECT_ROOT
from core.store import RESULTS_PATH

DEFAULT_PORT = 8000
HOST = "127.0.0.1"
PAGE = "/dashboard/index.html"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Same as the default, minus a log line for every asset."""

    def log_message(self, fmt: str, *args) -> None:
        if "GET /results.json" in (fmt % args):
            print("  served results.json")


def free_port(start: int, attempts: int = 20) -> int:
    """Find a usable port, so a second run does not just fail."""
    for port in range(start, start + attempts):
        with socket.socket() as probe:
            if probe.connect_ex((HOST, port)) != 0:
                return port
    raise SystemExit(f"No free port between {start} and {start + attempts}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="do not launch a browser")
    args = parser.parse_args()

    if not RESULTS_PATH.exists():
        raise SystemExit(
            f"No {RESULTS_PATH.name} yet. Run `uv run judge.py --include-past` first."
        )

    port = free_port(args.port)
    handler = partial(QuietHandler, directory=str(PROJECT_ROOT))
    url = f"http://{HOST}:{port}{PAGE}"

    with http.server.ThreadingHTTPServer((HOST, port), handler) as server:
        print(f"Dashboard: {url}")
        print("Ctrl+C to stop.\n")
        if not args.no_open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
