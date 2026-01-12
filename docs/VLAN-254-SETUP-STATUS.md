# VLAN 254 Setup Status

## Physical Topology

```
Internet
    │
    ▼
┌─────────────┐
│  OPNsense   │ 192.168.254.1 (Gateway)
│   Gateway   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Dumb Switch │  (Unmanaged - passes all traffic)
│             │  ◄── You are connected here
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────┐
│      Brocade FCX624-E Core          │ 192.168.254.2
│                                     │
│  Module 1 (M1): 24x 1G Copper       │
│  Module 2 (M2): 4x 10G SFP+         │
│                                     │
│  Management: Eth mgmt1 (separate)   │
└──────┬─────────────────────┬────────┘
       │ 1/2/1               │ 1/2/2
       │ (to Dumb Switch)    │
       │                     ▼
       │              ┌─────────────────────────────┐
       │              │   ONTI S508CL-8S Backend    │ 192.168.254.4
       │              │                             │
       │              │   lan1 ◄── from Brocade     │
       │              │   lan8 ──► to Zyxel         │
       │              └──────────────────┬──────────┘
       │                                 │ lan8
       │                                 ▼
       │              ┌─────────────────────────────┐
       │              │  Zyxel GS1900-24HP Frontend │ 192.168.254.3
       │              │                             │
       │              │   Port 26 ◄── from ONTI     │
       │              │   Ports 1-24: Copper        │
       │              │   Ports 25-26: SFP          │
       └──────────────┴─────────────────────────────┘
```

## Current VLAN Configuration

### Brocade Core (192.168.254.2)

| VLAN | Name | Untagged Ports | Tagged Ports | Dual-Mode |
|------|------|----------------|--------------|-----------|
| 1 | DEFAULT-VLAN | 1/1/9-24, 1/2/3-4 | - | 1/2/1, 1/2/2 |
| 254 | Management | 1/1/1-8 | 1/2/1, 1/2/2 | - |

**Port 1/2/1 and 1/2/2 Configuration:**
- Dual-mode with VLAN 1 as native
- Sends/receives untagged frames on VLAN 1
- Sends/receives tagged frames on VLAN 254

### Zyxel Frontend (192.168.254.3)

| VLAN | Name | Untagged Ports | Tagged Ports |
|------|------|----------------|--------------|
| 1 | default | 5-24, lag1-8 | - |
| 254 | Management | 1-4 | 25, 26 |

**Management IP:** On VLAN 1 (default)

### ONTI Backend (192.168.254.4)

UCI Configuration:
```
network.lan_vlan=bridge-vlan
network.lan_vlan.device='switch'
network.lan_vlan.vlan='1'
network.lan_vlan.ports='lan1 lan2 lan3 lan4 lan5 lan6 lan7 lan8'

network.mgmt_vlan=bridge-vlan
network.mgmt_vlan.device='switch'
network.mgmt_vlan.vlan='254'
network.mgmt_vlan.ports='lan1:t lan2:t lan7:t lan8:t'
```

| VLAN | Ports |
|------|-------|
| 1 | lan1-8 (all untagged) |
| 254 | lan1:t, lan2:t, lan7:t, lan8:t (tagged) |

**Management IP:** 192.168.254.4 on switch.1 (VLAN 1 interface)

## Current Issues

### Problem: Brocade 1/2/2 → ONTI lan1 Link Not Passing Management Traffic

**Symptoms:**
- Brocade port 1/2/2 shows UP, traffic flowing (64K packets in)
- ONTI not responding to pings from user network
- Zyxel also unreachable (depends on ONTI path)

**Brocade Side (Verified Working):**
```
10GigabitEthernet1/2/2 is up, line protocol is up
Member of 2 L2 VLANs, port is dual mode in Vlan 1, port state is FORWARDING
64884 packets input, 8083 packets output
```

**Suspected ONTI Issue:**
- Bridge VLAN filtering may not be correctly passing VLAN 1 traffic to switch.1 interface
- The `/etc/init.d/network reload` may have enabled strict VLAN filtering
- Management interface (switch.1) may not be receiving untagged VLAN 1 frames

## What Was Changed

### Brocade
1. Added VLAN 254 "Management" with access ports 1/1/1-8
2. Configured 1/2/1 and 1/2/2 as dual-mode (VLAN 1 native + VLAN 254 tagged)
3. Configuration saved to startup-config

### Zyxel
1. Created VLAN 254 "Management"
2. Ports 1-4: Untagged (access) on VLAN 254
3. Ports 25-26: Tagged (trunk) on VLAN 254
4. Auto-saved

### ONTI
1. Created mgmt_vlan (VLAN 254) with ports lan1:t, lan2:t, lan7:t, lan8:t
2. Ran `/etc/init.d/network reload`

## Required Fix

The ONTI needs to properly pass VLAN 1 untagged traffic to its management interface. Options:

### Option A: Check ONTI Bridge VLAN Filtering
- Verify bridge vlan_filtering is working correctly
- Check if switch.1 interface is receiving traffic
- May need to adjust bridge-vlan configuration

### Option B: Simplify ONTI Config
- Remove VLAN filtering on ONTI entirely
- Let it act as a simple bridge for management traffic
- Only use VLAN 254 tagged on specific ports

### Option C: Change Management to VLAN 254
- Move all management IPs to VLAN 254
- Configure VLAN 254 as the management VLAN on all devices
- Use VLAN 1 only for legacy/default traffic

## Link Requirements Summary

| Link | Port A | Port B | VLAN 1 | VLAN 254 |
|------|--------|--------|--------|----------|
| Dumb Switch → Brocade | - | 1/2/1 | Untagged (native) | Tagged |
| Brocade → ONTI | 1/2/2 | lan1 | Untagged (native) | Tagged |
| ONTI → Zyxel | lan8 | Port 26 | ? | Tagged |

## Next Steps

1. Reconnect temporary path via Zyxel to access ONTI
2. Investigate ONTI bridge VLAN configuration
3. Decide on management VLAN strategy (VLAN 1 or VLAN 254)
4. Fix ONTI configuration to pass management traffic
5. Test full path connectivity
6. Save all configurations
