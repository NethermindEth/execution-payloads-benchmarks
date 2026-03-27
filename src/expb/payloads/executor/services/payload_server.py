PAYLOAD_SERVER_PORT = 8080


def get_payload_server_script() -> str:
    return r'''#!/usr/bin/env python3
"""EXPB Payload Server — serves NP+FCU pairs sequentially to K6.

Reads directly from raw payloads and FCUs files (one JSON-RPC request per line),
extracts lightweight metadata on the fly, and returns tab-separated lines:
    {metadata_json}\t{raw_NP}\t{raw_FCU}

Supports per-block modes controlled by environment variables:
- GC drain (EXPB_EL_RPC_URL): sends eth_blockNumber before each measured block
  to absorb any pending .NET GC from the previous block's processing, preventing
  GC pauses from inflating K6 TTFB measurements.
- Client metrics (EXPB_CLIENT_METRICS_URL + EXPB_CLIENT_PROCESSING_METRIC):
  scrapes the client's Prometheus endpoint after each block to capture the
  server-side processing time, immune to GC/deserialization jitter.
- EVM warmup (EXPB_EL_RPC_URL + EXPB_SIMULATE_FILE): fires eth_simulateV1
  before each block to warm contract code, state trie, and DB block cache.
- Drop caches (EXPB_DROP_CACHES): writes to /proc/sys/vm/drop_caches
  before each block to force cold OS page cache reads.
"""

import json
import os
import re
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# --- Configuration from environment ---
PAYLOADS_FILE = os.environ["EXPB_PAYLOADS_FILE"]
FCUS_FILE = os.environ["EXPB_FCUS_FILE"]
SKIP = int(os.environ.get("EXPB_SKIP", "0"))
TOTAL = int(os.environ["EXPB_TOTAL"])
PORT = int(os.environ.get("EXPB_SERVER_PORT", "8080"))
EL_RPC_URL = os.environ.get("EXPB_EL_RPC_URL", "")
SIMULATE_FILE = os.environ.get("EXPB_SIMULATE_FILE", "")
DROP_CACHES = os.environ.get("EXPB_DROP_CACHES", "") == "1"
DROP_CACHES_SKIP = int(os.environ.get("EXPB_DROP_CACHES_SKIP", "0"))
GC_DRAIN_SKIP = int(os.environ.get("EXPB_GC_DRAIN_SKIP", "0"))
CLIENT_METRICS_URL = os.environ.get("EXPB_CLIENT_METRICS_URL", "")
CLIENT_PROCESSING_METRIC = os.environ.get("EXPB_CLIENT_PROCESSING_METRIC", "")
CLIENT_METRICS_SKIP = int(os.environ.get("EXPB_CLIENT_METRICS_SKIP", "0"))

_ETH_BLOCK_NUMBER_BODY = json.dumps(
    {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
).encode("utf-8")

# Regex for lightweight metadata extraction (avoid full JSON parse)
_METHOD_RE = re.compile(r'"method"\s*:\s*"([^"]+)"')
_ID_RE = re.compile(r'"id"\s*:\s*(\d+)')
_GAS_USED_RE = re.compile(r'"gasUsed"\s*:\s*"([^"]+)"')

# Global state
reader = None


class PairReader:
    """Thread-safe sequential reader for payloads + FCUs + optional simulate files."""

    def __init__(self, payloads_path, fcus_path, simulate_path, skip, total):
        self.lock = threading.Lock()
        self._pf = open(payloads_path, "r")
        self._ff = open(fcus_path, "r")
        self._sf = open(simulate_path, "r") if simulate_path else None
        self._idx = 0
        self._skip = skip
        self._limit = skip + total
        self._skipped = False

    def _do_skip(self):
        """Skip initial lines (called once, under lock)."""
        if self._skipped:
            return
        for _ in range(self._skip):
            self._pf.readline()
            self._ff.readline()
            if self._sf:
                self._sf.readline()
            self._idx += 1
        self._skipped = True

    def next_pair(self):
        """Returns (idx, payload_line, fcu_line, simulate_line) or None if done."""
        with self.lock:
            self._do_skip()
            if self._idx >= self._limit:
                return None
            pl = self._pf.readline()
            fl = self._ff.readline()
            sl = self._sf.readline() if self._sf else ""
            if not pl or not fl:
                return None
            idx = self._idx
            self._idx += 1
            return (
                idx,
                pl.rstrip("\r\n"),
                fl.rstrip("\r\n"),
                sl.rstrip("\r\n") if sl else "",
            )


def extract_metadata(idx, payload_head, fcu_head):
    """Extract lightweight metadata from raw JSON-RPC lines."""
    meta = {"idx": idx}
    m = _METHOD_RE.search(payload_head)
    if m:
        meta["method"] = m.group(1)
    m = _ID_RE.search(payload_head)
    if m:
        meta["jrpc_id"] = int(m.group(1))
    m = _GAS_USED_RE.search(payload_head)
    if m:
        meta["gas_used"] = int(m.group(1), 16)
    m = _METHOD_RE.search(fcu_head)
    if m:
        meta["fcu_method"] = m.group(1)
    return meta


def drop_caches_block(idx):
    """Drop OS page cache before the next block to force cold storage reads.

    Skips the first DROP_CACHES_SKIP blocks (warmup payloads that just
    advance chain state and don't need cold cache treatment).

    Returns (success: bool, elapsed_ms: float, error: str|None).
    """
    if not DROP_CACHES:
        return None, 0.0, None
    if isinstance(idx, int) and idx < DROP_CACHES_SKIP:
        return None, 0.0, None
    t0 = time.monotonic()
    try:
        import subprocess
        subprocess.run("sync", shell=True, check=True)
        with open("/host_proc_sys_vm/drop_caches", "w") as f:
            f.write("3")
        elapsed_ms = (time.monotonic() - t0) * 1000
        return True, elapsed_ms, None
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return False, elapsed_ms, str(e)


def drain_gc(idx):
    """Send eth_blockNumber to absorb pending GC before measurement.

    After block processing, .NET schedules a GC that may fire during the
    next JSON-RPC deserialization (outside the noGC region).  A cheap
    eth_blockNumber call lets that GC complete on an unrelated RPC so it
    does not inflate the measured newPayload TTFB.

    Skips warmup blocks (same pattern as drop_caches).
    Returns (success: bool, elapsed_ms: float, error: str|None).
    """
    if not EL_RPC_URL:
        return None, 0.0, None
    if isinstance(idx, int) and idx < GC_DRAIN_SKIP:
        return None, 0.0, None
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            EL_RPC_URL,
            data=_ETH_BLOCK_NUMBER_BODY,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        elapsed_ms = (time.monotonic() - t0) * 1000
        return True, elapsed_ms, None
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return False, elapsed_ms, str(e)


def scrape_client_metric(prev_idx):
    """Scrape the client's Prometheus endpoint to get server-side processing time.

    The metric value reflects the PREVIOUS block (the one K6 just finished
    processing).  Called after gc_drain so any pending GC has been absorbed
    and the metrics endpoint responds cleanly.

    Skips warmup blocks (prev_idx < CLIENT_METRICS_SKIP).
    Returns (value_ms: float|None, elapsed_ms: float, error: str|None).
    """
    if not CLIENT_METRICS_URL or not CLIENT_PROCESSING_METRIC:
        return None, 0.0, None
    if isinstance(prev_idx, int) and prev_idx < CLIENT_METRICS_SKIP:
        return None, 0.0, None
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(CLIENT_METRICS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.monotonic() - t0) * 1000
        prefix = CLIENT_PROCESSING_METRIC
        prefix_len = len(prefix)
        for line in body.splitlines():
            if not line.startswith(prefix):
                continue
            # Match "metric_name value" or "metric_name{labels} value"
            ch = line[prefix_len] if len(line) > prefix_len else ""
            if ch == " ":
                value_str = line[prefix_len + 1:].strip()
                return float(value_str), elapsed_ms, None
            elif ch == "{":
                # Skip labels: find closing brace then parse value
                brace_end = line.index("}", prefix_len)
                value_str = line[brace_end + 1:].strip()
                return float(value_str), elapsed_ms, None
        return None, elapsed_ms, f"metric {CLIENT_PROCESSING_METRIC} not found"
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return None, elapsed_ms, str(e)


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
        result = reader.next_pair()

        if result is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"exhausted")
            return

        idx, payload_line, fcu_line, simulate_json = result

        # Extract metadata from first ~2048 chars (avoid full JSON parse)
        meta = extract_metadata(idx, payload_line[:2048], fcu_line[:256])

        # Drain pending GC from previous block before measurement
        gc_ok, gc_ms, gc_err = drain_gc(idx)
        if gc_ok is not None:
            if gc_ok:
                print(
                    f"[payload-server] gc_drain block={idx} "
                    f"ok elapsed={gc_ms:.1f}ms",
                    flush=True,
                )
            else:
                print(
                    f"[payload-server] gc_drain block={idx} "
                    f"FAILED elapsed={gc_ms:.1f}ms error={gc_err}",
                    flush=True,
                )

        # Scrape client-side processing time for the previous block
        # (the one K6 just finished, before we return the next payload)
        prev_idx = idx - 1
        cm_val, cm_ms, cm_err = scrape_client_metric(prev_idx)
        if cm_val is not None:
            print(
                f"[payload-server] client_metric block={prev_idx} "
                f"processing_ms={cm_val:.1f} scrape_elapsed={cm_ms:.1f}ms",
                flush=True,
            )
        elif cm_err is not None and CLIENT_METRICS_URL and prev_idx >= CLIENT_METRICS_SKIP:
            print(
                f"[payload-server] client_metric block={prev_idx} "
                f"FAILED scrape_elapsed={cm_ms:.1f}ms error={cm_err}",
                flush=True,
            )

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

        # Return: {metadata}\t{NP}\t{FCU}
        response_line = f"{json.dumps(meta)}\t{payload_line}\t{fcu_line}"

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(response_line.encode("utf-8"))


def main():
    global reader

    print(f"[payload-server] Starting on port {PORT}", flush=True)
    print(f"[payload-server] Payloads file: {PAYLOADS_FILE}", flush=True)
    print(f"[payload-server] FCUs file: {FCUS_FILE}", flush=True)
    print(f"[payload-server] Skip: {SKIP}, Total: {TOTAL}", flush=True)
    print(f"[payload-server] EL RPC URL: {EL_RPC_URL or '(disabled)'}", flush=True)
    print(f"[payload-server] Simulate file: {SIMULATE_FILE or '(none)'}", flush=True)
    print(f"[payload-server] GC drain: {'enabled' if EL_RPC_URL else 'disabled'}"
          f"{f' (skip first {GC_DRAIN_SKIP} blocks)' if EL_RPC_URL and GC_DRAIN_SKIP else ''}", flush=True)
    print(f"[payload-server] Client metrics: {'enabled' if CLIENT_METRICS_URL else 'disabled'}"
          f"{f' metric={CLIENT_PROCESSING_METRIC}' if CLIENT_METRICS_URL else ''}"
          f"{f' (skip first {CLIENT_METRICS_SKIP} blocks)' if CLIENT_METRICS_URL and CLIENT_METRICS_SKIP else ''}", flush=True)
    print(f"[payload-server] Drop caches: {'enabled' if DROP_CACHES else 'disabled'}"
          f"{f' (skip first {DROP_CACHES_SKIP} blocks)' if DROP_CACHES and DROP_CACHES_SKIP else ''}", flush=True)

    reader = PairReader(PAYLOADS_FILE, FCUS_FILE, SIMULATE_FILE, SKIP, TOTAL)

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
