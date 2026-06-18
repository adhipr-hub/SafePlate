from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.local_app import run_server


def _default_port() -> int:
    """Honour the host platform's $PORT (Render/Heroku set it) so the app binds
    where the proxy expects; fall back to 8765 for local use."""
    raw = os.environ.get("PORT", "").strip()
    return int(raw) if raw.isdigit() else 8765


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the SafePlate local app.")
    parser.add_argument("--host", default=os.environ.get("SAFEPLATE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_default_port())
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with deterministic demo fixtures instead of live providers.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = run_server(host=args.host, port=args.port, demo_mode=args.demo)
    url = f"http://{args.host}:{args.port}"
    print(f"SafePlate Local is running at {url}")
    if args.demo:
        print("Demo mode is on; live provider/API calls are disabled for app requests.")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SafePlate Local...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
