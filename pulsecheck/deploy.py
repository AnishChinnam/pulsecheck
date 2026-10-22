"""Single-worker entry point for the restricted, shared public portfolio demo."""
import logging
import os

import uvicorn

from .api import create_app
from .monitor import Settings


def build_app():
    port = int(os.getenv("PORT", "10000"))
    if not 1024 <= port <= 65535:
        raise ValueError("PORT must be between 1024 and 65535")
    return create_app(
        db_path=os.getenv("PULSECHECK_DB", "/tmp/pulsecheck-demo.db"),
        demo=True,
        public_demo=True,
        base_url=f"http://127.0.0.1:{port}",
        settings=Settings(allowed_hosts=("127.0.0.1",), interval_seconds=30),
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.getenv("PORT", "10000")), workers=1)


if __name__ == "__main__":
    main()
