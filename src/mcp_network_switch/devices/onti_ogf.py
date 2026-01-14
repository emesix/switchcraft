"""ONTI S508CL Original Firmware switch handler via Telnet.

The ONTI S508CL with original firmware (not OpenWrt) has a Cisco-style CLI
accessible via telnet. This handler provides full management capabilities.

Technical details:
- Telnet on port 23 with login/password authentication
- Cisco-style CLI with Switch# prompt
- Port naming: Ethernet1/0/X (X = 1-8 for 8-port SFP+ model)
- Hardware: RTL930x-based with 8x SFP+ ports (10G capable)
- Default credentials: admin/admin

Command Reference (V300SP10250704 firmware):
- show version           : Device info, uptime, firmware
- show vlan              : List all VLANs with port membership
- show interface         : Detailed interface statistics
- show running-config    : Current running configuration
- show mac-address-table : MAC address table
- terminal length 0      : Disable --More-- pagination
- config                 : Enter configuration mode
- write                  : Save running config to startup

Port naming: Ethernet1/0/1 to Ethernet1/0/8 for 8-port model
"""
import asyncio
import logging
import re
import socket
import time
from typing import Optional

from .base import NetworkDevice, DeviceConfig, DeviceStatus, VLANConfig, PortConfig
from ..utils.connection import with_retry
from ..utils.logging_config import timed, perf_logger

logger = logging.getLogger(__name__)

# ONTI OGF prompt patterns
PROMPT_PATTERN = re.compile(r"Switch[#>(\(config\))]+\s*$")
CONFIG_PROMPT_PATTERN = re.compile(r"Switch\(config[^)]*\)#\s*$")
MORE_PATTERN = re.compile(r"--More--", re.IGNORECASE)


class OntiOGFTelnet:
    """Low-level telnet handler for ONTI Original Firmware switches."""

    def __init__(self, host: str, port: int, timeout: float = 30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None
        self._buffer = b""

    async def connect(self, username: str, password: str) -> None:
        """Establish telnet connection and authenticate."""
        loop = asyncio.get_event_loop()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(self.timeout)
        await loop.run_in_executor(
            None, self._socket.connect, (self.host, self.port)
        )

        # Wait for login prompt
        await asyncio.sleep(1)
        login_output = await self._read_until_pattern(r"login:", timeout=10)
        logger.debug(f"Login prompt received: {login_output[-50:]}")

        # Send username
        await self._send_raw(f"{username}\r\n".encode())
        await asyncio.sleep(0.5)

        # Wait for password prompt
        await self._read_until_pattern(r"[Pp]assword:", timeout=5)

        # Send password
        await self._send_raw(f"{password}\r\n".encode())
        await asyncio.sleep(1)

        # Wait for Switch# prompt
        output = await self._read_until_prompt(timeout=10)
        if "Switch#" not in output and "Switch>" not in output:
            raise ConnectionError(f"Login failed, got: {output[-100:]}")

        logger.info(f"Successfully authenticated to {self.host}")

    async def close(self) -> None:
        """Close telnet connection."""
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    async def _read_available(self, timeout: float = 1) -> bytes:
        """Read available data from socket."""
        if not self._socket:
            raise ConnectionError("Not connected")

        loop = asyncio.get_event_loop()
        self._socket.settimeout(timeout)
        try:
            data = await loop.run_in_executor(None, self._socket.recv, 8192)
            return data
        except socket.timeout:
            return b""
        except Exception as e:
            logger.debug(f"Read error: {e}")
            return b""

    async def _read_until_pattern(self, pattern: str, timeout: float = 30) -> str:
        """Read until we see a pattern or timeout."""
        output = b""
        start_time = asyncio.get_event_loop().time()
        compiled = re.compile(pattern, re.IGNORECASE)

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                break

            chunk = await self._read_available(timeout=min(2, timeout - elapsed))
            if chunk:
                output += chunk
                decoded = output.decode("ascii", errors="ignore")
                if compiled.search(decoded):
                    break

        return output.decode("ascii", errors="ignore")

    async def _read_until_prompt(self, timeout: float = 30) -> str:
        """Read until we see a prompt or timeout."""
        output = b""
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                break

            chunk = await self._read_available(timeout=min(2, timeout - elapsed))
            if chunk:
                output += chunk
                decoded = output.decode("ascii", errors="ignore")

                # Check for prompt
                if PROMPT_PATTERN.search(decoded):
                    break

                # Handle --More-- pagination
                if MORE_PATTERN.search(decoded):
                    await self._send_raw(b" ")  # Space to continue
                    await asyncio.sleep(0.3)

        return output.decode("ascii", errors="ignore")

    async def _send_raw(self, data: bytes) -> None:
        """Send raw bytes to socket."""
        if not self._socket:
            raise ConnectionError("Not connected")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._socket.sendall, data)

    async def send_command(self, command: str, timeout: float = 30) -> str:
        """Send a command and return the output."""
        await self._send_raw(f"{command}\r\n".encode())
        await asyncio.sleep(0.5)
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

    async def enter_config_mode(self) -> bool:
        """Enter configuration mode."""
        await self._send_raw(b"config\r\n")
        await asyncio.sleep(0.5)
        output = await self._read_until_prompt(timeout=5)
        return "config" in output.lower() or CONFIG_PROMPT_PATTERN.search(output) is not None

    async def exit_config_mode(self) -> None:
        """Exit configuration mode."""
        await self._send_raw(b"end\r\n")
        await asyncio.sleep(0.3)
        await self._read_until_prompt(timeout=5)


class OntiOGFDevice(NetworkDevice):
    """ONTI Original Firmware switch handler via Telnet."""

    def __init__(self, device_id: str, config: DeviceConfig):
        super().__init__(device_id, config)
        self._telnet: Optional[OntiOGFTelnet] = None

    @with_retry(max_attempts=3, min_wait=2, max_wait=10)
    @timed("connect")
    async def connect(self) -> bool:
        """Connect to ONTI OGF switch via telnet."""
        logger.info(f"Connecting to ONTI-OGF {self.device_id} at {self.host}")

        self._telnet = OntiOGFTelnet(
            self.host,
            self.config.port,
            timeout=self.config.timeout
        )

        username = self.config.username or "admin"
        password = self.config.get_password() or "admin"

        await self._telnet.connect(username, password)

        # Disable pagination
        await self._telnet.send_command("terminal length 0", timeout=5)

        self._connected = True
        logger.info(f"Connected to {self.device_id}")
        return True

    async def disconnect(self) -> None:
        """Disconnect from ONTI OGF switch."""
        if self._telnet:
            try:
                await self._telnet.send_command("exit", timeout=2)
            except Exception:
                pass
            await self._telnet.close()
            self._telnet = None
        self._connected = False
        logger.info(f"Disconnected from {self.device_id}")

    async def check_health(self) -> DeviceStatus:
        """Check device health."""
        try:
            if not self._connected:
                await self.connect()

            # Get version info
            success, output = await self.execute("show version")
            uptime = None
            version = None

            if success:
                for line in output.split("\n"):
                    if "Uptime" in line:
                        uptime = line.strip()
                    if "SoftWare Version" in line:
                        version = line.split()[-1] if line.split() else None

            # Get port count
            port_count = 8  # Default for ONTI S508CL
            active_ports = 0

            success, ports_output = await self.execute("show interface")
            if success:
                active_ports = ports_output.count("is up, line protocol is up")

            return DeviceStatus(
                reachable=True,
                uptime=uptime,
                firmware_version=version,
                port_count=port_count,
                active_ports=active_ports,
            )
        except Exception as e:
            return DeviceStatus(reachable=False, error=str(e))

    # Error patterns that indicate command failure
    ERROR_PATTERNS = [
        "Invalid input",
        "Error:",
        "error:",
        "Unknown command",
        "Incomplete command",
        "not found",
        "% ",
    ]

    def _has_error(self, output: str) -> Optional[str]:
        """Check if output contains error indicators."""
        for pattern in self.ERROR_PATTERNS:
            if pattern in output:
                for line in output.split("\n"):
                    if pattern in line:
                        return line.strip()
        return None

    @with_retry(max_attempts=3, min_wait=1, max_wait=5)
    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute a command on the ONTI OGF switch."""
        if not self._telnet:
            raise ConnectionError("Not connected")

        start = time.perf_counter()
        try:
            output = await self._telnet.send_command(command, timeout=self.config.timeout)
            elapsed = (time.perf_counter() - start) * 1000

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
        """Execute commands in configuration mode."""
        if not self._telnet:
            raise ConnectionError("Not connected")

        outputs = []

        # Enter config mode
        if not await self._telnet.enter_config_mode():
            return False, "Failed to enter config mode"

        try:
            for cmd in commands:
                output = await self._telnet.send_command(cmd, timeout=self.config.timeout)
                outputs.append(f"{cmd}: {output}" if output else cmd)

                error = self._has_error(output)
                if error:
                    outputs.append(f"ERROR: {error}")
                    return False, "\n".join(outputs)
        finally:
            # Always exit config mode
            await self._telnet.exit_config_mode()

        return True, "\n".join(outputs)

    async def get_running_config(self) -> str:
        """Get running configuration."""
        success, output = await self.execute("show running-config")
        return output if success else ""

    async def get_vlans(self) -> list[VLANConfig]:
        """Get all VLAN configurations.

        Parses 'show vlan' output:
        VLAN Name         Type       Media     Ports
        ---- ------------ ---------- --------- ----------------------------------------
        1    default      Static     ENET      Ethernet1/0/1       Ethernet1/0/2
        """
        success, output = await self.execute("show vlan")
        if not success:
            return []

        vlans = []
        lines = output.split("\n")

        for line in lines:
            # Skip header lines
            if line.startswith("VLAN") or line.startswith("----") or not line.strip():
                continue

            # Parse VLAN line
            # Format: "1    default      Static     ENET      Ethernet1/0/1 ..."
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                vlan_id = int(parts[0])
                vlan_name = parts[1]

                # Extract ports (everything after ENET)
                ports = []
                for part in parts:
                    if part.startswith("Ethernet"):
                        ports.append(part)

                vlans.append(VLANConfig(
                    id=vlan_id,
                    name=vlan_name,
                    untagged_ports=ports,  # Default assumption
                    tagged_ports=[],
                ))

        return vlans

    async def get_ports(self) -> list[PortConfig]:
        """Get port configurations.

        Parses 'show interface' output which shows detailed info for each port.
        """
        success, output = await self.execute("show interface")
        if not success:
            return []

        ports = []
        current_port = None
        current_status = {}

        for line in output.split("\n"):
            # Match interface header: "  Ethernet1/0/1 is down, line protocol is down"
            # Note: line may have leading whitespace
            match = re.match(r"\s*(Ethernet\d+/\d+/\d+) is (\w+), line protocol is (\w+)", line)
            if match:
                # Save previous port if exists
                if current_port and current_status:
                    ports.append(self._create_port_config(current_port, current_status))

                current_port = match.group(1)
                current_status = {
                    "link": match.group(2).lower(),
                    "protocol": match.group(3).lower(),
                }
                continue

            if current_port:
                # Parse additional info
                if "Hardware is" in line:
                    current_status["hardware"] = line.split("Hardware is")[-1].split(",")[0].strip()
                if "PVID is" in line:
                    pvid_match = re.search(r"PVID is (\d+)", line)
                    if pvid_match:
                        current_status["pvid"] = int(pvid_match.group(1))
                if "duplex" in line.lower():
                    if "full-duplex" in line.lower() or "full duplex" in line.lower():
                        current_status["duplex"] = "full"
                    elif "half-duplex" in line.lower() or "half duplex" in line.lower():
                        current_status["duplex"] = "half"
                # Parse speed: "Auto-speed: Negotiation 1G bits" or "BW 1000000 Kbit"
                if "speed" in line.lower() or "BW " in line:
                    speed_match = re.search(r"Negotiation (\d+[GMK])", line, re.IGNORECASE)
                    if speed_match:
                        current_status["speed"] = speed_match.group(1)
                    else:
                        bw_match = re.search(r"BW (\d+) Kbit", line)
                        if bw_match:
                            bw = int(bw_match.group(1))
                            if bw >= 1000000:
                                current_status["speed"] = f"{bw // 1000000}G"
                            elif bw >= 1000:
                                current_status["speed"] = f"{bw // 1000}M"

        # Don't forget the last port
        if current_port and current_status:
            ports.append(self._create_port_config(current_port, current_status))

        return ports

    def _create_port_config(self, port_name: str, status: dict) -> PortConfig:
        """Create PortConfig from parsed status.

        Note: 'down' means no link, not disabled. A port is disabled only if
        it's administratively shut down (we'd see 'administratively down').
        """
        # Link up/down indicates physical connectivity, not admin state
        link_up = status.get("link") == "up"
        # We assume enabled unless we see explicit shutdown indicator
        admin_enabled = status.get("admin_down") is not True

        return PortConfig(
            name=port_name,
            enabled=admin_enabled,
            speed=status.get("speed") if link_up else None,
            duplex=status.get("duplex") if link_up else None,
            native_vlan=status.get("pvid"),
            vlan_mode="access",  # Default
            description=f"{'UP' if link_up else 'DOWN'}",
        )

    async def create_vlan(self, vlan: VLANConfig) -> tuple[bool, str]:
        """Create or update a VLAN.

        ONTI OGF syntax:
          vlan <id>
          name <name>
          exit
          interface ethernet <port>
          switchport access vlan <id>  OR  switchport trunk allowed vlan add <id>
        """
        if vlan.id < 1 or vlan.id > 4094:
            return False, f"Invalid VLAN ID {vlan.id} - must be between 1 and 4094"

        commands = [f"vlan {vlan.id}"]
        if vlan.name:
            commands.append(f"name {vlan.name}")
        commands.append("exit")

        # Configure ports - convert port names to config commands
        for port in vlan.untagged_ports:
            port_num = self._extract_port_number(port)
            if port_num:
                commands.extend([
                    f"interface ethernet {port_num}",
                    f"switchport access vlan {vlan.id}",
                    "exit"
                ])

        for port in vlan.tagged_ports:
            port_num = self._extract_port_number(port)
            if port_num:
                commands.extend([
                    f"interface ethernet {port_num}",
                    f"switchport trunk allowed vlan add {vlan.id}",
                    "exit"
                ])

        return await self.execute_config_mode(commands)

    def _extract_port_number(self, port: str) -> Optional[str]:
        """Extract port identifier for interface command.

        Input: "Ethernet1/0/1" or "1/0/1"
        Output: "1/0/1"
        """
        match = re.search(r"(\d+/\d+/\d+)", port)
        return match.group(1) if match else None

    async def delete_vlan(self, vlan_id: int) -> tuple[bool, str]:
        """Delete a VLAN."""
        if vlan_id == 1:
            return False, "Cannot delete VLAN 1 (default VLAN)"
        if vlan_id < 1 or vlan_id > 4094:
            return False, f"Invalid VLAN ID {vlan_id}"

        return await self.execute_config_mode([f"no vlan {vlan_id}"])

    async def configure_port(self, port: PortConfig) -> tuple[bool, str]:
        """Configure a port."""
        port_num = self._extract_port_number(port.name)
        if not port_num:
            return False, f"Invalid port name: {port.name}"

        commands = [f"interface ethernet {port_num}"]

        if not port.enabled:
            commands.append("shutdown")
        else:
            commands.append("no shutdown")

        if port.description:
            commands.append(f"alias {port.description}")

        if port.native_vlan:
            commands.append(f"switchport access vlan {port.native_vlan}")

        commands.append("exit")
        return await self.execute_config_mode(commands)

    async def save_config(self) -> tuple[bool, str]:
        """Save running config to startup config."""
        return await self.execute("write")
