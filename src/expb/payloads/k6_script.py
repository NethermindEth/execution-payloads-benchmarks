from expb.configs.clients import Client


def build_k6_script_config(
    scenario_name: str,
    client: Client,
    iterations: int,
):
    return {
        "options": {
            "scenarios": {
                scenario_name: {
                    "executor": "shared-iterations",
                    "vus": 1,
                    "iterations": iterations,
                    "env": {},
                    "tags": {"client_type": f"{client.value.name}"},
                }
            },
            "thresholds": {"http_req_failed": ["rate < 0.01"]},
            "systemTags": [
                "scenario",
                "status",
                "url",
                "group",
                "check",
                "error",
                "error_code",
            ],
            "summaryTrendStats": [
                "avg",
                "min",
                "med",
                "max",
                "p(90)",
                "p(95)",
                "p(99)",
            ],
            "tags": {"testid": f"{scenario_name}"},
        }
    }


def get_k6_script_content() -> str:
    return """
import http from 'k6/http';
import { group, check, sleep } from 'k6';
import fs from 'k6/experimental/fs';
import csv from 'k6/experimental/csv';
import encoding from 'k6/encoding';
import crypto from 'k6/crypto';

// Payloads file
const payloadsFilePath = __ENV.EXPB_PAYLOADS_FILE_PATH;
const payloadsFile = await fs.open(payloadsFilePath);
const payloadsStart = parseInt(__ENV.EXPB_PAYLOADS_START);
// Using csv parser which is currently the only K6 module that supports reading a file line by line
// Review https://grafana.com/docs/k6/latest/javascript-api/k6-experimental/ in the future for other options
const payloadsParser = new csv.Parser(payloadsFile, {
  skipFirstLine: false,
  fromLine: payloadsStart,
});

// JWT secret file
function hex2ArrayBuffer(hex) {
  const buf = new ArrayBuffer(hex.length / 2);
  const bufView = new Uint8Array(buf);
  for (let i = 0; i < hex.length; i += 2) {
      bufView[i / 2] = parseInt(hex.slice(i, i + 2), 16);
  }
  return buf;
}

const jwtsecretFilePath = __ENV.EXPB_JWTSECRET_FILE_PATH;
const jwtsecret = open(jwtsecretFilePath).trim();
const jwtsecretBytes = hex2ArrayBuffer(jwtsecret);

// Delay between payloads
const payloadsDelay = parseFloat(__ENV.EXPB_PAYLOADS_DELAY);

// Engine endpoint
const engineEndpoint = __ENV.EXPB_ENGINE_ENDPOINT;

// Test config file
const configFilePath = __ENV.EXPB_CONFIG_FILE_PATH;
const configFile = open(configFilePath);
const config = JSON.parse(configFile);

export const options = config["options"]

// Get JWT token
async function getJwtToken() {
  const jwtHeaderString = encoding.b64encode(JSON.stringify({
    "typ": "JWT",
    "alg": "HS256",
  }), "rawurl");
  const iat = Math.floor(Date.now() / 1000);
  const exp = iat + 60;
  const jwtPayloadString = encoding.b64encode(JSON.stringify({
    "iat": iat,
    "exp": exp,
  }), "rawurl");

  const jwtHasher = crypto.createHMAC("sha256", jwtsecretBytes);
  jwtHasher.update([jwtHeaderString, jwtPayloadString].join("."));
  const signature = jwtHasher.digest("base64rawurl");
  return [jwtHeaderString, jwtPayloadString, signature].join(".");
}

export default async function () {
// Get the next payload
const {done, value} = await payloadsParser.next();
// If no more payloads, throw an error to finish the test
if (done) {
    throw new Error("No more payloads found");
}

// Parse payload and fcu requests
const payload = JSON.parse(value[1]);
const fcu = JSON.parse(value[2]);
  try {
    // Send newPayload request
    const payloadToken = await getJwtToken();
    group("engine_newPayload", function() {
    const tags = {
        "jrpc_method": payload["method"],
    }
    const headers = {
        "Authorization": `Bearer ${payloadToken}`,
        "Content-Type": "application/json",
    };
    const response = http.post(engineEndpoint, JSON.stringify(payload), {
        headers: headers,
        tags: tags,
    });
    // Checks
    check(response, {
        'status_200': (r) => r.status === 200,
        'has_result': (r) => {
        const data = r.json();
        return data !== undefined && data.result !== undefined && data.error === undefined;
        },
    }, tags);
    });

    // Send forkchoiceUpdated request
    const fcuToken = await getJwtToken();
    group("engine_forkchoiceUpdated", function() {
    const tags = {
        "jrpc_method": fcu["method"],
    }
    const headers = {
        "Authorization": `Bearer ${fcuToken}`,
        "Content-Type": "application/json",
    };
    const response = http.post(engineEndpoint, JSON.stringify(fcu), {
        headers: headers,
        tags: tags,
    });
    // Checks
    check(response, {
        'status_200': (r) => r.status === 200,
        'has_result': (r) => {
        const data = r.json();
        return data !== undefined && data.result !== undefined && data.error === undefined;
        },
    }, tags);
    });
  } catch (e) {
    console.error(e);
  }
  sleep(payloadsDelay); // Wait for the next payload
}
"""
