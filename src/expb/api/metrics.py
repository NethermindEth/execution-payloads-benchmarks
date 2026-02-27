import json
from pathlib import Path


def parse_k6_summary(summary_path: Path) -> dict | None:
    """
    Parse a k6-summary.json file (produced by K6's ``--summary-export`` flag)
    and extract per-group HTTP request duration statistics.

    Returns a dict of the form::

        {
            "engine_newPayload": {
                "avg": float, "min": float, "max": float,
                "med": float, "p90": float, "p95": float, "p99": float,
            },
            "engine_forkchoiceUpdated": { ... },
        }

    Keys whose values are missing from the summary file are set to ``None``.
    Returns ``None`` if the file cannot be read or parsed.
    """
    try:
        with summary_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    metrics = data.get("metrics", {})
    if not metrics:
        return None

    # K6 stores group-scoped metrics with keys like:
    #   "http_req_duration{group:::engine_newPayload}"
    #   "http_req_duration{group:::engine_forkchoiceUpdated}"
    group_keys = {
        "engine_newPayload": "http_req_duration{group:::engine_newPayload}",
        "engine_forkchoiceUpdated": "http_req_duration{group:::engine_forkchoiceUpdated}",
    }

    result: dict[str, dict] = {}

    for group_name, metric_key in group_keys.items():
        metric_data = metrics.get(metric_key)
        if metric_data is None:
            continue

        # K6 uses "p(90)" notation; normalise to "p90" for clean storage / API output.
        # Values are stored directly on the metric object (no "values" sub-key).
        result[group_name] = {
            "avg": metric_data.get("avg"),
            "min": metric_data.get("min"),
            "max": metric_data.get("max"),
            "med": metric_data.get("med"),
            "p90": metric_data.get("p(90)"),
            "p95": metric_data.get("p(95)"),
            "p99": metric_data.get("p(99)"),
        }

    return result if result else None
