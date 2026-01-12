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
from .devices.base import VLANConfig, PortConfig
from .utils.logging_config import setup_logging, timed_section

# Configure logging - now with file output and performance tracking
setup_logging()
logger = logging.getLogger(__name__)

# Global inventory (initialized on server start)
inventory: Optional[DeviceInventory] = None


def get_inventory() -> DeviceInventory:
    """Get or create the device inventory."""
    global inventory
    if inventory is None:
        config_path = os.environ.get("MCP_NETWORK_CONFIG")
        inventory = DeviceInventory(config_path)
    return inventory


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
            description="Create or update a VLAN on a device",
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
                    }
                },
                "required": ["device_id", "vlan_id"]
            }
        ),
        Tool(
            name="delete_vlan",
            description="Delete a VLAN from a device",
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
                    }
                },
                "required": ["device_id", "vlan_id"]
            }
        ),
        Tool(
            name="configure_port",
            description="Configure a port on a device",
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
                    arguments["vlan_id"]
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
    """Create or update a VLAN."""
    device_id = args["device_id"]
    device = inv.get_device(device_id)

    vlan = VLANConfig(
        id=args["vlan_id"],
        name=args.get("name", f"VLAN{args['vlan_id']}"),
        tagged_ports=args.get("tagged_ports", []),
        untagged_ports=args.get("untagged_ports", []),
    )

    async with device:
        success, output = await device.create_vlan(vlan)

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "action": "create_vlan",
            "vlan_id": args["vlan_id"],
            "success": success,
            "output": output,
        }, indent=2)
    )]


async def handle_delete_vlan(
    inv: DeviceInventory,
    device_id: str,
    vlan_id: int
) -> list[TextContent]:
    """Delete a VLAN."""
    device = inv.get_device(device_id)

    async with device:
        success, output = await device.delete_vlan(vlan_id)

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "action": "delete_vlan",
            "vlan_id": vlan_id,
            "success": success,
            "output": output,
        }, indent=2)
    )]


async def handle_configure_port(inv: DeviceInventory, args: dict) -> list[TextContent]:
    """Configure a port."""
    device_id = args["device_id"]
    device = inv.get_device(device_id)

    port = PortConfig(
        name=args["port_name"],
        enabled=args.get("enabled", True),
        speed=args.get("speed"),
        description=args.get("description", ""),
    )

    async with device:
        success, output = await device.configure_port(port)

    return [TextContent(
        type="text",
        text=json.dumps({
            "device_id": device_id,
            "action": "configure_port",
            "port": args["port_name"],
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
