# Switchcraft - MCP Network Switch Server

## Project Overview
MCP server for managing L2/L3 network switches with unified API across different vendors.

## Network Topology
```
[Your PC: 192.168.254.58]
    │
    ├── 1/1/1 ──► [Brocade FCX624-E: 192.168.254.2] ◄── Core Switch (Telnet:23)
    │                    │
    │                    ├── 1/1/2 ──► [iZombie: 192.168.254.99] ◄── Arch Linux (SSH:22)
    │                    │
    │                    └── 1/2/2 (10G SFP+) ──► [ONTI S508CL + OpenWrt: 192.168.254.4] ◄── Backend 10G (SSH:22)
    │
    └── [Unknown device: 192.168.254.3] ◄── SSH:22 (was Zyxel, now shows OpenSSH 6.2)
```

### Device Details
| IP | Hardware | Software | Ports | Status |
|----|----------|----------|-------|--------|
| .2 | Brocade FCX624-E | FastIron 08.0.30u | 24x1G + 4x10G SFP+ | ✅ Reachable |
| .3 | Unknown (was Zyxel) | OpenSSH 6.2 | ? | ⚠️ Auth failed |
| .4 | ONTI S508CL (RTL930x) | OpenWrt SNAPSHOT r32466 | 8x LAN (lan1-lan8) | ✅ Reachable |
| .99 | iZombie | Arch Linux | 1x 1G | ✅ Reachable |

## Device Credentials
- Password: Use `NETWORK_PASSWORD` env var (in `.env`)
- Brocade: admin user, enable password required
- OpenWrt (.4): root user, SSH with password auth
- Zyxel (if present): admin user, HTTPS API

## Logging Configuration
Environment variables for logging:
- `SWITCHCRAFT_LOG_LEVEL`: DEBUG, INFO, WARNING, ERROR (default: INFO)
- `SWITCHCRAFT_LOG_FILE`: Path to main log (default: ~/.switchcraft/switchcraft.log)
- `SWITCHCRAFT_LOG_MAX_SIZE`: Max log file size in MB (default: 10)
- `SWITCHCRAFT_LOG_BACKUPS`: Number of backup files to keep (default: 5)

Log files:
- `~/.switchcraft/switchcraft.log` - Main application log
- `~/.switchcraft/switchcraft-perf.log` - Performance timing log (for efficiency analysis)

Performance log format:
```
2026-01-12 17:30:45.123 | PERF | execute_batch        | brocade-core    |   523.45ms | OK | cmds=5 | failed=0 | avg=104.69ms/cmd
```

## Key Technical Details

### Brocade FCX624-E
- Port naming: `1/module/port` (e.g., `1/1/1` = unit 1, module 1, port 1)
- Module 1 (M1): 24x 1G copper ports (1/1/1 to 1/1/24)
- Module 2 (M2): 4x 10G SFP+ ports (1/2/1 to 1/2/4)
- Telnet sessions can be flaky - retry logic implemented
- Config mode requires killing stale console sessions: `kill console 1`
- **Batch execution**: Send multiple commands with newlines for speed

**CRITICAL: 10G SFP+ Module Stacking Fix**
The 10G ports (1/2/x) are stacking ports by default and will NOT bridge L2 traffic
to the copper ports (1/1/x) until stacking is disabled:
```
configure terminal
stack disable
write memory
```
Without this, the 10G module operates in "stacking-ready" mode and won't forward
regular Ethernet frames between modules. This is required for the OpenWrt link!

### VLAN Configuration (Current)
- VLAN 1: Default, ports 1/1/11-24 and 1/2/3-4
- VLAN 254: Management network (192.168.254.x)
  - Untagged: 1/1/1-10, 1/2/2
  - Tagged: 1/2/1
  - Router interface: VE 254

### Recent Changes (2026-01-13)
1. Added `config_sync` tool for applying desired state to devices with auto-rollback
2. Enabled automatic rollback on failure (Brocade only)
3. Fixed OpenWrt VLAN handler for DSA bridge-vlan with vlan_filtering
4. Added HIL (Hardware-in-the-Loop) testing system with server-enforced safety
5. Added Zyxel SSH CLI handler (`zyxel-cli` device type)

### Previous Changes (2026-01-12)
1. Added `execute_batch()` to Brocade handler for fast multi-command execution
2. Added `execute_config_batch` MCP tool for batch config commands
3. Changed port 1/2/2 from tagged to untagged in VLAN 254 (for ONTI connectivity)

## Development Workflow
```bash
cd /home/emesix/git/switchcraft
source .venv/bin/activate
source .env

# Run tests
pytest -v

# Lint
ruff check src/ tests/

# Test MCP server manually
python -m mcp_network_switch.server
```

### OpenWrt (DSA-based)
- Uses DSA (Distributed Switch Architecture) - each port is a separate netdev
- Port naming: `lan1`, `lan2`, ..., `lan8`
- Configuration via UCI (Unified Configuration Interface)
- VLANs via bridge-vlan sections in `/etc/config/network`
- **VLAN filtering must be enabled** on the bridge before bridge-vlan works
- Port status from `/sys/class/net/lanX/{operstate,speed,duplex}`
- Handler auto-enables vlan_filtering when creating VLANs

## Known Issues
- Device at .3: SSH responds (OpenSSH 6.2) but NETWORK_PASSWORD rejected - credentials unknown

## Resolved Issues
- **10G to 1G bridging fixed**: Required `stack disable` on Brocade (2026-01-12)

## MCP Tools Available

### Device Management
- `list_devices` - List all configured devices
- `device_status` - Health check

### Configuration Reading
- `get_config` - Get normalized config (VLANs, ports)
- `get_vlans` / `get_ports` - Read config

### Configuration Writing
- `create_vlan` / `delete_vlan` - VLAN management (supports dry_run)
- `configure_port` - Port configuration
- `save_config` - Write memory
- `apply_config` - Apply desired state config dict directly

### Batch Operations
- `execute_command` - Raw command execution
- `execute_batch` - Fast batch show commands (Brocade, 3-5x faster)
- `execute_config_batch` - Fast batch config commands (Brocade, 3x faster)
- `batch_command` - Run command on multiple devices

### Configuration Management (Git-Versioned)
- `config_save` - Save current device state as desired config
- `config_status` - Check drift between desired and actual
- `config_sync` - Apply desired state to device (with auto-rollback)
- `config_snapshot` / `config_restore` - Snapshot management
- `config_history` / `config_rollback` / `config_diff` - Version control

### Fleet Management
- `list_groups` - List device groups (defined in devices.yaml)
- `list_profiles` - List configuration profiles
- `save_profile` - Create reusable config template
- `config_sync_group` - Apply profile to all devices in a group

### Audit
- `get_audit_log` - View recent configuration changes

### Legacy (ONTI)
- `download_config_file` / `upload_config_file` - SCP workflow (device now runs OpenWrt)
