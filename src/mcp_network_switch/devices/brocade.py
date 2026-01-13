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
import time
from typing import Optional

from .base import NetworkDevice, DeviceConfig, DeviceStatus, VLANConfig, PortConfig
from ..utils.connection import with_retry
from ..utils.logging_config import timed, perf_logger

logger = logging.getLogger(__name__)

# Brocade prompt patterns - handle both > (user) and # (enable) modes
PROMPT_PATTERN = re.compile(r"[\r\n].*?(Router[>#]|config[)#])\s*$", re.IGNORECASE)
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
        """Enter enable mode.

        Handles both password-protected and password-less enable modes.
        Uses a read loop to ensure we capture the Password: prompt reliably.
        """
        await self._send_raw(b"enable\r\n")

        # Read until we see Password: prompt or # (already in enable) or timeout
        output = b""
        start_time = asyncio.get_event_loop().time()
        timeout = 5.0

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.warning(f"Enable mode timeout after {elapsed:.1f}s, output: {output!r}")
                break

            chunk = await self._read_available(timeout=min(1, timeout - elapsed))
            if chunk:
                output += chunk
                decoded = output.decode("ascii", errors="ignore")

                # Check if password is requested
                if "Password:" in decoded or "password:" in decoded:
                    logger.debug("Password prompt detected, sending password")
                    await self._send_raw(f"{password}\r\n".encode())
                    await asyncio.sleep(0.5)
                    # Read the response after password
                    prompt_output = await self._read_until_prompt(timeout=5)
                    # Check if we got # prompt (enable mode)
                    if "#" in prompt_output:
                        logger.info("Enable mode successful (with password)")
                        return True
                    else:
                        logger.warning(f"Enable mode failed, prompt was: {prompt_output!r}")
                        return False

                # Check if we're already in enable mode (no password required)
                if "#" in decoded:
                    logger.info("Enable mode successful (no password required)")
                    return True

                # Check for error messages
                if "Error" in decoded or "incorrect" in decoded.lower():
                    logger.warning(f"Enable mode error: {decoded}")
                    return False

            await asyncio.sleep(0.1)  # Small delay between reads

        # Final check - did we end up with # prompt?
        decoded = output.decode("ascii", errors="ignore")
        if "#" in decoded:
            logger.info("Enable mode successful (detected in final output)")
            return True

        logger.warning(f"Enable mode failed, final output: {decoded!r}")
        return False


class BrocadeDevice(NetworkDevice):
    """Brocade FCX switch handler."""

    def __init__(self, device_id: str, config: DeviceConfig):
        super().__init__(device_id, config)
        self._telnet: Optional[BrocadeTelnet] = None

    @with_retry(max_attempts=5, min_wait=2, max_wait=15)
    @timed("connect")
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

    # Error patterns that indicate command failure
    ERROR_PATTERNS = [
        "Invalid input",
        "Error:",
        "Error -",        # BUG-002 FIX: Brocade uses "Error -" format too
        "error:",
        "not found",
        "Please disable",  # e.g., "Please disable dual mode..."
        "Please use a different",  # BUG-002 FIX: e.g., "Please use a different VLAN ID"
        "cannot ",
        "denied",
        "failed",
        "Incomplete command",
        "is currently reserved",  # BUG-002 FIX: e.g., "VLAN 0 is currently reserved"
    ]

    # Informational patterns that look like errors but are actually OK
    # These override ERROR_PATTERNS when found on the same line
    INFO_PATTERNS = [
        "already a member",      # "Port(s) ethe 1/2/1 are already a member of VLAN 254"
        "Added untagged port",   # Success message
        "Added tagged port",     # Success message
        "Removed untagged port", # Success message
        "Removed tagged port",   # Success message
    ]

    def _has_error(self, output: str) -> Optional[str]:
        """Check if output contains error indicators.

        Returns the error message if found, None otherwise.
        Ignores lines that match INFO_PATTERNS (false positives).
        """
        output_lower = output.lower()

        for pattern in self.ERROR_PATTERNS:
            if pattern.lower() in output_lower:
                # Extract the error line
                for line in output.split("\n"):
                    line_lower = line.lower()
                    if pattern.lower() in line_lower:
                        # Check if this line matches an INFO_PATTERN (false positive)
                        is_info = any(
                            info.lower() in line_lower
                            for info in self.INFO_PATTERNS
                        )
                        if not is_info:
                            return line.strip()
                # Pattern found but all matching lines were info patterns
                # Continue checking other error patterns
        return None

    @with_retry(max_attempts=3, min_wait=1, max_wait=5)
    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute a command on the Brocade switch."""
        if not self._telnet:
            raise ConnectionError("Not connected")

        start = time.perf_counter()
        try:
            output = await self._telnet.send_command(command, timeout=self.config.timeout)
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

    async def execute_batch(
        self,
        commands: list[str],
        stop_on_error: bool = True
    ) -> tuple[bool, str, list[dict]]:
        """Execute multiple commands in a single batch (FAST!).

        Sends all commands separated by newlines in one transmission,
        then parses the output to check for errors per command.

        Args:
            commands: List of commands to execute
            stop_on_error: If True, report failure on first error (default)

        Returns:
            Tuple of (overall_success, full_output, per_command_results)
            where per_command_results is a list of:
                {"command": str, "success": bool, "output": str, "error": Optional[str]}
        """
        if not self._telnet:
            raise ConnectionError("Not connected")

        # BUG-005 FIX: Handle empty commands list gracefully
        if not commands:
            return True, "", []

        # Join all commands with newlines - Brocade processes them sequentially
        batch = "\n".join(commands)
        cmd_count = len(commands)

        start = time.perf_counter()
        try:
            output = await self._telnet.send_command(batch, timeout=self.config.timeout)
            elapsed = (time.perf_counter() - start) * 1000
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            perf_logger.warning(
                f"{'execute_batch':20s} | {self.device_id:15s} | {elapsed:8.2f}ms | "
                f"ERROR | cmds={cmd_count} | {e}"
            )
            logger.error(f"Batch execution failed on {self.device_id}: {e}")
            self._connected = False
            raise

        # Parse output to extract per-command results
        results = self._parse_batch_output(output, commands, stop_on_error)

        # Overall success = no command failed
        overall_success = all(r["success"] for r in results)
        failed_count = sum(1 for r in results if not r["success"])

        # Log performance - this is the key metric for batch vs single comparison
        perf_logger.info(
            f"{'execute_batch':20s} | {self.device_id:15s} | {elapsed:8.2f}ms | "
            f"{'OK' if overall_success else 'FAIL'} | "
            f"cmds={cmd_count} | failed={failed_count} | "
            f"avg={elapsed/cmd_count:.2f}ms/cmd"
        )

        return overall_success, output, results

    def _parse_batch_output(
        self,
        output: str,
        commands: list[str],
        stop_on_error: bool
    ) -> list[dict]:
        """Parse batch command output to extract per-command results.

        Brocade echoes each command followed by its output/prompt.
        We track which command we're on by looking for command echoes.
        """
        results = []
        lines = output.split("\n")

        # Track current command and its output
        current_cmd_idx = 0
        current_output_lines = []

        for line in lines:
            line_stripped = line.strip()

            # Check if this line contains the next command echo
            # Brocade echoes commands, so we look for our command in the line
            if current_cmd_idx < len(commands):
                cmd = commands[current_cmd_idx]
                # Check if this line is the command echo (contains the command text)
                if cmd in line_stripped or line_stripped.endswith(cmd):
                    # If we have accumulated output from previous command, save it
                    if current_cmd_idx > 0 and current_output_lines:
                        cmd_output = "\n".join(current_output_lines)
                        prev_cmd = commands[current_cmd_idx - 1]
                        error = self._has_error(cmd_output)
                        results.append({
                            "command": prev_cmd,
                            "success": error is None,
                            "output": cmd_output.strip(),
                            "error": error
                        })

                        # Stop if we hit an error and stop_on_error is True
                        if error and stop_on_error:
                            # Mark remaining commands as not executed
                            for remaining_cmd in commands[current_cmd_idx:]:
                                results.append({
                                    "command": remaining_cmd,
                                    "success": False,
                                    "output": "",
                                    "error": "Not executed (previous command failed)"
                                })
                            return results

                    current_output_lines = []
                    current_cmd_idx += 1
                    continue

            # Accumulate output lines (skip prompt lines)
            if line_stripped and not re.match(r".*Router[#>(\[]", line_stripped):
                current_output_lines.append(line_stripped)

        # Handle the last command's output
        if current_cmd_idx > 0:
            cmd_output = "\n".join(current_output_lines)
            last_cmd = commands[current_cmd_idx - 1] if current_cmd_idx <= len(commands) else commands[-1]
            error = self._has_error(cmd_output)
            results.append({
                "command": last_cmd,
                "success": error is None,
                "output": cmd_output.strip(),
                "error": error
            })

        # If we didn't process all commands, mark them
        while len(results) < len(commands):
            idx = len(results)
            results.append({
                "command": commands[idx],
                "success": True,  # No error detected
                "output": "",
                "error": None
            })

        return results

    async def execute_config_mode(self, commands: list[str]) -> tuple[bool, str]:
        """Execute commands in config mode using batch execution.

        FAST: Sends all commands in a single batch instead of one-by-one.
        """
        # Wrap commands with config mode entry/exit
        full_commands = ["conf t"] + commands + ["exit"]

        success, output, results = await self.execute_batch(full_commands)

        # Build summary output showing each command result
        summary_lines = []
        for r in results:
            status = "OK" if r["success"] else f"FAIL: {r['error']}"
            summary_lines.append(f"{r['command']}: {status}")
            if r["output"]:
                summary_lines.append(f"  {r['output']}")

        return success, "\n".join(summary_lines)

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

        BUG-002 FIX: Validates VLAN ID range before sending to device.
        """
        # BUG-002 FIX: Validate VLAN ID range (1-4094 per IEEE 802.1Q)
        if vlan.id < 1 or vlan.id > 4094:
            return False, f"Invalid VLAN ID {vlan.id} - must be between 1 and 4094"

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
        """Delete a VLAN.

        BUG-003 FIX: Validates VLAN ID to prevent false success on protected VLANs.
        """
        # BUG-003 FIX: Prevent deletion of default VLAN 1 (Brocade silently ignores)
        if vlan_id == 1:
            return False, "Cannot delete VLAN 1 (default VLAN is protected)"

        # BUG-002 FIX: VLAN 0 is reserved
        if vlan_id == 0:
            return False, "Cannot delete VLAN 0 (reserved for internal use)"

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
