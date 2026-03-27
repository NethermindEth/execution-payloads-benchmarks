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
- Client metrics via SSE (EXPB_CLIENT_SSE_URL): connects to the client's
  Server-Sent Events data feed (e.g. Nethermind /data/events) to receive
  real-time per-block processing times, immune to Prometheus snapshot staleness.
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
DROP_CACHES_SYNC = os.environ.get("EXPB_DROP_CACHES_SYNC", "1") == "1"
DROP_CACHES_SKIP = int(os.environ.get("EXPB_DROP_CACHES_SKIP", "0"))
GC_DRAIN_SKIP = int(os.environ.get("EXPB_GC_DRAIN_SKIP", "0"))
CLIENT_SSE_URL = os.environ.get("EXPB_CLIENT_SSE_URL", "")
CLIENT_SSE_SKIP = int(os.environ.get("EXPB_CLIENT_SSE_SKIP", "0"))

_ETH_BLOCK_NUMBER_BODY = json.dumps(
    {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
).encode("utf-8")

# Regex for lightweight metadata extraction (avoid full JSON parse)
_METHOD_RE = re.compile(r'"method"\s*:\s*"([^"]+)"')
_ID_RE = re.compile(r'"id"\s*:\s*(\d+)')
_GAS_USED_RE = re.compile(r'"gasUsed"\s*:\s*"([^"]+)"')
_BLOCK_NUMBER_RE = re.compile(r'"blockNumber"\s*:\s*"(0x[0-9a-fA-F]+)"')

# Global state
reader = None
sse_client = None


class SSEClient:
    """Background SSE client that receives real-time block processing events.

    Connects to the client's /data/events SSE endpoint and stores
    processingMs keyed by block number for lookup by the request handler.
    Must connect before the first block is processed (Nethermind's
    HaveSubscribers check silently drops events with no listeners).
    """

    def __init__(self, url):
        self.url = url
        self.lock = threading.Lock()
        self._data = {}  # block_number -> processingMs
        self._connected = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def wait_connected(self, timeout=30):
        return self._connected.wait(timeout)

    def get_processing_ms(self, block_number, timeout=2.0, poll_interval=0.005):
        """Look up and consume processingMs for a block number.

        Polls with a short interval to handle buffered SSE delivery.
        Returns the value or None if not available within timeout.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self.lock:
                val = self._data.pop(block_number, None)
            if val is not None:
                return val
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_interval)

    def _run(self):
        """Connect to SSE stream and parse events in a loop with reconnect."""
        while True:
            try:
                self._connect_and_read()
            except Exception as e:
                print(
                    f"[payload-server] SSE connection error: {e}, "
                    f"reconnecting in 1s",
                    flush=True,
                )
                time.sleep(1)

    def _connect_and_read(self):
        req = urllib.request.Request(
            self.url,
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            self._connected.set()
            print(
                f"[payload-server] SSE connected to {self.url}",
                flush=True,
            )
            event_type = ""
            data_buf = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_buf = line[5:].strip()
                elif line == "":
                    # Empty line = end of event
                    if event_type == "processed" and data_buf:
                        self._handle_processed(data_buf)
                    elif event_type and event_type != "processed":
                        print(
                            f"[payload-server] SSE ignored event_type={event_type} "
                            f"data={data_buf[:200]}",
                            flush=True,
                        )
                    event_type = ""
                    data_buf = ""

    def _handle_processed(self, data_str):
        try:
            evt = json.loads(data_str)
            print(
                f"[payload-server] SSE event: {data_str[:200]}",
                flush=True,
            )
            block_to = evt.get("blockTo")
            processing_ms = evt.get("processingMs")
            if block_to is not None and processing_ms is not None:
                with self.lock:
                    self._data[int(block_to)] = float(processing_ms)
        except Exception as e:
            print(
                f"[payload-server] SSE parse error: {e}",
                flush=True,
            )


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
    m = _BLOCK_NUMBER_RE.search(payload_head)
    if m:
        meta["block_number"] = int(m.group(1), 16)
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
        if DROP_CACHES_SYNC:
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


# Track the previous block's index and number so we can look up its SSE
# processing time when the next /next request arrives.
_prev_block_number = None
_prev_idx = None


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
        global reader, sse_client, _prev_block_number, _prev_idx
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

        # Look up SSE processing time for the previous block.
        # The SSE "processed" event fires synchronously after Nethermind
        # finishes processing, so by the time K6 calls /next again the
        # event should have arrived.
        # Skip warmup blocks (prev_idx < CLIENT_SSE_SKIP).
        if (sse_client and _prev_block_number is not None
                and _prev_idx is not None and _prev_idx >= CLIENT_SSE_SKIP):
            processing_ms = sse_client.get_processing_ms(_prev_block_number)
            if processing_ms is not None:
                meta["prev_client_processing_ms"] = processing_ms
                print(
                    f"[payload-server] client_metric block_number={_prev_block_number} "
                    f"processing_ms={processing_ms:.1f}",
                    flush=True,
                )
            else:
                print(
                    f"[payload-server] client_metric block_number={_prev_block_number} "
                    f"not yet available",
                    flush=True,
                )

        # Remember current block number and index for next iteration
        _prev_block_number = meta.get("block_number")
        _prev_idx = idx

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
    global reader, sse_client

    print(f"[payload-server] Starting on port {PORT}", flush=True)
    print(f"[payload-server] Payloads file: {PAYLOADS_FILE}", flush=True)
    print(f"[payload-server] FCUs file: {FCUS_FILE}", flush=True)
    print(f"[payload-server] Skip: {SKIP}, Total: {TOTAL}", flush=True)
    print(f"[payload-server] EL RPC URL: {EL_RPC_URL or '(disabled)'}", flush=True)
    print(f"[payload-server] Simulate file: {SIMULATE_FILE or '(none)'}", flush=True)
    print(f"[payload-server] GC drain: {'enabled' if EL_RPC_URL else 'disabled'}"
          f"{f' (skip first {GC_DRAIN_SKIP} blocks)' if EL_RPC_URL and GC_DRAIN_SKIP else ''}", flush=True)
    print(f"[payload-server] Client metrics (SSE): {'enabled' if CLIENT_SSE_URL else 'disabled'}"
          f"{f' url={CLIENT_SSE_URL}' if CLIENT_SSE_URL else ''}"
          f"{f' (skip first {CLIENT_SSE_SKIP} blocks)' if CLIENT_SSE_URL and CLIENT_SSE_SKIP else ''}", flush=True)
    print(f"[payload-server] Drop caches: {'enabled' if DROP_CACHES else 'disabled'}"
          f"{f' sync={DROP_CACHES_SYNC}' if DROP_CACHES else ''}"
          f"{f' (skip first {DROP_CACHES_SKIP} blocks)' if DROP_CACHES and DROP_CACHES_SKIP else ''}", flush=True)

    reader = PairReader(PAYLOADS_FILE, FCUS_FILE, SIMULATE_FILE, SKIP, TOTAL)

    # Connect SSE client BEFORE serving — Nethermind drops events if no
    # subscribers are connected (HaveSubscribers check in DataFeed.cs).
    if CLIENT_SSE_URL:
        sse_client = SSEClient(CLIENT_SSE_URL)
        sse_client.start()
        if sse_client.wait_connected(timeout=30):
            print("[payload-server] SSE client connected", flush=True)
        else:
            print(
                "[payload-server] WARNING: SSE client not connected after 30s, "
                "continuing without client metrics",
                flush=True,
            )

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
