#!/usr/bin/env python3
"""
Simple HTTP Server for Day09 L3B Multi-Agent Presentation Dashboard.
Run:
    python dashboard/server.py
Then open http://localhost:8000 in your browser.
"""

import http.server
import socketserver
import os
import webbrowser
from pathlib import Path

PORT = 8000
DASHBOARD_DIR = Path(__file__).resolve().parent

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

def run():
    os.chdir(DASHBOARD_DIR)
    with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"=======================================================")
        print(f"  A2A Multi-Agent Presentation Dashboard Server")
        print(f"  Serving at: {url}")
        print(f"  Press Ctrl+C to stop.")
        print(f"=======================================================")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")

if __name__ == "__main__":
    run()
