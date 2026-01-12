# ONTI S508CL Command Reference

**Firmware:** OpenWRT-based
**Hardware:** ONTI S508CL 8-port Managed Switch
**Config System:** UCI (Unified Configuration Interface)

## Device Overview

The ONTI S508CL runs OpenWRT, a Linux-based embedded operating system. Configuration is managed through the UCI system, which stores settings in plain-text files and provides a command-line interface for modifications.

Key characteristics:
- Linux shell access via SSH
- UCI-based configuration (not traditional switch CLI)
- Config files stored in `/etc/config/`
- Changes require explicit commit and service restart

## Connection Info

| Parameter | Value |
|-----------|-------|
| Protocol | SSH |
| Port | 22 |
| Shell | BusyBox ash |

```
ssh root@<switch-ip>
```

## Port Naming Convention

```
Port 0   = CPU port (connects switch ASIC to Linux stack)
Port 1-7 = Physical ports 1-7
Port 8   = CPU port (alternate, device-dependent)
```

**Tagging Suffix:**
- `0` = Untagged (access mode)
- `0t` = Tagged (trunk mode)

**Example port strings:**
```
"0 1 2 3 8t"     = Ports 0-3 untagged, CPU port tagged
"4t 5t 6t 8t"    = Ports 4-6 tagged, CPU port tagged
"0 1 2 3 4 5 6 7 8" = All ports untagged including CPU
```

**Note:** The CPU port (usually port 0 or 8) must be included for the VLAN to be accessible to the switch's management interface. Use tagged (`0t`) for trunk VLANs, untagged (`0`) for the management VLAN.

## Config File Paths

| File | Purpose |
|------|---------|
| `/etc/config/network` | Network interfaces, switch, VLANs |
| `/etc/config/system` | Hostname, timezone, logging |
| `/etc/config/firewall` | Firewall rules, zones |
| `/etc/config/wireless` | WiFi configuration (if applicable) |

## UCI Commands

### Viewing Configuration

```bash
# Show entire network config
uci show network

# Show all switch_vlan entries
uci show network | grep switch_vlan

# Show specific VLAN entry
uci show network.@switch_vlan[0]

# Export config in file format
uci export network
```

### Creating a VLAN

```bash
# Add new switch_vlan section
uci add network switch_vlan

# Configure the new VLAN (use index returned by add, or find it)
uci set network.@switch_vlan[-1].device='switch0'
uci set network.@switch_vlan[-1].vlan='100'
uci set network.@switch_vlan[-1].ports='0 1 2 3 8t'

# Commit changes to file
uci commit network

# Restart network to apply
/etc/init.d/network restart
```

**UCI Index Notes:**
- `@switch_vlan[-1]` = Last switch_vlan entry (just added)
- `@switch_vlan[0]` = First switch_vlan entry
- `@switch_vlan[2]` = Third switch_vlan entry (0-indexed)

### Modifying a VLAN

```bash
# Find the VLAN you want to modify
uci show network | grep switch_vlan

# Modify ports (example: add port 4 tagged)
uci set network.@switch_vlan[2].ports='0 1 2 3 4t 8t'

# Commit and restart
uci commit network
/etc/init.d/network restart
```

### Deleting a VLAN

```bash
# Find the index of the VLAN to delete
uci show network | grep switch_vlan

# Delete by index
uci delete network.@switch_vlan[2]

# Commit and restart
uci commit network
/etc/init.d/network restart
```

**Warning:** Deleting a VLAN by index shifts all subsequent indices. Always re-check indices after deletion before making additional changes.

### UCI Command Reference

| Command | Description |
|---------|-------------|
| `uci show <config>` | Display config in uci format |
| `uci export <config>` | Display config in file format |
| `uci get <option>` | Get single value |
| `uci set <option>=<value>` | Set value |
| `uci add <config> <type>` | Add new section |
| `uci delete <option>` | Delete option or section |
| `uci commit [config]` | Save changes to file |
| `uci revert <config>` | Discard uncommitted changes |
| `uci changes` | Show pending changes |

## SCP Workflow

For bulk changes or backup/restore, use SCP to transfer config files:

### Download Config

```bash
# From local machine
scp root@<switch-ip>:/etc/config/network ./network.backup
```

### Edit Locally

Edit the file with your preferred editor. The format is:

```
config switch_vlan
    option device 'switch0'
    option vlan '100'
    option ports '0 1 2 3 8t'

config switch_vlan
    option device 'switch0'
    option vlan '200'
    option ports '4t 5t 6t 7t 8t'
```

### Upload Config

```bash
# Upload modified config
scp ./network.modified root@<switch-ip>:/etc/config/network
```

### Reload Configuration

```bash
# SSH to switch and reload
ssh root@<switch-ip> "/etc/init.d/network reload"

# Or restart for full reset
ssh root@<switch-ip> "/etc/init.d/network restart"
```

## Useful Shell Commands

### System Information

```bash
# Show OpenWRT version
cat /etc/openwrt_release

# System uptime
uptime

# Memory usage
free

# Disk usage
df -h
```

### Switch ASIC Status

```bash
# Show switch configuration and port status
swconfig dev switch0 show

# Show VLAN table
swconfig dev switch0 vlan 100 show

# Show port status
swconfig dev switch0 port 1 show
```

### Network Status

```bash
# Show IP addresses
ip addr

# Show routing table
ip route

# Show bridge status
brctl show

# Show active connections
netstat -tuln
```

### Service Control

```bash
# Restart network (applies all changes)
/etc/init.d/network restart

# Reload network (lighter reload)
/etc/init.d/network reload

# Restart firewall
/etc/init.d/firewall restart
```

## Complete Examples

### Example 1: Creating a VLAN with Tagged and Untagged Ports

Create VLAN 100 with:
- Ports 1-4 untagged (access ports)
- Ports 5-6 tagged (trunk ports)
- CPU port tagged (for routing/management)

```bash
# Add the VLAN
uci add network switch_vlan

# Configure it
uci set network.@switch_vlan[-1].device='switch0'
uci set network.@switch_vlan[-1].vlan='100'
uci set network.@switch_vlan[-1].ports='1 2 3 4 5t 6t 0t'

# Optionally add a description/name (if supported)
uci set network.@switch_vlan[-1].description='Server-VLAN'

# Save and apply
uci commit network
/etc/init.d/network restart

# Verify
swconfig dev switch0 show | grep -A5 "VLAN 100"
```

### Example 2: Deleting a VLAN

Remove VLAN 100 from the configuration:

```bash
# First, find the VLAN's index
uci show network | grep -n "vlan='100'"

# Example output:
# network.@switch_vlan[3].vlan='100'

# Delete by index
uci delete network.@switch_vlan[3]

# Commit and restart
uci commit network
/etc/init.d/network restart

# Verify deletion
uci show network | grep switch_vlan
```

### Example 3: Modifying Port Membership

Add port 7 as tagged to existing VLAN 100:

```bash
# Find the VLAN index
uci show network | grep "vlan='100'"
# Output: network.@switch_vlan[2].vlan='100'

# Get current ports
uci get network.@switch_vlan[2].ports
# Output: 1 2 3 4 5t 6t 0t

# Update ports to include port 7 tagged
uci set network.@switch_vlan[2].ports='1 2 3 4 5t 6t 7t 0t'

# Commit and restart
uci commit network
/etc/init.d/network restart
```

## Network Config File Format

The `/etc/config/network` file structure:

```
config interface 'loopback'
    option ifname 'lo'
    option proto 'static'
    option ipaddr '127.0.0.1'
    option netmask '255.0.0.0'

config interface 'lan'
    option type 'bridge'
    option ifname 'eth0.1'
    option proto 'static'
    option ipaddr '192.168.1.1'
    option netmask '255.255.255.0'

config switch
    option name 'switch0'
    option reset '1'
    option enable_vlan '1'

config switch_vlan
    option device 'switch0'
    option vlan '1'
    option ports '0 1 2 3 4 5 6 7'

config switch_vlan
    option device 'switch0'
    option vlan '100'
    option ports '1 2 3 4 5t 6t 0t'
```

## Error Recovery

### Revert Uncommitted Changes

```bash
# Discard all pending changes
uci revert network

# Check for pending changes
uci changes
```

### Factory Reset Network

```bash
# Restore default network config (if backup exists)
cp /rom/etc/config/network /etc/config/network
/etc/init.d/network restart
```

### Emergency Access

If network config breaks SSH access:
1. Connect via serial console (if available)
2. Use failsafe mode (hold reset during boot)
3. Restore from `/rom/etc/config/` defaults

## Output Parsing Notes

### uci show output format

```
network.@switch_vlan[0].device='switch0'
network.@switch_vlan[0].vlan='1'
network.@switch_vlan[0].ports='0 1 2 3 4 5 6 7'
network.@switch_vlan[1].device='switch0'
network.@switch_vlan[1].vlan='100'
network.@switch_vlan[1].ports='1 2 3 4 5t 6t 0t'
```

- `@switch_vlan[N]` = Anonymous section index
- Parse VLAN ID from `.vlan=` value
- Parse ports from `.ports=` value (space-separated, `t` suffix = tagged)

### swconfig dev switch0 show output format

```
VLAN 100:
    vid: 100
    ports: 1 2 3 4 5t 6t 0t
```

Use this to verify the actual switch ASIC configuration matches UCI config.
