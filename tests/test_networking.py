from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from pydantic import ValidationError

from expb.configs.scenarios import ScenariosResources
from expb.payloads.utils.networking import get_veth_name, limit_container_bandwidth

NETNS = ["sudo", "nsenter", "-t", "4242", "-n"]


def htb_commands(prefix: list[str], device: str, rate: str) -> list:
    return [
        call(
            [
                *prefix,
                "tc",
                "qdisc",
                "replace",
                "dev",
                device,
                "root",
                "handle",
                "1:",
                "htb",
                "default",
                "1",
            ],
            check=True,
        ),
        call(
            [
                *prefix,
                "tc",
                "class",
                "replace",
                "dev",
                device,
                "parent",
                "1:",
                "classid",
                "1:1",
                "htb",
                "rate",
                rate,
            ],
            check=True,
        ),
    ]


@patch("expb.payloads.utils.networking.get_veth_name", return_value="veth1a2b3c")
@patch("expb.payloads.utils.networking.subprocess.run")
def test_shapes_download_on_host_veth_and_upload_in_container_netns(
    mock_run: MagicMock, mock_veth: MagicMock
) -> None:
    container = MagicMock(attrs={"State": {"Pid": 4242}})

    limit_container_bandwidth(container, "50mbit", "15mbit")

    mock_veth.assert_called_once_with(4242)
    assert mock_run.call_args_list == [
        *htb_commands(["sudo"], "veth1a2b3c", "50mbit"),
        *htb_commands(NETNS, "eth0", "15mbit"),
    ]
    assert all("shell" not in c.kwargs for c in mock_run.call_args_list)


def test_missing_container_attrs_raises() -> None:
    container = MagicMock(attrs=None)
    with pytest.raises(ValueError):
        limit_container_bandwidth(container, "50mbit", "15mbit")


@patch("expb.payloads.utils.networking.subprocess.check_output", return_value="17\n")
def test_get_veth_name_matches_peer_ifindex(
    mock_check_output: MagicMock, tmp_path: Path
) -> None:
    for name, index in [("lo", "1"), ("docker0", "3"), ("veth9f8e7d", "17")]:
        (tmp_path / name).mkdir()
        (tmp_path / name / "ifindex").write_text(f"{index}\n")

    assert get_veth_name(4242, sys_class_net=tmp_path) == "veth9f8e7d"
    mock_check_output.assert_called_once_with(
        [*NETNS, "cat", "/sys/class/net/eth0/iflink"], text=True
    )


@patch("expb.payloads.utils.networking.subprocess.check_output", return_value="99\n")
def test_get_veth_name_without_match_raises(_: MagicMock, tmp_path: Path) -> None:
    (tmp_path / "lo").mkdir()
    (tmp_path / "lo" / "ifindex").write_text("1\n")
    with pytest.raises(RuntimeError):
        get_veth_name(4242, sys_class_net=tmp_path)


def test_bandwidth_limiting_is_opt_in() -> None:
    assert ScenariosResources().limit_bandwidth is False


@pytest.mark.parametrize("rate", ["50mbit", "1gbit", "1.5Mbit", "500kbps", "800bit"])
def test_accepts_tc_rates(rate: str) -> None:
    resources = ScenariosResources(download_speed=rate, upload_speed=rate)
    assert resources.download_speed == rate


@pytest.mark.parametrize("rate", ["fast", "50", "50mbit; rm -rf /", "50 mbit", ""])
def test_rejects_invalid_rates(rate: str) -> None:
    with pytest.raises(ValidationError):
        ScenariosResources(upload_speed=rate)
