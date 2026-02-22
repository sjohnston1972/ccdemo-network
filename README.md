# ccdemo-network

Enterprise network automation using Python + Paramiko SSH.
Credentials are always loaded from a `.env` file — never hardcoded.

---

## Scripts

### `initial_discovery.py`
Layer 2 network discovery using **CDP** and **LLDP**.

- Runs a two-stage pre-check (ICMP ping + TCP/22) before attempting SSH
- Disables terminal pagination automatically
- Detects and gracefully skips unsupported protocols (CDP/LLDP not enabled)
- Supports multiple devices via a comma-separated `DEVICE_IPS` list

**Commands executed per device:**
```
show cdp neighbors
show cdp neighbors detail
show lldp neighbors
show lldp neighbors detail
```

---

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/sjohnston1972/ccdemo-network.git
cd ccdemo-network
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Configure credentials**
```bash
cp .env.example .env
```

Edit `.env` with your values:
```env
DEVICE_IPS=192.168.1.1,192.168.1.2
SSH_USERNAME=your_username
SSH_PASSWORD=your_password
SSH_PORT=22
CMD_DELAY=2.5
```

> `.env` is excluded from git via `.gitignore` — never commit credentials.

---

## Usage

```bash
python initial_discovery.py
```

**Example output:**
```
==============================================================
  INITIAL LAYER 2 NETWORK DISCOVERY
==============================================================

  Protocol   : CDP + LLDP
  Devices    : 1
  Targets    : 192.168.20.18
  SSH Port   : 22

  Running pre-checks ...

  [PRE-CHECK] 192.168.20.18
    ICMP ping  : PASS
    TCP port 22  : OPEN
    Pre-checks passed. Proceeding to discovery.

  [CDP - Cisco Discovery Protocol]
    -> show cdp neighbors
    -> show cdp neighbors detail
  ...
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEVICE_IPS` | Yes | — | Comma-separated list of device IPs |
| `SSH_USERNAME` | Yes | — | SSH login username |
| `SSH_PASSWORD` | Yes | — | SSH login password |
| `SSH_PORT` | No | `22` | SSH port |
| `CMD_DELAY` | No | `2.5` | Seconds to wait after each command |

> `DEVICE_IP` (singular) is also supported for single-device `.env` files.

---

## Requirements

- Python 3.10+
- `paramiko >= 3.4.0`
- `python-dotenv >= 1.0.0`
