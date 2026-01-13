# HIL (Hardware-in-the-Loop) Testing

This document describes Switchcraft's HIL testing system for validating network switch operations against real hardware.

## Overview

HIL testing validates that Switchcraft correctly interacts with physical network switches by:

1. **Server-enforced constraints** - Only VLAN 999 and designated ports can be modified
2. **Full lifecycle testing** - Snapshot → Apply → Verify → Cleanup → Validate
3. **Multi-device coverage** - Tests run across all three lab devices (192.168.254.2-4)
4. **Artifact generation** - Each test run produces evidence for auditing

## Safety First: The VLAN 999 Rule

**In HIL mode, the ONLY permitted VLAN is 999.**

This is enforced at the server level. Any attempt to create, modify, or delete VLANs other than 999 will be rejected with `HILConstraintError`.

```
HIL CONSTRAINT VIOLATION [HIL_VLAN_ONLY]: Only VLAN 999 operations permitted in HIL mode. Attempted: VLAN 100
```

### Why VLAN 999?

- **High range (900-999)** - Compatible with older hardware that may have reserved lower VLANs
- **Unlikely conflict** - Production networks rarely use this range
- **Single VLAN** - Simplifies testing and auditing; reduces risk of cascading errors

## Lab Device Inventory

| Device ID | IP Address | Hardware | Ports Used |
|-----------|------------|----------|------------|
| lab-brocade | 192.168.254.2 | Brocade FCX624-E | 1/1/23 (access), 1/1/24 (trunk) |
| lab-zyxel | 192.168.254.3 | Zyxel GS1900-24HP | 23 (access), 24 (trunk) |
| lab-openwrt | 192.168.254.4 | ONTI S508CL + OpenWrt | lan7 (access), lan8 (trunk) |

**Only these devices and ports are permitted in HIL mode.**

## Running HIL Tests

### Prerequisites

```bash
# Activate virtual environment
source .venv/bin/activate

# Load credentials
source .env

# Verify connectivity
ping -c1 192.168.254.2
ping -c1 192.168.254.3
ping -c1 192.168.254.4
```

### Run All Tests

```bash
make hil
```

This runs the full lifecycle test on all three devices. The test **fails if ANY device fails**, including cleanup.

### Run Single Device

```bash
make hil-brocade    # Test only Brocade (254.2)
make hil-zyxel      # Test only Zyxel (254.3)
make hil-openwrt    # Test only OpenWrt (254.4)
```

### View Results

```bash
make hil-report     # Show last HIL report
ls artifacts/hil/   # List all test runs
```

## Test Lifecycle Stages

Each device goes through these stages:

### 1. SNAPSHOT

- Read current VLANs and port configurations
- Record if VLAN 999 already exists
- Save as `artifacts/hil/<timestamp>/<device>/pre.json`

### 2. APPLY

- Create VLAN 999 (name: "HIL-TEST-999")
- Add access port (untagged) to VLAN 999
- Add trunk port (tagged) to VLAN 999
- Save configuration

### 3. VERIFY

- Re-read VLANs and confirm VLAN 999 exists
- Confirm access port is untagged member
- Confirm trunk port is tagged member
- Save as `post.json`

### 4. IDEMPOTENT

- Apply the same changes again
- Verify no errors (operations should be no-op)
- This validates idempotency of the handler

### 5. CLEANUP

- If VLAN 999 didn't exist before, delete it
- Restore ports to original state
- Save configuration

### 6. VALIDATE

- Verify VLAN 999 state matches pre-test state
- If it didn't exist before, confirm it's gone
- Save as `clean.json`

## Artifacts

Each HIL run produces:

```
artifacts/hil/
└── 20260113-143052/           # Timestamp
    ├── hil-report.json        # Overall results
    ├── lab-brocade/
    │   ├── pre.json           # Pre-test snapshot
    │   ├── post.json          # Post-apply verification
    │   └── clean.json         # Post-cleanup validation
    ├── lab-zyxel/
    │   └── ...
    └── lab-openwrt/
        └── ...
```

### Report Format

```json
{
  "timestamp": "20260113-143052",
  "success": true,
  "vlan_id": 999,
  "summary": {
    "total_devices": 3,
    "passed": 3,
    "failed": 0
  },
  "devices": [
    {
      "device_id": "lab-brocade",
      "host": "192.168.254.2",
      "success": true,
      "stages": [
        {"stage": "snapshot", "success": true, "duration_ms": 1234},
        {"stage": "apply", "success": true, "duration_ms": 2345},
        ...
      ]
    },
    ...
  ]
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SWITCHCRAFT_HIL_MODE` | `0` | Set to `1` to enable HIL constraints |
| `SWITCHCRAFT_HIL_VLAN` | `999` | Override test VLAN (not recommended) |
| `SWITCHCRAFT_HIL_ALLOWED_DEVICES` | `192.168.254.2,3,4` | Comma-separated allowed IPs |
| `NETWORK_PASSWORD` | (required) | Device credentials |

## Configuration Files

### `tests/hil_spec.yaml`

Defines the test VLAN and per-device port allocations:

```yaml
vlan_id: 999
vlan_name: "HIL-TEST-999"

devices:
  lab-brocade:
    host: 192.168.254.2
    access_port: "1/1/23"
    trunk_port: "1/1/24"
  # ...

constraints:
  allowed_vlans: [999]
  protected_vlans: [1, 254]
  max_ports_per_device: 2
```

### `configs/devices.lab.yaml`

Lab device connection details:

```yaml
devices:
  lab-brocade:
    name: "Brocade FCX624-E Core Switch"
    host: 192.168.254.2
    type: brocade
    protocol: telnet
    port: 23
  # ...
```

## Safety Constraints

HIL mode enforces:

1. **VLAN restriction** - Only VLAN 999 operations permitted
2. **Device restriction** - Only 192.168.254.2, .3, .4 allowed
3. **Port restriction** - Only designated ports per device
4. **Protected VLANs** - Cannot modify VLAN 1 or 254
5. **Port limit** - Maximum 2 ports per device
6. **No STP changes** - Spanning tree configuration locked
7. **No LAG changes** - Link aggregation locked
8. **No routing changes** - L3 configuration locked

---

# Ralph Loop Integration

## For Autonomous Development Loops

When using Ralph Loop or similar autonomous development workflows, HIL testing provides the safety net that ensures code changes don't break real hardware.

### Required Loop Instructions

Add these to your loop prompt or `ralph/plan.md`:

```markdown
## HIL Testing Requirements

1. HIL must run on **192.168.254.2, 192.168.254.3, 192.168.254.4**
2. The only permitted VLAN in HIL mode is **999**
3. A run only counts as success if `hil-report.json` shows **PASS for all devices** AND cleanup PASS

## Before Committing Network Code

If you modified any device handler or VLAN/port logic:

1. Run `make hil`
2. Verify ALL devices pass
3. Verify cleanup succeeded (check artifacts)
4. Only then commit

## HIL Failure = Block Commit

If `make hil` fails:
- Do NOT commit
- Fix the issue
- Re-run HIL
- Only commit when ALL stages pass on ALL devices
```

### Example Ralph Loop Workflow

```
┌─────────────────────────────────────────────────────────────┐
│                    RALPH LOOP                                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Read task → Plan implementation                          │
│                                                              │
│  2. Write code                                               │
│                                                              │
│  3. Run unit tests: `make test`                              │
│     └─ If fail → Fix and goto 2                              │
│                                                              │
│  4. If network code changed:                                 │
│     Run HIL tests: `make hil`                                │
│     └─ If fail → Fix and goto 2                              │
│     └─ Verify: ALL devices PASS, cleanup PASS                │
│                                                              │
│  5. Commit (only if all tests pass)                          │
│                                                              │
│  6. Loop → Next task                                         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Critical Safety Rule

**The loop cannot commit if `make hil` fails.**

This is enforced by:
1. Making HIL a blocking step before commit
2. Requiring ALL devices to pass
3. Requiring cleanup to succeed
4. Producing audit artifacts for every run

---

## Troubleshooting

### "NETWORK_PASSWORD is not set"

```bash
source .env
```

### "HIL spec file not found"

Ensure you're in the project root:
```bash
cd /home/emesix/git/switchcraft
```

### "Device unreachable"

Check connectivity:
```bash
ping 192.168.254.2
```

### "HIL CONSTRAINT VIOLATION"

You're trying to modify something outside the allowed scope:
- Wrong VLAN ID (must be 999)
- Wrong device IP
- Wrong port

### Cleanup Failed

If cleanup fails, manually verify the device state:
```bash
# For Brocade
telnet 192.168.254.2
show vlan 999

# For Zyxel
ssh admin@192.168.254.3
show vlan 999
```

---

## Quick Reference

```bash
# Run all HIL tests
make hil

# Run single device
make hil-brocade
make hil-zyxel
make hil-openwrt

# View last report
make hil-report

# Clean artifacts
make clean
```

**Remember: Only VLAN 999. Only ports 23/24 (or lan7/lan8). Always cleanup.**
