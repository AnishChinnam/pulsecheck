import argparse
import logging

import uvicorn

from .api import create_app


def main():
    parser = argparse.ArgumentParser(description="Run the PulseCheck local API monitor")
    parser.add_argument("--demo", action="store_true", help="Seed three local services and expose outage simulation controls")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--db", default="data/pulsecheck.db")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app(db_path=args.db, demo=args.demo, base_url=f"http://127.0.0.1:{args.port}")
    # One worker owns the scheduler. Keep this unauthenticated demo on loopback.
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
