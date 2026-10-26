import json
import re
import shutil
import subprocess

import pytest

from expb.clients import Client
from expb.payloads.executor.services.k6 import (
    build_k6_script_config,
    get_k6_script_content,
)


def test_k6_thresholds_fail_non_valid_engine_responses() -> None:
    config = build_k6_script_config(
        test_id="test",
        scenario_name="scenario",
        client=Client.GETH,
        iterations=1,
    )

    thresholds = config["options"]["thresholds"]
    assert thresholds["checks{kind:newPayload}"] == ["rate == 1"]
    assert thresholds["checks{kind:forkchoiceUpdated}"] == ["rate == 1"]


def test_k6_engine_response_validation_covers_engine_statuses() -> None:
    result = run_k6_driver("""
const { context, records } = createRuntime();
const cases = [
  ['newPayload', { status: 200, body: '{"result":{"status":"VALID"}}' }, true],
  ['forkchoiceUpdated', { status: 200, body: '{"result":{"payloadStatus":{"status":"VALID"}}}' }, true],
  ['newPayload', { status: 200, body: '{"result":{"status":"INVALID"}}' }, false],
  ['newPayload', { status: 200, body: '{"result":{"status":"SYNCING"}}' }, false],
  ['newPayload', { status: 200, body: '{"result":{"status":"ACCEPTED"}}' }, false],
  ['forkchoiceUpdated', { status: 200, body: '{"result":{"payloadStatus":{"status":"INVALID"}}}' }, false],
  ['forkchoiceUpdated', { status: 200, body: '{"result":{"payloadStatus":{"status":"SYNCING"}}}' }, false],
  ['forkchoiceUpdated', { status: 200, body: '{"result":{"payloadStatus":{"status":"ACCEPTED"}}}' }, false],
  ['newPayload', { status: 200, body: '{"error":{"code":-32000,"message":"failed"}}' }, false],
  ['forkchoiceUpdated', { status: 200, body: '{' }, false],
  ['newPayload', { status: 503, body: 'temporarily unavailable' }, false],
  ['forkchoiceUpdated', { status: 200, body: 'x'.repeat(2000) }, false],
];
for (const [kind, response, expected] of cases) {
  let aborted = false;
  try {
    context.checkEngineResponse(response, kind, 42, { kind });
  } catch (error) {
    if (!(error instanceof BenchmarkAbort)) throw error;
    aborted = true;
  }
  if (records.checks.at(-1).result.payload_status_valid !== expected || aborted === expected) {
    throw new Error(`unexpected result for ${kind}: ${JSON.stringify(response)}`);
  }
}
if (records.errors.length !== cases.length - 2) throw new Error('unexpected diagnostic count');
if (records.errors.some((message) => message.length > 700)) throw new Error('diagnostic was not bounded');
if (!records.errors.some((message) => message.includes('...'))) throw new Error('long diagnostic was not truncated');
if (records.aborts.some((message) => !message.includes('idx=42'))) throw new Error('abort lost the payload index');
process.stdout.write(JSON.stringify(records));
""")

    assert len(result["checks"]) == 12
    assert len(result["aborts"]) == 10


def test_k6_invalid_engine_response_stops_warmup_and_measurement_delivery() -> None:
    result = run_k6_driver("""
const phases = ['warmup', 'measurement'];
const failures = ['SYNCING', 'ACCEPTED', 'INVALID', 'http', 'rpc', 'malformed'];
function runPhase(context, phase) {
  if (phase === 'warmup') context.setup();
  else for (let i = 0; i < 3; i++) context.defaultFunction();
}
let failedCases = 0;
for (const phase of phases) {
  for (const kind of ['newPayload', 'forkchoiceUpdated']) {
    for (const failure of failures) {
      const response = failure === 'http' ? { status: 503, body: 'unavailable' }
        : failure === 'rpc' ? { status: 200, body: '{"error":{"code":-32000}}' }
        : failure === 'malformed' ? { status: 200, body: '{' }
        : engineResponse(kind, failure);
      const { context, records } = createRuntime(kind, response);
      let aborted = false;
      try {
        runPhase(context, phase);
      } catch (error) {
        if (!(error instanceof BenchmarkAbort)) throw error;
        aborted = true;
      }
      if (!aborted || records.aborts.length !== 1) throw new Error('invalid sample did not abort');
      if (records.fetches !== 1) throw new Error('more payloads were fetched after failure');
      const expectedPosts = kind === 'newPayload' ? 1 : 2;
      if (records.posts.length !== expectedPosts) throw new Error('engine delivery continued after failure');
      if (records.checks.at(-1).result.payload_status_valid) throw new Error('failed check was lost');
      if (records.errors.length !== 1) throw new Error('failure diagnostic was lost');
      if (records.sleeps.length !== 0) throw new Error('failed sample waited for its inter-payload delay');
      const validNewPayloadRows = phase === 'measurement' && kind === 'forkchoiceUpdated' ? 1 : 0;
      if (records.rows.length !== validNewPayloadRows) throw new Error('invalid newPayload produced a metric');
      failedCases++;
    }
  }
  const { context, records } = createRuntime();
  runPhase(context, phase);
  if (records.aborts.length || records.errors.length) throw new Error('valid sample aborted');
  if (records.fetches !== 3 || records.posts.length !== 6) throw new Error('valid payload delivery changed');
  if (records.sleeps.length !== 3) throw new Error('valid inter-payload delay changed');
  const expectedRows = phase === 'measurement' ? 3 : 0;
  if (records.rows.length !== expectedRows) throw new Error('valid metric count changed');
  if (records.rows.some((row) => !row.endsWith('processing_ms=2.00'))) throw new Error('request timing changed');
  if (records.gauges.length !== expectedRows * 2) throw new Error('valid gauge count changed');
  if (records.gauges.some(({ name, value }) => value !== (name === 'expb_slowest_payloads' ? 2 : 1))) {
    throw new Error('valid gauge timing changed');
  }
}
process.stdout.write(JSON.stringify({ failedCases, successfulCases: phases.length }));
""")

    assert result == {"failedCases": 24, "successfulCases": 2}


def run_k6_driver(test_body: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the K6 response validation")

    script = get_k6_script_content()
    script = re.sub(r"^import .*;\r?\n", "", script, flags=re.MULTILINE)
    script = script.replace("export const options", "const options")
    script = script.replace("export function setup", "function setup")
    script = script.replace("export default function ()", "function defaultFunction()")
    script += "\nglobalThis.checkEngineResponse = checkEngineResponse;"
    script += "\nglobalThis.setup = setup; globalThis.defaultFunction = defaultFunction;\n"

    driver = f"""
const vm = require('node:vm');
class BenchmarkAbort extends Error {{}}
function engineResponse(kind, status = 'VALID') {{
  const payloadStatus = {{ status }};
  const result = kind === 'newPayload' ? payloadStatus : {{ payloadStatus }};
  return {{ status: 200, body: JSON.stringify({{ result }}) }};
}}
function createRuntime(failureKind = '', failureResponse = null) {{
  const records = {{ checks: [], errors: [], aborts: [], rows: [], gauges: [], posts: [], sleeps: [], fetches: 0 }};
  const context = {{
    __ENV: {{
      EXPB_CONFIG_FILE_PATH: '/config.json', EXPB_JWTSECRET_FILE_PATH: '/jwt.hex',
      EXPB_PAYLOADS_WARMUP: '3', EXPB_ENABLE_LOGGING: '1', EXPB_PER_PAYLOAD_METRICS_LOGS: '1',
      EXPB_PER_PAYLOAD_METRICS: '1', EXPB_PAYLOADS_DELAY: '1', EXPB_PAYLOADS_WARMUP_DELAY: '1',
    }},
    open: (path) => path === '/config.json' ? '{{"options":{{}}}}' : '00'.repeat(32),
    Gauge: function Gauge(name) {{ this.add = (value) => records.gauges.push({{ name, value }}); }},
    encoding: {{ b64encode: () => '' }},
    crypto: {{ createHMAC: () => ({{ update: () => {{}}, digest: () => '' }}) }},
    exec: {{
      vu: {{ idInTest: 1 }}, scenario: {{ iterationInTest: 0 }},
      test: {{ abort: (message) => {{ records.aborts.push(message); throw new BenchmarkAbort(message); }} }},
    }},
    group: (_name, action) => action(),
    sleep: (duration) => records.sleeps.push(duration),
    http: {{
      get: () => {{
        const idx = records.fetches++;
        return {{ status: 200, body: JSON.stringify({{ idx, gas_used: 42 }}) + '\\t{{}}\\t{{}}' }};
      }},
      post: (_url, _payload, options) => {{
        const kind = options.tags.kind;
        records.posts.push(kind);
        const response = kind === failureKind ? failureResponse : engineResponse(kind);
        return {{ ...response, timings: {{ duration: 2, waiting: 1 }} }};
      }},
    }},
    check: (response, checkFunctions, tags) => {{
      const result = {{}};
      for (const [name, checkFunction] of Object.entries(checkFunctions)) result[name] = Boolean(checkFunction(response));
      records.checks.push({{ result, tags }});
      return Object.values(result).every(Boolean);
    }},
    console: {{ error: (message) => records.errors.push(message), log: (message) => records.rows.push(message) }},
  }};
  vm.runInNewContext({json.dumps(script)}, context);
  return {{ context, records }};
}}
""" + test_body
    result = subprocess.run(
        [node, "-e", driver],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
