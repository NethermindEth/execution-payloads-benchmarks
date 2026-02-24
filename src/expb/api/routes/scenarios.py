from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from expb.api.auth import verify_token

router = APIRouter()


class ScenarioInfo(BaseModel):
    name: str
    client: str
    network: str


class ScenarioListResponse(BaseModel):
    scenarios: list[ScenarioInfo]


@router.get("", response_model=ScenarioListResponse)
def list_scenarios(
    request: Request,
    _: None = Depends(verify_token),
) -> ScenarioListResponse:
    """List all scenarios available in the loaded config file."""
    scenarios = request.app.state.scenarios
    result = [
        ScenarioInfo(
            name=name,
            client=sc.client.value.name,
            network=sc.network.value.name,
        )
        for name, sc in scenarios.scenarios_configs.items()
    ]
    return ScenarioListResponse(scenarios=result)
