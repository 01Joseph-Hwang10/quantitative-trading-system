"""Monitor entrypoint: `python -m system.apps.monitor` (wraps `streamlit run`)."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    from streamlit.web import cli as streamlit_cli

    app_path = Path(__file__).with_name("app.py").resolve()
    sys.argv = ["streamlit", "run", str(app_path), *sys.argv[1:]]
    sys.exit(streamlit_cli.main())


if __name__ == "__main__":
    main()
