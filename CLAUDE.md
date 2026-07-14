# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Execution Payloads Benchmarks (expb) is a Python CLI tool for benchmarking Ethereum execution clients using real-world historical payloads. It orchestrates Docker containers to run execution clients with different configurations and uses Grafana K6 for load testing, measuring performance metrics like payload processing time and gas throughput.

## Development Setup

### Environment Setup

```bash
# Install dependencies using uv
uv sync

# Activate virtual environment
source .venv/bin/activate

# Install the tool in development mode
uv pip install -e .
```

### Running Tests

```bash
# Run all unit tests (integration tests are deselected by default)
uv run pytest

# Run a specific test file
uv run pytest tests/payloads/test_execution_requests.py

# Run with verbose output
uv run pytest -v

# Run the integration tests that hit live EL/Beacon endpoints
# (override endpoints with EXPB_TEST_RPC_URL / EXPB_TEST_BEACON_URL)
uv run pytest -m integration
```

## Core Architecture

### CLI Structure

The application uses [Typer](https://typer.tiangolo.com/) for the CLI interface. The main entry point is in [src/expb/\_\_init\_\_.py](src/expb/__init__.py), which aggregates sub-commands from:

- `generate_payloads` - Reconstructs the exact `engine_newPayload` / `engine_forkchoiceUpdated` requests for a block range by sourcing them from a Consensus client's Beacon API (execution RPC is used only for block→slot mapping)
- `execute_scenario` - Runs a single benchmark scenario
- `execute_scenarios` - Runs multiple benchmark scenarios (optionally in a loop)
- `compress_payloads` - Compresses multiple smaller payloads into larger blocks
- `send_payloads` - Directly sends payloads to an execution engine endpoint

### Execution Client Support

The tool supports multiple Ethereum execution clients, each with their own configuration in [src/expb/clients/](src/expb/clients/):

- Nethermind
- Besu
- Geth
- Reth
- Erigon
- Ethrex
- NimbusEL

Each client implementation extends `ClientConfig` and defines:

- Default Docker image
- Client-specific command-line flags
- Network-specific configurations
- Prometheus metrics endpoint path

### Snapshot Backends

Three snapshot backends are available in [src/expb/payloads/executor/services/snapshots/](src/expb/payloads/executor/services/snapshots/):

- **overlay** - Uses Docker overlay filesystem (fastest, Linux-only, default)
- **zfs** - Uses ZFS snapshots (requires ZFS filesystem)
- **copy** - Simple directory copy (slowest, most compatible)

The snapshot system allows each benchmark run to start from a clean, consistent blockchain state.

### Executor Architecture

The executor ([src/expb/payloads/executor/executor.py](src/expb/payloads/executor/executor.py)) orchestrates the entire benchmark lifecycle:

1. **Setup Phase**: Creates snapshots, prepares JWT secrets, generates K6 scripts
2. **Execution Phase**: Starts execution client container, runs K6 load tests, collects metrics
3. **Cleanup Phase**: Stops containers, captures logs, processes per-payload metrics

The executor uses Jinja2 templates in [src/expb/payloads/executor/services/templates/](src/expb/payloads/executor/services/templates/) to generate:

- K6 JavaScript test scripts ([k6-script.js.j2](src/expb/payloads/executor/services/templates/k6-script.js.j2))
- Grafana Alloy configuration for metrics collection ([config.alloy.j2](src/expb/payloads/executor/services/templates/config.alloy.j2))

### Configuration System

Configuration is managed through Pydantic models in [src/expb/configs/](src/expb/configs/):

- [scenarios.py](src/expb/configs/scenarios.py) - Scenario definitions (client, payloads, resources, etc.)
- [exports.py](src/expb/configs/exports.py) - Metrics export configuration (Prometheus, Pyroscope)
- [networks.py](src/expb/configs/networks.py) - Network-specific settings (genesis hash, chain ID)
- [snapshots.py](src/expb/configs/snapshots.py) - Snapshot backend configuration

Configuration files are YAML-based. See [example-expb.yaml](example-expb.yaml) for a fully documented example.

### Docker Integration

The tool extensively uses the Docker Python SDK to:

- Manage execution client containers with resource limits (CPU, memory, bandwidth)
- Create isolated networks for each benchmark
- Mount volumes (data directories, JWT secrets, extra volumes)
- Execute additional commands inside running containers
- Stream and capture container logs

Network bandwidth limiting is implemented in [src/expb/payloads/utils/networking.py](src/expb/payloads/utils/networking.py) using Linux `tc` (traffic control).

### Execution Lock Mechanism

To prevent concurrent benchmark runs that would conflict over shared resources (Docker, snapshots, ports), expb implements a file-based locking mechanism ([src/expb/utils/lock.py](src/expb/utils/lock.py)):

- **Enabled by default** on `execute_scenario` and `execute_scenarios` commands
- Uses the `filelock` library for cross-platform file locking
- Default lock file location: `/tmp/expb.lock` (Unix) or `%LOCALAPPDATA%\Temp\expb.lock` (Windows)
- Lock acquisition fails immediately (timeout=0) if another instance is running
- Can be disabled with `--no-use-lock` flag
- Lock file path can be customized with `--lock-file` option

If a benchmark is killed abruptly, the lock file is automatically released. Manual cleanup is rarely needed.

### Metrics Collection

Two types of metrics are collected:

1. **K6 Load Test Metrics**: HTTP request durations, success rates, throughput
   - Exported to Prometheus Remote Write or JSON files
   - Configured via the `export.prometheus_remote_write` section

2. **Execution Client Metrics**: Block processing times, gas throughput, resource usage
   - Scraped via Grafana Alloy from client Prometheus endpoints
   - Sent to configured Prometheus Remote Write endpoint

Per-payload metrics can be enabled with `--per-payload-metrics` flag, generating individual metrics for each payload (warning: can be high cardinality).

## Common Commands

### Generate Payloads

Reconstruct the Engine API requests for a block range from a Consensus (Beacon) API and an
execution RPC. Both endpoints must serve the requested range (archive node for older ranges):

```bash
expb generate-payloads \
  --rpc-url http://localhost:8545 \
  --beacon-url http://localhost:5052 \
  --start-block 19000000 \
  --end-block 19001000 \
  --output-dir ./payloads \
  --threads 10
```

Payloads are sourced from the beacon block, so the generated `ExecutionPayload`,
`executionRequests` (EIP-7685), blob versioned hashes and parent beacon block root exactly match
what the consensus client sends the execution client, across all forks through Osaka. The
`forkchoiceUpdated` requests set `safeBlockHash` and `finalizedBlockHash` to the parent block hash.

### Run Single Scenario

Execute a benchmark scenario defined in a config file:

```bash
# Basic execution
expb execute-scenario --scenario-name example --config-file expb.yaml

# With console output and per-payload metrics
expb execute-scenario \
  --scenario-name example \
  --config-file expb.yaml \
  --print-logs \
  --per-payload-metrics \
  --per-payload-metrics-logs

# Disable execution lock (allow concurrent runs)
expb execute-scenario \
  --scenario-name example \
  --config-file expb.yaml \
  --no-use-lock

# Use custom lock file location
expb execute-scenario \
  --scenario-name example \
  --config-file expb.yaml \
  --lock-file /path/to/custom.lock
```

### Run Multiple Scenarios

Execute all scenarios in a config file:

```bash
# Run once
expb execute-scenarios --config-file expb.yaml

# Run in continuous loop
expb execute-scenarios --config-file expb.yaml --loop

# Filter scenarios by regex pattern
expb execute-scenarios --config-file expb.yaml --filter "^nethermind.*"
```

### Compress Payloads

Combine multiple small payloads into larger blocks for stress testing:

```bash
expb compress-payloads \
  --nethermind-snapshot-dir ./snapshots/nethermind \
  --nethermind-docker-image nethermindeth/nethermind:latest \
  --input-payloads-file ./payloads/payloads.jsonl \
  --output-payloads-dir ./compressed-payloads \
  --compression-factor 2 \
  --target-gas-limit 4000000000
```

### Send Payloads Directly

Send payloads directly to a running execution client (bypass benchmarking):

```bash
expb send-payloads \
  --engine-url http://localhost:8551 \
  --payloads-file ./payloads/payloads.jsonl \
  --fcus-file ./payloads/fcus.jsonl \
  --jwt-secret-file ./jwt.hex
```

## Output Structure

After running a scenario, outputs are stored in `<outputs-directory>/expb-executor-<scenario-name>-<timestamp>/`:

- `k6-script.js` - Generated K6 test script
- `k6-config.json` - K6 configuration
- `k6-summary.json` - Test results summary
- `k6.log` - K6 process logs
- `k6-results.jsonl` - Detailed metrics (if file export enabled)
- `config.alloy` - Grafana Alloy configuration
- `<client_type>.log` - Execution client logs
- `volumes/` - Additional Docker volumes
- `commands/` - Output from extra commands (cmd-0.log, cmd-1.log, etc.)

## Configuration File Structure

The YAML configuration file ([example-expb.yaml](example-expb.yaml)) defines:

- `pull_images` - Whether to pull Docker images before execution
- `images` - Docker images for K6 and Alloy
- `paths` - Working and output directories
- `export` - Prometheus Remote Write and Pyroscope configuration
- `resources` - Default CPU, memory, and bandwidth limits
- `scenarios` - Dictionary of named scenario configurations

### Scenario Configuration Keys

- `client` - Execution client (nethermind, besu, geth, reth, erigon, ethrex, nimbusel)
- `snapshot_source` - Path to snapshot directory or ZFS snapshot name
- `payloads` - Path to payloads JSONL file
- `fcus` - Path to forkchoice updates JSONL file
- `network` - Ethereum network (mainnet, sepolia, holesky)
- `snapshot_backend` - overlay (default), zfs, or copy
- `amount` - Number of payloads to execute
- `warmup` - Number of warmup payloads (no metrics collected)
- `delay` - Delay between payloads in seconds
- `duration` - Max scenario duration
- `warmup_duration` - Max warmup phase duration
- `startup_wait` - Wait time for client startup (seconds)
- `image` - Override default client Docker image
- `extra_flags` - Additional client command-line flags
- `extra_env` - Additional environment variables
- `extra_volumes` - Additional Docker volume mounts
- `extra_commands` - Commands to run inside the container during execution
- `repeat` - Number of times to repeat the scenario

## Logging

The tool uses [structlog](https://www.structlog.org/) for structured logging. Log level can be controlled via `--log-level` flag (DEBUG, INFO, WARNING, ERROR).

Key loggers are initialized in [src/expb/logging/\_\_init\_\_.py](src/expb/logging/__init__.py).

## Key Implementation Details

### Per-Payload Metrics

When `--per-payload-metrics-logs` is enabled, the executor:

1. Captures K6 console logs containing `EXPB_PER_PAYLOAD_METRIC` markers
2. Parses these logs using regex pattern `PER_PAYLOAD_METRIC_LOG_PATTERN`
3. Extracts payload index, gas used, and processing time
4. Renders a formatted table after execution completes

This feature is useful for identifying performance regressions on specific payloads.

### JWT Secret Handling

Each execution requires a JWT secret for Engine API authentication:

- Generated automatically as 32 random bytes (hex-encoded)
- Stored in `<work_dir>/jwt-secret/jwtsecret.hex`
- Mounted into both execution client and K6 containers
- Used for authenticating Engine API requests

### Docker Networking

Each scenario creates an isolated Docker bridge network:

- Execution client container joins the network
- K6 and Alloy containers join the same network
- Enables service discovery by container name
- Network is removed during cleanup

### Resource Limiting

Container resources are limited via Docker API:

- `cpu` - CPU count/shares
- `mem` - Memory limit (e.g., "32g")
- `download_speed` / `upload_speed` - Bandwidth limits using tc-netem

Bandwidth limiting requires host capabilities and may require privileged mode on some systems.
