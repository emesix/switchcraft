# Switchcraft Configuration Management System

## Overview

A complete configuration management system that:
- Stores desired state configurations (YAML files)
- Tracks history with git integration
- Detects drift on session startup
- Supports named profiles for common configurations
- Enables network-wide configuration (VLANs across all switches)
- Provides auto-remediation with approval workflows

---

## Directory Structure

```
~/.switchcraft/
├── configs/
│   ├── desired/                    # Current desired state per device
│   │   ├── brocade-core.yaml
│   │   ├── onti-backend.yaml
│   │   └── zyxel-frontend.yaml
│   │
│   ├── profiles/                   # Named configuration profiles
│   │   ├── production.yaml         # Network-wide production config
│   │   ├── maintenance.yaml        # Maintenance mode (disable ports, etc.)
│   │   ├── testing.yaml            # Test lab configuration
│   │   └── README.md
│   │
│   ├── network/                    # Network-wide definitions
│   │   ├── vlans.yaml              # VLAN definitions (applied to all devices)
│   │   ├── acls.yaml               # Access control lists
│   │   └── stp.yaml                # Spanning tree config
│   │
│   ├── snapshots/                  # Point-in-time snapshots (git-managed)
│   │   ├── 2026-01-13T08:00:00/
│   │   │   ├── brocade-core.yaml
│   │   │   └── onti-backend.yaml
│   │   └── 2026-01-12T20:00:00/
│   │       └── ...
│   │
│   └── .git/                       # Git repository for versioning
│
├── state/
│   ├── last_known/                 # Last fetched actual state
│   │   ├── brocade-core.yaml
│   │   └── onti-backend.yaml
│   │
│   └── drift_reports/              # Drift detection reports
│       └── 2026-01-13T08:30:00.json
│
└── switchcraft.db                  # SQLite for fast queries & audit log
```

---

## Configuration File Formats

### Device Desired State (`configs/desired/brocade-core.yaml`)

```yaml
# Desired state for brocade-core
# Managed by Switchcraft - do not edit directly unless you know what you're doing
# Last updated: 2026-01-13T08:00:00Z
# Updated by: user@example.com

device_id: brocade-core
version: 3
checksum: sha256:abc123def456

vlans:
  1:
    name: "DEFAULT-VLAN"
    # No ports specified = don't manage, leave as-is

  254:
    name: "Management"
    untagged_ports:
      - "1/1/1-10"
      - "1/2/1-4"
    tagged_ports: []
    ip_interface:
      address: "192.168.254.1"
      mask: "255.255.255.0"

ports:
  "1/1/1":
    description: "Workstation-PC"
    enabled: true
  "1/2/1":
    description: "Uplink-ONTI"
    enabled: true
    speed: "10G"

settings:
  stp_enabled: true
  hostname: "FCX624-ADV"
```

### Network-Wide VLAN Definition (`configs/network/vlans.yaml`)

```yaml
# Network-wide VLAN definitions
# These VLANs should exist on ALL devices (or specified subset)

vlans:
  254:
    name: "Management"
    description: "Management network for all devices"
    ip_range: "192.168.254.0/24"
    apply_to: all  # or list: [brocade-core, onti-backend]

  100:
    name: "Production"
    description: "Production server network"
    ip_range: "10.100.0.0/24"
    apply_to:
      - brocade-core
      - onti-backend

  200:
    name: "Guest"
    description: "Guest WiFi network"
    ip_range: "10.200.0.0/24"
    apply_to:
      - zyxel-frontend
```

### Named Profile (`configs/profiles/maintenance.yaml`)

```yaml
# Maintenance Mode Profile
# Apply with: switchcraft apply-profile maintenance
# Revert with: switchcraft apply-profile production

name: "Maintenance Mode"
description: "Disable non-essential ports during maintenance window"
author: "admin"
created: "2026-01-10"

# Actions to perform
actions:
  - device: brocade-core
    ports:
      "1/1/11-24":
        enabled: false
        description: "MAINTENANCE - disabled"

  - device: onti-backend
    ports:
      "lan3-lan8":
        enabled: false

# Automatic revert after duration (optional)
auto_revert:
  enabled: true
  duration: "4h"
  notify: ["admin@example.com"]
```

---

## Session Startup Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SESSION STARTUP                              │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  1. LOAD DESIRED STATE                                              │
│     - Read configs/desired/*.yaml                                   │
│     - Read configs/network/*.yaml (network-wide)                    │
│     - Merge into per-device desired state                           │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  2. FETCH CURRENT STATE (parallel)                                  │
│     - Connect to each device                                        │
│     - Get VLANs, ports, settings                                    │
│     - Store in state/last_known/*.yaml                              │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  3. DETECT DRIFT                                                    │
│     - Compare desired vs actual for each device                     │
│     - Generate drift report                                         │
│     - Store in state/drift_reports/                                 │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  4. REPORT STATUS                                                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Switchcraft Session Started                                │   │
│  │                                                             │   │
│  │  Device Status:                                             │   │
│  │  ✅ brocade-core    IN SYNC     (last check: 2min ago)     │   │
│  │  ⚠️  onti-backend   DRIFT       VLAN 100 missing           │   │
│  │  ❌ zyxel-frontend  UNREACHABLE connection refused         │   │
│  │                                                             │   │
│  │  Actions available:                                         │   │
│  │  - "fix drift" to remediate onti-backend                   │   │
│  │  - "show drift onti-backend" for details                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  5. OPTIONAL: AUTO-REMEDIATION                                      │
│     - If configured, automatically fix drift                        │
│     - Requires: auto_remediate: true in settings                    │
│     - Always creates snapshot before changes                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## New MCP Tools

### Configuration Management Tools

| Tool | Description |
|------|-------------|
| `config_status` | Show sync status for all devices |
| `config_diff` | Show detailed drift for a device |
| `config_sync` | Remediate drift (apply desired state) |
| `config_save` | Save current device state as new desired state |
| `config_snapshot` | Create named snapshot of current state |
| `config_restore` | Restore from a snapshot |
| `config_history` | Show change history for a device |

### Profile Management Tools

| Tool | Description |
|------|-------------|
| `profile_list` | List available profiles |
| `profile_apply` | Apply a named profile |
| `profile_create` | Create new profile from current state |
| `profile_diff` | Show what a profile would change |

### Network-Wide Tools

| Tool | Description |
|------|-------------|
| `network_vlans` | Show VLAN status across all devices |
| `network_sync` | Sync network-wide config to all devices |

---

## Git Integration

### Automatic Commits

Every change to desired state triggers a git commit:

```
commit abc123
Author: switchcraft <switchcraft@local>
Date:   Mon Jan 13 08:30:00 2026

    [brocade-core] VLAN 100 created

    Changes:
    - Created VLAN 100 (Production)
    - Added ports 1/1/11-14 to VLAN 100

    Applied by: Claude via MCP
    Audit context: "Add production VLAN"
```

### Branches for Profiles

```
main              <- Current desired state
├── profile/maintenance
├── profile/testing
└── snapshots/2026-01-13
```

### Commands

```bash
# View history
switchcraft config history brocade-core

# Diff between versions
switchcraft config diff brocade-core HEAD~3

# Restore previous version
switchcraft config restore brocade-core HEAD~1
```

---

## SQLite Database Schema

For fast queries and audit trail:

```sql
-- Device state tracking
CREATE TABLE device_state (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    state_type TEXT NOT NULL,  -- 'desired', 'actual', 'snapshot'
    config_yaml TEXT NOT NULL,
    checksum TEXT NOT NULL,
    source TEXT  -- 'manual', 'auto_save', 'profile:maintenance'
);

-- Drift detection log
CREATE TABLE drift_log (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    drift_type TEXT NOT NULL,  -- 'vlan_missing', 'port_mismatch', etc.
    expected TEXT,
    actual TEXT,
    resolved_at DATETIME,
    resolution TEXT  -- 'auto_fix', 'manual', 'ignored'
);

-- Change audit trail
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    device_id TEXT,
    operation TEXT NOT NULL,
    user TEXT,
    source TEXT,  -- 'mcp', 'cli', 'auto_remediate'
    config_before TEXT,
    config_after TEXT,
    success BOOLEAN,
    error TEXT
);

-- Profiles
CREATE TABLE profiles (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    config_yaml TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    last_applied DATETIME
);
```

---

## Implementation Phases

### Phase 1: Config Storage & Loading
- [ ] Create `~/.switchcraft/configs/` directory structure
- [ ] Implement `ConfigStore` class for reading/writing YAML
- [ ] Add `config_save` tool to save current state as desired
- [ ] Add `config_load` to load desired state

### Phase 2: Drift Detection
- [ ] Implement `DriftDetector` class
- [ ] Add session startup hook for drift check
- [ ] Add `config_status` tool
- [ ] Add `config_diff` tool for detailed drift view

### Phase 3: Git Integration
- [ ] Initialize git repo in configs/
- [ ] Auto-commit on config changes
- [ ] Add `config_history` tool
- [ ] Add `config_restore` tool

### Phase 4: Profiles
- [ ] Implement profile storage
- [ ] Add `profile_list`, `profile_apply` tools
- [ ] Add `profile_create` tool
- [ ] Support auto-revert timer

### Phase 5: Network-Wide Config
- [ ] Implement network VLAN definitions
- [ ] Add `network_vlans` status tool
- [ ] Add `network_sync` for multi-device updates

### Phase 6: SQLite Integration
- [ ] Create database schema
- [ ] Migrate audit log to SQLite
- [ ] Add fast query tools
- [ ] Add reporting/analytics

---

## Example Workflows

### Workflow 1: Initial Setup

```
User: Set up config management for my network

AI: I'll initialize the config store and save current state as desired.

1. Created ~/.switchcraft/configs/
2. Fetched current state from all devices
3. Saved as desired state:
   - configs/desired/brocade-core.yaml
   - configs/desired/onti-backend.yaml
4. Initialized git repository
5. Created initial commit

Your network is now under configuration management!
```

### Workflow 2: Drift Detection

```
[Session Start]

Switchcraft Config Status:
┌─────────────────────────────────────────────────────────┐
│ Device          │ Status    │ Details                  │
├─────────────────────────────────────────────────────────┤
│ brocade-core    │ ✅ SYNC   │ Last check: just now     │
│ onti-backend    │ ⚠️ DRIFT  │ 2 differences detected   │
│ zyxel-frontend  │ ❌ DOWN   │ Connection refused       │
└─────────────────────────────────────────────────────────┘

User: Show drift for onti-backend

AI: [Uses config_diff tool]

Drift detected on onti-backend:
  VLAN 100:
    - Expected: exists with ports lan1, lan2
    - Actual: missing

  Port lan5:
    - Expected: enabled
    - Actual: disabled

User: Fix it

AI: [Uses config_sync tool]
Applied 2 changes to onti-backend:
- Created VLAN 100 with ports lan1, lan2
- Enabled port lan5

All devices now in sync.
```

### Workflow 3: Apply Maintenance Profile

```
User: Apply maintenance mode

AI: [Uses profile_apply tool]

Applying profile "maintenance" to network:

Preview:
  brocade-core:
    - Disable ports 1/1/11-24
  onti-backend:
    - Disable ports lan3-lan8

Auto-revert scheduled for: 4 hours

Proceed? [y/n]

User: y

AI: Profile applied. Changes will auto-revert at 12:30.
```

---

## Settings (`~/.switchcraft/settings.yaml`)

```yaml
config_management:
  enabled: true
  config_dir: "~/.switchcraft/configs"

  # Session startup behavior
  startup:
    check_drift: true
    auto_remediate: false  # Require manual approval
    parallel_fetch: true   # Fetch all devices in parallel

  # Git integration
  git:
    enabled: true
    auto_commit: true
    commit_author: "switchcraft"

  # Drift detection
  drift:
    ignore_fields:
      - "uptime"
      - "last_change"
    alert_on_drift: true

  # Snapshots
  snapshots:
    auto_before_changes: true
    retention_days: 30
    max_snapshots: 100
```

---

## Open Questions

1. **Remote storage**: Support pushing configs to remote git (GitHub/GitLab)?
2. **Multi-user**: How to handle concurrent changes from multiple users?
3. **Secrets**: How to handle sensitive data (SNMP communities, passwords)?
4. **Notifications**: Webhook/email alerts for drift detection?
