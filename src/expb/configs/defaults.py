from pathlib import Path

# Images
## Kute
KUTE_DEFAULT_IMAGE = "nethermindeth/kute:latest"

# Directories
## Payloads
PAYLOADS_DEFAULT_DIR = Path("payloads")
## Work
WORK_DEFAULT_DIR = Path("work")
## Logs
LOGS_DEFAULT_DIR = Path("logs")

# Resources
## Docker container
DOCKER_CONTAINER_DEFAULT_CPUS = 4.0
DOCKER_CONTAINER_DEFAULT_MEM_LIMIT = "32g"
DOCKER_CONTAINER_DEFAULT_DOWNLOAD_SPEED = "50mbit"
DOCKER_CONTAINER_DEFAULT_UPLOAD_SPEED = "15mbit"
