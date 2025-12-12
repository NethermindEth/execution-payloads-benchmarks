# Exports represents a collection of configured export options for a expb scenario
class Exports:
    def __init__(
        self,
        exports: dict[str, dict[str, str | None]] = {},
    ) -> None:
        # Initialize available exports
        self.prometheus_remote_write = None
        self.pyroscope = None

        # Load prometheus remote write config
        prometheus_rw = exports.get("prometheus_remote_write", None)
        if prometheus_rw is not None and isinstance(prometheus_rw, dict):
            self.prometheus_remote_write = PrometheusRW(prometheus_rw)

        # Load pyroscope config
        pyroscope = exports.get("pyroscope", None)
        if pyroscope is not None and isinstance(pyroscope, dict):
            self.pyroscope = Pyroscope(pyroscope)


# Prometheus remote write (K6 exporter)
class PrometheusRW:
    def __init__(
        self,
        prometheus_remote_write: dict[str, str | None],
    ) -> None:
        # Parse prometheus remote write endpoint
        self.endpoint = prometheus_remote_write.get("endpoint", None)
        if self.endpoint is None:
            raise ValueError("Prometheus remote write endpoint is required")
        # Parse prometheus remote write basic auth
        self.basic_auth = None
        basic_auth = prometheus_remote_write.get("basic_auth", None)
        if basic_auth is not None and isinstance(basic_auth, dict):
            self.basic_auth = BasicAuth(basic_auth)
        # Parse prometheus remote write tags
        self.tags: list[str] = prometheus_remote_write.get("tags", [])


# Grafana Pyroscope (Profiling exporter)
class Pyroscope:
    def __init__(
        self,
        pyroscope: dict[str, str | None],
    ) -> None:
        # Parse pyroscope endpoint
        pyroscope_endpoint = pyroscope.get("endpoint", None)
        if pyroscope_endpoint is None:
            raise ValueError("Pyroscope endpoint is required")
        self.endpoint = pyroscope_endpoint
        # Parse pyroscope basic auth
        self.basic_auth = None
        basic_auth = pyroscope.get("basic_auth", None)
        if basic_auth is not None and isinstance(basic_auth, dict):
            self.basic_auth = BasicAuth(basic_auth)
        # Parse pyroscope tags
        self.tags: list[str] = pyroscope.get("tags", [])
        if not isinstance(self.tags, list):
            raise ValueError("Pyroscope tags must be a list")


# BasicAuth class
class BasicAuth:
    def __init__(
        self,
        basic_auth_config: dict[str, str | None],
    ) -> None:
        # Parse basic auth username
        username = basic_auth_config.get("username", None)
        if username is None:
            raise ValueError("Basic auth username is required")
        self.username = username
        # Parse basic auth password
        password = basic_auth_config.get("password", None)
        if password is None:
            raise ValueError("Basic auth password is required")
        self.password = password
