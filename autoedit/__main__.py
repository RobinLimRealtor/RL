"""Start the app:  python -m autoedit"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser


def _free_port(preferred: int) -> int:
    for port in (preferred, *range(preferred + 1, preferred + 20)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def main() -> None:
    from waitress import serve

    from . import config, jobs, library
    from .server import app

    config.ensure_dirs()
    library.ensure_builtin_audio()
    jobs.start_worker()
    port = _free_port(int(os.environ.get("AUTOEDIT_PORT", "8765")))
    url = f"http://127.0.0.1:{port}"
    print("\n  Auto Video Editor is running.")
    print(f"  Open {url} in your browser (it should open by itself).")
    print("  Keep this window open while you edit. Close it to stop the app.\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    serve(app, host="127.0.0.1", port=port, threads=8, channel_timeout=600,
          max_request_body_size=1 << 40, _quiet=True)


if __name__ == "__main__":
    main()
