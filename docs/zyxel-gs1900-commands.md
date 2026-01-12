# Zyxel GS1900-24HP Command Reference

**Firmware:** V2.70(AAHM.3) | 07/26/2022
**Hardware:** GS1900-24HP (24-port PoE Gigabit + 2x SFP)
**Serial:** S142L03000081

## Configuration Limits

| Feature | Limit |
|---------|-------|
| VLANs (802.1Q) | 256 |
| VLAN ID Range | 1-4094 |
| MSTP Instances | 16 |
| Link Aggregation Groups | 8 |
| Ports per LAG | 8 |
| MAC Address Table | 16K entries |
| Static MAC Entries | 256 |
| Port Security MACs/Port | 64 |
| IGMP Snooping Groups | 256 |
| Voice VLAN OUI Entries | 10 |
| 802.1X Clients | 48 |
| ACL Rules | 256 |
| PoE Budget (24HP model) | 170W |

## Interface Overview

The Zyxel GS1900 has two interfaces:

### 1. SSH CLI (Read-Only)
- Port: 22
- Username: admin
- Limited to `show` commands and ping/traceroute
- No configuration capability

### 2. Web Interface (Full Control)
- Port: 80 (HTTP) or 443 (HTTPS)
- CGI-based: `/cgi-bin/dispatcher.cgi?cmd=<num>`
- Requires JavaScript-encoded login

## SSH CLI Commands

### Basic Commands
```
show vlan              - VLAN list with port assignments
show interfaces <port> - Port details (speed, duplex, status)
show running-config    - Current config (full)
show mac address-table - MAC address table
show arp               - ARP table
show version           - Firmware version and system uptime
ping <host>            - Ping
traceroute <host>      - Traceroute
```

### VLAN Output Format
```
  VID  |     VLAN Name    |        Untagged Ports        |        Tagged Ports          |  Type
-------+------------------+------------------------------+------------------------------+---------
     1 |          default |                  1-26,lag1-8 |                          --- | Default
   254 |       Management |                           25 |                         1-24 | Static
```

### Port List Notation
- Individual ports: `1,2,3`
- Port ranges: `1-4` (expands to 1,2,3,4)
- LAG ports: `lag1-8`
- Combined: `1-4,7,10-12,lag1-2`
- Empty/none: `---`

## Web Interface Authentication

### Login Flow

1. **Encode password** using Zyxel's obfuscation:
   - 321 - len(password) character string
   - Password chars placed at every 5th position (reversed)
   - Position 123: tens digit of password length
   - Position 289: ones digit of password length
   - Other positions: random alphanumeric

2. **POST** to `/cgi-bin/dispatcher.cgi`:
   ```
   username=<user>&password=<encoded>&login=true;
   ```
   Returns: `authId` (32-char hex string)

3. **POST** login check:
   ```
   authId=<authId>&login_chk=true
   ```
   Returns: `OK,` on success

4. **Session established** - access pages via `cmd=` parameter

### XSSID Token

All forms require an `XSSID` hidden field for CSRF protection.
Extract from page and include in all POST requests.

## Page Map (cmd values)

### Main Navigation
| cmd | Page |
|-----|------|
| 1 | Main frameset |
| 3 | Top menu |
| 27 | Configuration menu tree |
| 26 | Monitor menu tree |
| 28 | Maintenance menu tree |

### System Configuration
| cmd | Page | Submit cmd |
|-----|------|------------|
| 512 | System Information | 513 |
| 516 | IP Settings | 517 |
| 558 | Time Settings | 559 |
| 525 | Users | 526 |
| 544 | HTTP/HTTPS | 545 |
| 548 | Telnet/SSH | 549 |

### Port Configuration
| cmd | Page | Submit cmd |
|-----|------|------------|
| 768 | Port Settings | 769 |
| 771 | PoE Settings | 772 |
| 3072 | EEE (Energy Efficient Ethernet) | 3073 |
| 3334 | Bandwidth Management | 3335 |
| 3328 | Storm Control | 3329 |

### VLAN Configuration
| cmd | Page | Submit cmd | Notes |
|-----|------|------------|-------|
| 1282 | VLAN Main | - | Shows tabs |
| 1283 | VLAN List (AJAX) | - | List of VLANs |
| 1284 | Add VLAN | 1285 | Create new VLAN |
| 1286 | Edit VLAN | 1287 | Modify existing |
| 1290 | Port VLAN Settings | 1291 | Port assignments |
| 1293 | VLAN Port Matrix | 1294 | Tagged/Untagged |
| 1299 | Voice VLAN | 1300 | |
| 3847 | Guest VLAN | 3848 | |

### Layer 2 Features
| cmd | Page | Submit cmd |
|-----|------|------------|
| 1024 | Link Aggregation (LAG) | 1025 |
| 1792 | Mirror | 1793 |
| 2055 | MAC Table | 2056 |
| 4096 | Spanning Tree | 4097 |
| 4352 | LLDP | 4353 |
| 5376 | Loop Guard | 5377 |

### QoS
| cmd | Page | Submit cmd |
|-----|------|------------|
| 2306 | QoS General | 2307 |
| 2317 | Trust Mode | 2318 |

### Security
| cmd | Page | Submit cmd |
|-----|------|------------|
| 780 | Port Security | 781 |
| 786 | Port Isolation | 787 |
| 3840 | 802.1X | 3841 |
| 4608 | DoS Protection | 4609 |

### AAA
| cmd | Page | Submit cmd |
|-----|------|------------|
| 4864 | Auth Method | 4865 |
| 5121 | RADIUS | 5122 |
| 4887 | TACACS+ | 4888 |

### Management
| cmd | Page | Submit cmd |
|-----|------|------------|
| 2560 | SNMP | 2561 |
| 3584 | Syslog | 3585 |
| 791 | Error Disable | 792 |
| 6144 | Remote Access Control | 6145 |

## Form Parameters

### Add VLAN (cmd=1284)
```
XSSID=<token>
vlanlist=<vlan_id>         # e.g., "254"
vlanAction=0               # 0=create
name=<vlan_name>           # e.g., "Management"
cmd=1285
sysSubmit=Apply
```

### Edit VLAN (cmd=1286)
```
XSSID=<token>
vidValue=<vlan_id>
editName=<new_name>
cmd=1287
sysSubmit=Apply
```

### IP Settings (cmd=516)
```
XSSID=<token>
mode=1                     # 0=DHCP, 1=Static
ip=192.168.254.3
netmask=255.255.255.0
gateway=192.168.254.1
dns1=192.168.254.1
dns2=0.0.0.0
management_vlan=1          # Management VLAN ID
cmd=517
```

### Port VLAN Settings (cmd=1290)
```
XSSID=<token>
selall=                    # Select all checkbox
port=1                     # Port checkbox (repeat for each)
port=2
...
cmd=1291
```

### Port Settings (cmd=768)
```
XSSID=<token>
port_id=1                  # Port number
state=1                    # 0=Disabled, 1=Enabled
speed=0                    # 0=Auto, 1=10M, 2=100M, 3=1G
duplex=0                   # 0=Auto, 1=Half, 2=Full
flow_control=0             # 0=Off, 1=On
cmd=769
sysSubmit=Apply
```

### PoE Settings (cmd=771)
```
XSSID=<token>
port_id=1                  # Port number (1-24 for PoE ports)
poe_state=1                # 0=Disabled, 1=Enabled
poe_priority=2             # 0=Critical, 1=High, 2=Low
poe_power_limit=0          # 0=Auto, or watts (max 30)
cmd=772
sysSubmit=Apply
```

### VLAN Port Matrix (cmd=1293)
```
XSSID=<token>
vid=<vlan_id>              # VLAN ID to configure
port_1=U                   # U=Untagged, T=Tagged, -=Excluded
port_2=T
port_3=-
...
port_26=-
lag_1=-                    # LAG ports
...
lag_8=-
cmd=1294
sysSubmit=Apply
```

## Python Implementation Notes

### Password Encoding
```python
def zyxel_encode(pwd):
    text = ""
    possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    pwd_len = len(pwd)
    char_idx = pwd_len

    for i in range(1, 322 - pwd_len):
        if i % 5 == 0 and char_idx > 0:
            char_idx -= 1
            text += pwd[char_idx]
        elif i == 123:
            text += "0" if pwd_len < 10 else str(pwd_len // 10)
        elif i == 289:
            text += str(pwd_len % 10)
        else:
            text += random.choice(possible)
    return text
```

### Session Management
1. Login returns 32-char authId
2. Store authId for session validation
3. Extract XSSID from each page before submitting forms
4. Session timeout: ~30 minutes

### Reading VLAN Data via SSH
```python
# SSH CLI gives clean VLAN output
ssh.exec_command("show vlan")
# Output:
#   VID  |     VLAN Name    |        Untagged Ports        |        Tagged Ports          |  Type
# -------+------------------+------------------------------+------------------------------+---------
#      1 |          default |                  1-26,lag1-8 |                          --- | Default
```

## Recommended Approach

**For reading config:** Use SSH CLI - it's fast, reliable, and gives clean output.

**For writing config:** Use Web interface with proper XSSID handling.

This hybrid approach provides the best of both worlds:
- SSH for quick status checks and monitoring
- Web for configuration changes with proper form validation
