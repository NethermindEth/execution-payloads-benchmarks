from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from expb.configs.clients import Client
from expb.configs.exports import Pyroscope, PrometheusRW

ALLOY_PYROSCOPE_PORT = 9999


def get_alloy_config(
    scenario_name: str,
    execution_client: Client,
    execution_client_address: str,
    execution_client_scrape_interval: str,
    prometheus_rw: PrometheusRW | None = None,
    pyroscope: Pyroscope | None = None,
) -> str:
    # Get the directory containing this file
    current_dir = Path(__file__).parent
    templates_dir = current_dir / "templates"

    # Set up Jinja2 environment
    env = Environment(loader=FileSystemLoader(templates_dir))
    template = env.get_template("config.alloy.j2")

    pyroscope_config = (
        {
            "endpoint": pyroscope.endpoint,
            "basic_auth": {
                "username": pyroscope.basic_auth.username,
                "password": pyroscope.basic_auth.password,
            }
            if pyroscope.basic_auth is not None
            else None,
            "port": ALLOY_PYROSCOPE_PORT,
        }
        if pyroscope is not None
        else None
    )
    execution_metrics_config = (
        {
            "address": execution_client_address,
            "metrics_path": execution_client.value.prometheus_metrics_path,
            "scrape_interval": execution_client_scrape_interval,
            "labels": {
                "testid": scenario_name,
                "client_type": execution_client.value.name,
            },
        }
        if prometheus_rw is not None
        else None
    )
    prometheus_rw_config = (
        {
            "endpoint": prometheus_rw.endpoint,
            "basic_auth": {
                "username": prometheus_rw.basic_auth.username,
                "password": prometheus_rw.basic_auth.password,
            }
            if prometheus_rw.basic_auth is not None
            else None,
        }
        if prometheus_rw is not None
        else None
    )

    # Prepare template variables
    template_vars = {
        "pyroscope": pyroscope_config,
        "execution_metrics": execution_metrics_config,
        "prometheus_rw": prometheus_rw_config,
    }

    # Render the template
    return template.render(**template_vars)
