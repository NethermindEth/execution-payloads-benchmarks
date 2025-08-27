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

* `generate-payloads`: Generate execution payloads for a given...
* `execute-scenario`: Execute payloads for a given execution...
* `execute-scenarios`: Execute payloads for multiple execution...

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
