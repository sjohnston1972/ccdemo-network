"""
initial_discovery.py
--------------------
Layer 2 Network Discovery via CDP and LLDP using Paramiko SSH.

Credentials and target IPs are loaded exclusively from a .env file.
See .env.example for required variables.
"""

import os
import sys
import time
import socket
import subprocess
from dotenv import load_dotenv
import paramiko

load_dotenv()

# ---------------------------------------------------------------------------
# Environment / Config
# ---------------------------------------------------------------------------
DEVICE_IPS = os.getenv("DEVICE_IPS") or os.getenv("DEVICE_IP", "")  # support both singular and plural
USERNAME    = os.getenv("SSH_USERNAME")
PASSWORD    = os.getenv("SSH_PASSWORD")
PORT        = int(os.getenv("SSH_PORT", "22"))
CMD_DELAY   = float(os.getenv("CMD_DELAY", "2.5"))   # seconds between commands

# ---------------------------------------------------------------------------
# Discovery command sets
# ---------------------------------------------------------------------------
CDP_COMMANDS = [
    "show cdp neighbors",
    "show cdp neighbors detail",
]

LLDP_COMMANDS = [
    "show lldp neighbors",
    "show lldp neighbors detail",
]

BOOTSTRAP = "terminal length 0"   # disable pagination before anything else

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pre-checks
# ---------------------------------------------------------------------------

def icmp_ping(host: str, timeout: int = 2) -> bool:
    """Send a single ICMP ping. Works on Windows and Unix."""
    if sys.platform.startswith("win"):
        cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout), host]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def tcp_port_open(host: str, port: int, timeout: int = 3) -> bool:
    """Check whether a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def reachability_check(host: str) -> bool:
    """
    Two-stage pre-check before attempting SSH:
      1. ICMP ping  — basic layer 3 reachability
      2. TCP port 22 — SSH service is up
    Returns True only if both pass.
    """
    print(f"\n  [PRE-CHECK] {host}")

    ping_ok = icmp_ping(host)
    print(f"    ICMP ping  : {'PASS' if ping_ok else 'FAIL'}")
    if not ping_ok:
        print(f"    [SKIP] {host} is not reachable via ICMP — skipping SSH.")
        return False

    ssh_ok = tcp_port_open(host, PORT)
    print(f"    TCP port {PORT}  : {'OPEN' if ssh_ok else 'CLOSED'}")
    if not ssh_ok:
        print(f"    [SKIP] SSH port {PORT} is not open on {host} — skipping.")
        return False

    print(f"    Pre-checks passed. Proceeding to discovery.")
    return True


def _banner(title: str, width: int = 62) -> str:
    return f"\n{'=' * width}\n  {title}\n{'=' * width}"


def _section(title: str, width: int = 62) -> str:
    return f"\n  [{title}]\n  {'-' * (width - 4)}"


def recv_all(shell, delay: float = CMD_DELAY, buf: int = 65535) -> str:
    """Send enough wait time then drain the receive buffer."""
    time.sleep(delay)
    output = ""
    while shell.recv_ready():
        chunk = shell.recv(buf).decode("utf-8", errors="replace")
        output += chunk
        time.sleep(0.3)          # brief pause to catch trailing data
    return output


def send_command(shell, command: str, delay: float = CMD_DELAY) -> str:
    """Send a single command and return its output."""
    shell.send(command + "\n")
    return recv_all(shell, delay)


def run_discovery_commands(shell, commands: list[str], protocol: str) -> dict:
    """
    Execute a list of commands and return a dict of {command: output}.
    Skips a command if the output contains unsupported-protocol indicators.
    """
    results = {}
    for cmd in commands:
        print(f"    -> {cmd}")
        output = send_command(shell, cmd)

        # Detect unsupported protocol gracefully
        unsupported_markers = [
            "% CDP is not enabled",
            "% LLDP is not enabled",
            "Invalid input detected",
            "% Unknown command",
            "not supported",
        ]
        if any(m.lower() in output.lower() for m in unsupported_markers):
            results[cmd] = f"  [INFO] {protocol} not enabled or not supported on this device.\n"
        else:
            results[cmd] = output

    return results


# ---------------------------------------------------------------------------
# Per-device discovery
# ---------------------------------------------------------------------------

def discover_device(host: str) -> None:
    print(_banner(f"DISCOVERY TARGET: {host}"))

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        print(f"\n  Connecting to {host}:{PORT} ...")
        client.connect(
            hostname=host,
            port=PORT,
            username=USERNAME,
            password=PASSWORD,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        shell = client.invoke_shell()
        time.sleep(1.5)
        shell.recv(65535)          # drain login banner / initial prompt

        # Disable paging
        send_command(shell, BOOTSTRAP, delay=1.0)
        print(f"  Connected. Pagination disabled.\n")

        # --- CDP Discovery ---
        print(_section("CDP - Cisco Discovery Protocol"))
        cdp_results = run_discovery_commands(shell, CDP_COMMANDS, "CDP")
        for cmd, out in cdp_results.items():
            print(f"\n  Command : {cmd}")
            print("  " + "-" * 58)
            for line in out.strip().splitlines():
                print(f"  {line}")

        # --- LLDP Discovery ---
        print(_section("LLDP - Link Layer Discovery Protocol"))
        lldp_results = run_discovery_commands(shell, LLDP_COMMANDS, "LLDP")
        for cmd, out in lldp_results.items():
            print(f"\n  Command : {cmd}")
            print("  " + "-" * 58)
            for line in out.strip().splitlines():
                print(f"  {line}")

        print(f"\n  [OK] Discovery complete for {host}")

    except paramiko.AuthenticationException:
        print(f"\n  [ERROR] Authentication failed for {host}. Check SSH_USERNAME / SSH_PASSWORD.")
    except paramiko.SSHException as e:
        print(f"\n  [ERROR] SSH error on {host}: {e}")
    except TimeoutError:
        print(f"\n  [ERROR] Connection timed out for {host}.")
    except Exception as e:
        print(f"\n  [ERROR] Unexpected error on {host}: {e}")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    # Validate required env vars
    missing = [k for k, v in {
        "DEVICE_IPS":    DEVICE_IPS,
        "SSH_USERNAME":  USERNAME,
        "SSH_PASSWORD":  PASSWORD,
    }.items() if not v]

    if missing:
        print("[ERROR] The following required .env variables are not set:")
        for var in missing:
            print(f"        - {var}")
        print("\n  Copy .env.example to .env and fill in your values.")
        sys.exit(1)

    devices = [ip.strip() for ip in DEVICE_IPS.split(",") if ip.strip()]

    if not devices:
        print("[ERROR] DEVICE_IPS is set but contains no valid addresses.")
        sys.exit(1)

    print(_banner("INITIAL LAYER 2 NETWORK DISCOVERY"))
    print(f"\n  Protocol   : CDP + LLDP")
    print(f"  Devices    : {len(devices)}")
    print(f"  Targets    : {', '.join(devices)}")
    print(f"  SSH Port   : {PORT}")
    print(f"  Cmd Delay  : {CMD_DELAY}s")

    reachable   = []
    unreachable = []

    print("\n  Running pre-checks ...")
    for host in devices:
        if reachability_check(host):
            reachable.append(host)
        else:
            unreachable.append(host)

    if unreachable:
        print(f"\n  [WARN] Skipping {len(unreachable)} unreachable device(s): {', '.join(unreachable)}")

    if not reachable:
        print("\n  [ERROR] No reachable devices found. Exiting.")
        sys.exit(1)

    for host in reachable:
        discover_device(host)

    print(_banner("DISCOVERY COMPLETE"))
    print()


if __name__ == "__main__":
    main()
