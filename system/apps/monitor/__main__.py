"""Monitor entrypoint: `python -m system.apps.monitor` (wraps `streamlit run`)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    from system.config.logging_setup import setup_logging
    from system.config.settings import Settings

    # Runs before Streamlit executes app.py in-process, so the root handlers
    # (stderr + Cloud Logging on the VM) cover monitor logs as well.
    setup_logging(Settings(), service="monitor")

    from streamlit.web import cli as streamlit_cli

    app_path = Path(__file__).with_name("app.py").resolve()
    # Inside a container the app must listen on all interfaces; the compose
    # port mapping publishes it on the host's loopback only. Overridable.
    server_address = os.environ.get("STREAMLIT_SERVER_ADDRESS", "0.0.0.0")
    headless = os.environ.get("STREAMLIT_SERVER_HEADLESS", "true")
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        server_address,
        "--server.headless",
        headless,
        *sys.argv[1:],
    ]
    sys.exit(streamlit_cli.main())


if __name__ == "__main__":
    main()
