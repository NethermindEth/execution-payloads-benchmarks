import subprocess
from pathlib import Path

from docker.models.containers import Container

SYS_CLASS_NET = Path("/sys/class/net")


def _netns_prefix(pid: int) -> list[str]:
    return ["sudo", "nsenter", "-t", str(pid), "-n"]


def get_veth_name(pid: int, sys_class_net: Path = SYS_CLASS_NET) -> str:
    # The container's eth0 iflink is the ifindex of its veth peer on the host.
    peer_index = subprocess.check_output(
        [*_netns_prefix(pid), "cat", "/sys/class/net/eth0/iflink"], text=True
    ).strip()
    for iface in sys_class_net.iterdir():
        ifindex_file = iface / "ifindex"
        if ifindex_file.is_file() and ifindex_file.read_text().strip() == peer_index:
            return iface.name
    raise RuntimeError(f"No host interface found with ifindex {peer_index}")


def _shape_egress(cmd_prefix: list[str], device: str, rate: str) -> None:
    subprocess.run(
        [
            *cmd_prefix,
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
    )
    subprocess.run(
        [
            *cmd_prefix,
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
    )


def limit_container_bandwidth(
    container: Container,
    download_speed: str,
    upload_speed: str,
) -> None:
    """Shape each direction as egress on its own end of the container's veth pair.

    The host-side veth's egress is the container's download and the container's
    eth0 egress is its upload, so no ingress/IFB shaping is needed. Both qdiscs go
    away with the veth pair when the container is removed.
    """
    container.reload()
    if container.attrs is None:
        raise ValueError("Container attributes are not available")
    pid = container.attrs["State"]["Pid"]
    _shape_egress(["sudo"], get_veth_name(pid), download_speed)
    _shape_egress(_netns_prefix(pid), "eth0", upload_speed)
