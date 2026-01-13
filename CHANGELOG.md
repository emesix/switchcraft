# Changelog

## [0.2.0] - 2026-01-13

### Configuration Management System

#### Git-Versioned Desired State
- New `ConfigStore` class for YAML-based configuration storage
- Git integration with automatic commits on config changes
- Drift detection comparing desired vs actual device state
- Snapshot and restore functionality
- Version history with rollback support

#### New MCP Tools
- `config_save` - Save desired state to git-versioned store
- `config_status` - Check drift between desired and actual
- `config_sync` - Apply desired state to device with auto-rollback
- `config_snapshot` - Create named snapshot
- `config_restore` - Restore from snapshot
- `config_history` - View git commit history
- `config_rollback` - Rollback to previous version
- `config_diff` - Diff between revisions

#### Automatic Rollback
- ConfigEngine now supports automatic rollback on failure (Brocade)
- Rollback commands generated from diff (reverse changes)
- Enabled by default in `config_sync` tool
- Can be disabled via `rollback_on_error=false` parameter

### Zyxel SSH CLI Handler

#### New Device Type: `zyxel-cli`
- SSH CLI interface for Zyxel GS1900 switches (alternative to HTTPS API)
- Legacy SSH algorithm support for OpenSSH 6.2 compatibility
- Interactive shell with `--More--` pagination handling
- Smart error detection that ignores interface statistics
- Full VLAN and port management via CLI commands

### HIL (Hardware-in-the-Loop) Testing

#### Server-Enforced Safety
- Only VLAN 999 operations permitted in HIL mode
- Device allowlist: 192.168.254.2, .3, .4 only
- Port restrictions per device (23/24 or lan7/8)
- `HILConstraintError` raised on any violation

#### Full Lifecycle Testing
- 6-stage lifecycle: Snapshot → Apply → Verify → Idempotent → Cleanup → Validate
- Per-device pass/fail with detailed stage results
- JSON artifacts for audit: pre.json, post.json, clean.json, hil-report.json

#### New Files
- `src/mcp_network_switch/hil/` - HIL module (mode, constraints, runner, cli)
- `configs/devices.lab.yaml` - Lab device inventory
- `tests/hil_spec.yaml` - HIL test specification
- `docs/HIL-TESTING.md` - Full HIL documentation with ralph-loop instructions

### Build System

#### Makefile Added
- `make test` - Run unit tests
- `make lint` - Run ruff linter
- `make hil` - Run HIL tests on all devices
- `make hil-brocade/zyxel/openwrt` - Run HIL on single device
- `make hil-report` - View last HIL report
- `make server` - Start MCP server

### Enterprise Features (from 0.1.2)
- Dry-run mode for `create_vlan` and `delete_vlan`
- Audit logging with `get_audit_log` tool
- Input validation for VLAN IDs and port names
- Integration tests for multi-device workflows

### Quality
- 152 unit tests passing
- All lint checks passing (ruff)

---

## [0.1.1] - 2026-01-12

### Testing & Refinement Pass (Ralph Wiggum Iteration 1)

#### Lint Fixes (ruff)
- Removed unused imports: `os`, `Any`, `datetime`, `Path`, `sys`, `CommandResult`, `ResourceTemplate`
- Fixed unused variables: `current_module`, `state`, `link_up`, `page`
- Removed extraneous f-string prefixes in ONTI UCI commands
- Changed tuple unpacking to use `_` for unused module returns

#### Type Fixes (pyright)
- Fixed `BrocadeTelnet.enable()` - separated bytes vs string output handling
- Fixed `ONTIDevice.connect()` - proper transport null check before SCPClient init
- Fixed `ONTIDevice.execute()` - local reference for type narrowing in nested function
- Fixed `ONTIDevice.download_config()` / `upload_config()` - local SCP reference for type narrowing
- Fixed `ZyxelDevice._get_xssid()` - added HTTP session null check
- Fixed `ZyxelDevice.execute()` - local SSH reference + bytes encoding for shell.send()
- Fixed `ZyxelDevice.create_vlan()` / `configure_port()` - HTTP session null checks
- Fixed `ZyxelDevice._set_port_vlan_membership()` - ensure web session before HTTP calls
- Added `get_config_file()` / `put_config_file()` to base `NetworkDevice` class
- Fixed `server.py` Resource URI type - import `AnyUrl` from pydantic, convert to string for parsing
- Fixed `with_retry` decorator - added type: ignore comments for complex generic handling

#### Test Suite Created
```
tests/
├── __init__.py
├── test_brocade.py      # 10 tests - port parsing, range formatting
├── test_connection.py   # 13 tests - retry decorator, command results
├── test_devices.py      # 11 tests - DeviceConfig, VLANConfig, PortConfig
├── test_inventory.py    # 9 tests  - inventory loading, caching, defaults
├── test_schema.py       # 18 tests - normalization, diffing, serialization
└── test_zyxel.py        # 8 tests  - password encoding, port list parsing
```

**Total: 69 tests passing**

#### Security Improvements
- Removed hardcoded password `NikonD90` from `.env.example`
- Removed hardcoded Zabbix API token from `.env.example`
- Replaced hardcoded credentials in `README.md` with placeholders
- Replaced hardcoded paths in `README.md` with generic `/path/to/` examples

#### Configuration Updates
- Added `pytest.ini` with asyncio_mode=auto
- Added `ruff` and `pyright` to dev dependencies in `pyproject.toml`

#### Quality Gates
| Check | Status |
|-------|--------|
| ruff check src/ | All checks passed |
| pyright src/ | 0 errors, 0 warnings |
| pytest tests/ | 69 passed |
| pip install -e . | Success |

---

## [0.1.0] - Initial Release

- MCP server for network switch management
- Support for Brocade FCX (telnet), Zyxel GS1900 (HTTPS), ONTI S508CL (SSH/SCP)
- Normalized configuration schema across device types
- Automatic retry with exponential backoff
- SCP-based fast config workflow for ONTI devices
- Device inventory with YAML configuration
