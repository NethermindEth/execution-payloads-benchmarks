PAYLOAD_SERVER_PORT = 8080


def get_payload_server_script() -> str:
    return r'''#!/usr/bin/env python3
"""EXPB Payload Server — serves pre-processed NP+FCU pairs sequentially.

Reads from a merged file where each line is:
    {metadata_json}\t{raw_NP}\t{raw_FCU}\t{simulate_json}

Supports two per-block modes controlled by environment variables:
- EVM warmup (EXPB_EL_RPC_URL): fires eth_simulateV1 before each block
  to warm contract code, state trie, and DB block cache.
- Drop caches (EXPB_DROP_CACHES): writes to /proc/sys/vm/drop_caches
  before each block to force cold OS page cache reads.
"""

import json
import os
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# --- Configuration from environment ---
MERGED_FILE = os.environ["EXPB_MERGED_FILE"]
PORT = int(os.environ.get("EXPB_SERVER_PORT", "8080"))
EL_RPC_URL = os.environ.get("EXPB_EL_RPC_URL", "")
DROP_CACHES = os.environ.get("EXPB_DROP_CACHES", "") == "1"

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


def drop_caches_block(idx):
    """Drop OS page cache before the next block to force cold storage reads.

    Returns (success: bool, elapsed_ms: float, error: str|None).
    """
    if not DROP_CACHES:
        return None, 0.0, None
    t0 = time.monotonic()
    try:
        import subprocess
        subprocess.run("sync", shell=True, check=True)
        with open("/proc/sys/vm/drop_caches", "w") as f:
            f.write("3")
        elapsed_ms = (time.monotonic() - t0) * 1000
        return True, elapsed_ms, None
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return False, elapsed_ms, str(e)


def warmup_block(idx, simulate_json):
    """Fire eth_simulateV1 to warm EVM caches for the next block.

    Returns (success: bool, elapsed_ms: float, error: str|None).
    """
    if not simulate_json or not EL_RPC_URL:
        return None, 0.0, None
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            EL_RPC_URL,
            data=simulate_json.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
        elapsed_ms = (time.monotonic() - t0) * 1000
        result = json.loads(body)
        if "error" in result:
            err_msg = result["error"].get("message", str(result["error"]))
            return False, elapsed_ms, err_msg
        return True, elapsed_ms, None
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return False, elapsed_ms, str(e)


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
            # Parse idx from metadata for logging
            try:
                meta = json.loads(parts[0])
                idx = meta.get("idx", "?")
            except Exception:
                idx = "?"
            # Drop OS page cache before the block (cold storage mode)
            dc_ok, dc_ms, dc_err = drop_caches_block(idx)
            if dc_ok is not None:
                if dc_ok:
                    print(
                        f"[payload-server] drop_caches block={idx} "
                        f"ok elapsed={dc_ms:.1f}ms",
                        flush=True,
                    )
                else:
                    print(
                        f"[payload-server] drop_caches block={idx} "
                        f"FAILED elapsed={dc_ms:.1f}ms error={dc_err}",
                        flush=True,
                    )
            # Warm EVM caches before returning the payload to K6
            success, elapsed_ms, error = warmup_block(idx, simulate_json)
            if success is not None:
                if success:
                    print(
                        f"[payload-server] warmup block={idx} "
                        f"ok elapsed={elapsed_ms:.1f}ms",
                        flush=True,
                    )
                else:
                    print(
                        f"[payload-server] warmup block={idx} "
                        f"FAILED elapsed={elapsed_ms:.1f}ms error={error}",
                        flush=True,
                    )
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
    print(f"[payload-server] Drop caches: {'enabled' if DROP_CACHES else 'disabled'}", flush=True)

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
