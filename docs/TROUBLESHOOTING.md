# MCP Network Switch - Troubleshooting Guide

Common issues and solutions when using the MCP Network Switch server.

---

## Connection Issues

### Brocade FCX

**Problem:** Connection timeout or refused
```
Solutions:
1. Verify telnet is enabled: ssh to switch first, run `show ip telnet`
2. Check ACLs: `show access-list`
3. Verify IP reachability: ping from MCP host
4. Check port 23 is open: `nc -zv <ip> 23`
```

**Problem:** "skip-page-display" not working
```
The MCP sends this automatically, but if output is truncated:
1. Session may have timed out - MCP will auto-reconnect
2. Check `terminal length 0` as alternative
```

**Problem:** Enable password rejected
```
1. Verify enable_password_required: true in devices.yaml
2. Check NETWORK_PASSWORD env var is set
3. Try connecting manually: `telnet <ip>` then `enable`
```

### Zyxel GS1900

**Problem:** Web API returns 401/403
```
Solutions:
1. Password encoding may have failed - check logs
2. XSSID token expired - MCP auto-refreshes, but check session
3. Verify HTTPS port (443) is accessible
4. Check verify_ssl: false if using self-signed cert
```

**Problem:** SSH commands work but web changes don't apply
```
The GS1900 SSH is READ-ONLY by design.
- Use web interface (HTTPS) for all write operations
- MCP automatically routes writes through web API
```

### ONTI S508CL

**Problem:** SCP transfer fails
```
Solutions:
1. Check SSH key or password auth is working
2. Verify /etc/config/ directory exists
3. Check disk space: `df -h`
4. Try manual SCP: `scp root@<ip>:/etc/config/network /tmp/`
```

**Problem:** Changes don't take effect after UCI commit
```
After `uci commit network`, you must restart:
- `/etc/init.d/network restart`
- Or reboot: `reboot`
```

---

## VLAN Issues

### Port stuck in wrong VLAN

**Brocade:**
```
! Check current membership
show vlan | include "PORT-VLAN\|1/1/5"

! Remove from old VLAN first
configure terminal
vlan <old-vlan-id>
  no untagged ethe 1/1/5
exit

! Then add to new VLAN
vlan <new-vlan-id>
  untagged ethe 1/1/5
exit
write memory
```

**Zyxel:** Port must be explicitly removed from VLAN via web matrix (cmd=1294)

**ONTI:** Edit the switch_vlan ports string to remove port number

### Tagged vs Untagged confusion

**Rule of thumb:**
- **Untagged (Access):** End devices (PCs, servers, phones)
- **Tagged (Trunk):** Switch-to-switch links, router uplinks

**Brocade gotcha:** A port can only be UNTAGGED in ONE VLAN, but TAGGED in multiple VLANs.

### VLAN not passing traffic

Checklist:
1. ✅ VLAN exists on BOTH switches
2. ✅ Trunk port is TAGGED in the VLAN on both ends
3. ✅ Spanning tree not blocking (check `show span` on Brocade)
4. ✅ No ACLs blocking traffic
5. ✅ Native/PVID VLAN matches if using untagged frames

---

## MCP Tool-Specific Issues

### `get_config` returns empty

```
Possible causes:
1. Device connection failed silently - check device_status first
2. Parser couldn't interpret output - check device logs
3. Unsupported firmware version - verify against docs
```

### `create_vlan` succeeds but VLAN not visible

```
1. Run `save_config` after create_vlan (Brocade requires write memory)
2. For ONTI, ensure network service was restarted
3. Verify with `get_vlans` to confirm
```

### `batch_command` partial failures

```
Batch operations run in parallel. If some succeed and others fail:
1. Check individual device status
2. Review the batch result for per-device errors
3. Re-run failed devices individually
```

---

## Recovery Procedures

### Brocade: Revert to last saved config
```
! Discard running changes, reload startup config
reload
```

### Zyxel: Factory reset
```
! Via web: Management > Maintenance > Reset
! Physical: Hold reset button 10+ seconds
```

### ONTI: Revert UCI changes
```
! Before commit - discard changes
uci revert network

! After commit - restore backup
scp /path/to/backup/network root@<ip>:/etc/config/network
ssh root@<ip> "/etc/init.d/network restart"
```

---

## Performance Tips

1. **Use batch_command** for multi-device operations (parallel execution)
2. **Minimize get_config calls** - cache results when possible
3. **Brocade: skip-page-display** is critical - MCP handles this automatically
4. **Zyxel: Session reuse** - MCP maintains session to avoid re-auth overhead
5. **ONTI: Batch UCI changes** - Multiple `uci set` then single `uci commit`

---

## Log Locations

| Component | Location |
|-----------|----------|
| MCP Server | stdout/stderr (depends on launch method) |
| Brocade | `show log` on device |
| Zyxel | Web > Management > Log |
| ONTI | `/var/log/messages`, `logread` |
