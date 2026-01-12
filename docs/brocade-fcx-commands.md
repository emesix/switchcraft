# Brocade FCX624-E Command Reference

**Firmware:** 08.0.30uT7f3 (FCXR08030u.bin)
**Hardware:** FCX624-PREM (FCX-ADV-U)
**Modules:**
- M1: fcx-24-4x-port-management-module (24x 1G copper)
- M2: fcx-sfp-plus-4-port-10g-module (4x 10G SFP+)

## Port Naming Convention

```
1/1/1   = Unit 1 / Module 1 / Port 1 (copper)
1/2/1   = Unit 1 / Module 2 / Port 1 (SFP+)
```

Range syntax: `ethe 1/1/1 to 1/1/4`

## Essential Commands

### Session Management
| Command | Description |
|---------|-------------|
| `enable` | Enter privileged mode (prompts for password) |
| `skip-page-display` | **CRITICAL**: Disable --More-- pagination |
| `configure terminal` | Enter config mode |
| `end` | Exit config mode to privileged |
| `exit` | Exit current mode |
| `write memory` | Save running-config to startup |

### Show Commands

| Command | Output Format | Use For |
|---------|---------------|---------|
| `show vlan` | PORT-VLAN entries with (U1/M1) port lists | Get all VLANs with port membership |
| `show vlan <id>` | Single VLAN details | Check specific VLAN |
| `show running-config` | Full config in CLI format | Backup/review |
| `show running-config vlan` | Just VLAN config blocks | Clean VLAN config |
| `show interfaces brief` | Table: Port, Link, State, Speed, PVID, Tag | Quick port status |
| `show interfaces ethernet <port>` | Detailed port info | Full port details |
| `show chassis` | PSU, Fan, Temp status | Health check |
| `show version` | Firmware, uptime, hardware | System info |
| `show stack` | Stacking status | Stack topology |
| `show mac-address` | MAC table | L2 forwarding |
| `show mac-address vlan <id>` | MAC table filtered by VLAN | L2 forwarding per VLAN |
| `show arp` | ARP table | IP-MAC mappings |

### VLAN Configuration (in config mode)

#### VLAN Creation Syntax

```
! Basic VLAN creation (enters VLAN config context)
vlan <id>

! VLAN with name (recommended)
vlan <id> name <name>

! Full syntax with port-based switching
vlan <id> name <name> by port
```

**VLAN ID Range:** 1-4095 (VLAN 1 is default, VLAN 4095 often reserved)

**VLAN Limits:**
- Layer 2 devices: Up to 1023 port-based VLANs
- Layer 3 devices: Up to 4061 port-based VLANs

**Reserved VLANs (DO NOT USE):**
| VLAN ID | Purpose |
|---------|---------|
| 4087 | Internal use |
| 4090 | Internal use |
| 4093 | Internal use |
| 4094 | Single Spanning Tree (SSTP) |

#### Creating Multiple VLANs at Once

```
! Create VLAN range (each VLAN created individually)
vlan 100 to 110

! Create VLAN range with name prefix
vlan 100 to 110 name DataVLAN

! Create discontinuous VLANs (specific IDs)
vlan 2 4 7

! Mixed: ranges and individual IDs
vlan 2 to 7
vlan 2 4 7 10 to 15
```

**Note:** Range creation creates VLANs 100, 101, 102... 110 separately. Names will be "DataVLAN" for all (or append numbers depending on firmware). Discontinuous syntax allows creating non-sequential VLANs in a single command.

#### Port Membership Commands (inside VLAN context)

```
! Add untagged ports (access mode - port can only be untagged in ONE VLAN)
untagged ethe <port>
untagged ethe <start-port> to <end-port>

! Add tagged ports (trunk mode - port can be tagged in MULTIPLE VLANs)
tagged ethe <port>
tagged ethe <start-port> to <end-port>

! Remove port membership
no untagged ethe <port>
no tagged ethe <port>
```

**Tagged vs Untagged:**
- **Untagged (Access):** Frames enter/exit without 802.1Q tag. Port's PVID set to this VLAN.
- **Tagged (Trunk):** Frames carry 802.1Q VLAN tag. Port can carry multiple VLANs.

#### Multi-VLAN Port Assignment

Assign the same port to multiple VLANs in a single command sequence:

```
! Enter multiple VLAN context, then assign port
vlan 16 17 20 to 24
  tagged ethernet 1/1/1
exit

! This tags port 1/1/1 in VLANs 16, 17, 20, 21, 22, 23, 24
```

**Note:** This is efficient for trunk ports that need membership in many VLANs.

#### VLAN Groups (Bulk Operations)

VLAN groups allow managing up to 256 VLANs as a single entity:

```
! Create VLAN group with range
vlan-group 1 vlan 100 to 200

! Add more VLANs to existing group
vlan-group 1
  add-vlan 201 to 210
exit

! Apply tagged port to entire group
vlan-group 1
  tagged ethe 1/2/1
exit
```

**Use Case:** Efficient trunk port configuration when a port needs membership in many VLANs.

#### Dual-Mode Ports

Dual-mode allows a port to handle both tagged and untagged traffic simultaneously:

```
! Inside VLAN context - set dual-mode with native VLAN
vlan 100
  tagged ethe 1/1/5
  dual-mode 100
exit

! Port 1/1/5 will:
! - Accept untagged frames and assign them to VLAN 100 (native)
! - Accept tagged frames for any VLAN it's a member of
! - Send frames for VLAN 100 untagged, others tagged
```

**Use Case:** Connecting devices that send both tagged and untagged traffic on the same port (e.g., IP phones with attached PCs, or mixed environments).

#### Voice VLAN

Dedicated VLAN for VoIP traffic with automatic phone detection:

```
! Create voice VLAN
voice-vlan vlan-id 50
voice-vlan enable

! Configure ports for voice
interface ethernet 1/1/1 to 1/1/24
  voice-vlan enable
exit
```

**Note:** Voice VLAN works with LLDP-MED for automatic phone VLAN assignment.

#### Spanning Tree Configuration (per VLAN)

Configure spanning tree within VLAN context:

```
! Enable/disable spanning tree for a VLAN
vlan 100
  spanning-tree
exit

! Disable spanning tree for a VLAN
vlan 100
  no spanning-tree
exit

! Spanning tree is typically Off by default for VLANs
```

**Note:** Per-VLAN spanning tree helps prevent loops within specific VLANs. Check `show vlan` output for current spanning tree state.

#### Complete VLAN Examples

```
! Example 1: Simple access VLAN
configure terminal
vlan 100 name Servers by port
  untagged ethe 1/1/1 to 1/1/8
exit
write memory

! Example 2: VLAN with trunk uplinks
configure terminal
vlan 200 name Users by port
  untagged ethe 1/1/9 to 1/1/16
  tagged ethe 1/2/1 to 1/2/2
exit
write memory

! Example 3: VLAN with L3 interface (for routing)
configure terminal
vlan 254 name Management by port
  tagged ethe 1/2/1 to 1/2/2
  untagged ethe 1/1/1 to 1/1/4
  router-interface ve 254
exit
write memory

! Example 4: Creating multiple VLANs for MCP batch operations
configure terminal
vlan 10 name VLAN10 by port
exit
vlan 20 name VLAN20 by port
exit
vlan 30 name VLAN30 by port
exit
write memory
```

#### VLAN Deletion

```
! Delete entire VLAN (removes all port associations)
no vlan <id>

! Delete VLAN range
no vlan <start-id> to <end-id>
```

**Warning:** Deleting a VLAN moves its untagged ports back to default VLAN 1.

### Port Configuration (in config mode)

```
interface ethernet <port>
  port-name <description>
  enable
  disable
  speed-duplex <setting>
exit
```

#### Port Enable/Disable

```
! Enter interface context
interface ethernet 1/1/5

! Disable port (administratively down)
disable

! Enable port (administratively up)
enable

! Can also disable range from config mode
interface ethernet 1/1/1 to 1/1/4
  disable
exit
```

**Port States:**
- `enable` - Port is administratively up (default state)
- `disable` - Port is administratively down, no traffic passes

### Speed-Duplex Configuration

#### Copper Ports (Module 1: 1/1/x)

```
interface ethernet 1/1/1
  speed-duplex <option>
exit
```

**Speed-Duplex Options for 1G Copper:**

| Option | Speed | Duplex | Use Case |
|--------|-------|--------|----------|
| `10-full` | 10 Mbps | Full | Legacy devices |
| `10-half` | 10 Mbps | Half | Very old devices |
| `100-full` | 100 Mbps | Full | Fast Ethernet devices |
| `100-half` | 100 Mbps | Half | Legacy Fast Ethernet |
| `1000-full` | 1 Gbps | Full | Standard gigabit |
| `auto` | Auto-negotiate | Auto | **Default - recommended** |

**Example:**
```
configure terminal
interface ethernet 1/1/10
  speed-duplex 100-full
exit
write memory
```

#### SFP+ Ports (Module 2: 1/2/x)

SFP+ ports (10G) typically auto-negotiate based on inserted optic:
- 10G SFP+ modules run at 10 Gbps
- 1G SFP modules run at 1 Gbps (if supported)

```
! Force 10G (usually not needed)
interface ethernet 1/2/1
  speed-duplex 10g-full
exit
```

**Note:** Speed-duplex on SFP+ ports depends on optic type. Most configurations use auto.

#### Resetting to Default

```
interface ethernet 1/1/5
  no speed-duplex
exit
```

This returns the port to auto-negotiation (default).

### Port Configuration Examples for MCP

```
! Example 1: Configure access port with specific speed
configure terminal
interface ethernet 1/1/10
  port-name "Legacy-Server"
  speed-duplex 100-full
  enable
exit
write memory

! Example 2: Disable unused ports (security best practice)
configure terminal
interface ethernet 1/1/20 to 1/1/24
  disable
exit
write memory

! Example 3: Enable port and set to auto
configure terminal
interface ethernet 1/1/15
  enable
  speed-duplex auto
exit
write memory
```

### Delete Commands

```
no vlan <id>                    ! Delete VLAN
no tagged ethe <port>           ! Remove tagged membership (in vlan context)
no untagged ethe <port>         ! Remove untagged membership (in vlan context)
no speed-duplex                 ! Reset to auto-negotiate (in interface context)
no port-name                    ! Remove port description (in interface context)
```

## Output Parsing Notes

### show vlan output format
```
PORT-VLAN 254, Name Management, Priority level0, Spanning tree Off
 Untagged Ports: (U1/M1)   1   2   3   4
   Tagged Ports: (U1/M2)   1   2
   Uplink Ports: None
 DualMode Ports: None
 Mac-Vlan Ports: None
     Monitoring: Disabled
```

- `(U1/M1)` = Unit 1, Module 1 - convert ports to `1/1/<port>`
- `(U1/M2)` = Unit 1, Module 2 - convert ports to `1/2/<port>`

### show interfaces brief output format
```
Port       Link    State   Dupl Speed Trunk Tag Pvid Pri MAC             Name
1/1/1      Down    None    None None  None  No  254  0   748e.f87d.cf80
1/2/2      Up      Forward Full 10G   None  Yes N/A  0   748e.f87d.cf80
```

- `Link`: Up/Down
- `State`: Forward/Blocking/None
- `Tag`: Yes = trunk port, No = access port
- `Pvid`: Native/access VLAN (N/A for trunk-only)

### show running-config vlan format
```
vlan 254 name Management by port
 tagged ethe 1/2/1 to 1/2/2
 untagged ethe 1/1/1 to 1/1/4
 router-interface ve 254
!
```

This is the cleanest format for parsing VLAN config.

## Error Messages

| Error | Meaning |
|-------|---------|
| `Unrecognized command` | Command doesn't exist or wrong syntax |
| `Invalid input` | Parameter error |
| `Port is tagged in VLAN x` | Must untag before changing |

## Current Hardware Status

```
Power supply 1: OK (Fan BAD!)
Power supply 2: Not present
Temperature: 69°C (warning: 85°C, shutdown: 90°C)
Active ports: 1/2/2 (10G link up)
```

**Warning**: PSU fan has failed - monitor temperature closely!
