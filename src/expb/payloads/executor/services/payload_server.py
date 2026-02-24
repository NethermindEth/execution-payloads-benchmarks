PAYLOAD_SERVER_PORT = 8080


def get_payload_server_script() -> str:
    return r'''#!/usr/bin/env python3
"""EXPB Payload Server — serves NP+FCU pairs on demand with sliding-window prefetch."""

import json
import os
import re
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# --- Configuration from environment ---
PAYLOADS_FILE = os.environ["EXPB_PAYLOADS_FILE"]
FCUS_FILE = os.environ["EXPB_FCUS_FILE"]
PORT = int(os.environ.get("EXPB_SERVER_PORT", "8080"))
CACHE_SIZE = int(os.environ.get("EXPB_CACHE_SIZE", "100"))
SKIP = int(os.environ.get("EXPB_SKIP", "0"))
PREFETCH_THRESHOLD = 0.8

# Regex for metadata extraction (same as k6 script used)
METHOD_RE = re.compile(r'"method"\s*:\s*"([^"]+)"')
ID_RE = re.compile(r'"id"\s*:\s*(\d+)')
GAS_USED_RE = re.compile(r'"gasUsed"\s*:\s*"([^"]+)"')


class FileIndex:
    """Builds byte-offset index and extracts metadata for each line in a JSONL file."""

    def __init__(self, filepath, extract_gas=False):
        self.filepath = filepath
        self.extract_gas = extract_gas
        self.offsets = []       # byte offset of each line start
        self.lengths = []       # byte length of each line (excluding newline)
        self.metadata = []      # per-line metadata dicts
        self.count = 0
        self._build_index()

    def _build_index(self):
        with open(self.filepath, "rb") as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                stripped = line.rstrip(b"\r\n")
                if not stripped:
                    continue
                self.offsets.append(offset)
                self.lengths.append(len(stripped))

                # Extract metadata from first 2048 bytes of line
                head = stripped[:2048].decode("utf-8", errors="replace")
                meta = {}
                m = METHOD_RE.search(head)
                if m:
                    meta["method"] = m.group(1)
                m = ID_RE.search(head)
                if m:
                    meta["jrpc_id"] = int(m.group(1))
                if self.extract_gas:
                    m = GAS_USED_RE.search(head)
                    if m:
                        meta["gas_used"] = int(m.group(1), 16)

                self.metadata.append(meta)

        self.count = len(self.offsets)

    def read_line(self, idx):
        """Read a single line by index using byte offset."""
        if idx < 0 or idx >= self.count:
            return None
        with open(self.filepath, "rb") as f:
            f.seek(self.offsets[idx])
            return f.read(self.lengths[idx]).decode("utf-8", errors="replace")

    def read_lines(self, start, count):
        """Read a range of lines efficiently with a single file open."""
        result = {}
        end = min(start + count, self.count)
        if start >= self.count or start < 0:
            return result
        with open(self.filepath, "rb") as f:
            for idx in range(start, end):
                f.seek(self.offsets[idx])
                result[idx] = f.read(self.lengths[idx]).decode("utf-8", errors="replace")
        return result


class SlidingCache:
    """Sliding window cache with async prefetch for sequential access."""

    def __init__(self, file_index, cache_size=100):
        self.index = file_index
        self.cache_size = cache_size
        self.cache = {}
        self.cache_start = 0
        self.cache_end = 0
        self._next_prefetch_at = 0
        self.lock = threading.Lock()
        self._prefetching = False
        self._prefetch_lock = threading.Lock()

    def ensure_loaded(self, start):
        """Load initial cache window starting from the given index."""
        lines = self.index.read_lines(start, self.cache_size)
        with self.lock:
            self.cache = lines
            self.cache_start = start
            self.cache_end = start + len(lines)
            self._next_prefetch_at = start + int(self.cache_size * PREFETCH_THRESHOLD)

    def _do_prefetch(self, start, count):
        """Prefetch a range of lines in a background thread."""
        lines = self.index.read_lines(start, count)
        with self.lock:
            self.cache.update(lines)
            self.cache_end = max(self.cache_end, start + len(lines))
            self._next_prefetch_at = start + int(self.cache_size * PREFETCH_THRESHOLD)
            # Evict old entries beyond cache_size behind prefetch start
            evict_before = start - self.cache_size
            if evict_before > self.cache_start:
                self.cache = {k: v for k, v in self.cache.items() if k >= evict_before}
                self.cache_start = evict_before
        with self._prefetch_lock:
            self._prefetching = False

    def _maybe_prefetch(self, idx):
        """Trigger async prefetch if approaching end of cached range."""
        with self._prefetch_lock:
            if self._prefetching:
                return
        with self.lock:
            next_at = self._next_prefetch_at
            cache_end = self.cache_end

        if idx >= next_at:
            with self._prefetch_lock:
                if self._prefetching:
                    return
                self._prefetching = True
            t = threading.Thread(
                target=self._do_prefetch,
                args=(cache_end, self.cache_size),
                daemon=True,
            )
            t.start()

    def get(self, idx):
        """Get a line by index. Returns the raw line string or None."""
        with self.lock:
            line = self.cache.get(idx)

        if line is not None:
            self._maybe_prefetch(idx)
            return line

        # Cache miss — load synchronously from this index
        self.ensure_loaded(idx)
        with self.lock:
            return self.cache.get(idx)


class PairServer:
    """Manages two file caches and a shared counter for sequential pair serving."""

    def __init__(self, payloads_index, fcus_index, skip=0, cache_size=100):
        self.payloads_index = payloads_index
        self.fcus_index = fcus_index
        self.payloads_cache = SlidingCache(payloads_index, cache_size)
        self.fcus_cache = SlidingCache(fcus_index, cache_size)
        self.counter = skip
        self.lock = threading.Lock()
        self.max_pairs = min(payloads_index.count, fcus_index.count)

        # Pre-load initial cache windows
        if skip < self.max_pairs:
            self.payloads_cache.ensure_loaded(skip)
            self.fcus_cache.ensure_loaded(skip)

    def get_next(self):
        """Returns (idx, payload_line, fcu_line, payload_meta, fcu_meta) or None if exhausted."""
        with self.lock:
            idx = self.counter
            if idx >= self.max_pairs:
                return None
            self.counter += 1

        payload_line = self.payloads_cache.get(idx)
        fcu_line = self.fcus_cache.get(idx)

        if payload_line is None or fcu_line is None:
            return None

        payload_meta = self.payloads_index.metadata[idx] if idx < self.payloads_index.count else {}
        fcu_meta = self.fcus_index.metadata[idx] if idx < self.fcus_index.count else {}

        return (idx, payload_line, fcu_line, payload_meta, fcu_meta)


# Global state
server_ready = False
pair_server = None


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for payload serving."""

    def log_message(self, format, *args):
        # Suppress default access logs
        pass

    def do_GET(self):
        if self.path == "/ready":
            self._handle_ready()
        elif self.path == "/next":
            self._handle_next()
        elif self.path == "/count":
            self._handle_count()
        else:
            self.send_error(404, "Not Found")

    def _handle_ready(self):
        if server_ready:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"indexing")

    def _handle_count(self):
        global pair_server
        if not server_ready or pair_server is None:
            self.send_error(503, "Not ready")
            return
        data = json.dumps({
            "payloads": pair_server.payloads_index.count,
            "fcus": pair_server.fcus_index.count,
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def _handle_next(self):
        global pair_server
        if not server_ready or pair_server is None:
            self.send_error(503, "Not ready")
            return

        result = pair_server.get_next()
        if result is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"exhausted")
            return

        idx, payload_line, fcu_line, payload_meta, fcu_meta = result

        # Body: NP line + newline + FCU line
        body = payload_line + "\n" + fcu_line

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("X-Expb-Idx", str(idx))

        # Payload metadata
        if "method" in payload_meta:
            self.send_header("X-Expb-Method", payload_meta["method"])
        if "jrpc_id" in payload_meta:
            self.send_header("X-Expb-Jrpc-Id", str(payload_meta["jrpc_id"]))
        if "gas_used" in payload_meta:
            self.send_header("X-Expb-Gas-Used", str(payload_meta["gas_used"]))

        # FCU metadata
        if "method" in fcu_meta:
            self.send_header("X-Expb-Fcu-Method", fcu_meta["method"])

        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


def main():
    global server_ready, pair_server

    print(f"[payload-server] Starting on port {PORT}", flush=True)
    print(f"[payload-server] Payloads file: {PAYLOADS_FILE}", flush=True)
    print(f"[payload-server] FCUs file: {FCUS_FILE}", flush=True)
    print(f"[payload-server] Skip: {SKIP}, Cache size: {CACHE_SIZE}", flush=True)

    # Start HTTP server in a thread so we can respond to /ready while indexing
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    print(f"[payload-server] HTTP server listening on port {PORT}", flush=True)

    # Build indices
    t0 = time.time()
    print("[payload-server] Indexing payloads file...", flush=True)
    payloads_index = FileIndex(PAYLOADS_FILE, extract_gas=True)
    t1 = time.time()
    print(f"[payload-server] Payloads indexed: {payloads_index.count} lines in {t1-t0:.2f}s", flush=True)

    print("[payload-server] Indexing FCUs file...", flush=True)
    fcus_index = FileIndex(FCUS_FILE, extract_gas=False)
    t2 = time.time()
    print(f"[payload-server] FCUs indexed: {fcus_index.count} lines in {t2-t1:.2f}s", flush=True)

    # Create pair server
    pair_server = PairServer(payloads_index, fcus_index, skip=SKIP, cache_size=CACHE_SIZE)
    server_ready = True

    total_time = time.time() - t0
    print(f"[payload-server] Ready! Total indexing time: {total_time:.2f}s", flush=True)
    print(f"[payload-server] Available pairs: {pair_server.max_pairs} (skip={SKIP})", flush=True)

    # Keep main thread alive
    try:
        server_thread.join()
    except KeyboardInterrupt:
        print("[payload-server] Shutting down", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
'''
