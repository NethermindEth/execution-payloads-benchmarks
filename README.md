# Execution Payloads Benchmarks

## Requirements

* [uv](https://docs.astral.sh/uv/getting-started/installation/)
* [Docker](https://docs.docker.com/engine/install/)

## Installation

From Github:

```bash
uv tool install --from git+https://github.com/NethermindEth/execution-payloads-benchmarks expb
```

## Usage

1. Create a copy of the [example configuration file](example-expb.yaml).
2. Edit the configuration file.
3. Execute one or multiple scenarios.

### Scenarios Execution

#### Single Scenario

```bash
expb execute-scenario --scenario-name example --config-file expb.yaml
```

#### Multiple Scenarios

```bash
expb execute-scenarios --config-file expb.yaml --loop
```

#### Single Scenario with duration metrics per payload

```bash
expb execute-scenario --scenario-name example --config-file expb.yaml --per-payload-metrics
```

For other use cases or more details see [CLI usage docs](docs/USAGE.md).
