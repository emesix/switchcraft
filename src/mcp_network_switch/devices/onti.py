"""ONTI S508CL switch handler via SSH with SCP config workflow.

ONTI switches run OpenWRT and use UCI for configuration.
Key insight from user: SCP download -> edit -> upload is MUCH faster than shell editing.
This implementation prioritizes the SCP-based workflow.
"""
import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

import paramiko
from scp import SCPClient

from .base import NetworkDevice, DeviceConfig, DeviceStatus, VLANConfig, PortConfig
from ..utils.connection import with_retry

logger = logging.getLogger(__name__)


class ONTIDevice(NetworkDevice):
    """ONTI OpenWRT-based switch handler with SCP workflow support."""

    def __init__(self, device_id: str, config: DeviceConfig):
        super().__init__(device_id, config)
        self._ssh: Optional[paramiko.SSHClient] = None
        self._scp: Optional[SCPClient] = None
        # Local cache for downloaded configs
        self._config_cache: dict[str, str] = {}
        self._cache_dir = Path(tempfile.mkdtemp(prefix="onti_"))

    @with_retry(max_attempts=3, min_wait=1, max_wait=10)
    async def connect(self) -> bool:
        """Connect to ONTI switch via SSH."""
        logger.info(f"Connecting to ONTI {self.device_id} at {self.host}")

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

        ssh = await loop.run_in_executor(None, _connect)
        self._ssh = ssh
        transport = ssh.get_transport()
        if transport is None:
            raise ConnectionError("Failed to get SSH transport")
        self._scp = SCPClient(transport)
        self._connected = True
        logger.info(f"Connected to {self.device_id}")
        return True

    async def disconnect(self) -> None:
        """Disconnect from ONTI switch."""
        if self._scp:
            self._scp.close()
            self._scp = None
        if self._ssh:
            self._ssh.close()
            self._ssh = None
        self._connected = False
        # Clean up cache
        self._config_cache.clear()
        logger.info(f"Disconnected from {self.device_id}")

    async def check_health(self) -> DeviceStatus:
        """Check device health."""
        try:
            if not self._connected:
                await self.connect()

            success, output = await self.execute("uptime")
            uptime = output.strip() if success else None

            success, version_out = await self.execute("cat /etc/openwrt_release")
            version = None
            if success:
                for line in version_out.split("\n"):
                    if "DISTRIB_DESCRIPTION" in line:
                        version = line.split("=")[-1].strip("'\"")
                        break

            return DeviceStatus(
                reachable=True,
                uptime=uptime,
                firmware_version=version,
            )
        except Exception as e:
            return DeviceStatus(reachable=False, error=str(e))

    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute a command via SSH."""
        if not self._ssh:
            raise ConnectionError("Not connected")

        ssh = self._ssh  # Local reference for type narrowing
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
            return False, f"{out}\n{err}".strip()
        return True, out

    async def execute_config_mode(self, commands: list[str]) -> tuple[bool, str]:
        """Execute UCI commands - ONTI doesn't have a config mode like Brocade."""
        outputs = []
        for cmd in commands:
            success, output = await self.execute(cmd)
            outputs.append(f"{cmd}: {output}")
            if not success:
                return False, "\n".join(outputs)
        return True, "\n".join(outputs)

    # === SCP-BASED CONFIG WORKFLOW (PREFERRED) ===

    async def download_config(self, remote_path: str, local_path: str) -> tuple[bool, str]:
        """Download config file via SCP - FAST!"""
        if not self._scp:
            raise ConnectionError("Not connected")

        scp = self._scp  # Local reference for type narrowing
        loop = asyncio.get_event_loop()
        try:
            def _download():
                scp.get(remote_path, local_path)

            await loop.run_in_executor(None, _download)
            logger.info(f"Downloaded {remote_path} to {local_path}")
            return True, f"Downloaded {remote_path}"
        except Exception as e:
            logger.error(f"SCP download failed: {e}")
            return False, str(e)

    async def upload_config(self, local_path: str, remote_path: str) -> tuple[bool, str]:
        """Upload config file via SCP - FAST!"""
        if not self._scp:
            raise ConnectionError("Not connected")

        scp = self._scp  # Local reference for type narrowing
        loop = asyncio.get_event_loop()
        try:
            def _upload():
                scp.put(local_path, remote_path)

            await loop.run_in_executor(None, _upload)
            logger.info(f"Uploaded {local_path} to {remote_path}")
            return True, f"Uploaded to {remote_path}"
        except Exception as e:
            logger.error(f"SCP upload failed: {e}")
            return False, str(e)

    async def reload_config(self) -> tuple[bool, str]:
        """Reload network configuration after SCP changes."""
        return await self.execute("/etc/init.d/network restart")

    async def get_config_file(self, config_name: str) -> str:
        """Download and return a config file's contents.

        Supported config names: network, system, firewall, wireless
        """
        remote_path = self.config.config_paths.get(
            config_name, f"/etc/config/{config_name}"
        )
        local_path = self._cache_dir / f"{config_name}.conf"

        success, msg = await self.download_config(remote_path, str(local_path))
        if not success:
            raise IOError(f"Failed to download {config_name}: {msg}")

        content = local_path.read_text()
        self._config_cache[config_name] = content
        return content

    async def put_config_file(self, config_name: str, content: str) -> tuple[bool, str]:
        """Upload a config file's contents via SCP.

        This is the FAST path - download, edit externally, upload.
        """
        remote_path = self.config.config_paths.get(
            config_name, f"/etc/config/{config_name}"
        )
        local_path = self._cache_dir / f"{config_name}.conf"

        # Write content locally
        local_path.write_text(content)

        # Upload via SCP
        success, msg = await self.upload_config(str(local_path), remote_path)
        if not success:
            return False, msg

        self._config_cache[config_name] = content
        return True, f"Uploaded {config_name} config"

    # === UCI COMMAND INTERFACE (SLOWER BUT GRANULAR) ===

    async def uci_get(self, key: str) -> tuple[bool, str]:
        """Get a UCI config value."""
        return await self.execute(f"uci get {key}")

    async def uci_set(self, key: str, value: str) -> tuple[bool, str]:
        """Set a UCI config value."""
        return await self.execute(f"uci set {key}='{value}'")

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

    # === STANDARD INTERFACE IMPLEMENTATION ===

    async def get_running_config(self) -> str:
        """Get all UCI configuration."""
        configs = []
        for config_name in ["network", "system", "firewall"]:
            try:
                content = await self.get_config_file(config_name)
                configs.append(f"# === {config_name.upper()} ===\n{content}")
            except Exception as e:
                configs.append(f"# === {config_name.upper()} (error: {e}) ===")
        return "\n\n".join(configs)

    async def get_vlans(self) -> list[VLANConfig]:
        """Get VLAN configurations from UCI network config."""
        vlans = []

        success, output = await self.execute("uci show network")
        if not success:
            return vlans

        # Parse UCI output for VLAN definitions
        # Format: network.@switch_vlan[0].vlan='254'
        current_vlan = {}

        for line in output.split("\n"):
            if "switch_vlan" in line:
                match = re.match(r"network\.(@switch_vlan\[\d+\]|[\w]+)\.(\w+)='?([^']*)'?", line)
                if match:
                    _, key, value = match.groups()
                    if key == "vlan":
                        if current_vlan:
                            vlans.append(self._parse_vlan_dict(current_vlan))
                        current_vlan = {"vlan": value}
                    else:
                        current_vlan[key] = value

        if current_vlan:
            vlans.append(self._parse_vlan_dict(current_vlan))

        return vlans

    def _parse_vlan_dict(self, d: dict) -> VLANConfig:
        """Convert UCI dict to VLANConfig."""
        vlan_id = int(d.get("vlan", 0))
        ports = d.get("ports", "").split()
        tagged = [p for p in ports if "t" in p]
        untagged = [p.replace("t", "") for p in ports if "t" not in p]

        return VLANConfig(
            id=vlan_id,
            name=d.get("description", f"VLAN{vlan_id}"),
            tagged_ports=[p.replace("t", "") for p in tagged],
            untagged_ports=untagged,
        )

    async def get_ports(self) -> list[PortConfig]:
        """Get port configurations."""
        ports = []
        # ONTI port config is device-specific
        success, output = await self.execute("swconfig dev switch0 show")
        if success:
            # Parse swconfig output
            for line in output.split("\n"):
                if "link:" in line.lower():
                    # Extract port info
                    port_match = re.search(r"Port (\d+):", line)
                    if port_match:
                        ports.append(PortConfig(
                            name=f"port{port_match.group(1)}",
                            enabled="up" in line.lower(),
                        ))
        return ports

    async def create_vlan(self, vlan: VLANConfig) -> tuple[bool, str]:
        """Create a VLAN using UCI commands."""
        commands = [
            "uci add network switch_vlan",
            "uci set network.@switch_vlan[-1].device='switch0'",
            f"uci set network.@switch_vlan[-1].vlan='{vlan.id}'",
        ]

        # Format ports - OpenWRT uses space-separated, t suffix for tagged
        ports = []
        ports.extend(vlan.untagged_ports)
        ports.extend([f"{p}t" for p in vlan.tagged_ports])
        if ports:
            commands.append(f"uci set network.@switch_vlan[-1].ports='{' '.join(ports)}'")

        commands.append("uci commit network")

        return await self.execute_config_mode(commands)

    async def delete_vlan(self, vlan_id: int) -> tuple[bool, str]:
        """Delete a VLAN via UCI."""
        # Find the vlan index first
        success, output = await self.execute("uci show network | grep switch_vlan")
        if not success:
            return False, "Failed to list VLANs"

        # Find matching vlan
        for line in output.split("\n"):
            if f".vlan='{vlan_id}'" in line or f".vlan={vlan_id}" in line:
                # Extract section name
                match = re.match(r"network\.(\S+)\.vlan", line)
                if match:
                    section = match.group(1)
                    return await self.execute_config_mode([
                        f"uci delete network.{section}",
                        "uci commit network"
                    ])

        return False, f"VLAN {vlan_id} not found"

    async def configure_port(self, port: PortConfig) -> tuple[bool, str]:
        """Configure a port - limited on ONTI."""
        # ONTI port config is via switch_port sections
        return False, "Port configuration not fully implemented for ONTI"

    async def save_config(self) -> tuple[bool, str]:
        """Save config - UCI changes are persistent after commit."""
        return await self.execute("uci commit")
