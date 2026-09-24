import json
from unittest.mock import MagicMock, Mock, patch

from expb.payloads.executor.services.payload_server import get_payload_server_script


def _load_payload_server(monkeypatch, gc_drain: str | None):
    monkeypatch.setenv("EXPB_PAYLOADS_FILE", "/payloads.jsonl")
    monkeypatch.setenv("EXPB_FCUS_FILE", "/fcus.jsonl")
    monkeypatch.setenv("EXPB_TOTAL", "10")
    monkeypatch.setenv("EXPB_EL_RPC_URL", "http://client:8545")
    monkeypatch.setenv("EXPB_SIMULATE_FILE", "")
    monkeypatch.setenv("EXPB_DROP_CACHES", "")
    monkeypatch.setenv("EXPB_CLIENT_SSE_URL", "")
    if gc_drain is None:
        monkeypatch.delenv("EXPB_GC_DRAIN", raising=False)
    else:
        monkeypatch.setenv("EXPB_GC_DRAIN", gc_drain)

    namespace = {"__name__": "payload_server_test"}
    exec(get_payload_server_script(), namespace)
    return namespace


def test_gc_drain_remains_enabled_by_default(monkeypatch):
    payload_server = _load_payload_server(monkeypatch, gc_drain=None)
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b"{}"

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        success, _, error = payload_server["drain_gc"](5)

    assert success is True
    assert error is None
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://client:8545"
    assert json.loads(request.data)["method"] == "eth_blockNumber"


def test_gc_drain_opt_out_skips_only_drain_and_logs_disabled(
    monkeypatch, capsys
):
    payload_server = _load_payload_server(monkeypatch, gc_drain="0")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b"{}"

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        assert payload_server["drain_gc"](5) == (None, 0.0, None)
        urlopen.assert_not_called()

        simulate_json = '{"jsonrpc":"2.0","method":"eth_simulateV1"}'
        success, _, error = payload_server["warmup_block"](6, simulate_json)

    assert success is True
    assert error is None
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://client:8545"
    assert request.data == simulate_json.encode("utf-8")

    payload_server["PairReader"] = Mock()
    payload_server["ThreadingHTTPServer"] = Mock(
        return_value=Mock(serve_forever=Mock())
    )
    payload_server["main"]()
    assert "[payload-server] GC drain: disabled" in capsys.readouterr().out
