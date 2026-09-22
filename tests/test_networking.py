from unittest.mock import MagicMock, patch

from expb.payloads.utils.networking import apply_tc_limits


@patch("expb.payloads.utils.networking.subprocess.run")
def test_apply_tc_limits_warns_shaping_one_directional(mock_run: MagicMock) -> None:
    # only egress is shaped, not ingress (#15) - warn, don't silently mislead
    logger = MagicMock()
    apply_tc_limits("veth0@if4", "50mbit", "60mbit", logger=logger)

    logger.warning.assert_called_once()
    msg = logger.warning.call_args.args[0]
    assert "download_speed" in msg
    assert "not enforced" in msg
    assert mock_run.called  # egress qdisc/classes still applied


@patch("expb.payloads.utils.networking.subprocess.run")
def test_apply_tc_limits_without_logger_is_silent(mock_run: MagicMock) -> None:
    # no logger -> no warn, no crash
    apply_tc_limits("veth0@if4", "50mbit", "60mbit")
    assert mock_run.called
