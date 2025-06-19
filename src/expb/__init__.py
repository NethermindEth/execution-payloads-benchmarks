import typer

from expb.generate_payloads import app as generate_payloads_app

app = typer.Typer()

typer_apps = [
    generate_payloads_app,
]


for typer_app in typer_apps:
    app.add_typer(typer_app)
