"""MCP Server for Network Switch Configuration Management.

Provides stable, unified access to L2/L3 network switches:
- Brocade FCX (telnet) - with retry logic for stability
- Zyxel GS1900 (HTTPS) - web API interaction
- ONTI S508CL (SSH/SCP) - optimized SCP workflow

Tools exposed:
- list_devices: List all configured network devices
- device_status: Get health/status of a device
- get_config: Get normalized config from a device
- get_vlans: Get VLAN configurations
- get_ports: Get port configurations
- execute_command: Execute raw command on device
- create_vlan: Create/update a VLAN
- delete_vlan: Remove a VLAN
- configure_port: Configure a port
- save_config: Save running config
- diff_config: Compare expected vs actual config
- download_config_file: Download config via SCP (ONTI)
- upload_config_file: Upload config via SCP (ONTI)
- apply_config: Apply desired state configuration (declarative)
- config_save: Save current device state as desired config
- config_status: Show drift status (desired vs actual)
- config_snapshot: Create point-in-time backup of configs
- config_restore: Restore configs from snapshot
"""
import asyncio
import json
import logging
import os
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
    Resource,
)
from pydantic import AnyUrl

from .config.inventory import DeviceInventory
from .config.schema import normalize_config, diff_configs, NetworkConfig
from .config_engine import ConfigEngine
from .config_store import ConfigStore
from .devices.base import VLANConfig, PortConfig
from .utils.logging_config import setup_logging, timed_section
from .utils.audit_log import ChangeTracker, setup_audit_logging, get_recent_changes

# Initialize audit logging
setup_audit_logging()

# Configure logging - now with file output and performance tracking
setup_logging()
logger = logging.getLogger(__name__)

# Global inventory (initialized on server start)
inventory: Optional[DeviceInventory] = None
config_store: Optional[ConfigStore] = None


def get_inventory() -> DeviceInventory:
    """Get or create the device inventory."""
    global inventory
    if inventory is None:
        config_path = os.environ.get("MCP_NETWORK_CONFIG")
        inventory = DeviceInventory(config_path)
    return inventory


def get_config_store() -> ConfigStore:
    """Get or create the config store."""
    global config_store
    if config_store is None:
        config_store = ConfigStore()
    return config_store


# Create MCP server
server = Server("mcp-network-switch")


# === TOOLS ===

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        Tool(
            name="list_devices",
            description="List all configured network devices with their types and connection info",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="device_status",
            description="Get health and status information for a network device",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID (e.g., 'brocade-core', 'onti-backend', 'zyxel-frontend')"
                    }
                },
                "required": ["device_id"]
            }
        ),
        Tool(
            name="get_config",
            description="Get normalized configuration from a device (VLANs, ports, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    },
                    "include_raw": {
                        "type": "boolean",
                        "description": "Include raw device config in output",
                        "default": False
                    }
                },
                "required": ["device_id"]
            }
        ),
        Tool(
            name="get_vlans",
            description="Get VLAN configurations from a device",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    }
                },
                "required": ["device_id"]
            }
        ),
        Tool(
            name="get_ports",
            description="Get port configurations from a device",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    }
                },
                "required": ["device_id"]
            }
        ),
        Tool(
            name="execute_command",
            description="Execute a raw command on a device. Use with caution!",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute"
                    }
                },
                "required": ["device_id", "command"]
            }
        ),
        Tool(
            name="create_vlan",
            description="Create or update a VLAN on a device. Use dry_run=true to preview changes without applying.",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    },
                    "vlan_id": {
                        "type": "integer",
                        "description": "VLAN ID (1-4094)"
                    },
                    "name": {
                        "type": "string",
                        "description": "VLAN name"
                    },
                    "tagged_ports": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tagged (trunk) ports"
                    },
                    "untagged_ports": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of untagged (access) ports"
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview changes without applying (default: false)",
                        "default": False
                    }
                },
                "required": ["device_id", "vlan_id"]
            }
        ),
        Tool(
            name="delete_vlan",
            description="Delete a VLAN from a device. Use dry_run=true to preview changes without applying.",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    },
                    "vlan_id": {
                        "type": "integer",
                        "description": "VLAN ID to delete"
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview changes without applying (default: false)",
                        "default": False
                    }
                },
                "required": ["device_id", "vlan_id"]
            }
        ),
        Tool(
            name="configure_port",
            description="Configure a port on a device. Use dry_run=true to preview changes without applying.",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    },
                    "port_name": {
                        "type": "string",
                        "description": "Port name/ID"
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable or disable the port"
                    },
                    "description": {
                        "type": "string",
                        "description": "Port description"
                    },
                    "speed": {
                        "type": "string",
                        "description": "Port speed (auto, 100M, 1G, 10G)"
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview changes without applying (default: false)",
                        "default": False
                    }
                },
                "required": ["device_id", "port_name"]
            }
        ),
        Tool(
            name="save_config",
            description="Save running configuration to startup config",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    }
                },
                "required": ["device_id"]
            }
        ),
        Tool(
            name="diff_config",
            description="Compare expected configuration against actual device config",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID"
                    },
                    "expected_config": {
                        "type": "object",
                        "description": "Expected configuration (JSON with vlans, ports, etc.)"
                    }
                },
                "required": ["device_id", "expected_config"]
            }
        ),
        # ONTI-specific SCP tools
        Tool(
            name="download_config_file",
            description="Download a config file from ONTI device via SCP (FAST!). Returns file contents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID (must be ONTI type)"
                    },
                    "config_name": {
                        "type": "string",
                        "description": "Config name: 'network', 'system', 'firewall', 'wireless'",
                        "enum": ["network", "system", "firewall", "wireless"]
                    }
                },
                "required": ["device_id", "config_name"]
            }
        ),
        Tool(
            name="upload_config_file",
            description="Upload a config file to ONTI device via SCP (FAST!). Provide full file contents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID (must be ONTI type)"
                    },
                    "config_name": {
                        "type": "string",
                        "description": "Config name: 'network', 'system', 'firewall', 'wireless'",
                        "enum": ["network", "system", "firewall", "wireless"]
                    },
                    "content": {
                        "type": "string",
                        "description": "Full config file content to upload"
                    },
                    "reload": {
                        "type": "boolean",
                        "description": "Reload config after upload",
                        "default": True
                    }
                },
                "required": ["device_id", "config_name", "content"]
            }
        ),
        Tool(
            name="batch_command",
            description="Execute the same command on multiple devices",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of device IDs (or 'all' for all devices)"
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute"
                    }
                },
                "required": ["device_ids", "command"]
            }
        ),
        Tool(
            name="execute_config_batch",
            description="Execute multiple config commands in a single fast batch (Brocade). "
                        "Sends all commands at once, checks each for errors, stops on first failure.",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID (currently Brocade only)"
                    },
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of config commands to execute (will be wrapped in conf t/exit)"
                    },
                    "stop_on_error": {
                        "type": "boolean",
                        "description": "Stop execution on first error (default: true)",
                        "default": True
                    }
                },
                "required": ["device_id", "commands"]
            }
        ),
        Tool(
            name="execute_batch",
            description="Execute multiple show/read commands in a single fast batch (Brocade). "
                        "3x faster than individual commands. Use for show commands, not config changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID (currently Brocade only)"
                    },
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of show/read commands to execute (no config mode)"
                    }
                },
                "required": ["device_id", "commands"]
            }
        ),
        Tool(
            name="get_audit_log",
            description="Get recent configuration changes from the audit log",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Filter by device ID (optional)"
                    },
                    "operation": {
                        "type": "string",
                        "description": "Filter by operation type (e.g., 'create_vlan', 'delete_vlan', 'configure_port')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of records to return (default: 20)",
                        "default": 20
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="apply_config",
            description="""Apply a desired state configuration to a device. This is the recommended way to make changes - send the desired end state and the engine handles validation, diffing, and execution.

Example config:
{
  "device": "brocade-core",
  "vlans": {
    "100": {
      "name": "Production",
      "untagged_ports": ["1/1/1", "1/1/2", "1/2/1", "1/2/2"]
    }
  }
}

Supports port ranges like "1/1/1-4" which expands to ["1/1/1", "1/1/2", "1/1/3", "1/1/4"].
Use dry_run=true to preview changes without applying.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "description": "Desired state configuration with device, vlans, ports"
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview changes without applying (default: false)",
                        "default": False
                    },
                    "audit_context": {
                        "type": "string",
                        "description": "Description for audit log (e.g., 'Add production VLAN')"
                    }
                },
                "required": ["config"]
            }
        ),
        # === Configuration Management Tools ===
        Tool(
            name="config_save",
            description="""Save current device state as the desired configuration.

This captures the current VLAN and port configuration from the device and stores it
as the desired state in ~/.switchcraft/configs/desired/. Future drift checks will
compare against this saved state.

Use this to:
- Initialize config management for a device
- Accept manual changes as the new baseline
- Create a known-good configuration checkpoint""",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID to save config from"
                    },
                    "source": {
                        "type": "string",
                        "description": "Source tag (manual, auto_save, sync)",
                        "default": "manual"
                    }
                },
                "required": ["device_id"]
            }
        ),
        Tool(
            name="config_status",
            description="""Show configuration sync status for devices.

Compares the desired state (from ~/.switchcraft/configs/desired/) against
the actual device configuration. Reports:
- IN SYNC: Device matches desired state
- DRIFT: Device has changed from desired state
- UNMANAGED: No desired state defined yet
- UNREACHABLE: Cannot connect to device

Use device_id to check a specific device, or omit for all devices.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID to check (optional, defaults to all)"
                    },
                    "detailed": {
                        "type": "boolean",
                        "description": "Include detailed drift information",
                        "default": False
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="config_snapshot",
            description="""Create a snapshot of current desired configurations.

Saves all (or specified) desired configs to a timestamped snapshot directory.
Snapshots can be restored later with config_restore.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Snapshot name (default: timestamp)"
                    },
                    "device_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Devices to snapshot (default: all)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="config_restore",
            description="""Restore desired configurations from a snapshot.

Restores saved configs from a snapshot back to the desired directory.
Does NOT apply to devices - use config_sync to apply after restore.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Snapshot name to restore from"
                    },
                    "device_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Devices to restore (default: all in snapshot)"
                    }
                },
                "required": ["name"]
            }
        ),
        Tool(
            name="config_history",
            description="""View version history for device configurations.

Shows git commit history for config changes. Each change is tracked with:
- Commit hash for reference
- Timestamp and author
- Commit message describing the change

Use revision (e.g., HEAD~3, commit hash) with config_rollback to restore.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID to show history for (optional, shows all if omitted)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum commits to return (default: 20)",
                        "default": 20
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="config_rollback",
            description="""Rollback a device config to a previous version.

Restores a config from a git revision (commit hash or reference like HEAD~1).
Creates a new commit recording the rollback.

Does NOT apply to devices - use config_status then apply_config to sync.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID to rollback"
                    },
                    "revision": {
                        "type": "string",
                        "description": "Git revision to restore from (e.g., HEAD~1, abc1234)"
                    }
                },
                "required": ["device_id", "revision"]
            }
        ),
        Tool(
            name="config_diff",
            description="""Show diff between config versions.

Compares two revisions of a device config and shows changes.
Useful for reviewing what changed between versions.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID to diff"
                    },
                    "revision1": {
                        "type": "string",
                        "description": "First revision (older, default: HEAD~1)",
                        "default": "HEAD~1"
                    },
                    "revision2": {
                        "type": "string",
                        "description": "Second revision (newer, default: HEAD)",
                        "default": "HEAD"
                    }
                },
                "required": ["device_id"]
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    device_id = arguments.get("device_id", "N/A")

    async with timed_section(f"tool:{name}", device_id=device_id):
        try:
            inv = get_inventory()

            if name == "list_devices":
                return await handle_list_devices(inv)

            elif name == "device_status":
                return await handle_device_status(inv, arguments["device_id"])

            elif name == "get_config":
                return await handle_get_config(
                    inv,
                    arguments["device_id"],
                    arguments.get("include_raw", False)
                )

            elif name == "get_vlans":
                return await handle_get_vlans(inv, arguments["device_id"])

            elif name == "get_ports":
                return await handle_get_ports(inv, arguments["device_id"])

            elif name == "execute_command":
                return await handle_execute_command(
                    inv,
                    arguments["device_id"],
                    arguments["command"]
                )

            elif name == "create_vlan":
                return await handle_create_vlan(inv, arguments)

            elif name == "delete_vlan":
                return await handle_delete_vlan(
                    inv,
                    arguments["device_id"],
                    arguments["vlan_id"],
                    arguments.get("dry_run", False)
                )

            elif name == "configure_port":
                return await handle_configure_port(inv, arguments)

            elif name == "save_config":
                return await handle_save_config(inv, arguments["device_id"])

            elif name == "diff_config":
                return await handle_diff_config(
                    inv,
                    arguments["device_id"],
                    arguments["expected_config"]
                )

            elif name == "download_config_file":
                return await handle_download_config(
                    inv,
                    arguments["device_id"],
                    arguments["config_name"]
                )

            elif name == "upload_config_file":
                return await handle_upload_config(
                    inv,
                    arguments["device_id"],
                    arguments["config_name"],
                    arguments["content"],
                    arguments.get("reload", True)
                )

            elif name == "batch_command":
                return await handle_batch_command(
                    inv,
                    arguments["device_ids"],
                    arguments["command"]
                )

            elif name == "execute_config_batch":
                return await handle_execute_config_batch(
                    inv,
                    arguments["device_id"],
                    arguments["commands"],
                    arguments.get("stop_on_error", True)
                )

            elif name == "execute_batch":
                return await handle_execute_batch(
                    inv,
                    arguments["device_id"],
                    arguments["commands"]
                )

            elif name == "get_audit_log":
                return await handle_get_audit_log(
                    arguments.get("device_id"),
                    arguments.get("operation"),
                    arguments.get("limit", 20)
                )

            elif name == "apply_config":
                return await handle_apply_config(
                    inv,
                    arguments["config"],
                    arguments.get("dry_run", False),
                    arguments.get("audit_context", "")
                )

            elif name == "config_save":
                return await handle_config_save(
                    inv,
                    arguments["device_id"],
                    arguments.get("source", "manual")
                )

            elif name == "config_status":
                return await handle_config_status(
                    inv,
                    arguments.get("device_id"),
                    arguments.get("detailed", False)
                )

            elif name == "config_snapshot":
                return await handle_config_snapshot(
                    arguments.get("name"),
                    arguments.get("device_ids")
                )

            elif name == "config_restore":
                return await handle_config_restore(
                    arguments["name"],
                    arguments.get("device_ids")
                )

            elif name == "config_history":
                return await handle_config_history(
                    arguments.get("device_id"),
                    arguments.get("limit", 20)
                )

            elif name == "config_rollback":
                return await handle_config_rollback(
                    arguments["device_id"],
                    arguments["revision"]
                )

            elif name == "config_diff":
                return await handle_config_diff_versions(
                    arguments["device_id"],
                    arguments.get("revision1", "HEAD~1"),
                    arguments.get("revision2", "HEAD")
                )

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as e:
            logger.exception(f"Tool {name} failed")
            return [TextContent(type="text", text=f"Error: {str(e)}")]


# === TOOL HANDLERS ===

async def handle_list_devices(inv: DeviceInventory) -> list[TextContent]:
    """List all configured devices."""
    devices = []
    for device_id in inv.get_device_ids():
        config = inv.get_device_config(device_id)
        devices.append({
            "id": device_id,
            "name": config.get("name", device_id),
            "type": config.get("type"),
            "host": config.get("host"),
            "protocol": config.get("protocol"),
            "port": config.get("port"),
        })

    return [TextContent(
        type="text",
        text=json.dumps({"devices": devices}, indent=2)
    )]


async def handle_device_status(inv: DeviceInventory, device_id: str) -> list[TextContent]:
    """Get device health status."""
    device = inv.get_device(device_id)

    async with device:
        status = await device.check_health()

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "reachable": status.reachable,
            "uptime": status.uptime,
            "firmware": status.firmware_version,
            "error": status.error,
        }, indent=2)
    )]


async def handle_get_config(
    inv: DeviceInventory,
    device_id: str,
    include_raw: bool
) -> list[TextContent]:
    """Get normalized device configuration."""
    device = inv.get_device(device_id)
    config = inv.get_device_config(device_id)

    async with device:
        vlans = await device.get_vlans()
        ports = await device.get_ports()
        raw = await device.get_running_config() if include_raw else ""

    normalized = normalize_config(
        device_id=device_id,
        device_type=config.get("type", "unknown"),
        device_name=config.get("name", device_id),
        vlans=vlans,
        ports=ports,
        raw_config=raw if include_raw else "",
    )

    return [TextContent(type="text", text=normalized.to_json())]


async def handle_get_vlans(inv: DeviceInventory, device_id: str) -> list[TextContent]:
    """Get VLAN configurations."""
    device = inv.get_device(device_id)

    async with device:
        vlans = await device.get_vlans()

    vlan_list = []
    for v in vlans:
        vlan_list.append({
            "id": v.id,
            "name": v.name,
            "tagged_ports": v.tagged_ports,
            "untagged_ports": v.untagged_ports,
        })

    return [TextContent(
        type="text",
        text=json.dumps({"device_id": device_id, "vlans": vlan_list}, indent=2)
    )]


async def handle_get_ports(inv: DeviceInventory, device_id: str) -> list[TextContent]:
    """Get port configurations."""
    device = inv.get_device(device_id)

    async with device:
        ports = await device.get_ports()

    port_list = []
    for p in ports:
        port_list.append({
            "name": p.name,
            "enabled": p.enabled,
            "speed": p.speed,
            "description": p.description,
        })

    return [TextContent(
        type="text",
        text=json.dumps({"device_id": device_id, "ports": port_list}, indent=2)
    )]


async def handle_execute_command(
    inv: DeviceInventory,
    device_id: str,
    command: str
) -> list[TextContent]:
    """Execute raw command on device."""
    device = inv.get_device(device_id)

    async with device:
        success, output = await device.execute(command)

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "command": command,
            "success": success,
            "output": output,
        }, indent=2)
    )]


async def handle_create_vlan(inv: DeviceInventory, args: dict) -> list[TextContent]:
    """Create or update a VLAN with dry-run support and audit logging."""
    device_id = args["device_id"]
    dry_run = args.get("dry_run", False)
    device = inv.get_device(device_id)
    tracker = ChangeTracker(device_id)

    vlan = VLANConfig(
        id=args["vlan_id"],
        name=args.get("name", f"VLAN{args['vlan_id']}"),
        tagged_ports=args.get("tagged_ports", []),
        untagged_ports=args.get("untagged_ports", []),
    )

    async with device:
        # Capture before state for rollback/audit
        before_vlans = await device.get_vlans()
        before_state = {
            "vlans": [{"id": v.id, "name": v.name} for v in before_vlans]
        }
        tracker.snapshot("before", before_state)

        if dry_run:
            # Preview mode - don't actually apply changes
            output = f"DRY RUN: Would create VLAN {vlan.id} ({vlan.name})"
            if vlan.tagged_ports:
                output += f"\n  Tagged ports: {vlan.tagged_ports}"
            if vlan.untagged_ports:
                output += f"\n  Untagged ports: {vlan.untagged_ports}"
            success = True

            # Log the dry-run
            tracker.log_change(
                operation="create_vlan",
                parameters={"vlan_id": vlan.id, "name": vlan.name},
                success=True,
                output=output,
                dry_run=True,
                before_state=before_state,
            )
        else:
            # Actually create the VLAN
            success, output = await device.create_vlan(vlan)

            # Capture after state
            after_vlans = await device.get_vlans()
            after_state = {
                "vlans": [{"id": v.id, "name": v.name} for v in after_vlans]
            }

            # Log the change
            tracker.log_change(
                operation="create_vlan",
                parameters={"vlan_id": vlan.id, "name": vlan.name},
                success=success,
                output=output,
                dry_run=False,
                before_state=before_state,
                after_state=after_state,
                error=None if success else output,
            )

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "action": "create_vlan",
            "vlan_id": args["vlan_id"],
            "dry_run": dry_run,
            "success": success,
            "output": output,
        }, indent=2)
    )]


async def handle_delete_vlan(
    inv: DeviceInventory,
    device_id: str,
    vlan_id: int,
    dry_run: bool = False
) -> list[TextContent]:
    """Delete a VLAN with dry-run support and audit logging."""
    device = inv.get_device(device_id)
    tracker = ChangeTracker(device_id)

    async with device:
        # Capture before state
        before_vlans = await device.get_vlans()
        before_state = {
            "vlans": [{"id": v.id, "name": v.name} for v in before_vlans]
        }
        tracker.snapshot("before", before_state)

        # Check if VLAN exists
        vlan_exists = any(v.id == vlan_id for v in before_vlans)
        existing_vlan = next((v for v in before_vlans if v.id == vlan_id), None)

        if dry_run:
            # Preview mode
            if vlan_exists:
                output = f"DRY RUN: Would delete VLAN {vlan_id} ({existing_vlan.name})"
                success = True
            else:
                output = f"DRY RUN: VLAN {vlan_id} does not exist"
                success = True

            tracker.log_change(
                operation="delete_vlan",
                parameters={"vlan_id": vlan_id},
                success=True,
                output=output,
                dry_run=True,
                before_state=before_state,
            )
        else:
            # Actually delete
            success, output = await device.delete_vlan(vlan_id)

            # Capture after state
            after_vlans = await device.get_vlans()
            after_state = {
                "vlans": [{"id": v.id, "name": v.name} for v in after_vlans]
            }

            tracker.log_change(
                operation="delete_vlan",
                parameters={"vlan_id": vlan_id},
                success=success,
                output=output,
                dry_run=False,
                before_state=before_state,
                after_state=after_state,
                error=None if success else output,
            )

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "action": "delete_vlan",
            "vlan_id": vlan_id,
            "dry_run": dry_run,
            "success": success,
            "output": output,
        }, indent=2)
    )]


async def handle_configure_port(inv: DeviceInventory, args: dict) -> list[TextContent]:
    """Configure a port with dry-run support and audit logging."""
    device_id = args["device_id"]
    dry_run = args.get("dry_run", False)
    device = inv.get_device(device_id)
    tracker = ChangeTracker(device_id)

    port = PortConfig(
        name=args["port_name"],
        enabled=args.get("enabled", True),
        speed=args.get("speed"),
        description=args.get("description", ""),
    )

    async with device:
        # Capture before state
        before_ports = await device.get_ports()
        current_port = next((p for p in before_ports if p.name == port.name), None)
        before_state = {
            "port": port.name,
            "current": {
                "enabled": current_port.enabled if current_port else None,
                "speed": current_port.speed if current_port else None,
                "description": current_port.description if current_port else None,
            } if current_port else None
        }

        if dry_run:
            # Preview mode
            changes = []
            if current_port:
                if current_port.enabled != port.enabled:
                    changes.append(f"enabled: {current_port.enabled} -> {port.enabled}")
                if port.speed and current_port.speed != port.speed:
                    changes.append(f"speed: {current_port.speed} -> {port.speed}")
                if port.description and current_port.description != port.description:
                    changes.append(f"description: '{current_port.description}' -> '{port.description}'")

            if changes:
                output = f"DRY RUN: Would configure port {port.name}:\n  " + "\n  ".join(changes)
            else:
                output = f"DRY RUN: Port {port.name} - no changes detected"
            success = True

            tracker.log_change(
                operation="configure_port",
                parameters={"port": port.name, "enabled": port.enabled},
                success=True,
                output=output,
                dry_run=True,
                before_state=before_state,
            )
        else:
            # Actually configure
            success, output = await device.configure_port(port)

            # Capture after state
            after_ports = await device.get_ports()
            after_port = next((p for p in after_ports if p.name == port.name), None)
            after_state = {
                "port": port.name,
                "current": {
                    "enabled": after_port.enabled if after_port else None,
                    "speed": after_port.speed if after_port else None,
                    "description": after_port.description if after_port else None,
                } if after_port else None
            }

            tracker.log_change(
                operation="configure_port",
                parameters={"port": port.name, "enabled": port.enabled},
                success=success,
                output=output,
                dry_run=False,
                before_state=before_state,
                after_state=after_state,
                error=None if success else output,
            )

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "action": "configure_port",
            "port": args["port_name"],
            "dry_run": dry_run,
            "success": success,
            "output": output,
        }, indent=2)
    )]


async def handle_save_config(inv: DeviceInventory, device_id: str) -> list[TextContent]:
    """Save device configuration."""
    device = inv.get_device(device_id)

    async with device:
        success, output = await device.save_config()

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "action": "save_config",
            "success": success,
            "output": output,
        }, indent=2)
    )]


async def handle_diff_config(
    inv: DeviceInventory,
    device_id: str,
    expected_config: dict
) -> list[TextContent]:
    """Compare expected vs actual configuration."""
    device = inv.get_device(device_id)
    config = inv.get_device_config(device_id)

    async with device:
        vlans = await device.get_vlans()
        ports = await device.get_ports()

    actual = normalize_config(
        device_id=device_id,
        device_type=config.get("type", "unknown"),
        device_name=config.get("name", device_id),
        vlans=vlans,
        ports=ports,
    )

    expected = NetworkConfig.from_dict({
        "device_id": device_id,
        "device_type": config.get("type", "unknown"),
        "device_name": config.get("name", device_id),
        **expected_config
    })

    diff = diff_configs(expected, actual)

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "has_changes": diff.has_changes(),
            "changes": diff.changes,
            "summary": diff.to_text(),
        }, indent=2)
    )]


async def handle_download_config(
    inv: DeviceInventory,
    device_id: str,
    config_name: str
) -> list[TextContent]:
    """Download config file via SCP (ONTI only)."""
    device = inv.get_device(device_id)
    config = inv.get_device_config(device_id)

    if config.get("type") != "onti":
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": "SCP workflow only supported on ONTI devices",
                "device_type": config.get("type"),
            }, indent=2)
        )]

    async with device:
        content = await device.get_config_file(config_name)

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "config_name": config_name,
            "content": content,
            "hint": "Edit this content and use upload_config_file to apply changes",
        }, indent=2)
    )]


async def handle_upload_config(
    inv: DeviceInventory,
    device_id: str,
    config_name: str,
    content: str,
    reload: bool
) -> list[TextContent]:
    """Upload config file via SCP (ONTI only)."""
    device = inv.get_device(device_id)
    config = inv.get_device_config(device_id)

    # BUG-001 FIX: Validate content is not empty (can brick device!)
    if not content or not content.strip():
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": "Content cannot be empty - this would wipe the device config!",
                "hint": "Provide valid UCI configuration content",
            }, indent=2)
        )]

    if config.get("type") != "onti":
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": "SCP workflow only supported on ONTI devices",
                "device_type": config.get("type"),
            }, indent=2)
        )]

    async with device:
        success, output = await device.put_config_file(config_name, content)

        reload_output = ""
        if success and reload:
            reload_success, reload_output = await device.reload_config()
            success = success and reload_success

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "config_name": config_name,
            "success": success,
            "output": output,
            "reload_output": reload_output if reload else "skipped",
        }, indent=2)
    )]


async def handle_batch_command(
    inv: DeviceInventory,
    device_ids: list[str],
    command: str
) -> list[TextContent]:
    """Execute command on multiple devices."""
    if device_ids == ["all"] or "all" in device_ids:
        device_ids = inv.get_device_ids()

    results = []

    async def run_on_device(did: str):
        try:
            device = inv.get_device(did)
            async with device:
                success, output = await device.execute(command)
            return {"device_id": did, "success": success, "output": output}
        except Exception as e:
            return {"device_id": did, "success": False, "output": str(e)}

    # Run in parallel
    tasks = [run_on_device(did) for did in device_ids]
    results = await asyncio.gather(*tasks)

    return [TextContent(
        type="text",
        text=json.dumps({
            "command": command,
            "results": results,
        }, indent=2)
    )]


async def handle_execute_config_batch(
    inv: DeviceInventory,
    device_id: str,
    commands: list[str],
    stop_on_error: bool
) -> list[TextContent]:
    """Execute multiple config commands in a single fast batch.

    Uses batch execution to send all commands at once (much faster than
    one-by-one), with per-command error checking.
    """
    # Handle empty commands list gracefully
    if not commands:
        return [TextContent(
            type="text",
            text=json.dumps({
                "device_id": device_id,
                "success": True,
                "command_count": 0,
                "results": [],
                "raw_output": "",
            }, indent=2)
        )]

    device = inv.get_device(device_id)
    config = inv.get_device_config(device_id)

    # Currently only Brocade supports batch execution
    if config.get("type") != "brocade":
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": "Batch config execution currently only supported on Brocade devices",
                "device_type": config.get("type"),
                "hint": "Use execute_command with newline-separated commands for other devices",
            }, indent=2)
        )]

    async with device:
        # Use the fast batch execution (wraps commands in conf t / exit)
        full_commands = ["conf t"] + commands + ["exit"]
        success, raw_output, results = await device.execute_batch(
            full_commands,
            stop_on_error=stop_on_error
        )

    # Build response
    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "success": success,
            "command_count": len(commands),
            "results": results,
            "raw_output": raw_output,
        }, indent=2)
    )]


async def handle_execute_batch(
    inv: DeviceInventory,
    device_id: str,
    commands: list[str]
) -> list[TextContent]:
    """Execute multiple show/read commands in a single fast batch.

    Unlike execute_config_batch, this does NOT wrap commands in conf t/exit.
    Use this for show commands to get 3x speedup over individual calls.
    """
    # BUG-005 FIX: Handle empty commands list gracefully
    if not commands:
        return [TextContent(
            type="text",
            text=json.dumps({
                "device_id": device_id,
                "success": True,
                "command_count": 0,
                "results": [],
                "raw_output": "",
            }, indent=2)
        )]

    device = inv.get_device(device_id)
    config = inv.get_device_config(device_id)

    # Currently only Brocade supports batch execution
    if config.get("type") != "brocade":
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": "Batch execution currently only supported on Brocade devices",
                "device_type": config.get("type"),
            }, indent=2)
        )]

    async with device:
        # Direct batch execution without config mode wrapper
        success, raw_output, results = await device.execute_batch(commands)

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "success": success,
            "command_count": len(commands),
            "results": results,
            "raw_output": raw_output,
        }, indent=2)
    )]


async def handle_get_audit_log(
    device_id: Optional[str] = None,
    operation: Optional[str] = None,
    limit: int = 20
) -> list[TextContent]:
    """Get recent configuration changes from the audit log."""
    records = get_recent_changes(
        device_id=device_id,
        operation=operation,
        limit=limit
    )

    # Format for display
    formatted_records = []
    for r in records:
        formatted_records.append({
            "timestamp": r.timestamp,
            "device_id": r.device_id,
            "operation": r.operation,
            "dry_run": r.dry_run,
            "success": r.success,
            "parameters": r.parameters,
            "error": r.error,
        })

    return [TextContent(
        type="text",
        text=json.dumps({
            "total_records": len(formatted_records),
            "filters": {
                "device_id": device_id,
                "operation": operation,
                "limit": limit,
            },
            "records": formatted_records,
        }, indent=2)
    )]


async def handle_apply_config(
    inv: DeviceInventory,
    config: dict,
    dry_run: bool,
    audit_context: str
) -> list[TextContent]:
    """
    Apply a desired state configuration to a device.

    This is the primary tool for making changes. It:
    1. Validates the config for errors
    2. Calculates diff against current state
    3. Generates optimized command batches
    4. Executes with automatic error recovery
    5. Returns detailed results

    Use dry_run=True to preview changes without applying.
    """
    # Create config engine
    engine = ConfigEngine(inv)

    # Apply config (or dry-run)
    result = await engine.apply_config(
        config=config,
        dry_run=dry_run,
        audit_context=audit_context,
    )

    # Build response
    response = {
        "success": result.success,
        "dry_run": result.dry_run,
        "changes_made": result.changes_made,
    }

    if result.error:
        response["error"] = result.error

    if result.error_context:
        response["error_context"] = result.error_context

    if result.requires_ai_intervention:
        response["requires_ai_intervention"] = True
        response["message"] = (
            "Auto-recovery failed. Please review the error context and "
            "either fix the issue manually or provide more specific instructions."
        )

    if result.recovery_attempts:
        response["warnings"] = result.recovery_attempts

    if result.rollback_performed:
        response["rollback_performed"] = True

    if not result.dry_run and result.commands_executed:
        response["commands_executed"] = len(result.commands_executed)

    return [TextContent(
        type="text",
        text=json.dumps(response, indent=2)
    )]


# === CONFIG MANAGEMENT HANDLERS ===

async def handle_config_save(
    inv: DeviceInventory,
    device_id: str,
    source: str
) -> list[TextContent]:
    """
    Save current device state as the desired configuration.

    Fetches VLANs and ports from device and stores as desired state.
    """
    store = get_config_store()
    device = inv.get_device(device_id)

    try:
        async with device:
            vlans = await device.get_vlans()
            ports = await device.get_ports()

        # Convert to config dict format
        config = {
            "vlans": {},
            "ports": {},
        }

        for vlan in vlans:
            config["vlans"][vlan.id] = {
                "name": vlan.name,
                "untagged_ports": vlan.untagged_ports,
                "tagged_ports": vlan.tagged_ports,
            }

        for port in ports:
            config["ports"][port.name] = {
                "enabled": port.enabled,
                "speed": port.speed,
                "description": port.description,
            }

        # Save to config store
        stored = store.save_desired_config(
            device_id=device_id,
            config=config,
            source=source,
        )

        return [TextContent(
            type="text",
            text=json.dumps({
                "device_id": device_id,
                "action": "config_save",
                "success": True,
                "version": stored.version,
                "checksum": stored.checksum,
                "vlan_count": len(config["vlans"]),
                "port_count": len(config["ports"]),
                "saved_to": str(store.desired_dir / f"{device_id}.yaml"),
            }, indent=2)
        )]

    except Exception as e:
        logger.exception(f"Failed to save config for {device_id}")
        return [TextContent(
            type="text",
            text=json.dumps({
                "device_id": device_id,
                "action": "config_save",
                "success": False,
                "error": str(e),
            }, indent=2)
        )]


async def handle_config_status(
    inv: DeviceInventory,
    device_id: Optional[str],
    detailed: bool
) -> list[TextContent]:
    """
    Show configuration sync status for devices.

    Compares desired state against actual device configuration.
    """
    store = get_config_store()

    # Determine which devices to check
    if device_id:
        device_ids = [device_id]
    else:
        device_ids = inv.get_device_ids()

    results = []

    for did in device_ids:
        status = {
            "device_id": did,
            "status": "UNKNOWN",
        }

        # Check if we have a desired config
        desired = store.get_desired_config(did)
        if not desired:
            status["status"] = "UNMANAGED"
            status["message"] = "No desired config defined"
            results.append(status)
            continue

        # Try to connect and compare
        try:
            device = inv.get_device(did)

            async with device:
                vlans = await device.get_vlans()
                ports = await device.get_ports()

            # Convert to dict for drift detection
            actual_vlans = [
                {
                    "id": v.id,
                    "name": v.name,
                    "untagged_ports": v.untagged_ports,
                    "tagged_ports": v.tagged_ports,
                }
                for v in vlans
            ]
            actual_ports = [
                {
                    "name": p.name,
                    "enabled": p.enabled,
                    "speed": p.speed,
                    "description": p.description,
                }
                for p in ports
            ]

            # Calculate drift
            drift = store.calculate_drift(did, actual_vlans, actual_ports)

            if drift.in_sync:
                status["status"] = "IN_SYNC"
                status["message"] = "Device matches desired state"
            else:
                status["status"] = "DRIFT"
                status["drift_count"] = drift.drift_count
                status["message"] = f"{drift.drift_count} differences detected"

                if detailed:
                    status["drift_items"] = [
                        {
                            "category": item.category,
                            "item_id": item.item_id,
                            "drift_type": item.drift_type,
                            "details": item.details,
                        }
                        for item in drift.items
                    ]

            status["version"] = desired.version
            status["last_saved"] = desired.updated_at.isoformat() if desired.updated_at else None

        except Exception as e:
            status["status"] = "UNREACHABLE"
            status["error"] = str(e)

        results.append(status)

    # Build summary
    summary = {
        "total": len(results),
        "in_sync": sum(1 for r in results if r["status"] == "IN_SYNC"),
        "drift": sum(1 for r in results if r["status"] == "DRIFT"),
        "unmanaged": sum(1 for r in results if r["status"] == "UNMANAGED"),
        "unreachable": sum(1 for r in results if r["status"] == "UNREACHABLE"),
    }

    return [TextContent(
        type="text",
        text=json.dumps({
            "summary": summary,
            "devices": results,
        }, indent=2)
    )]


async def handle_config_snapshot(
    name: Optional[str],
    device_ids: Optional[list[str]]
) -> list[TextContent]:
    """Create a snapshot of current desired configurations."""
    store = get_config_store()

    try:
        snapshot_name = store.create_snapshot(name=name, device_ids=device_ids)

        return [TextContent(
            type="text",
            text=json.dumps({
                "action": "config_snapshot",
                "success": True,
                "snapshot_name": snapshot_name,
                "snapshot_path": str(store.snapshots_dir / snapshot_name),
            }, indent=2)
        )]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({
                "action": "config_snapshot",
                "success": False,
                "error": str(e),
            }, indent=2)
        )]


async def handle_config_restore(
    name: str,
    device_ids: Optional[list[str]]
) -> list[TextContent]:
    """Restore desired configurations from a snapshot."""
    store = get_config_store()

    try:
        restored = store.restore_snapshot(name=name, device_ids=device_ids)

        return [TextContent(
            type="text",
            text=json.dumps({
                "action": "config_restore",
                "success": True,
                "snapshot_name": name,
                "restored_devices": restored,
                "hint": "Use config_status to see differences, then apply_config to sync devices",
            }, indent=2)
        )]

    except ValueError as e:
        return [TextContent(
            type="text",
            text=json.dumps({
                "action": "config_restore",
                "success": False,
                "error": str(e),
                "available_snapshots": store.list_snapshots(),
            }, indent=2)
        )]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({
                "action": "config_restore",
                "success": False,
                "error": str(e),
            }, indent=2)
        )]


async def handle_config_history(
    device_id: Optional[str],
    limit: int
) -> list[TextContent]:
    """Get version history for device configurations."""
    store = get_config_store()

    history = store.get_config_history(device_id=device_id, limit=limit)

    return [TextContent(
        type="text",
        text=json.dumps({
            "action": "config_history",
            "device_id": device_id or "all",
            "commit_count": len(history),
            "commits": history,
            "hint": "Use config_rollback with a revision to restore a previous version",
        }, indent=2)
    )]


async def handle_config_rollback(
    device_id: str,
    revision: str
) -> list[TextContent]:
    """Rollback a device config to a previous version."""
    store = get_config_store()

    try:
        restored = store.restore_config_from_revision(
            device_id=device_id,
            revision=revision,
        )

        if restored is None:
            return [TextContent(
                type="text",
                text=json.dumps({
                    "action": "config_rollback",
                    "success": False,
                    "error": f"Could not find config for {device_id} at revision {revision}",
                }, indent=2)
            )]

        return [TextContent(
            type="text",
            text=json.dumps({
                "action": "config_rollback",
                "success": True,
                "device_id": device_id,
                "revision": revision,
                "new_version": restored.version,
                "hint": "Use config_status to verify, then apply_config to sync device",
            }, indent=2)
        )]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({
                "action": "config_rollback",
                "success": False,
                "error": str(e),
            }, indent=2)
        )]


async def handle_config_diff_versions(
    device_id: str,
    revision1: str,
    revision2: str
) -> list[TextContent]:
    """Show diff between two config versions."""
    store = get_config_store()

    diff_output = store.diff_config_revisions(
        device_id=device_id,
        revision1=revision1,
        revision2=revision2,
    )

    return [TextContent(
        type="text",
        text=json.dumps({
            "action": "config_diff",
            "device_id": device_id,
            "revision1": revision1,
            "revision2": revision2,
            "diff": diff_output if diff_output else "(no differences)",
        }, indent=2)
    )]


# === RESOURCES ===

@server.list_resources()
async def list_resources() -> list[Resource]:
    """List available resources."""
    inv = get_inventory()
    resources = []

    for device_id in inv.get_device_ids():
        config = inv.get_device_config(device_id)
        resources.append(Resource(
            uri=AnyUrl(f"switch://{device_id}/config"),
            name=f"{config.get('name', device_id)} Configuration",
            description=f"Running configuration for {device_id}",
            mimeType="application/json",
        ))

    return resources


@server.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    """Read a resource."""
    # Parse URI: switch://device_id/config
    uri_str = str(uri)
    if uri_str.startswith("switch://"):
        parts = uri_str[9:].split("/")
        if len(parts) >= 2:
            device_id = parts[0]
            resource_type = parts[1]

            inv = get_inventory()

            if resource_type == "config":
                result = await handle_get_config(inv, device_id, False)
                return result[0].text

    return json.dumps({"error": f"Unknown resource: {uri}"})


def main():
    """Run the MCP server."""

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options()
            )

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        # Cleanup
        if inventory:
            asyncio.run(inventory.close_all())


if __name__ == "__main__":
    main()
