"""Confirm the environment still works: deps import, keys load, Steel opens.

    uv run check_setup.py                 # full check, opens a real session
    uv run check_setup.py --skip-session  # offline check only
"""

import argparse
import os
from importlib.metadata import version

from dotenv import load_dotenv

load_dotenv()

from browser_use import Agent, Browser, ChatOpenAI  # noqa: E402,F401
from steel import Steel  # noqa: E402

REQUIRED_KEYS = ("STEEL_API_KEY", "OPENAI_API_KEY")


def check_keys() -> bool:
    """Report which keys loaded. Returns True only if all of them did."""
    all_present = True
    for name in REQUIRED_KEYS:
        value = os.getenv(name)
        if not value or value.startswith("your-"):
            print(f"  {name:<16} MISSING (unset or still a placeholder)")
            all_present = False
        else:
            print(f"  {name:<16} loaded ({value[:6]}...{value[-4:]})")
    return all_present


def check_session() -> None:
    """Open a Steel session, print its viewer URL, then always release it."""
    client = Steel(steel_api_key=os.environ["STEEL_API_KEY"])
    print("\nOpening a Steel session...")
    session = client.sessions.create()
    try:
        print(f"  session id  {session.id}")
        print(f"  status      {session.status}")
        print(f"  viewer      {session.session_viewer_url}")
    finally:
        client.sessions.release(session.id)
        print(f"  released    (status now: {client.sessions.retrieve(session.id).status})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-session", action="store_true", help="don't open a Steel session")
    args = parser.parse_args()

    print("Packages")
    print(f"  browser-use     {version('browser-use')}")
    print(f"  steel-sdk       {version('steel-sdk')}")

    print("\nKeys")
    if not check_keys():
        raise SystemExit("\nFix the missing keys in .env, then re-run.")

    if args.skip_session:
        print("\nSkipping the Steel session check.")
        return
    check_session()
    print("\nEnvironment is good.")


if __name__ == "__main__":
    main()
