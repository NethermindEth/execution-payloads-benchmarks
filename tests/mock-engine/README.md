# Mock Engine Test

Tests expb tool variance by replacing the execution client with a mock
that returns fixed responses after a fixed delay. If results vary across
runs, the variance comes from the tool/environment, not the client.

## Setup

```bash
# 1. Build the mock engine image
docker build -t expb-mock-engine tests/mock-engine/

# 2. Generate fake payloads (1000 blocks)
python3 tests/mock-engine/generate_payloads.py --count 1000 --output-dir ./test-payloads

# 3. Create a minimal snapshot directory (mock doesn't use it, but expb requires one)
mkdir -p ./mock-snapshot
touch ./mock-snapshot/.keep
```

## Config

Create `mock-expb.yaml`:

```yaml
pull_images: false

images:
  k6: grafana/k6:1.6.1
  alloy: grafana/alloy:v1.13.2
  payload_server: python:3.12-slim

paths:
  work: ./work
  outputs: ./outputs

scenarios:
  mock-10ms:
    client: nethermind
    # Override with mock engine image
    image: expb-mock-engine
    snapshot_source: ./mock-snapshot
    snapshot_backend: copy
    payloads: ./test-payloads/payloads.jsonl
    fcus: ./test-payloads/fcus.jsonl
    network: mainnet
    amount: 1000
    warmup: 0
    delay: 0
    startup_wait: 5
    duration: 30m
```

Note: The mock engine image handles `eth_blockNumber` for the readiness
check and responds to all `engine_*` methods with a fixed 10ms delay.

## Run

```bash
# Run the scenario multiple times and compare avg processing times
expb execute-scenario --scenario-name mock-10ms --config-file mock-expb.yaml --per-payload-metrics-logs --print-logs

# Expected: avg ~10ms with <0.5ms variance between runs
# If variance is higher, it's tool/environment noise
```

## Adjusting delay

The mock engine accepts `--delay-ms` as an argument. To test with a
different delay, rebuild with a custom entrypoint or pass extra flags
via the scenario config.
