# MCP Network Switch - Quick Reference

Quick reference card for the MCP Network Switch server. For detailed documentation, see the individual device guides.

---

## Device Inventory Format

**File:** `configs/devices.yaml`

```yaml
defaults:
  password_env: "NETWORK_PASSWORD"
  timeout: 30
  retries: 3
  retry_delay: 2

devices:
  # Brocade FCX
  brocade-core:
    type: brocade
    name: "Core Switch"
    host: 192.168.1.2
    protocol: telnet
    port: 23
    username: admin
    enable_password_required: true

  # Zyxel GS1900
  zyxel-access:
    type: zyxel
    name: "Access Switch"
    host: 192.168.1.3
    protocol: https
    port: 443
    username: admin
    verify_ssl: false

  # ONTI S508CL
  onti-edge:
    type: onti
    name: "Edge Switch"
    host: 192.168.1.4
    protocol: ssh
    port: 22
    username: root
    use_scp_workflow: true
    config_paths:
      network: /etc/config/network
```

---

## Common Operations by Vendor

### Create VLAN

| Brocade | Zyxel | ONTI |
|---------|-------|------|
| `configure terminal` | Web API POST: | `uci add network switch_vlan` |
| `vlan 100 name Servers by port` | `cmd=1285` | `uci set network.@switch_vlan[-1].device='switch0'` |
| `exit` | `vlanlist=100` | `uci set network.@switch_vlan[-1].vlan='100'` |
| | `vlanAction=0` | `uci set network.@switch_vlan[-1].ports='0 1 2 3'` |
| | `name=Servers` | `uci commit network` |

### Add Tagged Port to VLAN

| Brocade | Zyxel | ONTI |
|---------|-------|------|
| `configure terminal` | Web API POST: | Edit ports with `t` suffix: |
| `vlan 100` | `cmd=1294` | `uci set network.@switch_vlan[X].ports='0 1t 2t 3t'` |
| `tagged ethe 1/1/5` | `vid=100` | `uci commit network` |
| `exit` | `port_5=T` | |

### Add Untagged Port to VLAN

| Brocade | Zyxel | ONTI |
|---------|-------|------|
| `configure terminal` | Web API POST: | Edit ports without suffix: |
| `vlan 100` | `cmd=1294` | `uci set network.@switch_vlan[X].ports='0 1 2 3'` |
| `untagged ethe 1/1/5` | `vid=100` | `uci commit network` |
| `exit` | `port_5=U` | |

### Delete VLAN

| Brocade | Zyxel | ONTI |
|---------|-------|------|
| `configure terminal` | Web UI checkbox | Find index: `uci show network \| grep vlan` |
| `no vlan 100` | selection + delete | `uci delete network.@switch_vlan[X]` |
| `exit` | | `uci commit network` |

### Save Configuration

| Brocade | Zyxel | ONTI |
|---------|-------|------|
| `write memory` | Auto-saves | `uci commit network` |
| | | `/etc/init.d/network restart` |

### Show VLANs

| Brocade | Zyxel | ONTI |
|---------|-------|------|
| `show vlan` | SSH: `show vlan` | `uci show network \| grep switch_vlan` |
| `show vlan 100` | Web: `cmd=1282` | `swconfig dev switch0 show` |

---

## Port Naming Conventions

| Vendor | Format | Examples | Notes |
|--------|--------|----------|-------|
| **Brocade** | `unit/module/port` | `1/1/1`, `1/1/24`, `1/2/1` | Module 1 = 1G copper, Module 2 = 10G SFP+ |
| **Zyxel** | `port_number` | `1`, `24`, `25` | Ports 1-24 = copper, 25-26 = SFP |
| **ONTI** | `port_number[t]` | `1`, `2t`, `0` | No suffix = untagged, `t` = tagged, 0/8 = CPU |

### Brocade Port Ranges
```
ethe 1/1/1              # Single port
ethe 1/1/1 to 1/1/8     # Range
ethe 1/1/1 1/1/3 1/1/5  # Non-contiguous
```

### ONTI Port Strings
```
"0 1 2 3"           # All untagged
"0t 1t 2t 3t"       # All tagged
"0 1 2 3 4t 5t"     # Mixed (1-3 untagged, 4-5 tagged)
```

---

## Connection Methods

| Vendor | Protocol | Default Port | Auth Method |
|--------|----------|--------------|-------------|
| **Brocade** | Telnet | 23 | Username + Enable password |
| **Zyxel** | SSH (read) / HTTPS (write) | 22 / 443 | Encoded password + XSSID token |
| **ONTI** | SSH + SCP | 22 | Username/password |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `NETWORK_PASSWORD` | Default device password |
| `ZABBIX_API_TOKEN` | Zabbix integration (optional) |

---

## Quick Commands Reference

### Brocade CLI Mode
```
enable                    # Enter privileged mode
configure terminal        # Enter config mode
exit                      # Exit current mode
skip-page-display         # Disable pagination (required)
```

### Zyxel Web API Codes
| Code | Function |
|------|----------|
| 1282 | VLAN list |
| 1285 | Create VLAN |
| 1294 | VLAN port matrix |
| 768 | Port settings |
| 771 | PoE settings |

### ONTI UCI Commands
```
uci show network          # Show all network config
uci commit network        # Save changes
uci revert network        # Discard changes
/etc/init.d/network restart
```

---

## MCP Tools Summary

| Tool | Purpose | Devices |
|------|---------|---------|
| `list_devices` | List configured devices | All |
| `device_status` | Health check | All |
| `get_config` | Get normalized config | All |
| `get_vlans` | List VLANs | All |
| `get_ports` | List ports | All |
| `create_vlan` | Create/update VLAN | All |
| `delete_vlan` | Remove VLAN | All |
| `configure_port` | Set port options | All |
| `execute_command` | Raw command | All |
| `save_config` | Save to startup | Brocade |
| `diff_config` | Compare configs | All |
| `download_config_file` | SCP download | ONTI |
| `upload_config_file` | SCP upload | ONTI |
| `batch_command` | Multi-device parallel | All |

---

## Emergency Commands

### Disable a port immediately

| Vendor | Command |
|--------|---------|
| **Brocade** | `conf t` → `int e 1/1/X` → `disable` → `write mem` |
| **Zyxel** | Web: Port Settings (cmd=768) → Uncheck Enable |
| **ONTI** | `swconfig dev switch0 port X set enable 0` |

### Revert all changes

| Vendor | Command |
|--------|---------|
| **Brocade** | `reload` (discards unsaved changes) |
| **Zyxel** | Reboot without saving |
| **ONTI** | `uci revert network` (before commit) |

---

## Common Mistakes

| Mistake | Result | Fix |
|---------|--------|-----|
| Forgot `write memory` (Brocade) | Changes lost on reboot | Always `save_config` after changes |
| Port in wrong VLAN | No connectivity | Check PVID, remove from old VLAN first |
| Tagged when should be untagged | Device can't communicate | Access ports = untagged, trunks = tagged |
| VLAN not on trunk | Traffic doesn't pass between switches | Add VLAN as tagged on both ends |
| ONTI: forgot `uci commit` | Changes not applied | Always commit, then restart network |

---

## Useful Show Commands

### Brocade
```
show vlan brief              # Quick VLAN summary
show int brief               # All ports one-liner
show mac-address             # MAC table
show arp                     # ARP table
show span                    # Spanning tree
show log                     # System log
```

### Zyxel (SSH)
```
show vlan                    # VLAN list
show interfaces status       # Port status
show mac address-table       # MAC table
show running-config          # Current config
```

### ONTI (SSH)
```
swconfig dev switch0 show    # Switch ASIC status
uci show network             # UCI network config
cat /etc/config/network      # Raw config file
logread                      # System log
```

---

## File Locations

| Item | Path |
|------|------|
| Device inventory | `configs/devices.yaml` |
| Brocade docs | `docs/brocade-fcx-commands.md` |
| Zyxel docs | `docs/zyxel-gs1900-commands.md` |
| ONTI docs | `docs/onti-s508cl-commands.md` |
| Troubleshooting | `docs/TROUBLESHOOTING.md` |
| MCP Examples | `docs/MCP-EXAMPLES.md` |