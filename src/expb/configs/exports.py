from pydantic import BaseModel, Field


# BasicAuth class
class BasicAuth(BaseModel):
    username: str = Field(
        description="Username for the basic auth.",
        min_length=1,
    )
    password: str = Field(
        description="Password for the basic auth.",
        min_length=1,
    )


# Prometheus Remote Write
class PrometheusRW(BaseModel):
    endpoint: str = Field(
        description="Prometheus remote write endpoint.",
        min_length=1,
    )
    basic_auth: BasicAuth | None = Field(
        description="Basic auth for the prometheus remote write endpoint.",
        default=None,
    )
    tags: list[str] = Field(
        description="Tags to add to the prometheus remote write data.",
        default=[],
    )


# Pyroscope configuration
class Pyroscope(BaseModel):
    endpoint: str = Field(
        description="Pyroscope endpoint.",
        min_length=1,
    )
    basic_auth: BasicAuth | None = Field(
        description="Basic auth for the pyroscope endpoint.",
        default=None,
    )
    tags: list[str] = Field(
        description="Tags to add to the pyroscope profiling data.",
        default=[],
    )


# Exports represents a collection of configured export options for a expb scenario
class Exports(BaseModel):
    prometheus_rw: PrometheusRW | None = Field(
        description="Prometheus remote write configuration.",
        default=None,
    )
    pyroscope: Pyroscope | None = Field(
        description="Pyroscope configuration.",
        default=None,
    )
