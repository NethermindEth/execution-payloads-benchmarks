import typer

from expb.generate_payloads import app as generate_payloads_app
from expb.execute_scenario import app as execute_scenario_app
from expb.execute_scenarios import app as execute_scenarios_app

app = typer.Typer()

typer_apps = [
    generate_payloads_app,
    execute_scenario_app,
    execute_scenarios_app,
]


for typer_app in typer_apps:
    app.add_typer(typer_app)
