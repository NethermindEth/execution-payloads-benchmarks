import typer

from expb.cli.api import app as api_app
from expb.cli.compress_payloads import app as compress_payloads_app
from expb.cli.execute_scenario import app as execute_scenario_app
from expb.cli.execute_scenarios import app as execute_scenarios_app
from expb.cli.generate_payloads import app as generate_payloads_app
from expb.cli.send_payloads import app as send_payloads_app
from expb.cli.version import app as version_app

app = typer.Typer()

for _sub in [
    generate_payloads_app,
    execute_scenario_app,
    execute_scenarios_app,
    compress_payloads_app,
    send_payloads_app,
    api_app,
    version_app,
]:
    app.add_typer(_sub)
