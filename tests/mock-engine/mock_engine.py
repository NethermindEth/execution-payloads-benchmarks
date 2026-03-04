#!/usr/bin/env python3
"""Mock Engine API server for measuring expb tool variance.

Returns canned VALID responses for newPayload and forkchoiceUpdated.
Adds a configurable fixed delay to simulate processing time.

Usage:
    python3 mock_engine.py [--delay-ms 10] [--port 8551]

If measured results vary significantly across runs, the variance
comes from the tool/environment, not the execution client.
"""

import argparse
import json
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

DELAY_S = 0.01  # default 10ms
PORT = 8551


RESPONSES = {
    "engine_newPayloadV1": {"status": "VALID", "latestValidHash": "0x0000000000000000000000000000000000000000000000000000000000000000", "validationError": None},
    "engine_newPayloadV2": {"status": "VALID", "latestValidHash": "0x0000000000000000000000000000000000000000000000000000000000000000", "validationError": None},
    "engine_newPayloadV3": {"status": "VALID", "latestValidHash": "0x0000000000000000000000000000000000000000000000000000000000000000", "validationError": None},
    "engine_newPayloadV4": {"status": "VALID", "latestValidHash": "0x0000000000000000000000000000000000000000000000000000000000000000", "validationError": None},
    "engine_forkchoiceUpdatedV1": {"payloadStatus": {"status": "VALID", "latestValidHash": "0x0000000000000000000000000000000000000000000000000000000000000000", "validationError": None}, "payloadId": None},
    "engine_forkchoiceUpdatedV2": {"payloadStatus": {"status": "VALID", "latestValidHash": "0x0000000000000000000000000000000000000000000000000000000000000000", "validationError": None}, "payloadId": None},
    "engine_forkchoiceUpdatedV3": {"payloadStatus": {"status": "VALID", "latestValidHash": "0x0000000000000000000000000000000000000000000000000000000000000000", "validationError": None}, "payloadId": None},
}

# Fallback for any engine_ method
DEFAULT_RESPONSE = {"status": "VALID"}

# eth_blockNumber response for the JSON-RPC readiness check
BLOCK_NUMBER_RESPONSE = "0x1000000"


class MockEngineHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        method = req.get("method", "")
        req_id = req.get("id", 1)

        # eth_blockNumber — used by expb to check client readiness
        if method == "eth_blockNumber":
            result = BLOCK_NUMBER_RESPONSE
        else:
            # Engine API method — apply fixed delay
            time.sleep(DELAY_S)
            result = RESPONSES.get(method, DEFAULT_RESPONSE)

        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

        response_bytes = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)


def main():
    global DELAY_S, PORT

    parser = argparse.ArgumentParser(description="Mock Engine API server")
    parser.add_argument("--delay-ms", type=float, default=10.0,
                        help="Fixed delay per engine request in milliseconds (default: 10)")
    parser.add_argument("--port", type=int, default=8551,
                        help="Port to listen on (default: 8551)")
    args = parser.parse_args()

    DELAY_S = args.delay_ms / 1000.0
    PORT = args.port

    print(f"[mock-engine] Starting on port {PORT}", flush=True)
    print(f"[mock-engine] Fixed delay: {args.delay_ms}ms per engine request", flush=True)

    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), MockEngineHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[mock-engine] Shutting down", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
