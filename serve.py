#!/usr/bin/env python3
"""Serve www/ for the TV browser and note when the page is on screen.

build.py skips its API calls while nobody watches; every request for morning.json
touches state/seen, which is how it knows the page is up.

Usage: serve.py [--bind 0.0.0.0] [--port 8777]
"""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEN = HERE / "state" / "seen"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path.endswith("morning.json"):
            SEEN.parent.mkdir(exist_ok=True)
            SEEN.touch()
        elif path.endswith("alarm-report"):          # radio state beacons from alarm mode; just log them
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8777)
    a = ap.parse_args()
    handler = functools.partial(Handler, directory=str(HERE / "www"))
    print(f"serving {HERE / 'www'} on http://{a.bind}:{a.port}/morning.html")
    ThreadingHTTPServer((a.bind, a.port), handler).serve_forever()
