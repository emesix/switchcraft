"""OpenWrt device handler for DSA-based switches.

Modern OpenWrt uses DSA (Distributed Switch Architecture) where each port
is a separate network interface (lan1, lan2, etc.) bridged together.

This handler supports:
- SSH connection with password auth
- UCI configuration interface
- DSA port management via sysfs/ip commands
- Bridge-based VLAN configuration
"""
import asyncio
import logging
import re
from typing import Optional

import paramiko

from .base import NetworkDevice, DeviceConfig, DeviceStatus, VLANConfig, PortConfig
from ..utils.connection import with_retry

logger = logging.getLogger(__name__)


class OpenWrtDevice(NetworkDevice):
    """OpenWrt DSA-based switch handler."""

    def __init__(self, device_id: str, config: DeviceConfig):
        super().__init__(device_id, config)
        self._ssh: Optional[paramiko.SSHClient] = None
        self._system_info: dict = {}

    @with_retry(max_attempts=3, min_wait=1, max_wait=10)
    async def connect(self) -> bool:
        """Connect to OpenWrt device via SSH."""
        logger.info(f"Connecting to OpenWrt {self.device_id} at {self.host}")

        loop = asyncio.get_event_loop()

        def _connect():
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=self.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.get_password(),
                timeout=self.config.timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            return ssh

        self._ssh = await loop.run_in_executor(None, _connect)
        self._connected = True
        logger.info(f"Connected to {self.device_id}")

        # Cache system info on connect
        await self._cache_system_info()
        return True

    async def _cache_system_info(self) -> None:
        """Cache system information for later use."""
        success, output = await self.execute("cat /etc/openwrt_release")
        if success:
            for line in output.split("\n"):
                if "=" in line:
                    key, _, value = line.partition("=")
                    self._system_info[key.strip()] = value.strip("'\"")

        # Detect available ports (DSA ports like lan1, lan2, etc.)
        success, output = await self.execute("ls -1 /sys/class/net/ | grep -E '^lan[0-9]+$'")
        if success:
            self._system_info["ports"] = [p for p in output.strip().split("\n") if p]
        else:
            self._system_info["ports"] = []

        # Detect bridge name - look for actual bridge device
        success, output = await self.execute(
            "uci -q get network.switch.type 2>/dev/null && echo 'switch' || "
            "ls /sys/class/net/br-lan/bridge 2>/dev/null && echo 'br-lan' || "
            "echo 'switch'"
        )
        if success:
            lines = output.strip().split("\n")
            self._system_info["bridge"] = lines[-1] if lines else "switch"

        # Check if bridge supports VLAN filtering
        bridge = self._system_info.get("bridge", "switch")
        success, output = await self.execute(
            f"cat /sys/class/net/{bridge}/bridge/vlan_filtering 2>/dev/null || echo '-1'"
        )
        if success:
            try:
                self._system_info["vlan_filtering"] = int(output.strip())
            except ValueError:
                self._system_info["vlan_filtering"] = -1  # Not a bridge
        else:
            self._system_info["vlan_filtering"] = -1

    async def disconnect(self) -> None:
        """Disconnect from OpenWrt device."""
        if self._ssh:
            self._ssh.close()
            self._ssh = None
        self._connected = False
        self._system_info.clear()
        logger.info(f"Disconnected from {self.device_id}")

    async def check_health(self) -> DeviceStatus:
        """Check device health and status."""
        try:
            if not self._connected:
                await self.connect()

            # Get uptime
            success, uptime_out = await self.execute("uptime")
            uptime = None
            if success:
                # Parse: "20:30:45 up 1 day, 2:30, load average: 0.00, 0.00, 0.00"
                match = re.search(r"up\s+(.+?),\s+load", uptime_out)
                if match:
                    uptime = match.group(1).strip()
                else:
                    uptime = uptime_out.strip()

            # Get version
            version = self._system_info.get("DISTRIB_DESCRIPTION") or \
                      self._system_info.get("DISTRIB_RELEASE", "Unknown")

            # Count ports
            ports = self._system_info.get("ports", [])
            port_count = len(ports)

            # Count active ports
            active_ports = 0
            for port in ports:
                success, state = await self.execute(f"cat /sys/class/net/{port}/operstate")
                if success and "up" in state.lower():
                    active_ports += 1

            return DeviceStatus(
                reachable=True,
                uptime=uptime,
                firmware_version=version,
                port_count=port_count,
                active_ports=active_ports,
            )
        except Exception as e:
            return DeviceStatus(reachable=False, error=str(e))

    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute a command via SSH."""
        if not self._ssh:
            raise ConnectionError("Not connected")

        ssh = self._ssh
        loop = asyncio.get_event_loop()

        def _exec():
            stdin, stdout, stderr = ssh.exec_command(
                command, timeout=self.config.timeout
            )
            out = stdout.read().decode("utf-8", errors="ignore")
            err = stderr.read().decode("utf-8", errors="ignore")
            exit_code = stdout.channel.recv_exit_status()
            return exit_code, out, err

        exit_code, out, err = await loop.run_in_executor(None, _exec)

        if exit_code != 0:
            logger.debug(f"Command '{command}' failed (exit {exit_code}): {err}")
            return False, f"{out}\n{err}".strip()
        return True, out.strip()

    async def execute_config_mode(self, commands: list[str]) -> tuple[bool, str]:
        """Execute multiple commands sequentially."""
        outputs = []
        for cmd in commands:
            success, output = await self.execute(cmd)
            outputs.append(f"$ {cmd}\n{output}")
            if not success:
                return False, "\n".join(outputs)
        return True, "\n".join(outputs)

    # === UCI Interface ===

    async def uci_get(self, key: str) -> tuple[bool, str]:
        """Get a UCI config value."""
        return await self.execute(f"uci get {key}")

    async def uci_set(self, key: str, value: str) -> tuple[bool, str]:
        """Set a UCI config value."""
        # Escape single quotes in value
        escaped = value.replace("'", "'\\''")
        return await self.execute(f"uci set {key}='{escaped}'")

    async def uci_add(self, config: str, section_type: str) -> tuple[bool, str]:
        """Add a new UCI section."""
        return await self.execute(f"uci add {config} {section_type}")

    async def uci_delete(self, key: str) -> tuple[bool, str]:
        """Delete a UCI config entry."""
        return await self.execute(f"uci delete {key}")

    async def uci_add_list(self, key: str, value: str) -> tuple[bool, str]:
        """Add a value to a UCI list."""
        escaped = value.replace("'", "'\\''")
        return await self.execute(f"uci add_list {key}='{escaped}'")

    async def uci_del_list(self, key: str, value: str) -> tuple[bool, str]:
        """Delete a value from a UCI list."""
        escaped = value.replace("'", "'\\''")
        return await self.execute(f"uci del_list {key}='{escaped}'")

    async def uci_commit(self, config: str = "") -> tuple[bool, str]:
        """Commit UCI changes."""
        if config:
            return await self.execute(f"uci commit {config}")
        return await self.execute("uci commit")

    async def uci_show(self, config: str = "") -> tuple[bool, str]:
        """Show UCI configuration."""
        if config:
            return await self.execute(f"uci show {config}")
        return await self.execute("uci show")

    # === Configuration Retrieval ===

    async def get_running_config(self) -> str:
        """Get all UCI network configuration."""
        success, output = await self.execute("uci export network")
        if success:
            return output
        # Fallback to show
        success, output = await self.uci_show("network")
        return output if success else ""

    async def get_vlans(self) -> list[VLANConfig]:
        """Get VLAN configurations.

        OpenWrt DSA VLANs can be configured in several ways:
        1. Bridge VLAN filtering (modern)
        2. Separate bridge per VLAN
        3. Tagged interfaces (e.g., lan1.100)
        """
        vlans = []

        # Method 1: Check for bridge VLAN devices
        success, output = await self.execute("uci show network | grep -E 'bridge-vlan|vlan'")
        if success and output:
            current_vlan: dict = {}
            for line in output.split("\n"):
                # Parse: network.vlan100=bridge-vlan
                #        network.vlan100.device='switch'
                #        network.vlan100.vlan='100'
                #        network.vlan100.ports='lan1:t lan2'
                match = re.match(r"network\.(\w+)\.(\w+)='?([^']*)'?", line)
                if match:
                    section, key, value = match.groups()
                    if key == "vlan":
                        if current_vlan and "id" in current_vlan:
                            vlans.append(self._dict_to_vlan(current_vlan))
                        current_vlan = {"section": section, "id": int(value)}
                    elif current_vlan:
                        current_vlan[key] = value

            if current_vlan and "id" in current_vlan:
                vlans.append(self._dict_to_vlan(current_vlan))

        # Method 2: Check for tagged VLAN interfaces (lan1.100 style)
        success, output = await self.execute("ls -1 /sys/class/net/ | grep -E '\\.[0-9]+$'")
        if success and output:
            for iface in output.split("\n"):
                if "." in iface:
                    base, vid = iface.rsplit(".", 1)
                    try:
                        vlan_id = int(vid)
                        # Check if we already have this VLAN
                        if not any(v.id == vlan_id for v in vlans):
                            vlans.append(VLANConfig(
                                id=vlan_id,
                                name=f"VLAN{vlan_id}",
                                tagged_ports=[base],
                            ))
                    except ValueError:
                        continue

        # If no VLANs found, report the default untagged setup
        if not vlans:
            bridge = self._system_info.get("bridge", "switch")
            ports = self._system_info.get("ports", [])
            vlans.append(VLANConfig(
                id=1,
                name="default",
                untagged_ports=ports,
                description=f"Default bridge ({bridge})",
            ))

        return vlans

    def _dict_to_vlan(self, d: dict) -> VLANConfig:
        """Convert parsed UCI dict to VLANConfig."""
        vlan_id = d.get("id", 0)
        tagged = []
        untagged = []

        # Parse ports: "lan1:t lan2 lan3:t" -> tagged=[lan1,lan3], untagged=[lan2]
        ports_str = d.get("ports", "")
        for port_spec in ports_str.split():
            if ":t" in port_spec:
                tagged.append(port_spec.replace(":t", ""))
            elif port_spec:
                untagged.append(port_spec)

        return VLANConfig(
            id=vlan_id,
            name=d.get("section", f"VLAN{vlan_id}"),
            tagged_ports=tagged,
            untagged_ports=untagged,
            description=d.get("description", ""),
        )

    async def get_ports(self) -> list[PortConfig]:
        """Get port configurations from sysfs."""
        ports = []
        port_names = self._system_info.get("ports", [])

        if not port_names:
            # Re-detect ports
            success, output = await self.execute("ls -1 /sys/class/net/ | grep -E '^lan[0-9]+$'")
            if success:
                port_names = [p for p in output.strip().split("\n") if p]

        for port_name in port_names:
            # Get link state
            success, state = await self.execute(f"cat /sys/class/net/{port_name}/operstate")
            enabled = success and state.strip() == "up"

            # Get speed
            speed = None
            success, speed_out = await self.execute(f"cat /sys/class/net/{port_name}/speed")
            if success:
                try:
                    speed_mbps = int(speed_out.strip())
                    if speed_mbps >= 10000:
                        speed = "10G"
                    elif speed_mbps >= 1000:
                        speed = "1G"
                    elif speed_mbps >= 100:
                        speed = "100M"
                    elif speed_mbps > 0:
                        speed = f"{speed_mbps}M"
                except ValueError:
                    pass

            # Get duplex
            duplex = None
            success, duplex_out = await self.execute(f"cat /sys/class/net/{port_name}/duplex")
            if success:
                duplex = duplex_out.strip()

            # Get description from UCI if available
            description = ""
            success, desc = await self.execute(f"uci get network.{port_name}.description 2>/dev/null")
            if success:
                description = desc.strip()

            ports.append(PortConfig(
                name=port_name,
                enabled=enabled,
                speed=speed,
                duplex=duplex,
                description=description,
            ))

        return ports

    # === Configuration Modification ===

    async def create_vlan(self, vlan: VLANConfig) -> tuple[bool, str]:
        """Create a VLAN using bridge-vlan UCI configuration.

        OpenWrt DSA VLAN setup requires:
        1. Bridge with vlan_filtering enabled
        2. bridge-vlan sections in UCI for port membership
        """
        if vlan.id < 1 or vlan.id > 4094:
            return False, f"Invalid VLAN ID {vlan.id} - must be between 1 and 4094"

        bridge = self._system_info.get("bridge", "switch")
        vlan_filtering = self._system_info.get("vlan_filtering", -1)
        section_name = f"vlan{vlan.id}"

        commands = []

        # Step 1: Enable VLAN filtering on bridge if not already enabled
        if vlan_filtering == 0:
            logger.info(f"Enabling VLAN filtering on bridge {bridge}")
            commands.extend([
                f"uci set network.{bridge}.vlan_filtering='1'",
            ])

        # Step 2: Create bridge-vlan section
        commands.extend([
            f"uci set network.{section_name}=bridge-vlan",
            f"uci set network.{section_name}.device='{bridge}'",
            f"uci set network.{section_name}.vlan='{vlan.id}'",
        ])

        # Step 3: Build ports specification: "lan1:t lan2" (t = tagged, * = pvid)
        ports_spec = []
        for port in vlan.tagged_ports:
            ports_spec.append(f"{port}:t")
        for port in vlan.untagged_ports:
            # Untagged ports get the VLAN as PVID
            ports_spec.append(f"{port}:u*")

        if ports_spec:
            commands.append(f"uci set network.{section_name}.ports='{' '.join(ports_spec)}'")
        else:
            # No ports specified - just create the VLAN
            commands.append(f"uci set network.{section_name}.ports=''")

        commands.append("uci commit network")

        success, output = await self.execute_config_mode(commands)
        if not success:
            return False, output

        # Step 4: Apply changes - reload network
        logger.info("Reloading network configuration...")
        reload_success, reload_output = await self.execute(
            "/etc/init.d/network reload 2>&1"
        )

        # Update cached vlan_filtering state
        if vlan_filtering == 0:
            self._system_info["vlan_filtering"] = 1

        if reload_success:
            return True, f"Created VLAN {vlan.id} on {bridge} with ports: {ports_spec}"
        else:
            return True, f"Created VLAN {vlan.id} (UCI committed, network reload pending: {reload_output})"

    async def delete_vlan(self, vlan_id: int) -> tuple[bool, str]:
        """Delete a VLAN."""
        if vlan_id == 1:
            return False, "Cannot delete default VLAN 1"

        if vlan_id < 1 or vlan_id > 4094:
            return False, f"Invalid VLAN ID {vlan_id}"

        section_name = f"vlan{vlan_id}"

        # Check if VLAN exists by common section name
        success, _ = await self.execute(f"uci get network.{section_name} 2>/dev/null")
        if not success:
            # Try to find by searching for the VLAN ID
            success, output = await self.execute(
                f"uci show network | grep -E \"\\.vlan='?{vlan_id}'?\" | head -1"
            )
            if success and output:
                match = re.match(r"network\.(\w+)\.vlan", output)
                if match:
                    section_name = match.group(1)
                else:
                    return False, f"VLAN {vlan_id} not found"
            else:
                return False, f"VLAN {vlan_id} not found"

        commands = [
            f"uci delete network.{section_name}",
            "uci commit network",
        ]

        success, output = await self.execute_config_mode(commands)
        if not success:
            return False, output

        # Apply changes
        reload_success, reload_output = await self.execute(
            "/etc/init.d/network reload 2>&1"
        )

        if reload_success:
            return True, f"Deleted VLAN {vlan_id}"
        else:
            return True, f"Deleted VLAN {vlan_id} (UCI committed, network reload pending)"

    async def configure_port(self, port: PortConfig) -> tuple[bool, str]:
        """Configure a port.

        For DSA ports, we can:
        - Enable/disable via ip link
        - Set description in UCI
        - Speed/duplex typically via ethtool
        """
        results = []

        # Enable/disable port
        if port.enabled:
            success, output = await self.execute(f"ip link set {port.name} up")
        else:
            success, output = await self.execute(f"ip link set {port.name} down")
        results.append(f"Port {port.name} {'enabled' if port.enabled else 'disabled'}: {output if not success else 'OK'}")

        # Set description in UCI (optional)
        if port.description:
            await self.uci_set(f"network.{port.name}.description", port.description)
            await self.uci_commit("network")
            results.append(f"Description set: {port.description}")

        # Speed configuration via ethtool if available
        if port.speed:
            speed_map = {"10G": "10000", "1G": "1000", "100M": "100", "auto": "autoneg on"}
            if port.speed in speed_map:
                if port.speed == "auto":
                    await self.execute(f"ethtool -s {port.name} autoneg on 2>/dev/null")
                else:
                    await self.execute(f"ethtool -s {port.name} speed {speed_map[port.speed]} 2>/dev/null")
                results.append(f"Speed set: {port.speed}")

        return True, "; ".join(results)

    async def save_config(self) -> tuple[bool, str]:
        """Save configuration - UCI commit persists to flash."""
        return await self.uci_commit()

    async def reload_network(self) -> tuple[bool, str]:
        """Reload network configuration."""
        return await self.execute("/etc/init.d/network restart")
