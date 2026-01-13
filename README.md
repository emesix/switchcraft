# Switchcraft - MCP Network Switch Server

A Model Context Protocol (MCP) server for **stable** L2/L3 network switch configuration management with **git-versioned desired state** and **hardware-in-the-loop testing**.

## Why This Exists

Managing a heterogeneous network with Brocade (telnet), Zyxel (HTTPS/SSH), and OpenWrt (SSH) switches is painful:
- **Unreliable communication** - Brocade telnet is notoriously flaky
- **Inconsistent configs** - Each vendor speaks a different language
- **No version control** - Changes are made ad-hoc without history
- **Risky testing** - No safe way to validate changes on real hardware

Switchcraft provides:
- **Stable connections** with automatic retries and exponential backoff
- **Normalized configuration** across all switch types
- **Git-versioned desired state** with drift detection
- **HIL testing** with server-enforced safety constraints
- **Unified API** for Claude Code to manage your entire network

## Supported Devices

| Device | Type | Protocol | Features |
|--------|------|----------|----------|
| **Brocade FCX** | `brocade` | Telnet | VLAN, port config, batch commands, retry logic |
| **Zyxel GS1900** | `zyxel` | HTTPS | Web API, VLAN, port config |
| **Zyxel GS1900** | `zyxel-cli` | SSH | CLI interface (like Brocade), legacy SSH support |
| **OpenWrt** | `openwrt` | SSH | UCI config, DSA bridge-vlan |

## Quick Start

```bash
# Clone and setup
cd /home/emesix/git/switchcraft
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Configure credentials
cp .env.example .env
# Edit .env with your NETWORK_PASSWORD

# Run tests
make test

# Start MCP server
make server
```

## Configuration

### 1. Environment Variables

```bash
# Required
export NETWORK_PASSWORD="your_switch_password"

# Optional
export SWITCHCRAFT_LOG_LEVEL=INFO
export SWITCHCRAFT_LOG_FILE=~/.switchcraft/switchcraft.log
```

### 2. Device Inventory

Edit `configs/devices.yaml`:

```yaml
devices:
  brocade-core:
    type: brocade
    host: 192.168.254.2
    protocol: telnet
    port: 23
    username: admin
    enable_password_required: true

  zyxel-frontend:
    type: zyxel-cli
    host: 192.168.254.3
    protocol: ssh
    port: 22
    username: admin
```

### 3. Claude Code Integration

Add to `~/.config/claude-code/settings.json`:

```json
{
  "mcpServers": {
    "switchcraft": {
      "command": "/path/to/switchcraft/.venv/bin/python",
      "args": ["-m", "mcp_network_switch.server"],
      "cwd": "/path/to/switchcraft",
      "env": {
        "NETWORK_PASSWORD": "your_password"
      }
    }
  }
}
```

## Available Tools

### Device Management
| Tool | Description |
|------|-------------|
| `list_devices` | List all configured network devices |
| `device_status` | Get health/status of a device |

### Configuration Reading
| Tool | Description |
|------|-------------|
| `get_config` | Get normalized config (VLANs, ports) |
| `get_vlans` | Get VLAN configurations |
| `get_ports` | Get port configurations |

### Configuration Writing
| Tool | Description |
|------|-------------|
| `create_vlan` | Create/update a VLAN (supports dry_run) |
| `delete_vlan` | Remove a VLAN (supports dry_run) |
| `configure_port` | Configure a port |
| `save_config` | Save running config to startup |

### Batch Operations
| Tool | Description |
|------|-------------|
| `execute_command` | Run raw command on device |
| `execute_batch` | Fast batch show commands (Brocade) |
| `execute_config_batch` | Fast batch config commands (Brocade) |
| `batch_command` | Run command on multiple devices |

### Configuration Management
| Tool | Description |
|------|-------------|
| `config_save` | Save desired state to git-versioned store |
| `config_status` | Check drift between desired and actual |
| `config_sync` | Apply desired state to device (with rollback) |
| `config_snapshot` | Create named snapshot |
| `config_restore` | Restore from snapshot |
| `config_history` | View git commit history |
| `config_rollback` | Rollback to previous version |
| `config_diff` | Diff between revisions |

### Fleet Management
| Tool | Description |
|------|-------------|
| `list_groups` | List device groups defined in inventory |
| `list_profiles` | List available configuration profiles |
| `save_profile` | Create a reusable configuration profile |
| `config_sync_group` | Apply profile to all devices in a group |

### Audit
| Tool | Description |
|------|-------------|
| `get_audit_log` | View recent configuration changes |

## Make Targets

```bash
make help           # Show all targets
make test           # Run unit tests (160 tests)
make lint           # Run ruff linter
make server         # Start MCP server

# HIL Testing (Hardware-in-the-Loop)
make hil            # Run on ALL devices (192.168.254.2-4)
make hil-brocade    # Test only Brocade
make hil-zyxel      # Test only Zyxel
make hil-openwrt    # Test only OpenWrt
make hil-report     # View last HIL report
```

## HIL Testing

HIL (Hardware-in-the-Loop) testing validates changes against real hardware with **server-enforced safety**:

- **Only VLAN 999** - Any other VLAN operation is rejected
- **Only designated ports** - 23/24 on Brocade/Zyxel, lan7/8 on OpenWrt
- **Full lifecycle** - Snapshot → Apply → Verify → Cleanup → Validate
- **Audit artifacts** - Every run produces JSON evidence

```bash
# Run HIL tests (requires NETWORK_PASSWORD)
source .env
make hil
```

See [docs/HIL-TESTING.md](docs/HIL-TESTING.md) for full documentation.

## Configuration Management

Switchcraft maintains **git-versioned desired state** for each device:

```bash
~/.switchcraft/configs/
├── .git/                    # Git repository
├── desired/
│   ├── brocade-core.yaml   # Desired state
│   ├── zyxel-frontend.yaml
│   └── onti-backend.yaml
├── last_known/             # Last observed state
└── snapshots/              # Named snapshots
```

### Drift Detection

```
"Check if brocade-core matches desired state"
-> Uses config_status tool
-> Returns: IN_SYNC or DRIFT with differences
```

### Version History

```
"Show config history for brocade-core"
-> Uses config_history tool
-> Returns: Git commits with messages, timestamps, authors
```

## Architecture

```
switchcraft/
├── configs/
│   ├── devices.yaml         # Production device inventory
│   └── devices.lab.yaml     # HIL lab devices
├── src/mcp_network_switch/
│   ├── server.py            # MCP server entry point
│   ├── devices/
│   │   ├── base.py          # Abstract device class
│   │   ├── brocade.py       # Brocade telnet handler
│   │   ├── openwrt.py       # OpenWrt SSH handler
│   │   ├── zyxel.py         # Zyxel HTTPS handler
│   │   └── zyxel_cli.py     # Zyxel SSH CLI handler
│   ├── config_store/
│   │   ├── store.py         # Git-versioned config store
│   │   └── git_manager.py   # Git operations
│   ├── hil/
│   │   ├── mode.py          # HIL mode enforcement
│   │   ├── constraints.py   # Safety constraints
│   │   └── runner.py        # Lifecycle test runner
│   └── utils/
│       ├── connection.py    # Retry logic
│       └── logging_config.py
├── tests/
│   ├── hil_spec.yaml        # HIL test specification
│   └── *.py                 # 133 unit tests
├── docs/
│   ├── HIL-TESTING.md       # HIL documentation
│   └── *.md                 # Other docs
└── Makefile                 # Build targets
```

## Stability Features

### Brocade (Telnet)
- 5 retry attempts with exponential backoff (2s → 15s)
- Batch command execution (3-5x faster)
- Proper `--More--` pagination handling
- Automatic reconnection on failure

### Zyxel (SSH CLI)
- Legacy SSH algorithm support (OpenSSH 6.2)
- Interactive shell with pagination handling
- Smart error detection (ignores statistics)

### OpenWrt (SSH)
- UCI command interface
- DSA bridge-vlan support
- Port status from sysfs

## Documentation

| Document | Description |
|----------|-------------|
| [CLAUDE.md](CLAUDE.md) | Instructions for Claude Code |
| [docs/HIL-TESTING.md](docs/HIL-TESTING.md) | HIL testing guide |
| [docs/QUICK-REFERENCE.md](docs/QUICK-REFERENCE.md) | Command reference |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

## Troubleshooting

### "Connection refused" on Brocade
- Ensure telnet is enabled on the switch
- Check if another session is active (Brocade limits concurrent sessions)
- The server will retry automatically

### "HIL CONSTRAINT VIOLATION"
- HIL mode only permits VLAN 999 operations
- Check that you're using the correct device and ports

### Device unreachable
- Verify network connectivity: `ping 192.168.254.X`
- Check credentials in `.env`

## Development

```bash
# Run tests
make test

# Run with verbose output
pytest -v

# Lint
make lint

# Fix lint errors
make lint-fix
```

## License

MIT
