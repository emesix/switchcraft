# Switchcraft Project Status

**Date:** 2026-01-12
**Status:** QA Complete - Ready for Production (with caveats)

---

## Project Overview

Switchcraft is an MCP (Model Context Protocol) server for managing L2/L3 network switches with a unified API across different vendors.

### Supported Devices
| Vendor | Model | Protocol | Handler |
|--------|-------|----------|---------|
| Brocade | FCX624-E | Telnet | `brocade.py` |
| Zyxel | GS1900-24HP | HTTPS | `zyxel.py` |
| ONTI | S508CL | SSH/SCP | `onti.py` |
| OpenWrt | Any DSA-based | SSH | `openwrt.py` |

---

## MCP Tools (16 Total)

### Discovery & Status
- `list_devices` - List all configured devices
- `device_status` - Get health/status for a device

### Configuration Retrieval
- `get_config` - Get normalized configuration
- `get_vlans` - Get VLAN configurations
- `get_ports` - Get port configurations

### Configuration Modification
- `create_vlan` - Create or update a VLAN
- `delete_vlan` - Delete a VLAN
- `configure_port` - Configure a port
- `save_config` - Save running config to startup

### Command Execution
- `execute_command` - Execute raw command
- `batch_command` - Execute command on multiple devices
- `execute_batch` - Fast batch show commands (Brocade)
- `execute_config_batch` - Fast batch config commands (Brocade)

### Advanced
- `diff_config` - Compare expected vs actual config
- `download_config_file` - Download ONTI config via SCP
- `upload_config_file` - Upload ONTI config via SCP

---

## Recent Development Session (2026-01-12)

### Features Added
1. **OpenWrt Device Handler** (`openwrt.py`)
   - DSA (Distributed Switch Architecture) support
   - UCI configuration interface
   - SSH-based execution

2. **Batch Command Execution**
   - `execute_batch` for show commands (3x speedup)
   - `execute_config_batch` for config commands

3. **Critical Fix: Brocade 10G SFP+ Bridging**
   - Discovered 10G ports are stacking ports by default
   - Fix: `stack disable` command enables L2 bridging

### Bugs Found & Fixed
| Bug | Severity | Status | Description |
|-----|----------|--------|-------------|
| BUG-001 | CRITICAL | ✅ FIXED | Empty config upload could brick device |
| BUG-002 | MEDIUM | ✅ FIXED | VLAN 0 false positive success |
| BUG-003 | MEDIUM | ✅ FIXED | Delete VLAN 1 false positive success |
| BUG-004 | LOW | DEFERRED | Empty command causes connection close |

### Files Modified
- `src/mcp_network_switch/server.py` - Added empty content validation
- `src/mcp_network_switch/devices/brocade.py` - Added error patterns + VLAN validation
- `src/mcp_network_switch/devices/openwrt.py` - NEW: OpenWrt handler
- `src/mcp_network_switch/devices/__init__.py` - Registered OpenWrt handler
- `CLAUDE.md` - Updated topology, added stack disable documentation
- `docs/TEST-PLAN.md` - Comprehensive test plan
- `docs/TEST-RESULTS.md` - Test results with bug documentation

---

## Network Topology

```
[Your PC: 192.168.254.58]
    │
    ├── 1/1/1 ──► [Brocade FCX624-E: 192.168.254.2] ◄── Core Switch (Telnet:23)
    │                    │
    │                    ├── 1/1/2 ──► [iZombie: 192.168.254.99] ◄── Test Machine
    │                    │
    │                    └── 1/2/2 (10G SFP+) ──► [OpenWrt: 192.168.254.4] ◄── 10G Switch (SSH:22)
    │
    └── [Zyxel GS1900-24HP: 192.168.254.3] ◄── Frontend PoE (HTTPS:443)
```

### Device Status (Post-Testing)
| Device | Status | Notes |
|--------|--------|-------|
| brocade-core | ✅ Operational | Primary test target, working |
| onti-backend | ✅ Operational | Port 1/2/2 was disabled during testing, re-enabled |
| zyxel-frontend | ❌ Unreachable | Unknown status |

---

## Known Issues

### Brocade 10G Port Quirk
The 10G SFP+ ports (module 2) on Brocade FCX are stacking ports by default. To enable L2 bridging between 1G and 10G ports:
```
configure terminal
stack disable
write memory
```

---

## Test Summary

| Phase | Status | Details |
|-------|--------|---------|
| Connectivity | ✅ Pass | Brocade & ONTI reachable (pre-test) |
| Happy Path | ✅ Pass | All 16 tools tested |
| Edge Cases | ⚠️ Issues Found | 4 bugs identified |
| Security | ⚠️ Issues Found | 1 critical (fixed) |
| Bug Fixes | ✅ Complete | 3 of 4 fixed |

---

## Deployment Checklist

- [x] All core MCP tools functional
- [x] Brocade handler stable with retry logic
- [x] Batch command execution implemented
- [x] Critical bugs fixed (BUG-001, 002, 003)
- [x] Input validation for VLAN operations
- [x] Empty config upload protection
- [ ] Restore ONTI device
- [ ] Verify Zyxel connectivity
- [ ] Production environment testing

---

## Development Commands

```bash
cd /home/emesix/git/switchcraft
source .venv/bin/activate
source .env

# Run tests
pytest -v

# Lint
ruff check src/ tests/

# Run MCP server
python -m mcp_network_switch.server
```
