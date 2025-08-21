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

// Payloads and Fcus files
const payloadsFilePath = __ENV.EXPB_PAYLOADS_FILE_PATH;
const fcusFilePath = __ENV.EXPB_FCUS_FILE_PATH;
const payloadsFile = await fs.open(payloadsFilePath);
const fcusFile = await fs.open(fcusFilePath);
const startLine = parseInt(__ENV.EXPB_PAYLOADS_START);

const buffer = new Uint8Array(2 ** 20); // 1MB buffer
async function readFileLine(file) {
  let line = "";
  let done = false;
  while(true) {
    let bytesRead = await file.read(buffer);
    if (bytesRead === 0 || bytesRead === null) {
      break;
    }
    for (let i = 0; i < bytesRead; i++) {
      if (buffer[i] === 10) {
        file.seek( i - bytesRead + 1, SeekMode.Current);
        done = true;
        break;
      } if (buffer[i] === 13) {
        continue;
      } else {
        line += String.fromCharCode(buffer[i]);
      }
    }
    if (done) {
      break;
    }
  }
  return line;
}


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

export async function setup() {
  // Skip the first payloads and fcus lines
  for (let i = 0; i < startLine; i++) {
    await readFileLine(payloadsFile);
    await readFileLine(fcusFile);
  }
}

export default async function () {
  // Get the next payload
  const payloadRaw = await readFileLine(payloadsFile);
  const fcuRaw = await readFileLine(fcusFile);
  if (payloadRaw === "" || fcuRaw === "") {
    throw new Error("No more payloads or fcus found");
  }

  // Parse payload and fcu requests
  const payload = JSON.parse(payloadRaw);
  const fcu = JSON.parse(fcuRaw);
  try {
    // Send newPayload request
    const payloadToken = await getJwtToken();
    group("engine_newPayload", function() {
      const headers = {
          "Authorization": `Bearer ${payloadToken}`,
          "Content-Type": "application/json",
      };
      const tags = {
        "jrpc_method": payload.method,
      }
      const response = http.post(engineEndpoint, payloadRaw, {
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
      const headers = {
        "Authorization": `Bearer ${fcuToken}`,
        "Content-Type": "application/json",
      };
      const tags = {
        "jrpc_method": fcu.method,
      }
      const response = http.post(engineEndpoint, fcuRaw, {
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
