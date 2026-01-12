# Switchcraft - MCP Network Switch Server

## Project Overview
MCP server for managing L2/L3 network switches with unified API across different vendors.

## Network Topology
```
[Your PC: 192.168.254.58]
    │
    ├── 1/1/1 ──► [Brocade FCX624-E: 192.168.254.2] ◄── Core Switch (Telnet:23)
    │                    │
    │                    └── 1/2/2 (10G SFP+ optical) ──► [ONTI S508CL: 192.168.254.4] ◄── Backend 10G (SSH:22)
    │
    └── [Zyxel GS1900-24HP: 192.168.254.3] ◄── Frontend PoE (HTTPS:443)
```

## Device Credentials
- Password: Use `NETWORK_PASSWORD` env var (in `.env`)
- Brocade: admin user, enable password required
- ONTI: root user, SCP workflow for config files
- Zyxel: admin user, HTTPS API

## Key Technical Details

### Brocade FCX624-E
- Port naming: `1/module/port` (e.g., `1/1/1` = unit 1, module 1, port 1)
- Module 1 (M1): 24x 1G copper ports (1/1/1 to 1/1/24)
- Module 2 (M2): 4x 10G SFP+ ports (1/2/1 to 1/2/4)
- Telnet sessions can be flaky - retry logic implemented
- Config mode requires killing stale console sessions: `kill console 1`
- **Batch execution**: Send multiple commands with newlines for speed

### VLAN Configuration (Current)
- VLAN 1: Default, ports 1/1/11-24 and 1/2/3-4
- VLAN 254: Management network (192.168.254.x)
  - Untagged: 1/1/1-10, 1/2/2
  - Tagged: 1/2/1
  - Router interface: VE 254

### Recent Changes (2026-01-12)
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

## Known Issues
- ONTI (192.168.254.4) currently unreachable - may need IP/config check on device
- Zyxel (192.168.254.3) also unreachable - topology unclear

## MCP Tools Available
- `list_devices` - List all configured devices
- `device_status` - Health check
- `get_vlans` / `get_ports` - Read config
- `execute_command` - Raw command execution
- `execute_config_batch` - **NEW** Fast batch config execution (Brocade)
- `create_vlan` / `delete_vlan` - VLAN management
- `configure_port` - Port configuration
- `save_config` - Write memory
- `download_config_file` / `upload_config_file` - ONTI SCP workflow
