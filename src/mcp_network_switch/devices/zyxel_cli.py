"""Zyxel GS1900 switch handler via SSH CLI.

The Zyxel GS1900 series supports both HTTPS API and SSH CLI access.
This handler uses the CLI interface, similar to Brocade's approach.

Technical details:
- SSH with legacy algorithms (OpenSSH 6.2 - disable rsa-sha2-*)
- Interactive shell via invoke_shell()
- Cisco-like CLI with GS1900# prompt
- Pagination with --More-- (no terminal length 0)
- Port naming: simple numbers 1-26 for GS1900-24HP

Command Reference (GS1900-24HP):
- show vlan              : List all VLANs with port membership
- show vlan <id>         : Single VLAN details
- show running-config    : Full running configuration
- show interfaces X      : Interface status (X = port number or range)
- show interfaces switchport X : VLAN membership per port
- show version           : Firmware version info
- copy running-config startup-config : Save config
"""
import asyncio
import logging
import re
import time
from typing import Optional

import paramiko

from .base import NetworkDevice, DeviceConfig, DeviceStatus, VLANConfig, PortConfig
from ..utils.connection import with_retry
from ..utils.logging_config import timed, perf_logger

logger = logging.getLogger(__name__)

# Zyxel prompt pattern
PROMPT_PATTERN = re.compile(r"GS1900[#>]\s*$")
MORE_PATTERN = re.compile(r"--More--")


class ZyxelSSH:
    """Low-level SSH handler for Zyxel GS1900 switches."""

    def __init__(self, host: str, port: int, username: str, password: str, timeout: float = 30):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self._client: Optional[paramiko.SSHClient] = None
        self._shell: Optional[paramiko.Channel] = None

    async def connect(self) -> None:
        """Establish SSH connection with interactive shell."""
        loop = asyncio.get_event_loop()

        def _connect():
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            # Zyxel uses older OpenSSH 6.2 - disable modern algorithms
            client.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False,
                disabled_algorithms={'pubkeys': ['rsa-sha2-256', 'rsa-sha2-512']}
            )
            return client

        self._client = await loop.run_in_executor(None, _connect)

        # Get interactive shell
        def _get_shell():
            shell = self._client.invoke_shell()
            shell.settimeout(self.timeout)
            return shell

        self._shell = await loop.run_in_executor(None, _get_shell)

        # Wait for initial banner and handle "Press <Enter>" prompt
        await asyncio.sleep(1.5)
        await self._read_until_prompt(timeout=10)

    async def close(self) -> None:
        """Close SSH connection."""
        if self._shell:
            try:
                self._shell.close()
            except Exception:
                pass
            self._shell = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    async def _read_available(self, timeout: float = 1) -> str:
        """Read available data from shell."""
        if not self._shell:
            raise ConnectionError("Not connected")

        loop = asyncio.get_event_loop()

        def _recv():
            try:
                if self._shell.recv_ready():
                    data = self._shell.recv(65535)
                    # Clean ANSI escape codes
                    text = data.decode('utf-8', errors='ignore')
                    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
                    text = re.sub(r'\x1b\[\??\d+[hl]', '', text)  # cursor control
                    return text
                return ""
            except Exception as e:
                logger.debug(f"Read error: {e}")
                return ""

        return await asyncio.wait_for(
            loop.run_in_executor(None, _recv),
            timeout=timeout
        )

    async def _read_until_prompt(self, timeout: float = 30) -> str:
        """Read until we see a prompt or timeout."""
        output = ""
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                break

            try:
                chunk = await self._read_available(timeout=min(2, timeout - elapsed))
                if chunk:
                    output += chunk

                    # Check for prompt
                    if PROMPT_PATTERN.search(output):
                        break

                    # Handle --More-- pagination
                    if MORE_PATTERN.search(output):
                        await self._send_raw(" ")  # Space to continue
                        # Remove --More-- from output
                        output = re.sub(r'--More--\s*', '', output)
                        await asyncio.sleep(0.3)
                else:
                    # No data available, short sleep
                    await asyncio.sleep(0.2)

            except asyncio.TimeoutError:
                await asyncio.sleep(0.1)

        return output

    async def _send_raw(self, data: str) -> None:
        """Send raw string to shell."""
        if not self._shell:
            raise ConnectionError("Not connected")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._shell.send, data)

    async def send_command(self, command: str, timeout: float = 30) -> str:
        """Send a command and return the output."""
        await self._send_raw(f"{command}\r\n")
        await asyncio.sleep(0.5)  # Wait for command to be processed
        output = await self._read_until_prompt(timeout=timeout)

        # Clean up output - remove the command echo and prompt
        lines = output.split("\n")
        # Remove first line (command echo)
        if lines and command in lines[0]:
            lines = lines[1:]
        # Remove last line if it's the prompt
        if lines and PROMPT_PATTERN.search(lines[-1]):
            lines = lines[:-1]

        return "\n".join(lines).strip()


class ZyxelCLIDevice(NetworkDevice):
    """Zyxel GS1900 switch handler via SSH CLI."""

    def __init__(self, device_id: str, config: DeviceConfig):
        super().__init__(device_id, config)
        self._ssh: Optional[ZyxelSSH] = None

    # Error patterns that indicate command failure (must appear at line start)
    ERROR_PATTERNS = [
        r"^Invalid",
        r"^Unknown command",
        r"^Error[:\s]",
        r"^Incomplete command",
        r"^.*not found$",
    ]

    # Patterns that look like errors but are actually OK (statistics, etc.)
    INFO_PATTERNS = [
        r"\d+\s+(input\s+)?errors",  # "0 input errors"
        r"errors,",  # statistics line
    ]

    def _has_error(self, output: str) -> Optional[str]:
        """Check if output contains error indicators.

        Only matches errors at line start to avoid false positives from
        interface statistics like "0 input errors".
        """
        for line in output.split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # Check if line matches any info pattern (false positive)
            is_info = any(
                re.search(info_pat, line_stripped, re.IGNORECASE)
                for info_pat in self.INFO_PATTERNS
            )
            if is_info:
                continue

            # Check if line matches any error pattern
            for pattern in self.ERROR_PATTERNS:
                if re.search(pattern, line_stripped, re.IGNORECASE):
                    return line_stripped

        return None

    @with_retry(max_attempts=3, min_wait=2, max_wait=10)
    @timed("connect")
    async def connect(self) -> bool:
        """Connect to Zyxel switch via SSH CLI."""
        logger.info(f"Connecting to Zyxel CLI {self.device_id} at {self.host}")

        self._ssh = ZyxelSSH(
            self.host,
            self.config.port,
            self.config.username,
            self.config.get_password(),
            timeout=self.config.timeout
        )
        await self._ssh.connect()

        self._connected = True
        logger.info(f"Connected to {self.device_id} via CLI")
        return True

    async def disconnect(self) -> None:
        """Disconnect from Zyxel switch."""
        if self._ssh:
            await self._ssh.close()
            self._ssh = None
        self._connected = False
        logger.info(f"Disconnected from {self.device_id}")

    async def check_health(self) -> DeviceStatus:
        """Check device health."""
        try:
            if not self._connected:
                await self.connect()

            # Get version info
            output = await self.execute("show version")
            version = None
            uptime = None

            if output[0]:
                for line in output[1].split("\n"):
                    if "Version" in line or "version" in line:
                        version = line.strip()
                    if "uptime" in line.lower():
                        uptime = line.strip()

            return DeviceStatus(
                reachable=True,
                uptime=uptime,
                firmware_version=version,
            )
        except Exception as e:
            return DeviceStatus(reachable=False, error=str(e))

    @with_retry(max_attempts=3, min_wait=1, max_wait=5)
    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute a command on the Zyxel switch."""
        if not self._ssh:
            raise ConnectionError("Not connected")

        start = time.perf_counter()
        try:
            output = await self._ssh.send_command(command, timeout=self.config.timeout)
            elapsed = (time.perf_counter() - start) * 1000

            # Check for error messages
            error = self._has_error(output)
            if error:
                perf_logger.debug(
                    f"{'execute':20s} | {self.device_id:15s} | {elapsed:8.2f}ms | "
                    f"FAIL | cmd={command[:50]}"
                )
                return False, output

            perf_logger.debug(
                f"{'execute':20s} | {self.device_id:15s} | {elapsed:8.2f}ms | "
                f"OK | cmd={command[:50]}"
            )
            return True, output
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            perf_logger.warning(
                f"{'execute':20s} | {self.device_id:15s} | {elapsed:8.2f}ms | "
                f"ERROR | cmd={command[:50]} | {e}"
            )
            logger.error(f"Command failed on {self.device_id}: {e}")
            self._connected = False
            raise

    async def execute_config_mode(self, commands: list[str]) -> tuple[bool, str]:
        """Execute commands in config mode.

        Zyxel uses 'configure' to enter config mode.
        """
        # Enter config mode
        success, output = await self.execute("configure")
        if not success:
            return False, f"Failed to enter config mode: {output}"

        results = [output]
        overall_success = True

        for cmd in commands:
            success, cmd_output = await self.execute(cmd)
            results.append(f"{cmd}: {cmd_output}")
            if not success:
                overall_success = False
                break

        # Exit config mode
        await self.execute("exit")

        return overall_success, "\n".join(results)

    async def get_running_config(self) -> str:
        """Get running configuration."""
        success, output = await self.execute("show running-config")
        return output if success else ""

    async def get_vlans(self) -> list[VLANConfig]:
        """Get all VLAN configurations.

        Parses 'show vlan' output:
          VID  |     VLAN Name    |        Untagged Ports        |        Tagged Ports          |  Type
        -------+------------------+------------------------------+------------------------------+---------
             1 |          default |                  1-26,lag1-8 |                          --- | Default
           254 |   Management0254 |                            7 |                        25-26 | Static
        """
        success, output = await self.execute("show vlan")
        if not success:
            return []

        vlans = []

        for line in output.split("\n"):
            # Skip header lines
            if "|" not in line or line.startswith("---") or "VID" in line:
                continue

            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 5:
                continue

            try:
                vlan_id = int(parts[0])
                vlan_name = parts[1] or f"VLAN{vlan_id}"
                untagged_str = parts[2]
                tagged_str = parts[3]

                # Parse port lists
                untagged_ports = self._parse_port_list(untagged_str)
                tagged_ports = self._parse_port_list(tagged_str)

                vlans.append(VLANConfig(
                    id=vlan_id,
                    name=vlan_name,
                    tagged_ports=tagged_ports,
                    untagged_ports=untagged_ports,
                ))
            except (ValueError, IndexError):
                continue

        return vlans

    def _parse_port_list(self, port_str: str) -> list[str]:
        """Parse Zyxel port list string into individual port names.

        Input: "1-5,7,10-12,lag1-8"
        Output: ["1", "2", "3", "4", "5", "7", "10", "11", "12"]

        Ignores LAG ports for now.
        """
        if not port_str or port_str == "---":
            return []

        ports = []
        # Remove LAG references
        port_str = re.sub(r',?lag\d+-?\d*', '', port_str, flags=re.IGNORECASE)

        for part in port_str.split(","):
            part = part.strip()
            if not part:
                continue

            # Handle ranges like "1-5"
            range_match = re.match(r"(\d+)-(\d+)", part)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2))
                for i in range(start, end + 1):
                    ports.append(str(i))
            elif part.isdigit():
                ports.append(part)

        return ports

    def _format_port_list(self, ports: list[str]) -> str:
        """Format port list for Zyxel commands.

        Input: ["1", "2", "3", "5", "7", "8"]
        Output: "1-3,5,7-8"
        """
        if not ports:
            return ""

        # Convert to integers and sort
        port_nums = sorted(int(p) for p in ports if p.isdigit())
        if not port_nums:
            return ""

        # Build ranges
        ranges = []
        i = 0
        while i < len(port_nums):
            start = port_nums[i]
            end = start

            # Extend range
            while i + 1 < len(port_nums) and port_nums[i + 1] == port_nums[i] + 1:
                i += 1
                end = port_nums[i]

            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            i += 1

        return ",".join(ranges)

    async def get_ports(self) -> list[PortConfig]:
        """Get port configurations.

        Uses 'show interfaces X' for status.
        """
        success, output = await self.execute("show interfaces 1-26")
        if not success:
            return []

        ports = []
        current_port = None
        enabled = True
        speed = None

        for line in output.split("\n"):
            # Match port header: "GigabitEthernet1 is down"
            port_match = re.match(r"GigabitEthernet(\d+)\s+is\s+(\w+)", line)
            if port_match:
                # Save previous port
                if current_port:
                    ports.append(PortConfig(
                        name=current_port,
                        enabled=enabled,
                        speed=speed,
                    ))

                current_port = port_match.group(1)
                status = port_match.group(2).lower()
                enabled = status != "disabled"
                speed = None
                continue

            # Match speed: "Auto-duplex, Auto-speed" or "Full-duplex, 1000M-speed"
            speed_match = re.search(r"(\d+[MG]?)-speed", line)
            if speed_match:
                speed_raw = speed_match.group(1)
                # Normalize speed
                if "1000" in speed_raw or "1G" in speed_raw:
                    speed = "1G"
                elif "100" in speed_raw:
                    speed = "100M"
                elif "10G" in speed_raw:
                    speed = "10G"

        # Save last port
        if current_port:
            ports.append(PortConfig(
                name=current_port,
                enabled=enabled,
                speed=speed,
            ))

        return ports

    async def create_vlan(self, vlan: VLANConfig) -> tuple[bool, str]:
        """Create or update a VLAN.

        Zyxel VLAN configuration:
          configure
          vlan <id>
          name "<name>"
          fixed <port-list>
          untagged <port-list>
          exit
        """
        if vlan.id < 1 or vlan.id > 4094:
            return False, f"Invalid VLAN ID {vlan.id} - must be between 1 and 4094"

        vlan_name = vlan.name or f"VLAN{vlan.id}"

        commands = [f"vlan {vlan.id}"]

        if vlan_name:
            commands.append(f'name "{vlan_name}"')

        # Combine tagged and untagged for 'fixed' ports (all member ports)
        all_ports = list(set(vlan.tagged_ports + vlan.untagged_ports))
        if all_ports:
            commands.append(f"fixed {self._format_port_list(all_ports)}")

        if vlan.untagged_ports:
            commands.append(f"untagged {self._format_port_list(vlan.untagged_ports)}")

        commands.append("exit")

        return await self.execute_config_mode(commands)

    async def delete_vlan(self, vlan_id: int) -> tuple[bool, str]:
        """Delete a VLAN."""
        if vlan_id == 1:
            return False, "Cannot delete VLAN 1 (default VLAN)"
        if vlan_id < 1 or vlan_id > 4094:
            return False, f"Invalid VLAN ID {vlan_id}"

        return await self.execute_config_mode([f"no vlan {vlan_id}"])

    async def configure_port(self, port: PortConfig) -> tuple[bool, str]:
        """Configure a port."""
        commands = [f"interface port {port.name}"]

        if port.enabled:
            commands.append("no inactive")
        else:
            commands.append("inactive")

        if port.description:
            commands.append(f'name "{port.description}"')

        commands.append("exit")

        return await self.execute_config_mode(commands)

    async def save_config(self) -> tuple[bool, str]:
        """Save running config to startup config."""
        return await self.execute("copy running-config startup-config")
