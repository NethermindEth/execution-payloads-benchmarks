from pathlib import Path

# Images
## Grafana K6
K6_DEFAULT_IMAGE = "grafana/k6:1.4.2"
## Grafana Alloy
ALLOY_DEFAULT_IMAGE = "grafana/alloy:latest"

# Directories
## Payloads
PAYLOADS_DEFAULT_FILE = Path("payloads.jsonl")
## Forkchoice Updated
FCUS_DEFAULT_FILE = Path("fcus.jsonl")
## Work
WORK_DEFAULT_DIR = Path("work")
## Logs
OUTPUTS_DEFAULT_DIR = Path("outputs")

# Resources
## Docker container
DOCKER_CONTAINER_DEFAULT_CPUS = 4
DOCKER_CONTAINER_DEFAULT_MEM_LIMIT = "32g"
DOCKER_CONTAINER_DEFAULT_DOWNLOAD_SPEED = "50mbit"
DOCKER_CONTAINER_DEFAULT_UPLOAD_SPEED = "15mbit"
