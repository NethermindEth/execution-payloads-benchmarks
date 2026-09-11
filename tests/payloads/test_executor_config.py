#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Demerzel Solutions Limited
# SPDX-License-Identifier: LGPL-3.0-only

import os
from unittest.mock import Mock

import pytest

from expb.configs.scenarios import Scenario, ScenariosPaths
from expb.payloads.executor.executor_config import ExecutorConfig


def make_config(tmp_path, monkeypatch, *, skip=0, warmup=0, amount=5):
    payloads = tmp_path / "payloads.jsonl"
    fcus = tmp_path / "fcus.jsonl"
    payloads.write_text("{}\n")
    fcus.write_text("{}\n")
    scenario = Scenario(
        name="fixture",
        client="nethermind",
        payloads=payloads,
        fcus=fcus,
        snapshot_source="fixture",
        skip=skip,
        warmup=warmup,
        amount=amount,
    )
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 1000, raising=False)
    monkeypatch.setattr("docker.from_env", lambda: Mock())
    return ExecutorConfig(
        scenario=scenario,
        snapshot_service=Mock(),
        paths=ScenariosPaths(
            work=tmp_path / "work",
            outputs=tmp_path / "outputs",
        ),
    )


def test_skip_override_is_absent_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPB_SKIP_OVERRIDE", raising=False)

    config = make_config(tmp_path, monkeypatch, skip=3)

    assert config.k6_payloads_skip == 3


def test_skip_override_is_applied(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPB_SKIP_OVERRIDE", "10")

    config = make_config(tmp_path, monkeypatch, skip=0)

    assert config.k6_payloads_skip == 10


@pytest.mark.parametrize("value", ["", "-1", "+1", "1.5", "ten"])
def test_skip_override_rejects_malformed_values(tmp_path, monkeypatch, value):
    monkeypatch.setenv("EXPB_SKIP_OVERRIDE", value)

    with pytest.raises(ValueError, match="EXPB_SKIP_OVERRIDE"):
        make_config(tmp_path, monkeypatch)


def test_skip_and_warmup_overrides_forward_payload_server_boundaries(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EXPB_SKIP_OVERRIDE", "10")
    monkeypatch.setenv("EXPB_WARMUP_OVERRIDE", "1")

    config = make_config(tmp_path, monkeypatch, skip=0, warmup=11, amount=5)

    payload_server_env = config.get_payload_server_environment(
        el_rpc_url="http://execution-client:8551",
        drop_caches=True,
        client_sse_url="http://execution-client:8545/data/events",
    )
    k6_command = config.get_k6_command(
        execution_client_engine_url="http://execution-client:8551",
        payload_server_url="http://payload-server:8080",
        collect_per_payload_metrics=True,
        enable_logging=True,
        per_payload_metrics_logs=True,
    )

    assert config.k6_payloads_skip == 10
    assert config.k6_payloads_warmup == 1
    assert payload_server_env["EXPB_SKIP"] == "10"
    assert payload_server_env["EXPB_TOTAL"] == "6"
    assert payload_server_env["EXPB_GC_DRAIN_SKIP"] == "11"
    assert payload_server_env["EXPB_DROP_CACHES_SKIP"] == "11"
    assert payload_server_env["EXPB_CLIENT_SSE_SKIP"] == "11"
    assert "--env=EXPB_PAYLOADS_WARMUP=1" in k6_command
    assert config.k6_payloads_skip + config.k6_payloads_warmup == 11
