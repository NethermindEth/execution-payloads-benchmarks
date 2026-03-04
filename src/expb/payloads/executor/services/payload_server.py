PAYLOAD_SERVER_PORT = 8080


def get_payload_server_script() -> str:
    return r'''#!/usr/bin/env python3
"""EXPB Payload Server — serves pre-processed NP+FCU pairs sequentially.

Reads from a merged file where each line is:
    {metadata_json}\t{raw_NP}\t{raw_FCU}\t{simulate_json}

Before returning each payload to K6, fires an eth_simulateV1 request
to the execution client to warm EVM caches (contract code, state trie,
DB pages) for that specific block.  The simulate call does not persist
state, so the subsequent real newPayload execution hits only warm caches.
"""

import os
import threading
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# --- Configuration from environment ---
MERGED_FILE = os.environ["EXPB_MERGED_FILE"]
PORT = int(os.environ.get("EXPB_SERVER_PORT", "8080"))
EL_RPC_URL = os.environ.get("EXPB_EL_RPC_URL", "")

# Global state
reader = None


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


def warmup_block(simulate_json):
    """Fire eth_simulateV1 to warm EVM caches for the next block."""
    if not simulate_json or not EL_RPC_URL:
        return
    try:
        req = urllib.request.Request(
            EL_RPC_URL,
            data=simulate_json.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
    except Exception:
        # Non-fatal — warmup failure should not block the benchmark
        pass


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

        # Line format: {metadata}\t{NP}\t{FCU}\t{simulate_json}
        # Split off the simulate payload (4th field)
        parts = line.split("\t", 3)
        if len(parts) == 4:
            simulate_json = parts[3]
            # Warm EVM caches before returning the payload to K6
            warmup_block(simulate_json)
            # Return only the first 3 fields to K6
            response_line = "\t".join(parts[:3])
        else:
            response_line = line

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(response_line.encode("utf-8"))


def main():
    global reader

    print(f"[payload-server] Starting on port {PORT}", flush=True)
    print(f"[payload-server] Merged file: {MERGED_FILE}", flush=True)
    print(f"[payload-server] EL RPC URL: {EL_RPC_URL or '(disabled)'}", flush=True)

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
