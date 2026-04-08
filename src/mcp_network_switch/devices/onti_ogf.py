"""ONTI S508CL Original Firmware switch handler via Telnet.

The ONTI S508CL-8S with original/stock firmware has a Cisco-style CLI
accessible via telnet (port 23) or serial console (9600 8N1). This handler
provides full management capabilities via both transports.

Also sold as: Onti-S508-S8, SKS8300-8, HK-ES3058-8P-L (rebrands of same hardware).

Technical details:
- Telnet on port 23 or serial-over-TCP (socat bridge) for console access
- Cisco-style CLI with configurable hostname prompt (default: "Switch#")
- Port naming: Ethernet1/0/X (X = 1-8 for 8-port SFP+ model)
- Hardware: RTL930x-based with 8x SFP+ ports (10G capable)
- Default credentials: admin/admin
- Serial console: 9600 baud, 8N1, no flow control

Exec Command Reference (V300SP10250704 firmware):
- show version              : Device info, MAC, uptime, firmware, serial
- show vlan                 : List all VLANs with port membership
- show interface            : Detailed stats for ALL ports (verbose)
- show interface EthernetX  : Stats for single port
- show running-config       : Current running configuration
- show startup-config       : Saved startup configuration
- show mac-address-table    : MAC address table (VLAN, MAC, type, port)
- show transceiver          : SFP module summary (temp, voltage, power)
- show transceiver detail   : SFP module detailed diagnostics + thresholds
- show ip route             : IP routing table
- show spanning-tree        : STP status (global MSTP)
- show lldp                 : LLDP status and settings
- show clock                : Current system time
- show users                : Active console/telnet/ssh sessions
- terminal length 0         : Disable --More-- pagination
- config                    : Enter configuration mode
- write                     : Save running config (requires Y confirmation)
- ping <ip>                 : ICMP ping
- reload                    : Reboot switch

Config Mode Commands:
- vlan <id>                           : Create/enter VLAN context
- name <name>                         : Set VLAN name (in vlan context)
- no vlan <id>                        : Delete VLAN
- interface ethernet <slot/port>      : Enter interface config
- switchport access vlan <id>         : Set access VLAN
- switchport mode trunk               : Set port to trunk mode
- switchport mode access              : Set port to access mode
- switchport trunk allowed vlan add   : Add VLAN to trunk
- shutdown / no shutdown              : Admin disable/enable port
- alias <name>                        : Set port description/alias
- ip address <ip> <mask>              : Set management IP (on vlan interface)

Commands that DO NOT work (firmware limitation):
- show interface status/brief/counters : Use "show interface" instead
- show vlan <id>                       : Use "show vlan" (all) instead
- show cpu / show memory               : Not supported
- no alias                             : Cannot clear alias via CLI

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

# ONTI OGF prompt patterns — hostname may vary (e.g. "Switch", "NZ", custom)
# Must NOT match syslog lines like "%Jul 04 05:01:23.456 ..."
# Leading [\x00-\x1f]* strips control chars (e.g. \x1f unit separator) that
# some firmware versions inject before the prompt after password echo.
PROMPT_PATTERN = re.compile(
    r"^[\x00-\x1f]*[A-Za-z][\w-]*(?:\(config[^)]*\))?[#>]\s*$", re.MULTILINE
)
CONFIG_PROMPT_PATTERN = re.compile(
    r"^[\x00-\x1f]*[A-Za-z][\w-]*\(config[^)]*\)#\s*$", re.MULTILINE
)
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
        """Establish telnet connection and authenticate.

        Works with both direct telnet (port 23) and serial-over-TCP bridges
        (e.g. ser2net/socat). Handles unknown console state without wasting
        login attempts on empty CR probes.

        Strategy: send the username directly instead of a bare CR. This works
        regardless of which prompt the console is at:
        - At Username: → username accepted, proceeds to Password:
        - At Switch#  → "admin" is an invalid command, but we detect the prompt
        - At Password: → "admin" used as password, may succeed or fail gracefully
        - Silent       → username wakes the console and gets processed
        """
        loop = asyncio.get_event_loop()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(self.timeout)
        await loop.run_in_executor(
            None, self._socket.connect, (self.host, self.port)
        )

        # Drain any buffered output from previous sessions.
        # ser2net buffers serial data (e.g. syslog messages like %PORT-5-UPDOWN)
        # between TCP connections. We must drain ALL of it before probing state.
        await asyncio.sleep(0.5)
        buffered = ""
        for _ in range(5):
            chunk = (await self._read_available(timeout=1)).decode("ascii", errors="ignore")
            if not chunk:
                break
            buffered += chunk
        logger.debug(f"Drained buffer ({len(buffered)} chars): {buffered[-100:]!r}")

        # Already at CLI prompt — session persisted from previous connection
        if PROMPT_PATTERN.search(buffered):
            logger.info(f"Already authenticated to {self.host} (existing session)")
            return

        # If we see Username: in buffered output, go straight to login
        if "Username" in buffered or "login" in buffered.lower():
            return await self._do_login(username, password)

        # Send username directly (not a bare CR) to probe + advance state
        await self._send_raw(f"{username}\r".encode())
        await asyncio.sleep(2)
        response = (await self._read_available(timeout=3)).decode("ascii", errors="ignore")
        logger.debug(f"After username probe: {response[-100:]!r}")

        # Case: we were at Switch# — username was treated as bad command
        if PROMPT_PATTERN.search(response):
            logger.info(f"Already authenticated to {self.host}")
            return

        # Case: we were at Username: — switch accepted it, now at Password:
        if re.search(r"[Pp]assword:", response):
            await self._send_raw(f"{password}\r".encode())
            await asyncio.sleep(1)
            output = await self._read_until_prompt(timeout=10)
            if PROMPT_PATTERN.search(output):
                logger.info(f"Successfully authenticated to {self.host}")
                return
            # Password failed — might be at Username: again
            if "Username" in output or "login" in output.lower():
                return await self._do_login(username, password)
            raise ConnectionError(f"Login failed, got: {output[-100:]}")

        # Case: we were at Password: — "admin" was used as password
        # If it succeeded, we'd see a prompt. If it failed, we see Username:
        if "Username" in response or "login" in response.lower():
            return await self._do_login(username, password)

        # Fallback: wait for any recognizable prompt
        response += await self._read_until_pattern(
            r"(?:login|[Uu]sername|[Pp]assword):|[#>]\s*$", timeout=10
        )

        if PROMPT_PATTERN.search(response):
            logger.info(f"Authenticated to {self.host}")
            return
        if "Username" in response or "login" in response.lower():
            return await self._do_login(username, password)
        if re.search(r"[Pp]assword:", response):
            await self._send_raw(f"{password}\r".encode())
            await asyncio.sleep(1)
            output = await self._read_until_prompt(timeout=10)
            if PROMPT_PATTERN.search(output):
                logger.info(f"Authenticated to {self.host}")
                return

        raise ConnectionError(f"Cannot authenticate, got: {response[-100:]}")

    async def _do_login(self, username: str, password: str) -> None:
        """Perform username/password login from a clean Username: prompt."""
        await self._send_raw(f"{username}\r".encode())
        await self._read_until_pattern(r"[Pp]assword:", timeout=5)
        await self._send_raw(f"{password}\r".encode())
        await asyncio.sleep(1)
        output = await self._read_until_prompt(timeout=10)
        if not PROMPT_PATTERN.search(output):
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
        await self._send_raw(f"{command}\r".encode())
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
        await self._send_raw(b"config\r")
        await asyncio.sleep(0.5)
        output = await self._read_until_prompt(timeout=5)
        return "config" in output.lower() or CONFIG_PROMPT_PATTERN.search(output) is not None

    async def exit_config_mode(self) -> None:
        """Exit configuration mode."""
        await self._send_raw(b"end\r")
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
        """Connect to ONTI OGF switch via telnet.

        Reuses an existing connection if still alive (critical for serial-over-
        TCP bridges where reconnection is unreliable due to syslog noise and
        login lockouts).
        """
        # Reuse existing connection if it's still alive
        if self._telnet and self._connected:
            try:
                # Quick health check — send empty command to verify connection
                await self._telnet._send_raw(b"\r")
                await asyncio.sleep(0.3)
                probe = (await self._telnet._read_available(timeout=1)).decode(
                    "ascii", errors="ignore"
                )
                if PROMPT_PATTERN.search(probe):
                    logger.info(f"Reusing existing connection to {self.device_id}")
                    return True
            except Exception:
                logger.debug(f"Existing connection to {self.device_id} is dead")
                await self._telnet.close()
                self._telnet = None
                self._connected = False

        logger.info(f"Connecting to ONTI-OGF {self.device_id} at {self.host}")

        # Close any leftover connection from a previous failed attempt
        if self._telnet:
            await self._telnet.close()
            self._telnet = None

        self._telnet = OntiOGFTelnet(
            self.host,
            self.config.port,
            timeout=self.config.timeout
        )

        username = self.config.username or "admin"
        password = self.config.get_password() or "admin"

        try:
            await self._telnet.connect(username, password)
        except Exception:
            await self._telnet.close()
            self._telnet = None
            raise

        # Disable pagination
        await self._telnet.send_command("terminal length 0", timeout=5)

        self._connected = True
        logger.info(f"Connected to {self.device_id}")
        return True

    async def disconnect(self) -> None:
        """Soft disconnect — keeps the connection alive for reuse.

        Serial-over-TCP connections are kept persistent to avoid reconnection
        issues (syslog noise, login lockouts). The connection is only truly
        closed on error or when the MCP server shuts down.
        """
        # Intentionally do NOT close the connection — keep it for reuse.
        # The device instance is cached by DeviceInventory, so the next
        # MCP call will reuse it via connect()'s health check.
        logger.debug(f"Soft disconnect from {self.device_id} (connection kept alive)")

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
    # Note: "% " prefixes error messages like "% Invalid input" and "% Incomplete command"
    # but syslog messages like "%Jul 04..." and "%PORT-5-UPDOWN" should NOT match
    ERROR_PATTERNS = [
        "% Invalid input",
        "% Incomplete command",
        "% Unknown command",
        "Error:",
        "error!",
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

        Parses 'show vlan' output (ports may span multiple lines):
        VLAN Name         Type       Media     Ports
        ---- ------------ ---------- --------- ----------------------------------------
        1    default      Static     ENET      Ethernet1/0/1       Ethernet1/0/2
                                               Ethernet1/0/3       Ethernet1/0/4
        """
        success, output = await self.execute("show vlan")
        if not success:
            return []

        vlans = []
        current_vlan = None
        lines = output.split("\n")

        for line in lines:
            stripped = line.strip()
            # Skip header and empty lines
            if stripped.startswith("VLAN") or stripped.startswith("----") or not stripped:
                continue

            parts = stripped.split()

            # New VLAN entry: starts with a digit (VLAN ID)
            if parts and parts[0].isdigit():
                # Save previous VLAN
                if current_vlan:
                    vlans.append(current_vlan)

                vlan_id = int(parts[0])
                vlan_name = parts[1] if len(parts) > 1 else ""

                ports = [p for p in parts if p.startswith("Ethernet")]
                current_vlan = VLANConfig(
                    id=vlan_id,
                    name=vlan_name,
                    untagged_ports=ports,
                    tagged_ports=[],
                )
            elif current_vlan:
                # Continuation line — just port names
                ports = [p for p in parts if p.startswith("Ethernet")]
                current_vlan.untagged_ports.extend(ports)

        # Don't forget the last VLAN
        if current_vlan:
            vlans.append(current_vlan)

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

        # Note: 'alias' can be set but NOT cleared ('no alias' is invalid)
        if port.description:
            commands.append(f"alias {port.description}")

        if port.native_vlan:
            commands.append(f"switchport access vlan {port.native_vlan}")

        commands.append("exit")
        return await self.execute_config_mode(commands)

    async def save_config(self) -> tuple[bool, str]:
        """Save running config to startup config.

        The 'write' command prompts for confirmation:
          Confirm to overwrite current startup-config configuration [Y/N]:
        We send 'Y' to confirm.
        """
        if not self._telnet:
            raise ConnectionError("Not connected")

        await self._telnet._send_raw(b"write\r")
        # Wait for confirmation prompt
        await self._telnet._read_until_pattern(r"\[Y/N\]", timeout=5)
        # Confirm
        await self._telnet._send_raw(b"Y\r")
        await asyncio.sleep(1)
        output = await self._telnet._read_until_prompt(timeout=10)

        if "successful" in output.lower():
            return True, "Configuration saved"
        return False, output
