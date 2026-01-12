# Switchcraft MCP Server - Comprehensive Test Plan

## Overview

This document outlines the comprehensive testing strategy for the Switchcraft MCP Network Switch Server. The testing approach follows the "Ralph Wiggum" methodology - testing not just happy paths, but also edge cases, invalid inputs, and scenarios that "could break things."

## Test Environment

### Network Topology
```
[Test PC: 192.168.254.58]
    │
    ├── 1/1/1 ──► [brocade-core: 192.168.254.2] ◄── Core Switch (Telnet:23) ✅ REACHABLE
    │                    │
    │                    └── 1/2/2 (10G SFP+) ──► [onti-backend: 192.168.254.4] ◄── Backend 10G (SSH:22) ⚠️ CHECK
    │
    └── [zyxel-frontend: 192.168.254.3] ◄── SSH:22 (⚠️ Auth issues - credentials unknown)
```

### Configured Devices
| Device ID | IP | Protocol | Status |
|-----------|-----|----------|--------|
| brocade-core | 192.168.254.2 | telnet:23 | ✅ Primary test target |
| zyxel-frontend | 192.168.254.3 | https:443 | ⚠️ Auth failed (unknown creds) |
| onti-backend | 192.168.254.4 | ssh:22 | ⚠️ Verify L2 connectivity |

---

## MCP Tools Under Test (16 Total)

### Category 1: Discovery & Status
1. `list_devices` - List all configured devices
2. `device_status` - Get health/status for a device

### Category 2: Configuration Retrieval
3. `get_config` - Get normalized configuration
4. `get_vlans` - Get VLAN configurations
5. `get_ports` - Get port configurations

### Category 3: Configuration Modification
6. `create_vlan` - Create or update a VLAN
7. `delete_vlan` - Delete a VLAN
8. `configure_port` - Configure a port
9. `save_config` - Save running config to startup

### Category 4: Command Execution
10. `execute_command` - Execute raw command
11. `batch_command` - Execute command on multiple devices
12. `execute_batch` - Fast batch show commands (Brocade)
13. `execute_config_batch` - Fast batch config commands (Brocade)

### Category 5: Advanced
14. `diff_config` - Compare expected vs actual config
15. `download_config_file` - Download ONTI config via SCP
16. `upload_config_file` - Upload ONTI config via SCP

---

## Test Categories

### A. Normal Operation Tests (Happy Path)
Standard usage with valid inputs.

### B. Ralph Wiggum Tests (Edge Cases & Invalid Inputs)
Test what happens when things go wrong:

| Category | Test Cases |
|----------|------------|
| **Invalid Device IDs** | `"ralph"`, `""`, `"🔥"`, `None`, `"../../etc/passwd"`, very long strings |
| **VLAN ID Edge Cases** | `0`, `4095`, `4096`, `9999`, `-1`, `"banana"`, `1.5`, `null` |
| **Port Name Chaos** | `"99/99/99"`, `""`, `"drop table"`, `"lan999"`, Unicode characters |
| **Command Injection** | `"; rm -rf /"`, `"| cat /etc/passwd"`, backticks |
| **Special Characters** | `!@#$%^&*()`, newlines, tabs, null bytes |
| **Type Confusion** | Strings where numbers expected, lists where strings expected |
| **Boundary Testing** | Empty strings, max length strings, negative numbers |

### C. Error Handling Tests
Verify graceful failure modes.

### D. Performance Tests
Verify batch operations provide expected speedup.

---

## Detailed Test Cases

### Tool 1: `list_devices`

| # | Test Case | Expected Result |
|---|-----------|-----------------|
| 1.1 | Call with no parameters | Returns list of all 3 devices |
| 1.2 | Verify response structure | Each device has id, name, type, host |

### Tool 2: `device_status`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 2.1 | Valid device | `device_id="brocade-core"` | Returns health status |
| 2.2 | Invalid device ID | `device_id="ralph"` | Error: device not found |
| 2.3 | Empty string | `device_id=""` | Error: invalid device ID |
| 2.4 | Unicode emoji | `device_id="🔥"` | Error: device not found |
| 2.5 | Path traversal | `device_id="../../etc/passwd"` | Error: device not found |
| 2.6 | SQL injection | `device_id="'; DROP TABLE--"` | Error: device not found |
| 2.7 | Very long string | `device_id="a" * 10000` | Error/timeout gracefully |
| 2.8 | Unreachable device | `device_id="zyxel-frontend"` | Error: connection failed |

### Tool 3: `get_config`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 3.1 | Valid device, raw=false | `device_id="brocade-core"` | Normalized config JSON |
| 3.2 | Valid device, raw=true | `device_id="brocade-core", include_raw=true` | Includes raw output |
| 3.3 | Invalid device | `device_id="invalid"` | Error: device not found |

### Tool 4: `get_vlans`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 4.1 | Valid device | `device_id="brocade-core"` | List of VLANs with ports |
| 4.2 | Invalid device | `device_id="nope"` | Error: device not found |

### Tool 5: `get_ports`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 5.1 | Valid device | `device_id="brocade-core"` | List of ports with status |
| 5.2 | Invalid device | `device_id="fake"` | Error: device not found |

### Tool 6: `create_vlan`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 6.1 | Create valid VLAN | `vlan_id=100, name="Test"` | VLAN created |
| 6.2 | VLAN ID 0 | `vlan_id=0` | Error: invalid VLAN ID |
| 6.3 | VLAN ID 4095 | `vlan_id=4095` | Success (max valid) |
| 6.4 | VLAN ID 4096 | `vlan_id=4096` | Error: invalid VLAN ID |
| 6.5 | VLAN ID -1 | `vlan_id=-1` | Error: invalid VLAN ID |
| 6.6 | VLAN ID "banana" | `vlan_id="banana"` | Error: type validation |
| 6.7 | VLAN name with special chars | `name="Test; DROP--"` | Sanitized or error |
| 6.8 | VLAN name with emoji | `name="🔥Fire🔥"` | Sanitized or error |
| 6.9 | Empty VLAN name | `name=""` | Handled gracefully |
| 6.10 | Very long VLAN name | `name="A" * 1000` | Truncated or error |
| 6.11 | Invalid port list | `untagged_ports=["99/99/99"]` | Error: invalid port |

### Tool 7: `delete_vlan`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 7.1 | Delete existing VLAN | `vlan_id=100` | VLAN deleted |
| 7.2 | Delete non-existent VLAN | `vlan_id=999` | Error or no-op |
| 7.3 | Delete VLAN 1 (default) | `vlan_id=1` | Error: cannot delete default |
| 7.4 | Delete VLAN 0 | `vlan_id=0` | Error: invalid VLAN ID |
| 7.5 | Invalid device | `device_id="fake"` | Error: device not found |

### Tool 8: `configure_port`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 8.1 | Valid port config | `port_name="1/1/10", enabled=true` | Port configured |
| 8.2 | Invalid port | `port_name="99/99/99"` | Error: invalid port |
| 8.3 | Empty port name | `port_name=""` | Error: invalid port |
| 8.4 | Port with injection | `port_name="1/1/1; reboot"` | Error: invalid port |
| 8.5 | Set description | `description="Server 1"` | Description set |
| 8.6 | Description with special chars | `description="<script>alert(1)</script>"` | Sanitized |
| 8.7 | Invalid speed | `speed="ludicrous"` | Error: invalid speed |
| 8.8 | Valid speeds | `speed="auto"/"100M"/"1G"/"10G"` | Speed configured |

### Tool 9: `save_config`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 9.1 | Save valid device | `device_id="brocade-core"` | Config saved (write memory) |
| 9.2 | Save invalid device | `device_id="fake"` | Error: device not found |

### Tool 10: `execute_command`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 10.1 | Valid show command | `command="show version"` | Command output |
| 10.2 | Invalid command | `command="foobar"` | Error: invalid command |
| 10.3 | Dangerous command | `command="reload"` | Error/warning |
| 10.4 | Command with pipe | `command="show vlan \| include 254"` | Output or blocked |
| 10.5 | Empty command | `command=""` | Error: empty command |
| 10.6 | Shell injection | `command="; rm -rf /"` | Error/no execution |

### Tool 11: `batch_command`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 11.1 | Valid batch | `device_ids=["brocade-core"], command="show vlan"` | Results per device |
| 11.2 | All devices | `device_ids=["all"]` | Results from all |
| 11.3 | Mixed valid/invalid | `device_ids=["brocade-core", "fake"]` | Partial results + errors |
| 11.4 | Empty device list | `device_ids=[]` | Error: no devices |
| 11.5 | Invalid device in list | `device_ids=["🔥"]` | Error: device not found |

### Tool 12: `execute_batch` (Brocade-specific)

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 12.1 | Multiple show commands | `commands=["show vlan", "show ver"]` | Per-command results |
| 12.2 | Empty commands | `commands=[]` | Error: no commands |
| 12.3 | Invalid command in batch | `commands=["show vlan", "foobar"]` | Partial results + error |
| 12.4 | Non-Brocade device | `device_id="onti-backend"` | Error: not supported |
| 12.5 | Config command in show batch | `commands=["conf t"]` | May enter config mode (careful!) |

### Tool 13: `execute_config_batch` (Brocade-specific)

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 13.1 | Valid config batch | `commands=["interface 1/1/10", "no shut"]` | Commands executed |
| 13.2 | Invalid config command | `commands=["foobar"]` | Error detected |
| 13.3 | Stop on error | `stop_on_error=true, commands=["bad", "good"]` | Stops at first error |
| 13.4 | Continue on error | `stop_on_error=false` | Continues past errors |
| 13.5 | Non-Brocade device | `device_id="onti-backend"` | Error: not supported |

### Tool 14: `diff_config`

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 14.1 | Matching config | Expected matches actual | No differences |
| 14.2 | Missing VLAN | Expected has VLAN not on device | Shows difference |
| 14.3 | Invalid expected format | `expected_config="not json"` | Error: invalid format |
| 14.4 | Empty expected | `expected_config={}` | Shows all as extra |

### Tool 15: `download_config_file` (ONTI-specific)

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 15.1 | Download network config | `config_name="network"` | File contents |
| 15.2 | Download system config | `config_name="system"` | File contents |
| 15.3 | Invalid config name | `config_name="passwd"` | Error: invalid name |
| 15.4 | Non-ONTI device | `device_id="brocade-core"` | Error: not supported |
| 15.5 | Path traversal | `config_name="../etc/shadow"` | Error: invalid name |

### Tool 16: `upload_config_file` (ONTI-specific)

| # | Test Case | Input | Expected Result |
|---|-----------|-------|-----------------|
| 16.1 | Valid upload | `config_name="network", content="..."` | Uploaded + reloaded |
| 16.2 | Invalid config name | `config_name="secret"` | Error: invalid name |
| 16.3 | Empty content | `content=""` | Warning/error |
| 16.4 | Malformed UCI | `content="not valid uci"` | Error on reload |
| 16.5 | Non-ONTI device | `device_id="brocade-core"` | Error: not supported |
| 16.6 | reload=false | `reload=false` | Uploaded, no reload |

---

## Ralph Wiggum Special Tests

### Input Chaos Matrix

For each tool accepting string parameters, test:

| Input Type | Example | Risk |
|------------|---------|------|
| Empty string | `""` | Null pointer / empty check |
| Whitespace only | `"   "` | Strip check |
| Unicode emoji | `"🔥💥🎉"` | Encoding issues |
| Path traversal | `"../../etc/passwd"` | Security breach |
| Shell injection | `"; rm -rf /"` | Command injection |
| SQL injection | `"'; DROP TABLE--"` | (Not applicable here but test anyway) |
| Null bytes | `"test\x00evil"` | String termination issues |
| Very long | `"A" * 100000` | Buffer overflow / DoS |
| Newlines | `"test\nshow version"` | Command injection |
| Control characters | `"\x01\x02\x03"` | Terminal issues |
| HTML/XSS | `"<script>alert(1)</script>"` | If displayed in UI |
| Format strings | `"%s%s%s%s%s"` | Format string vulnerability |

### Type Confusion Tests

| Parameter | Wrong Type | Expected Behavior |
|-----------|-----------|-------------------|
| `vlan_id` (int) | `"banana"` | Type error |
| `vlan_id` (int) | `1.5` | Type error |
| `vlan_id` (int) | `null` | Error |
| `enabled` (bool) | `"yes"` | May work or error |
| `enabled` (bool) | `1` | May work |
| `ports` (array) | `"1/1/1"` | Type error |
| `ports` (array) | `{"port": "1/1/1"}` | Type error |

### Boundary Value Tests

| Parameter | Boundary | Test Values |
|-----------|----------|-------------|
| VLAN ID | 1-4094 | 0, 1, 4094, 4095, 4096 |
| Port number | Device-specific | 0, max+1, negative |
| Timeout | Positive int | 0, -1, very large |
| String length | Varies | 0, 1, max, max+1 |

---

## Test Execution Procedure

### Phase 1: Connectivity Check
1. Verify network access to test devices
2. Check credentials work
3. Document any unreachable devices

### Phase 2: Happy Path Tests
1. Run all normal operation tests
2. Verify expected responses
3. Document any failures

### Phase 3: Ralph Wiggum Tests
1. Run edge case tests systematically
2. Record all error messages
3. Note any crashes, hangs, or unexpected behavior

### Phase 4: Error Analysis
1. Review all errors for proper handling
2. Identify any security concerns
3. Document bugs found

### Phase 5: Bug Fixes
1. Fix critical bugs
2. Re-test affected areas
3. Update documentation

---

## Test Results Template

```markdown
## Tool: [tool_name]

### Test Environment
- Date: YYYY-MM-DD
- Device: [device_id]
- Status: [reachable/unreachable]

### Test Results

| # | Test Case | Input | Expected | Actual | Status |
|---|-----------|-------|----------|--------|--------|
| X.X | Description | `input` | Expected result | Actual result | ✅/❌ |

### Issues Found
- Issue #1: Description

### Notes
- Additional observations
```

---

## Success Criteria

1. **All happy path tests pass** - Core functionality works
2. **No crashes on invalid input** - Graceful error handling
3. **No security vulnerabilities** - Input sanitization working
4. **Clear error messages** - Users understand what went wrong
5. **No resource leaks** - Connections properly closed on error
6. **Consistent behavior** - Same input = same output

---

## Appendix: Quick Reference Commands

### Brocade Test Commands
```
show version
show vlan brief
show interfaces brief
show running-config
show vlan 254
```

### ONTI Test Commands
```
uci show network
cat /etc/config/network
cat /sys/class/net/lan1/operstate
```

### Device Status Check
```bash
ping -c 1 192.168.254.2  # Brocade
ping -c 1 192.168.254.4  # ONTI
ping -c 1 192.168.254.3  # Zyxel
```
