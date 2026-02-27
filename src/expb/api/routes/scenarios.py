from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from expb.api.auth import verify_token
from expb.api.schemas.runs import ScenarioOverrides

router = APIRouter()

# Derived directly from the ScenarioOverrides model so it stays in sync automatically.
_OVERRIDABLE_PARAMS: list[str] = list(ScenarioOverrides.model_fields.keys())


class ScenarioInfo(BaseModel):
    name: str
    client: str
    network: str
    # Scenario defaults — lets API consumers know what they are overriding
    default_duration: str
    default_warmup_duration: str
    default_delay: float
    default_warmup: int | None
    default_amount: int
    # Informational: which fields can be overridden in POST /runs
    overridable_params: list[str]


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
            default_duration=sc.duration,
            default_warmup_duration=sc.warmup_duration,
            default_delay=sc.payloads_delay,
            default_warmup=sc.payloads_warmup,
            default_amount=sc.payloads_amount,
            overridable_params=_OVERRIDABLE_PARAMS,
        )
        for name, sc in scenarios.scenarios_configs.items()
    ]
    return ScenarioListResponse(scenarios=result)
