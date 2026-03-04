PAYLOAD_SERVER_PORT = 8080


def get_payload_server_script() -> str:
    return r'''#!/usr/bin/env python3
"""EXPB Payload Server — serves pre-processed NP+FCU pairs sequentially.

Reads from a merged file where each line is:
    {metadata_json}\t{raw_NP}\t{raw_FCU}

All heavy processing (file indexing, metadata extraction, slicing) is done
before this server starts. This server just reads lines sequentially.
"""

import os
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# --- Configuration from environment ---
MERGED_FILE = os.environ["EXPB_MERGED_FILE"]
PORT = int(os.environ.get("EXPB_SERVER_PORT", "8080"))


class LineReader:
    """Thread-safe sequential line reader for a pre-processed merged file."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.lock = threading.Lock()
        self._file = open(filepath, "r")

    def next_line(self):
        """Returns the next line (stripped) or None if EOF."""
        with self.lock:
            line = self._file.readline()
            if not line:
                return None
            return line.rstrip("\r\n")


# Global state
reader = None


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for payload serving."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/ready":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        elif self.path == "/next":
            self._handle_next()
        else:
            self.send_error(404, "Not Found")

    def _handle_next(self):
        global reader
        line = reader.next_line()

        if line is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"exhausted")
            return

        # Line format: {metadata_json}\t{raw_NP}\t{raw_FCU}
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(line.encode("utf-8"))


def main():
    global reader

    print(f"[payload-server] Starting on port {PORT}", flush=True)
    print(f"[payload-server] Merged file: {MERGED_FILE}", flush=True)

    reader = LineReader(MERGED_FILE)

    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
    print(f"[payload-server] Ready, serving on port {PORT}", flush=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[payload-server] Shutting down", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
'''
