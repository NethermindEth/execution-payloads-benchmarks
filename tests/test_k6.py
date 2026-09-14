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
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the K6 response validation")

    script = get_k6_script_content()
    script = re.sub(r"^import .*;\r?\n", "", script, flags=re.MULTILINE)
    script = script.replace("export const options", "const options")
    script = script.replace("export function setup", "function setup")
    script = script.replace("export default function ()", "function defaultFunction()")
    script += "\nglobalThis.checkEngineResponse = checkEngineResponse;\n"

    driver = f"""
const vm = require('node:vm');
const checks = [];
const errors = [];
const context = {{
  __ENV: {{
    EXPB_CONFIG_FILE_PATH: '/config.json',
    EXPB_JWTSECRET_FILE_PATH: '/jwt.hex',
  }},
  open: (path) => path === '/config.json' ? '{{"options":{{}}}}' : '00'.repeat(32),
  Gauge: function Gauge() {{ this.add = () => {{}}; }},
  encoding: {{ b64encode: () => '' }},
  check: (response, checkFunctions, tags) => {{
    const result = {{}};
    for (const [name, checkFunction] of Object.entries(checkFunctions)) {{
      result[name] = Boolean(checkFunction(response));
    }}
    checks.push({{ result, tags }});
    return Object.values(result).every(Boolean);
  }},
  console: {{ error: (message) => errors.push(message) }},
}};
vm.runInNewContext({json.dumps(script)}, context);
const cases = [
  ['newPayload', {{ status: 200, body: '{{"result":{{"status":"VALID"}}}}' }}, true],
  ['forkchoiceUpdated', {{ status: 200, body: '{{"result":{{"payloadStatus":{{"status":"VALID"}}}}}}' }}, true],
  ['newPayload', {{ status: 200, body: '{{"result":{{"status":"INVALID"}}}}' }}, false],
  ['newPayload', {{ status: 200, body: '{{"result":{{"status":"SYNCING"}}}}' }}, false],
  ['newPayload', {{ status: 200, body: '{{"result":{{"status":"ACCEPTED"}}}}' }}, false],
  ['forkchoiceUpdated', {{ status: 200, body: '{{"result":{{"payloadStatus":{{"status":"INVALID"}}}}}}' }}, false],
  ['forkchoiceUpdated', {{ status: 200, body: '{{"result":{{"payloadStatus":{{"status":"SYNCING"}}}}}}' }}, false],
  ['forkchoiceUpdated', {{ status: 200, body: '{{"result":{{"payloadStatus":{{"status":"ACCEPTED"}}}}}}' }}, false],
  ['newPayload', {{ status: 200, body: '{{"error":{{"code":-32000,"message":"failed"}}}}' }}, false],
  ['forkchoiceUpdated', {{ status: 200, body: '{{' }}, false],
  ['newPayload', {{ status: 503, body: 'temporarily unavailable' }}, false],
  ['forkchoiceUpdated', {{ status: 200, body: 'x'.repeat(2000) }}, false],
];
for (const [kind, response, expected] of cases) {{
  context.checkEngineResponse(response, kind, 42, {{ kind }});
  if (checks.at(-1).result.payload_status_valid !== expected) {{
    throw new Error(`unexpected result for ${{kind}}: ${{JSON.stringify(response)}}`);
  }}
}}
if (errors.length !== cases.length - 2) throw new Error(`unexpected diagnostic count: ${{errors.length}}`);
if (errors.some((message) => message.length > 700)) throw new Error('diagnostic was not bounded');
if (!errors.some((message) => message.includes('...'))) throw new Error('long diagnostic was not truncated');
process.stdout.write(JSON.stringify({{ checks, errors }}));
"""
    result = subprocess.run(
        [node, "-e", driver],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["checks"]) == 12
