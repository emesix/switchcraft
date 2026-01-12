"""Brocade FCX switch handler via Telnet.

Brocade FCX switches have notoriously unstable telnet connections.
This implementation focuses on:
1. Robust connection handling with retries
2. Proper command timing (Brocade needs delays)
3. Clean parsing of CLI output

Command Reference (FCX624-E, firmware 08.0.30uT7f3):
- show vlan              : List all VLANs with port membership
- show vlan <id>         : Single VLAN details
- show running-config vlan : Clean VLAN config blocks
- show interfaces brief  : Port status table
- write memory           : Save config
- skip-page-display      : Disable --More-- pagination
- configure terminal     : Enter config mode

Port naming: 1/1/1 = unit/module/port
- Module 1 (M1): 24x 1G copper
- Module 2 (M2): 4x 10G SFP+
"""
import asyncio
import logging
import re
import socket
from typing import Optional

from .base import NetworkDevice, DeviceConfig, DeviceStatus, VLANConfig, PortConfig
from ..utils.connection import with_retry

logger = logging.getLogger(__name__)

# Brocade prompt patterns - handle both > (user) and # (enable) modes
# Matches: "telnet@FCX624-ADV Router>", "Router#", "Router(config)#", "Router(config-if)#"
# Fixed: Pattern now works without leading newline (for initial connection)
PROMPT_PATTERN = re.compile(r"(?:^|[\r\n]).*?Router(?:\([^)]+\))?[>#]\s*$", re.IGNORECASE)
MORE_PATTERN = re.compile(r"--More--", re.IGNORECASE)


class BrocadeTelnet:
    """Low-level telnet handler for Brocade switches with stability improvements."""

    def __init__(self, host: str, port: int, timeout: float = 30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None
        self._buffer = b""

    async def connect(self) -> None:
        """Establish telnet connection."""
        loop = asyncio.get_event_loop()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(self.timeout)
        await loop.run_in_executor(
            None, self._socket.connect, (self.host, self.port)
        )
        # Wait for initial banner
        await asyncio.sleep(2)
        await self._read_until_prompt(timeout=10)

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
        await asyncio.sleep(0.5)  # Brocade needs time to process
        output = await self._read_until_prompt(timeout=timeout)

        # Clean up output - remove the command echo and prompt
        lines = output.split("\n")
        # Remove first line (command echo) and last line (prompt)
        if lines and command in lines[0]:
            lines = lines[1:]
        if lines and PROMPT_PATTERN.search(lines[-1]):
            lines = lines[:-1]

        return "\n".join(lines).strip()

    async def enable(self, password: str) -> bool:
        """Enter enable mode."""
        await self._send_raw(b"enable\r\n")
        await asyncio.sleep(1)
        initial_output = await self._read_available(timeout=3)

        if b"Password:" in initial_output or b"password:" in initial_output:
            await self._send_raw(f"{password}\r\n".encode())
            await asyncio.sleep(1)
            prompt_output = await self._read_until_prompt(timeout=5)
            # Check if we got # prompt (enable mode)
            return "#" in prompt_output

        # Check if we got # prompt (enable mode) without password
        return "#" in initial_output.decode("ascii", errors="ignore")


class BrocadeDevice(NetworkDevice):
    """Brocade FCX switch handler."""

    def __init__(self, device_id: str, config: DeviceConfig):
        super().__init__(device_id, config)
        self._telnet: Optional[BrocadeTelnet] = None

    @with_retry(max_attempts=5, min_wait=2, max_wait=15)
    async def connect(self) -> bool:
        """Connect to Brocade switch via telnet."""
        logger.info(f"Connecting to Brocade {self.device_id} at {self.host}")

        self._telnet = BrocadeTelnet(
            self.host,
            self.config.port,
            timeout=self.config.timeout
        )
        await self._telnet.connect()

        # Enter enable mode
        password = self.config.get_password()
        if self.config.enable_password_required:
            if not await self._telnet.enable(password):
                raise ConnectionError("Failed to enter enable mode")

        # CRITICAL: Disable pagination to avoid --More-- prompts
        await self._telnet.send_command("skip-page-display", timeout=5)

        self._connected = True
        logger.info(f"Connected to {self.device_id}")
        return True

    async def disconnect(self) -> None:
        """Disconnect from Brocade switch."""
        if self._telnet:
            await self._telnet.close()
            self._telnet = None
        self._connected = False
        logger.info(f"Disconnected from {self.device_id}")

    async def check_health(self) -> DeviceStatus:
        """Check device health."""
        try:
            if not self._connected:
                await self.connect()

            # Get uptime and version
            output = await self.execute("show version")
            uptime = None
            version = None

            if output[0]:
                for line in output[1].split("\n"):
                    if "uptime" in line.lower():
                        uptime = line.strip()
                    if "SW:" in line or "software" in line.lower():
                        version = line.strip()

            return DeviceStatus(
                reachable=True,
                uptime=uptime,
                firmware_version=version,
            )
        except Exception as e:
            return DeviceStatus(reachable=False, error=str(e))

    @with_retry(max_attempts=3, min_wait=1, max_wait=5)
    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute a command on the Brocade switch."""
        if not self._telnet:
            raise ConnectionError("Not connected")

        try:
            output = await self._telnet.send_command(command, timeout=self.config.timeout)
            # Check for error messages
            if "Invalid input" in output or "Error" in output:
                return False, output
            return True, output
        except Exception as e:
            logger.error(f"Command failed on {self.device_id}: {e}")
            self._connected = False
            raise

    async def execute_config_mode(self, commands: list[str]) -> tuple[bool, str]:
        """Execute commands in config mode."""
        all_output = []

        # Enter config mode
        success, output = await self.execute("configure terminal")
        if not success:
            return False, f"Failed to enter config mode: {output}"
        all_output.append(output)

        # Execute each command
        for cmd in commands:
            success, output = await self.execute(cmd)
            all_output.append(f"{cmd}: {output}")
            if not success:
                # Exit config mode on error
                await self.execute("end")
                return False, "\n".join(all_output)

        # Exit config mode
        await self.execute("end")
        return True, "\n".join(all_output)

    async def get_running_config(self) -> str:
        """Get running configuration."""
        success, output = await self.execute("show running-config")
        return output if success else ""

    async def get_vlans(self) -> list[VLANConfig]:
        """Get all VLAN configurations.

        Uses 'show vlan' which outputs:
        PORT-VLAN 254, Name Management, Priority level0, Spanning tree Off
         Untagged Ports: (U1/M1)   1   2   3   4
           Tagged Ports: (U1/M2)   1   2
        """
        success, output = await self.execute("show vlan")
        if not success:
            return []

        vlans = []
        current_vlan: Optional[VLANConfig] = None

        for line in output.split("\n"):
            # Match VLAN header: PORT-VLAN 254, Name Management, ...
            vlan_match = re.match(r"PORT-VLAN\s+(\d+)(?:,\s*Name\s+(\S+))?", line)
            if vlan_match:
                if current_vlan:
                    vlans.append(current_vlan)
                vlan_id = int(vlan_match.group(1))
                vlan_name = vlan_match.group(2) or f"VLAN{vlan_id}"
                current_vlan = VLANConfig(id=vlan_id, name=vlan_name)
                continue

            if not current_vlan:
                continue

            # Parse port lines - handle multi-line and module prefixes
            # Format: " Untagged Ports: (U1/M1)   1   2   3   4"
            # or continuation: " Untagged Ports: (U1/M1)  17  18  19  20"

            if "Tagged Ports:" in line:
                _, ports = self._parse_port_line(line, "Tagged Ports:")
                if ports:
                    current_vlan.tagged_ports.extend(ports)
            elif "Untagged Ports:" in line:
                _, ports = self._parse_port_line(line, "Untagged Ports:")
                if ports:
                    current_vlan.untagged_ports.extend(ports)

        if current_vlan:
            vlans.append(current_vlan)

        return vlans

    def _parse_port_line(self, line: str, prefix: str) -> tuple[int, list[str]]:
        """Parse a Brocade port line with module prefix.

        Input: " Untagged Ports: (U1/M1)   1   2   3   4"
        Output: (1, ["1/1/1", "1/1/2", "1/1/3", "1/1/4"])

        Module mapping:
        - (U1/M1) = Module 1 = 1G copper = 1/1/x
        - (U1/M2) = Module 2 = 10G SFP+ = 1/2/x
        """
        text = line.split(prefix)[-1].strip()

        # Check for "None"
        if text.lower() == "none" or not text:
            return 0, []

        # Extract module number from (U1/M1) or (U1/M2)
        module = 1  # Default to module 1
        module_match = re.search(r"\(U\d+/M(\d+)\)", text)
        if module_match:
            module = int(module_match.group(1))
            # Remove the module prefix
            text = re.sub(r"\([^)]+\)", "", text).strip()

        # Parse port numbers
        ports = []
        for part in text.split():
            if part.isdigit():
                ports.append(f"1/{module}/{part}")

        return module, ports

    async def get_ports(self) -> list[PortConfig]:
        """Get port configurations.

        Parses 'show interfaces brief' output:
        Port       Link    State   Dupl Speed Trunk Tag Pvid Pri MAC             Name
        1/1/1      Down    None    None None  None  No  254  0   748e.f87d.cf80
        1/2/2      Up      Forward Full 10G   None  Yes N/A  0   748e.f87d.cf80
        """
        success, output = await self.execute("show interfaces brief")
        if not success:
            return []

        ports = []
        for line in output.split("\n"):
            # Skip header and empty lines
            line = line.strip()
            if not line or line.startswith("Port") or line.startswith("="):
                continue

            # Parse: Port Link State Dupl Speed Trunk Tag Pvid Pri MAC Name
            # Example: 1/1/1      Down    None    None None  None  No  254  0   748e.f87d.cf80
            parts = line.split()
            if len(parts) >= 8:
                port_match = re.match(r"(\d+/\d+/\d+)", parts[0])
                if port_match:
                    port_name = port_match.group(1)
                    link_status = parts[1].lower()  # Up/Down
                    # parts[2] is State (Forward/Blocking/None) - not currently used
                    duplex = parts[3] if parts[3] != "None" else None
                    speed = parts[4] if parts[4] != "None" else None
                    is_tagged = parts[6].lower() == "yes"  # Tag column
                    pvid = parts[7] if parts[7] != "N/A" else None

                    ports.append(PortConfig(
                        name=port_name,
                        enabled=link_status != "disabled",
                        speed=speed,
                        duplex=duplex,
                        vlan_mode="trunk" if is_tagged else "access",
                        native_vlan=int(pvid) if pvid and pvid.isdigit() else None,
                    ))
        return ports

    async def create_vlan(self, vlan: VLANConfig) -> tuple[bool, str]:
        """Create or update a VLAN.

        Brocade syntax:
          vlan <id> name <name> by port
            tagged ethe <port-range>
            untagged ethe <port-range>
            router-interface ve <vlan-id>
          exit
        """
        # Create VLAN with name in single command
        vlan_name = vlan.name or f"VLAN{vlan.id}"
        commands = [
            f"vlan {vlan.id} name {vlan_name} by port",
        ]

        # Add untagged ports - can use range syntax
        if vlan.untagged_ports:
            port_spec = self._format_port_range(vlan.untagged_ports)
            commands.append(f"untagged ethe {port_spec}")

        # Add tagged ports
        if vlan.tagged_ports:
            port_spec = self._format_port_range(vlan.tagged_ports)
            commands.append(f"tagged ethe {port_spec}")

        # Add L3 interface if IP configured
        if vlan.ip_address and vlan.ip_mask:
            commands.append(f"router-interface ve {vlan.id}")

        commands.append("exit")

        return await self.execute_config_mode(commands)

    def _format_port_range(self, ports: list[str]) -> str:
        """Format ports for Brocade command.

        Input: ["1/1/1", "1/1/2", "1/1/3", "1/1/4"]
        Output: "1/1/1 to 1/1/4" (if contiguous)

        Input: ["1/1/1", "1/1/3", "1/1/5"]
        Output: "1/1/1 to 1/1/1 1/1/3 to 1/1/3 1/1/5 to 1/1/5" (individual)

        Brocade requires "ethe X/Y/Z to X/Y/W" syntax for port ranges.
        """
        if not ports:
            return ""

        # Parse ports into (unit, module, port) tuples
        parsed = []
        for p in ports:
            try:
                parts = p.split("/")
                if len(parts) == 3:
                    parsed.append((int(parts[0]), int(parts[1]), int(parts[2]), p))
            except (ValueError, IndexError):
                # Keep original string for non-standard formats
                parsed.append((0, 0, 0, p))

        # Sort by unit, module, port
        parsed.sort(key=lambda x: (x[0], x[1], x[2]))

        # Group contiguous ranges within same unit/module
        ranges = []
        i = 0
        while i < len(parsed):
            unit, module, port_num, port_str = parsed[i]
            start = port_str
            end = port_str

            # Find contiguous ports in same unit/module
            j = i + 1
            while j < len(parsed):
                next_unit, next_module, next_port, next_str = parsed[j]
                prev_unit, prev_module, prev_port, _ = parsed[j - 1]

                if (next_unit == prev_unit and
                    next_module == prev_module and
                    next_port == prev_port + 1):
                    end = next_str
                    j += 1
                else:
                    break

            ranges.append(f"{start} to {end}")
            i = j

        return " ".join(ranges)

    async def delete_vlan(self, vlan_id: int) -> tuple[bool, str]:
        """Delete a VLAN."""
        return await self.execute_config_mode([f"no vlan {vlan_id}"])

    async def configure_port(self, port: PortConfig) -> tuple[bool, str]:
        """Configure a port."""
        commands = [f"interface ethernet {port.name}"]

        if not port.enabled:
            commands.append("disable")
        else:
            commands.append("enable")

        if port.description:
            commands.append(f'port-name {port.description}')

        commands.append("exit")
        return await self.execute_config_mode(commands)

    async def save_config(self) -> tuple[bool, str]:
        """Save running config to startup config."""
        return await self.execute("write memory")
