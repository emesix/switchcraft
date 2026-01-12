# MCP Network Switch Server

A Model Context Protocol (MCP) server for **stable** L2/L3 network switch configuration management.

## Why This Exists

Managing a heterogeneous network with Brocade (telnet), Zyxel (HTTPS), and ONTI (SSH) switches is painful:
- **Unreliable communication** - Brocade telnet is notoriously flaky
- **Inconsistent configs** - Each vendor speaks a different language
- **Slow interactive editing** - Shell editing on busybox is painful

This MCP server provides:
- **Stable connections** with automatic retries and exponential backoff
- **Normalized configuration** across all switch types
- **SCP-based workflow** for ONTI (download -> edit -> upload is 100x faster)
- **Unified API** for Claude Code to manage your entire network

## Supported Devices

| Device | Protocol | Features |
|--------|----------|----------|
| **Brocade FCX** | Telnet | VLAN, port config, retry logic |
| **Zyxel GS1900** | HTTPS | Web API scraping, VLAN, port config |
| **ONTI S508CL** | SSH + SCP | UCI config, **fast SCP workflow** |

## Installation

```bash
cd /home/emesix/mcp-network-switch

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
```

## Configuration

### 1. Set Environment Variables

```bash
# Create .env file (or export in shell)
export NETWORK_PASSWORD="your_password_here"
export ZABBIX_API_TOKEN="your_zabbix_api_token_here"
```

### 2. Device Inventory

Edit `configs/devices.yaml` to match your network. The default is pre-configured for the VOS network.

### 3. Configure Claude Code

Add to your Claude Code MCP settings (`~/.config/claude-code/settings.json`):

```json
{
  "mcpServers": {
    "network-switch": {
      "command": "/path/to/mcp-network-switch/.venv/bin/python",
      "args": ["-m", "mcp_network_switch.server"],
      "cwd": "/path/to/mcp-network-switch",
      "env": {
        "NETWORK_PASSWORD": "your_password_here",
        "MCP_NETWORK_CONFIG": "/path/to/mcp-network-switch/configs/devices.yaml"
      }
    }
  }
}
```

Or in project-local `.claude/settings.json`:

```json
{
  "mcpServers": {
    "network-switch": {
      "command": ".venv/bin/python",
      "args": ["-m", "mcp_network_switch.server"],
      "env": {
        "NETWORK_PASSWORD": "your_password_here"
      }
    }
  }
}
```

## Available Tools

### Device Management
- `list_devices` - List all configured network devices
- `device_status` - Get health/status of a device

### Configuration Reading
- `get_config` - Get normalized config (VLANs, ports)
- `get_vlans` - Get VLAN configurations
- `get_ports` - Get port configurations

### Configuration Writing
- `create_vlan` - Create/update a VLAN
- `delete_vlan` - Remove a VLAN
- `configure_port` - Configure a port
- `save_config` - Save running config to startup

### Advanced
- `execute_command` - Run raw command on device
- `diff_config` - Compare expected vs actual config
- `batch_command` - Run command on multiple devices

### ONTI SCP Workflow (FAST!)
- `download_config_file` - Download UCI config via SCP
- `upload_config_file` - Upload UCI config via SCP

## Usage Examples

### Check Device Status
```
"Check the status of the Brocade core switch"
-> Uses device_status tool with device_id="brocade-core"
```

### Get VLANs
```
"Show me all VLANs on the Brocade"
-> Uses get_vlans tool
```

### Create VLAN
```
"Create VLAN 100 named 'Servers' with ports 1/1/5-8 untagged and 1/2/1 tagged on Brocade"
-> Uses create_vlan tool
```

### ONTI Config Edit (Fast Path)
```
"Download the network config from ONTI, add VLAN 300, and upload it back"
-> Uses download_config_file, then upload_config_file
```

### Diff Expected Config
```
"Check if VLAN 254 has the expected configuration on all switches"
-> Uses diff_config tool
```

## Architecture

```
mcp-network-switch/
├── configs/
│   └── devices.yaml          # Device inventory
├── src/mcp_network_switch/
│   ├── server.py             # MCP server entry point
│   ├── devices/
│   │   ├── base.py           # Abstract device class
│   │   ├── brocade.py        # Brocade telnet handler
│   │   ├── onti.py           # ONTI SSH/SCP handler
│   │   └── zyxel.py          # Zyxel HTTPS handler
│   ├── config/
│   │   ├── schema.py         # Normalized config schema
│   │   └── inventory.py      # Device inventory loader
│   └── utils/
│       └── connection.py     # Retry logic, health checks
```

## Stability Features

### Brocade (Telnet)
- 5 retry attempts with exponential backoff (2s -> 15s)
- Proper --More-- pagination handling
- Command timing delays for reliable output
- Automatic reconnection on failure

### ONTI (SSH)
- 3 retry attempts with exponential backoff
- SCP-based config workflow (bypasses slow shell)
- UCI command interface for granular changes

### Zyxel (HTTPS)
- Session management with auto-login
- Multiple login endpoint fallbacks
- HTML parsing for config extraction

## Troubleshooting

### "Connection refused" on Brocade
- Ensure telnet is enabled on the switch
- Check if another session is active (Brocade limits concurrent sessions)
- The server will retry automatically

### "Unreachable" devices
- Verify VLAN 254 is configured on OPNsense (see VOS-CURRENT-STATE.md)
- Check physical connectivity through the dumb switch

### SCP timeout on ONTI
- Ensure SSH is enabled and root login allowed
- Check if the device is overloaded (wait and retry)

## Development

```bash
# Run tests
pytest

# Run server manually
python -m mcp_network_switch.server
```
