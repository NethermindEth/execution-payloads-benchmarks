# Execution Payloads Benchmark

**Usage**:

```console
expb [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `generate-payloads`: Generate execution payloads requests for a given block range.
* `execute-scenario`: Execute payloads for a given execution client using Grafana K6.
* `execute-scenarios`: Execute payloads for multiple execution clients using Grafana K6.
* `compress-payloads`: Compress execution payloads transactions for a given block range into bigger blocks.
* `send-payloads`: Send payloads to an Ethereum Execution Engine endpoint.

## `expb generate-payloads`

Generate execution payloads for a given block range.

**Usage**:

```console
expb generate-payloads [OPTIONS]
```

**Options**:

* `--rpc-url TEXT`: Ethereum RPC URL  [required]
* `--network [mainnet]`: Network  [default: mainnet]
* `--start-block INTEGER`: Start block  [default: 0]
* `--end-block INTEGER`: End block
* `--output-dir PATH`: Output directory  [default: payloads]
* `--join-payloads / --no-join-payloads`: Join payloads and FCUs into a single file (payloads.jsonl and fcus.jsonl)  [default: join-payloads]
* `--log-level TEXT`: Log level (e.g., DEBUG, INFO, WARNING)  [default: INFO]
* `--threads INTEGER`: Number of threads for parallel processing  [default: 10]
* `--workers INTEGER`: Number of workers per thread for parallel processing  [default: 30]
* `--help`: Show this message and exit.

## `expb execute-scenario`

Execute payloads for a given execution client using Grafana K6.

**Usage**:

```console
expb execute-scenario [OPTIONS]
```

**Options**:

* `--scenario-name TEXT`: Scenario name  [required]
* `--config-file PATH`: Config file  [default: expb.yaml]
* `--log-level TEXT`: Log level (e.g., DEBUG, INFO, WARNING)  [default: INFO]
* `--per-payload-metrics / --no-per-payload-metrics`: Collect per-payload metric. This generates a metric for each payload, which can overload the configured outputs.  [default: no-per-payload-metrics]
* `--help`: Show this message and exit.

## `expb execute-scenarios`

Execute payloads for multiple execution clients using Grafana K6.

**Usage**:

```console
expb execute-scenarios [OPTIONS]
```

**Options**:

* `--loop / --no-loop`: Run in infinite loop  [default: no-loop]
* `--config-file PATH`: Config file  [default: expb.yaml]
* `--log-level TEXT`: Log level (e.g., DEBUG, INFO, WARNING)  [default: INFO]
* `--per-payload-metrics / --no-per-payload-metrics`: Collect per-payload metric. This generates a metric for each payload, which can overload the configured outputs.  [default: no-per-payload-metrics]
* `--help`: Show this message and exit.

## `expb compress-payloads`

Compress execution payloads txs for a given block range into bigger blocks.

**Usage**:

```console
expb compress-payloads [OPTIONS]
```

**Options**:

* `--nethermind-snapshot-dir PATH`: Nethermind snapshot directory  [required]
* `--nethermind-docker-image TEXT`: Nethermind docker image  [required]
* `--input-payloads-file PATH`: Input payloads jsonl file  [required]
* `--output-payloads-dir PATH`: Output directory to use for compressed payloads and forkchoice messages  [required]
* `--network [mainnet]`: Network  [default: mainnet]
* `--compression-factor INTEGER`: Compress factor  [default: 2]
* `--target-gas-limit INTEGER`: Target Gas limit for compressed blocks  [default: 4000000000]
* `--cpu-count INTEGER`: CPU count for the Nethermind container  [default: 4]
* `--mem-limit TEXT`: Memory limit for the Nethermind container  [default: 32g]
* `--include-blobs / --no-include-blobs`: Include blobs in the compressed payloads transactions  [default: no-include-blobs]
* `--log-level TEXT`: Log level (e.g., DEBUG, INFO, WARNING)  [default: INFO]
* `--help`: Show this message and exit.

## `expb send-payloads`

Send payloads to an Ethereum Execution Engine endpoint.

**Usage**:

```console
expb send-payloads [OPTIONS]
```

**Options**:

* `--engine-url TEXT`: Ethereum Execution Engine URL  [required]
* `--payloads-file PATH`: Payloads file  [required]
* `--fcus-file PATH`: FCUs file  [required]
* `--jwt-secret-file PATH`: JWT secret file  [required]
* `--log-level TEXT`: Log level (e.g., DEBUG, INFO, WARNING)  [default: INFO]
* `--help`: Show this message and exit.
