# Changelog

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
