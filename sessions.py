"""Check for, and clean up, Steel sessions this project left running.

    uv run sessions.py             # list anything still live
    uv run sessions.py --release   # release everything still live

A leaked session keeps billing. `agent.session.steel_browser` releases in a
`finally`, so leaks should not happen — this is the belt to that braces, and
the thing to run if a process was killed with SIGKILL.
"""

from __future__ import annotations

import argparse

from agent.session import release_session, steel_client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", action="store_true", help="release every live session")
    args = parser.parse_args()

    client = steel_client()
    live = list(client.sessions.list(status="live"))

    if not live:
        print("No live Steel sessions. Nothing to clean up.")
        return

    print(f"{len(live)} live session(s):")
    for session in live:
        print(f"  {session.id}  started {getattr(session, 'created_at', '?')}")

    if not args.release:
        print("\nRelease them with:  uv run sessions.py --release")
        return

    released = sum(release_session(client, session.id) for session in live)
    print(f"\nReleased {released}/{len(live)}.")


if __name__ == "__main__":
    main()
