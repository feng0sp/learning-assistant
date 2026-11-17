"""Local launcher. Run `python start.py --no-browser` for a console-only start."""
import argparse
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn


def wait_and_open(url):
    for _ in range(30):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                if json.load(response).get("name") == "知章":
                    webbrowser.open(url)
                    return
        except (OSError, ValueError):
            time.sleep(0.3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知章个人学习助手")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    url = f"http://127.0.0.1:{args.port}"
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", args.port))
    except OSError:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                existing = json.load(response)
            if existing.get("name") == "知章":
                print(f"Study Assistant is already running: {url}")
                if not args.no_browser:
                    webbrowser.open(url)
                raise SystemExit(0)
        except (OSError, ValueError):
            pass
        parser.exit(1, f"Port {args.port} is already in use. Run with --port followed by another port number.\n")
    print(f"\nZhiZhang Study Assistant: {url}\nPress Ctrl+C to stop.\n", flush=True)
    if not args.no_browser:
        threading.Thread(target=wait_and_open, args=(url,), daemon=True).start()
    uvicorn.run("backend.main:app", host="127.0.0.1", port=args.port, log_level="info", timeout_graceful_shutdown=5)
