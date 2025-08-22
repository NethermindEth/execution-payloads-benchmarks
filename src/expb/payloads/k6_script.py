import math
from typing import Optional
from expb.configs.clients import Client

def build_k6_script_config(
    scenario_name: str,
    client: Client,
    iterations: int,
    rate: Optional[int] = 4,           # iterations per second (IPS)
    duration: Optional[str] = None, 
    pre_allocated_vus: int = 4,  
    max_vus: int = 4,
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
            "env": {"EXPB_RATE_MODE": "1", "EXPB_ABORT_ON_EOF": "0"},
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
            "thresholds": {
                "http_req_failed": ["rate < 0.01"],
                'checks{check:"result_status_VALID"}': ["rate==1.0"],
                'checks{check:"payloadStatus_VALID"}': ["rate==1.0"],
                'checks{check:"under_slot_np"}': ["rate==1.0"],
                'checks{check:"under_slot_fcu"}': ["rate==1.0"],
            },
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
import { group, check } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import encoding from 'k6/encoding';
import crypto from 'k6/crypto';
import { Counter } from 'k6/metrics';

// --- Env / config ---
const payloadsFilePath = __ENV.EXPB_PAYLOADS_FILE_PATH;
const fcusFilePath     = __ENV.EXPB_FCUS_FILE_PATH;
const startLine        = parseInt(__ENV.EXPB_PAYLOADS_START || '1', 10);
const RATE_MODE        = __ENV.EXPB_RATE_MODE === '1';                  // when using arrival-rate
const ABORT_ON_EOF     = (__ENV.EXPB_ABORT_ON_EOF || '0') === '1';      // default off for graceful exit
const ABORT_ON_PARSE   = (__ENV.EXPB_ABORT_ON_PARSE_FAIL || '1') === '1';
const LOG_NON_VALID    = (__ENV.EXPB_LOG_NON_VALID || '1') === '1';
const SKIP_FCU_ON_NON_VALID = (__ENV.EXPB_SKIP_FCU_ON_NON_VALID || '0') === '1'; // default 0 so FCU always sent
const ADD_CORRELATION_HEADER = (__ENV.EXPB_ADD_CID || '1') === '1';

const engineEndpoint   = __ENV.EXPB_ENGINE_ENDPOINT;

// Load k6 options JSON
const configFilePath = __ENV.EXPB_CONFIG_FILE_PATH;
const config = JSON.parse(open(configFilePath));
export const options = config["options"];

// --- simple slot budget: 1s / rate ---
const _scn = Object.values(options.scenarios || {})[0] || {};
const _rate = Number(_scn.rate || __ENV.EXPB_RATE || 0);
const SLOT_MS = Number(__ENV.EXPB_SLOT_MS || (_rate > 0 ? Math.ceil(1000 / _rate) : 0));

// --- Metrics to diagnose "lost" requests ---
const skipped_fcu = new Counter('expb_skipped_fcu');               // when FCU is skipped due to non-VALID newPayload
const nonvalid_newpayload = new Counter('expb_nonvalid_newpayload');
const nonvalid_fcu       = new Counter('expb_nonvalid_fcu');

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
  return v.replace(/^\\uFEFF/, '').trim(); // Trim BOM and whitespace
}

function parseJsonStrict(raw, label, idx) {
  try {
    return JSON.parse(raw);
  } catch (e) {
    console.error(`JSON parse failed for ${label} at idx=${idx}: ` + JSON.stringify({ snippet: String(raw).slice(0, 200) }));
    if (ABORT_ON_PARSE) exec.test.abort(`Parse error in ${label} at idx=${idx}`);
    return null;
  }
}

function cid() {
  return `${__ENV.testid || 'expb'}-vu${exec.vu.idInTest}-it${exec.scenario.iterationInTest}`;
}

export async function setup() {
  // nothing; data preloaded
}

export default async function () {
  // Global iteration index across all VUs/scenario runs
  const idx = startIdx0 + exec.scenario.iterationInTest;

  // Out of data? graceful (default) or abort if explicitly requested
  if (idx >= startIdx0 + totalPairs) {
    if (ABORT_ON_EOF) exec.test.abort('No more payloads or fcus found');
    return;
  }

  const payloadRaw = safeGetLine(payloadLines, idx);
  const fcuRaw     = safeGetLine(fcuLines, idx);

  if (!payloadRaw || !fcuRaw) {
    console.error('Empty/undefined line encountered: ' + JSON.stringify({
      idx, payloadOk: !!payloadRaw, fcuOk: !!fcuRaw
    }));
    if (ABORT_ON_EOF) exec.test.abort('Dataset contains empty lines or ended prematurely');
    return;
  }

  const payload = parseJsonStrict(payloadRaw, 'payload', idx);
  const fcu     = parseJsonStrict(fcuRaw, 'fcu', idx);
  if (!payload || !fcu) return; // already logged/aborted

  try {
    // --- engine_newPayload ---
    const tok1 = await getJwtToken();
    let npValid = false;

    group('engine_newPayload', function () {
      const tags = { jrpc_method: payload.method, kind: 'newPayload' };
      const headers = { Authorization: 'Bearer ' + tok1, 'Content-Type': 'application/json' };
      if (ADD_CORRELATION_HEADER) headers['X-Expb-Cid'] = cid();

      const r = http.post(engineEndpoint, payloadRaw, { headers, tags });
      const data = r.json();
      const st = data?.result?.status;
      npValid = (st === 'VALID');

      check(r, {
        'status_200': (x) => x.status === 200,
        'has_result': () => data && data.result !== undefined && data.error === undefined,
        'result_status_VALID': () => npValid,
        'under_slot_np': () => SLOT_MS ? r.timings.duration <= SLOT_MS : true,
      }, tags);

      if (!npValid) {
        nonvalid_newpayload.add(1, tags);
        if (LOG_NON_VALID) {
          console.error('newPayload not VALID: ' + JSON.stringify({
            idx,
            status: st,
            latestValidHash: data?.result?.latestValidHash,
            validationError: data?.result?.validationError,
          }));
        }
      }
    });

    // Optionally skip FCU if newPayload failed (default OFF here)
    if (!npValid && SKIP_FCU_ON_NON_VALID) {
      skipped_fcu.add(1);
      return;
    }

    // --- engine_forkchoiceUpdated ---
    const tok2 = await getJwtToken();
    group('engine_forkchoiceUpdated', function () {
      const tags = { jrpc_method: fcu.method, kind: 'forkchoiceUpdated' };
      const headers = { Authorization: 'Bearer ' + tok2, 'Content-Type': 'application/json' };
      if (ADD_CORRELATION_HEADER) headers['X-Expb-Cid'] = cid();

      const r = http.post(engineEndpoint, fcuRaw, { headers, tags });
      const data = r.json();
      const st = data?.result?.payloadStatus?.status;
      const fcuValid = (st === 'VALID');

      check(r, {
        'status_200': (x) => x.status === 200,
        'has_result': () => data && data.result !== undefined && data.error === undefined,
        'payloadStatus_VALID': () => fcuValid,
        'under_slot_fcu': () => SLOT_MS ? r.timings.duration <= SLOT_MS : true,
      }, tags);

      if (!fcuValid) {
        nonvalid_fcu.add(1, tags);
        if (LOG_NON_VALID) {
          console.error('forkchoiceUpdated not VALID: ' + JSON.stringify({
            idx,
            status: st,
            latestValidHash: data?.result?.payloadStatus?.latestValidHash,
            validationError: data?.result?.payloadStatus?.validationError,
            payloadId: data?.result?.payloadId,
          }));
        }
      }
    });
  } catch (e) {
    console.error('Iteration error at idx=' + idx + ': ' + String(e));
  }
}
"""
