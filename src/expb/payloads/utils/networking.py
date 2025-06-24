import subprocess

from docker.models.containers import Container
from docker.models.networks import Network


def get_veth_name(pid: int) -> str:
    # This command finds the veth interface on the host for the container's eth0
    cmd = f"sudo nsenter -t {pid} -n ip link show eth0 | grep -oP '(?<=eth0@if)\\d+'"
    veth_index = subprocess.check_output(cmd, shell=True, text=True).strip()
    # Now find the veth name on the host with that index
    cmd = f"ip link | grep '^{veth_index}:' | awk -F: '{{print $2}}' | tr -d ' '"
    veth_name = subprocess.check_output(cmd, shell=True, text=True).strip()
    return veth_name


def apply_tc_limits(
    veth_name: str,
    download_speed: str,
    upload_speed: str,
):
    # Remove any existing qdisc
    subprocess.run(
        f"sudo tc qdisc del dev {veth_name} root || true",
        shell=True,
        check=True,
    )
    # Add root qdisc
    subprocess.run(
        f"sudo tc qdisc add dev {veth_name} root handle 1: htb default 30",
        shell=True,
        check=True,
    )
    # Add download (ingress) class
    subprocess.run(
        f"sudo tc class add dev {veth_name} parent 1: classid 1:1 htb rate {download_speed}",
        shell=True,
        check=True,
    )
    # Add upload (egress) class
    subprocess.run(
        f"sudo tc class add dev {veth_name} parent 1: classid 1:2 htb rate {upload_speed}",
        shell=True,
        check=True,
    )


def limit_container_bandwidth(
    container: Container,
    download_speed: str,
    upload_speed: str,
) -> None:
    container.reload()
    container_pid = container.attrs["State"]["Pid"]
    veth_name = get_veth_name(container_pid)
    apply_tc_limits(veth_name, download_speed, upload_speed)
