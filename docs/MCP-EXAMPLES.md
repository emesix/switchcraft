# MCP Network Switch - Usage Examples

Practical examples for using the MCP Network Switch tools via Claude or other MCP clients.

---

## Device Discovery & Status

### List all configured devices
```
Tool: list_devices
Input: {}

Response: List of all devices with type, name, host, protocol
```

### Check device health
```
Tool: device_status
Input: { "device_id": "brocade-core" }

Response: Connection status, uptime, temperature, port summary
```

---

## VLAN Operations

### View all VLANs on a device
```
Tool: get_vlans
Input: { "device_id": "brocade-core" }

Response: Normalized VLAN list with ID, name, tagged ports, untagged ports
```

### Create a new VLAN
```
Tool: create_vlan
Input: {
  "device_id": "brocade-core",
  "vlan_id": 100,
  "name": "Servers",
  "untagged_ports": ["1/1/1", "1/1/2", "1/1/3", "1/1/4"],
  "tagged_ports": ["1/2/1", "1/2/2"]
}

Result: VLAN 100 created with 4 access ports and 2 trunk ports
```

### Create same VLAN on multiple switches (batch)
```
Tool: batch_command
Input: {
  "device_ids": ["brocade-core", "zyxel-access"],
  "command": "create_vlan",
  "params": {
    "vlan_id": 100,
    "name": "Servers"
  }
}

Result: VLAN created on both switches in parallel
```

### Delete a VLAN
```
Tool: delete_vlan
Input: {
  "device_id": "brocade-core",
  "vlan_id": 100
}

Warning: This removes all port associations!
```

---

## Port Operations

### View all ports
```
Tool: get_ports
Input: { "device_id": "brocade-core" }

Response: Port list with status, speed, duplex, VLAN membership
```

### Configure a port
```
Tool: configure_port
Input: {
  "device_id": "brocade-core",
  "port": "1/1/10",
  "enabled": true,
  "speed": "100-full",
  "description": "Legacy-Server"
}
```

### Disable unused ports (security)
```
Tool: configure_port
Input: {
  "device_id": "brocade-core",
  "port": "1/1/20",
  "enabled": false
}
```

---

## Configuration Management

### Get normalized config (all VLANs + ports)
```
Tool: get_config
Input: { "device_id": "brocade-core" }

Response: Complete normalized config object
```

### Compare expected vs actual
```
Tool: diff_config
Input: {
  "device_id": "brocade-core",
  "expected": {
    "vlans": [
      { "id": 100, "name": "Servers", "untagged_ports": ["1/1/1", "1/1/2"] }
    ]
  }
}

Response: Differences between expected and actual config
```

### Save running config
```
Tool: save_config
Input: { "device_id": "brocade-core" }

Result: Running config saved to startup (write memory)
```

---

## Raw Command Execution

### Execute arbitrary command (advanced)
```
Tool: execute_command
Input: {
  "device_id": "brocade-core",
  "command": "show mac-address vlan 100"
}

Response: Raw command output
```

### Show spanning tree status
```
Tool: execute_command
Input: {
  "device_id": "brocade-core",
  "command": "show span"
}
```

---

## ONTI-Specific Operations

### Download config file via SCP
```
Tool: download_config_file
Input: {
  "device_id": "onti-edge",
  "config_type": "network"
}

Response: Contents of /etc/config/network
```

### Upload modified config
```
Tool: upload_config_file
Input: {
  "device_id": "onti-edge",
  "config_type": "network",
  "content": "<modified UCI config>",
  "reload": true
}

Result: Config uploaded and network service restarted
```

---

## Real-World Scenarios

### Scenario 1: Deploy new server VLAN across all switches

```
Step 1: Create VLAN on core switch
Tool: create_vlan
Input: {
  "device_id": "brocade-core",
  "vlan_id": 150,
  "name": "NewServers",
  "tagged_ports": ["1/2/1", "1/2/2"]
}

Step 2: Create VLAN on access switch
Tool: create_vlan
Input: {
  "device_id": "zyxel-access",
  "vlan_id": 150,
  "name": "NewServers",
  "tagged_ports": ["25", "26"]
}

Step 3: Verify on both
Tool: get_vlans
Input: { "device_id": "brocade-core" }

Tool: get_vlans
Input: { "device_id": "zyxel-access" }

Step 4: Save configs
Tool: save_config
Input: { "device_id": "brocade-core" }
```

### Scenario 2: Troubleshoot missing connectivity

```
Step 1: Check VLAN exists
Tool: get_vlans
Input: { "device_id": "brocade-core" }

Step 2: Verify port is in VLAN
Tool: get_ports
Input: { "device_id": "brocade-core" }

Step 3: Check spanning tree
Tool: execute_command
Input: {
  "device_id": "brocade-core",
  "command": "show span"
}

Step 4: Check MAC table
Tool: execute_command
Input: {
  "device_id": "brocade-core",
  "command": "show mac-address vlan 100"
}
```

### Scenario 3: Emergency port shutdown

```
Tool: configure_port
Input: {
  "device_id": "brocade-core",
  "port": "1/1/15",
  "enabled": false
}

Tool: save_config
Input: { "device_id": "brocade-core" }
```

---

## Tips for Claude Integration

1. **Always check device_status first** before making changes
2. **Use get_config to understand current state** before modifications
3. **Prefer high-level tools** (create_vlan, configure_port) over execute_command
4. **Use batch_command** for multi-device consistency
5. **Always save_config after changes** on Brocade
6. **For ONTI, use SCP workflow** - download, modify, upload, reload
