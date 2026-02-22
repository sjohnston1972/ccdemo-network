"""
recursive_discovery.py
----------------------
Recursive Layer 2 / Layer 3 topology discovery using CDP.

Starting from seed device(s) in .env, the script:
  1. Connects via SSH and runs CDP neighbor detail
  2. Parses all neighbor management IPs from the output
  3. Queues any newly discovered IPs not yet visited
  4. Repeats until the full reachable topology is mapped
  5. Outputs a topology summary and saves results to topology.json
"""

import os
import sys
import time
import re
import json
import socket
import subprocess
from collections import deque
from datetime import datetime
from dotenv import load_dotenv
import paramiko

load_dotenv()

# ---------------------------------------------------------------------------
# Environment / Config
# ---------------------------------------------------------------------------
DEVICE_IPS = os.getenv("DEVICE_IPS") or os.getenv("DEVICE_IP", "")
USERNAME   = os.getenv("SSH_USERNAME")
PASSWORD   = os.getenv("SSH_PASSWORD")
PORT       = int(os.getenv("SSH_PORT", "22"))
CMD_DELAY  = float(os.getenv("CMD_DELAY", "2.5"))

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _banner(title: str, width: int = 62) -> str:
    return f"\n{'=' * width}\n  {title}\n{'=' * width}"

def _section(title: str, width: int = 62) -> str:
    return f"\n  [{title}]\n  {'-' * (width - 4)}"

# ---------------------------------------------------------------------------
# Pre-checks (reused from initial_discovery)
# ---------------------------------------------------------------------------

def icmp_ping(host: str, timeout: int = 2) -> bool:
    if sys.platform.startswith("win"):
        cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout), host]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0

def tcp_port_open(host: str, port: int, timeout: int = 3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def reachability_check(host: str) -> bool:
    ping_ok = icmp_ping(host)
    ssh_ok  = tcp_port_open(host, PORT) if ping_ok else False
    status  = "PASS" if (ping_ok and ssh_ok) else "FAIL"
    print(f"  [PRE-CHECK] {host:<20}  ICMP: {'OK' if ping_ok else 'FAIL':<6}  SSH:{PORT}: {'OPEN' if ssh_ok else 'CLOSED':<8}  -> {status}")
    return ping_ok and ssh_ok

# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def recv_all(shell, delay: float = CMD_DELAY, buf: int = 65535) -> str:
    time.sleep(delay)
    output = ""
    while shell.recv_ready():
        output += shell.recv(buf).decode("utf-8", errors="replace")
        time.sleep(0.3)
    return output

def send_command(shell, command: str, delay: float = CMD_DELAY) -> str:
    shell.send(command + "\n")
    return recv_all(shell, delay)

# ---------------------------------------------------------------------------
# CDP parser
# ---------------------------------------------------------------------------

def parse_cdp_detail(output: str) -> list[dict]:
    """
    Parse 'show cdp neighbors detail' output into a list of neighbor dicts:
      {device_id, platform, capabilities, local_intf, remote_intf, mgmt_ips}
    """
    neighbors = []
    blocks = re.split(r"-{10,}", output)

    for block in blocks:
        device_id = re.search(r"Device ID:\s*(\S+)", block)
        if not device_id:
            continue

        platform     = re.search(r"Platform:\s*([^,]+),", block)
        capabilities = re.search(r"Capabilities:\s*(.+)", block)
        local_intf   = re.search(r"Interface:\s*(\S+),", block)
        remote_intf  = re.search(r"Port ID \(outgoing port\):\s*(\S+)", block)
        mgmt_ips     = re.findall(r"IP address:\s*(\d+\.\d+\.\d+\.\d+)", block)

        neighbors.append({
            "device_id":    device_id.group(1).strip(),
            "platform":     platform.group(1).strip()     if platform     else "unknown",
            "capabilities": capabilities.group(1).strip() if capabilities else "unknown",
            "local_intf":   local_intf.group(1).strip()   if local_intf   else "unknown",
            "remote_intf":  remote_intf.group(1).strip()  if remote_intf  else "unknown",
            "mgmt_ips":     list(dict.fromkeys(mgmt_ips)),  # deduplicate, preserve order
        })

    return neighbors

# ---------------------------------------------------------------------------
# Single device probe
# ---------------------------------------------------------------------------

def probe_device(host: str) -> dict | None:
    """
    SSH into host, run CDP detail, return parsed neighbor list.
    Returns None on failure.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host, port=PORT,
            username=USERNAME, password=PASSWORD,
            timeout=15, look_for_keys=False, allow_agent=False,
        )
        shell = client.invoke_shell()
        time.sleep(1.5)
        shell.recv(65535)                             # drain banner

        send_command(shell, "terminal length 0", delay=1.0)

        # Grab hostname from prompt if possible
        send_command(shell, "show version | include uptime", delay=1.5)

        raw_cdp = send_command(shell, "show cdp neighbors detail", delay=CMD_DELAY)
        neighbors = parse_cdp_detail(raw_cdp)

        return {"host": host, "neighbors": neighbors, "raw_cdp": raw_cdp}

    except paramiko.AuthenticationException:
        print(f"    [ERROR] Auth failed for {host}")
    except paramiko.SSHException as e:
        print(f"    [ERROR] SSH error on {host}: {e}")
    except Exception as e:
        print(f"    [ERROR] {host}: {e}")
    finally:
        client.close()

    return None

# ---------------------------------------------------------------------------
# Recursive walk
# ---------------------------------------------------------------------------

def recursive_discover(seed_ips: list[str]) -> dict:
    """
    BFS walk of the network starting from seed_ips.
    Returns topology dict keyed by IP.
    """
    visited  = set()
    skipped  = set()
    topology = {}          # ip -> probe result
    queue    = deque(seed_ips)

    print(_banner("RECURSIVE CDP TOPOLOGY DISCOVERY"))
    print(f"\n  Seed device(s) : {', '.join(seed_ips)}")
    print(f"  Strategy       : BFS via CDP neighbor management IPs")

    hop = 0

    while queue:
        # Collect all IPs at the current hop level
        current_hop = list(queue)
        queue.clear()
        hop += 1

        print(_section(f"HOP {hop}  —  {len(current_hop)} device(s) to probe"))

        # Pre-check all devices in this hop
        reachable = []
        for host in current_hop:
            if host in visited:
                print(f"  [SKIP] {host} — already visited")
                continue
            if reachability_check(host):
                reachable.append(host)
            else:
                skipped.add(host)

        # Probe reachable devices
        for host in reachable:
            visited.add(host)
            print(f"\n  Probing {host} ...")

            result = probe_device(host)
            if not result:
                skipped.add(host)
                continue

            topology[host] = result
            neighbors = result["neighbors"]
            print(f"  Found {len(neighbors)} CDP neighbor(s):")

            new_ips = []
            for nbr in neighbors:
                for ip in nbr["mgmt_ips"]:
                    if ip not in visited and ip not in skipped:
                        new_ips.append(ip)
                        queue.append(ip)
                print(f"    {nbr['device_id']:<30} {nbr['platform']:<20} "
                      f"{nbr['local_intf']} -> {nbr['remote_intf']}")

            if new_ips:
                print(f"  Queuing {len(new_ips)} new IP(s): {', '.join(new_ips)}")

    return topology, visited, skipped

# ---------------------------------------------------------------------------
# Topology summary + JSON export
# ---------------------------------------------------------------------------

def print_topology_summary(topology: dict, visited: set, skipped: set) -> None:
    print(_banner("TOPOLOGY SUMMARY"))
    print(f"\n  Devices discovered  : {len(topology)}")
    print(f"  Total IPs visited   : {len(visited)}")
    print(f"  Unreachable/skipped : {len(skipped)}")

    print(_section("Adjacency Table"))
    print(f"\n  {'Device IP':<20} {'Neighbor':<32} {'Platform':<22} {'Link'}")
    print(f"  {'-'*18}   {'-'*30}   {'-'*20}   {'-'*25}")

    for host, data in topology.items():
        for nbr in data["neighbors"]:
            link = f"{nbr['local_intf']} -> {nbr['remote_intf']}"
            print(f"  {host:<20} {nbr['device_id']:<32} {nbr['platform']:<22} {link}")

    if skipped:
        print(_section("Unreachable Devices"))
        for ip in sorted(skipped):
            print(f"  {ip}")


def save_topology_json(topology: dict, visited: set, skipped: set) -> str:
    payload = {
        "timestamp":   datetime.now().isoformat(),
        "discovered":  len(topology),
        "skipped":     sorted(skipped),
        "topology":    {
            host: {
                "neighbors": data["neighbors"]
            }
            for host, data in topology.items()
        }
    }
    path = "topology.json"
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return path

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    missing = [k for k, v in {
        "DEVICE_IPS":   DEVICE_IPS,
        "SSH_USERNAME": USERNAME,
        "SSH_PASSWORD": PASSWORD,
    }.items() if not v]

    if missing:
        print("[ERROR] Missing required .env variables:")
        for var in missing:
            print(f"        - {var}")
        sys.exit(1)

    seeds = [ip.strip() for ip in DEVICE_IPS.split(",") if ip.strip()]
    if not seeds:
        print("[ERROR] DEVICE_IPS contains no valid addresses.")
        sys.exit(1)

    topology, visited, skipped = recursive_discover(seeds)

    print_topology_summary(topology, visited, skipped)

    json_path = save_topology_json(topology, visited, skipped)
    print(f"\n  Topology saved to : {json_path}")
    print(_banner("DONE"))
    print()


if __name__ == "__main__":
    main()
