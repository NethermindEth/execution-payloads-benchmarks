import math
from typing import Optional
from expb.configs.clients import Client

def build_k6_script_config(
    scenario_name: str,
    client: Client,
    iterations: int,
    rate: Optional[int] = 2,           # iterations per second (IPS)
    duration: Optional[str] = None,       # e.g. "20m", "600s"; if None we'll compute
    pre_allocated_vus: int = 8,           # >1 enables overlap
    max_vus: int = 8,
    time_unit: str = "1s",
):
    if rate and rate > 0:
        # If duration not provided, compute from iterations/rate
        if not duration:
            duration_seconds = max(1, math.ceil(iterations / rate))
            duration = f"{duration_seconds}s"

        scenario = {
            "executor": "constant-arrival-rate",
            "rate": rate,                       # iterations per timeUnit
            "timeUnit": time_unit,
            "duration": duration,
            "preAllocatedVUs": pre_allocated_vus,
            "maxVUs": max_vus,
            # tells the JS to skip sleep(); k6 controls pacing now
            "env": {"EXPB_RATE_MODE": "1"},
            "tags": {"client_type": f"{client.value.name}"},
        }
    else:
        # legacy single-stream behavior
        scenario = {
            "executor": "shared-iterations",
            "vus": 1,
            "iterations": iterations,
            "env": {},
            "tags": {"client_type": f"{client.value.name}"},
        }

    return {
        "options": {
            "scenarios": {scenario_name: scenario},
            "thresholds": {"http_req_failed": ["rate < 0.01"]},
            "systemTags": [
                "scenario", "status", "url", "group", "check",
                "error", "error_code",
            ],
            "summaryTrendStats": ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
            "tags": {"testid": f"{scenario_name}"},
        }
    }

def get_k6_script_content() -> str:
    return """
import http from 'k6/http';
import { group, check, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import encoding from 'k6/encoding';
import crypto from 'k6/crypto';

// --- Env / config ---
const payloadsFilePath = __ENV.EXPB_PAYLOADS_FILE_PATH;
const fcusFilePath     = __ENV.EXPB_FCUS_FILE_PATH;
const startLine        = parseInt(__ENV.EXPB_PAYLOADS_START || '1', 10);
const payloadsDelay    = parseFloat(__ENV.EXPB_PAYLOADS_DELAY || '0');
const RATE_MODE        = __ENV.EXPB_RATE_MODE === '1';             // set when using arrival-rate
const ABORT_ON_EOF     = (__ENV.EXPB_ABORT_ON_EOF || '1') === '1';
const ABORT_ON_PARSE   = (__ENV.EXPB_ABORT_ON_PARSE_FAIL || '1') === '1';
const engineEndpoint   = __ENV.EXPB_ENGINE_ENDPOINT;

// Load k6 options JSON
const configFilePath = __ENV.EXPB_CONFIG_FILE_PATH;
const config = JSON.parse(open(configFilePath));
export const options = config["options"];

// --- Multi-VU safe data: shared, preloaded lines ---
const payloadLines = new SharedArray('expb_payload_lines', () =>
  open(payloadsFilePath).trim().split(/\\r?\\n/)
);
const fcuLines = new SharedArray('expb_fcu_lines', () =>
  open(fcusFilePath).trim().split(/\\r?\\n/)
);

const startIdx0  = Math.max(0, startLine - 1);
const totalPairs = Math.max(0, Math.min(payloadLines.length, fcuLines.length) - startIdx0);

// --- Helpers ---
function hex2ArrayBuffer(hex) {
  const buf = new ArrayBuffer(hex.length / 2);
  const view = new Uint8Array(buf);
  for (let i = 0; i < hex.length; i += 2) view[i / 2] = parseInt(hex.slice(i, i + 2), 16);
  return buf;
}
const jwtsecretBytes = hex2ArrayBuffer(open(__ENV.EXPB_JWTSECRET_FILE_PATH).trim());

async function getJwtToken() {
  const header = encoding.b64encode(JSON.stringify({ typ: 'JWT', alg: 'HS256' }), 'rawurl');
  const iat = Math.floor(Date.now() / 1000), exp = iat + 60;
  const payload = encoding.b64encode(JSON.stringify({ iat, exp }), 'rawurl');
  const h = crypto.createHMAC('sha256', jwtsecretBytes);
  h.update(header + '.' + payload);
  return header + '.' + payload + '.' + h.digest('base64rawurl');
}

function safeGetLine(arr, idx) {
  const v = (idx >= 0 && idx < arr.length) ? arr[idx] : undefined;
  if (typeof v !== 'string') return '';
  // Trim BOM and whitespace
  return v.replace(/^\\uFEFF/, '').trim();
}

function parseJsonStrict(raw, label, idx) {
  try {
    return JSON.parse(raw);
  } catch (e) {
    console.error(`JSON parse failed for ${label} at idx=${idx}`, { snippet: String(raw).slice(0, 160) });
    if (ABORT_ON_PARSE) {
      exec.test.abort(`Parse error in ${label} at idx=${idx}`);
    }
    return null;
  }
}

export async function setup() {
  // nothing; data preloaded
}

export default async function () {
  // Global iteration index across all VUs/scenario runs
  const idx = startIdx0 + exec.scenario.iterationInTest;

  // Out of data? abort or noop
  if (idx >= startIdx0 + totalPairs) {
    if (ABORT_ON_EOF) exec.test.abort('No more payloads or fcus found');
    return;
  }

  const payloadRaw = safeGetLine(payloadLines, idx);
  const fcuRaw     = safeGetLine(fcuLines, idx);

  if (!payloadRaw || !fcuRaw) {
    console.error('Empty/undefined line encountered', { idx, payloadOk: !!payloadRaw, fcuOk: !!fcuRaw });
    if (ABORT_ON_EOF) exec.test.abort('Dataset contains empty lines or ended prematurely');
    return;
  }

  const payload = parseJsonStrict(payloadRaw, 'payload', idx);
  const fcu     = parseJsonStrict(fcuRaw, 'fcu', idx);
  if (!payload || !fcu) return; // already logged/aborted

  try {
    // --- engine_newPayload ---
    const tok1 = await getJwtToken();
    group('engine_newPayload', function () {
      const tags = { jrpc_method: payload.method, kind: 'newPayload' };
      const r = http.post(engineEndpoint, payloadRaw, {
        headers: { Authorization: 'Bearer ' + tok1, 'Content-Type': 'application/json' },
        tags,
      });
      const data = r.json();
      check(r, {
        'status_200': (x) => x.status === 200,
        'has_result': () => data && data.result !== undefined && data.error === undefined,
        'result_status_VALID': () => {
          const st = data?.result?.status;
          const ok = st === 'VALID';
          if (!ok) {
            console.error('newPayload not VALID:', {
              status: st,
              latestValidHash: data?.result?.latestValidHash,
              validationError: data?.result?.validationError,
            });
          }
          return ok;
        },
      }, tags);
    });

    // --- engine_forkchoiceUpdated ---
    const tok2 = await getJwtToken();
    group('engine_forkchoiceUpdated', function () {
      const tags = { jrpc_method: fcu.method, kind: 'forkchoiceUpdated' };
      const r = http.post(engineEndpoint, fcuRaw, {
        headers: { Authorization: 'Bearer ' + tok2, 'Content-Type': 'application/json' },
        tags,
      });
      const data = r.json();
      check(r, {
        'status_200': (x) => x.status === 200,
        'has_result': () => data && data.result !== undefined && data.error === undefined,
        'payloadStatus_VALID': () => {
          const st = data?.result?.payloadStatus?.status;
          const ok = st === 'VALID';
          if (!ok) {
            console.error('forkchoiceUpdated not VALID:', {
              status: st,
              latestValidHash: data?.result?.payloadStatus?.latestValidHash,
              validationError: data?.result?.payloadStatus?.validationError,
              payloadId: data?.result?.payloadId,
            });
          }
          return ok;
        },
      }, tags);
    });
  } catch (e) {
    console.error(e);
  }
}
"""

