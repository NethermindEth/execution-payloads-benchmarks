import math
from pathlib import Path

from jinja2 import Template

from expb.clients import Client


def build_k6_script_config(
    test_id: str,
    scenario_name: str,
    client: Client,
    iterations: int,
    duration: str
    | None = "10m",  # Default duration is 10 minutes (the actual usage of this value depends on the executor type)
    rate: int | None = None,  # Force sequantional running
    pre_allocated_vus: int = 2,
    max_vus: int = 2,
    time_unit: str = "1s",
    setup_timeout: str | None = "10m",
):
    if rate and rate > 0:
        # If duration not provided, compute from iterations/rate
        if not duration:
            duration_seconds = max(1, math.ceil(iterations / rate))
            duration = f"{duration_seconds}s"

        scenario = {
            "executor": "constant-arrival-rate",
            "rate": rate,  # iterations per timeUnit
            "timeUnit": time_unit,
            "duration": duration,
            "preAllocatedVUs": pre_allocated_vus,
            "maxVUs": max_vus,
            # tells the JS to skip sleep(); k6 controls pacing now
            "env": {"EXPB_RATE_MODE": "1", "EXPB_ABORT_ON_EOF": "0"},
            "tags": {"client_type": f"{client.value.name}"},
        }
    else:
        scenario = {
            "executor": "shared-iterations",
            "vus": 1,
            "iterations": iterations,
            "maxDuration": duration,
            "env": {},
            "tags": {"client_type": f"{client.value.name}"},
        }

    return {
        "options": {
            "scenarios": {scenario_name: scenario},
            "thresholds": {
                "http_req_failed": ["rate < 0.01"],
            },
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
            "tags": {"testid": f"{test_id}"},
            "setupTimeout": setup_timeout,
        }
    }


def get_k6_script_content() -> str:
    # Get the directory containing this file
    current_dir = Path(__file__).parent
    templates_dir = current_dir / "templates"
    template_file = templates_dir / "k6-script.js.j2"

    # Set up Jinja2 environment
    template = Template(template_file.read_text())

    # Render the template (no variables needed for now)
    return template.render()
