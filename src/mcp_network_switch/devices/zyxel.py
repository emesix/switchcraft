"""Zyxel GS1900 switch handler.

The Zyxel GS1900 has two interfaces:
1. SSH CLI (read-only) - Fast, reliable for status/config reading
2. Web CGI (read-write) - Required for configuration changes

This implementation uses a hybrid approach:
- SSH for reading config (show commands)
- Web for writing config (form POSTs)
"""
import asyncio
import logging
import random
import re
from typing import Optional

import httpx
import paramiko

from .base import NetworkDevice, DeviceConfig, DeviceStatus, VLANConfig, PortConfig
from ..utils.connection import with_retry

logger = logging.getLogger(__name__)


def zyxel_encode_password(pwd: str) -> str:
    """Encode password using Zyxel's obfuscation algorithm.

    The password is embedded at every 5th position (reversed),
    with length info at positions 123 and 289.
    """
    text = ""
    possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    pwd_len = len(pwd)
    char_idx = pwd_len

    for i in range(1, 322 - pwd_len):
        if i % 5 == 0 and char_idx > 0:
            char_idx -= 1
            text += pwd[char_idx]
        elif i == 123:
            text += "0" if pwd_len < 10 else str(pwd_len // 10)
        elif i == 289:
            text += str(pwd_len % 10)
        else:
            text += random.choice(possible)
    return text


class ZyxelDevice(NetworkDevice):
    """Zyxel GS1900 switch handler using SSH + Web hybrid approach."""

    # Web page cmd values
    CMD_VLAN_LIST = 1282
    CMD_VLAN_ADD = 1284
    CMD_VLAN_ADD_SUBMIT = 1285
    CMD_VLAN_EDIT = 1286
    CMD_VLAN_EDIT_SUBMIT = 1287
    CMD_PORT_VLAN = 1290
    CMD_PORT_VLAN_SUBMIT = 1291
    CMD_VLAN_MEMBERSHIP = 1293
    CMD_VLAN_MEMBERSHIP_SUBMIT = 1294
    CMD_IP_SETTINGS = 516
    CMD_IP_SETTINGS_SUBMIT = 517

    # VLAN membership values
    MEMBERSHIP_EXCLUDED = 0
    MEMBERSHIP_FORBIDDEN = 1
    MEMBERSHIP_TAGGED = 2
    MEMBERSHIP_UNTAGGED = 3

    def __init__(self, device_id: str, config: DeviceConfig):
        super().__init__(device_id, config)
        self._ssh: Optional[paramiko.SSHClient] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._xssid: Optional[str] = None
        self._base_url = f"http://{config.host}"

    @with_retry(max_attempts=3, min_wait=1, max_wait=10)
    async def connect(self) -> bool:
        """Connect to Zyxel switch via SSH."""
        logger.info(f"Connecting to Zyxel {self.device_id} at {self.host}")

        loop = asyncio.get_event_loop()

        def _ssh_connect():
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=self.host,
                port=22,  # SSH always on 22
                username=self.config.username,
                password=self.config.get_password(),
                timeout=self.config.timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            return ssh

        self._ssh = await loop.run_in_executor(None, _ssh_connect)
        self._connected = True
        logger.info(f"Connected to {self.device_id} via SSH")
        return True

    async def _ensure_web_session(self) -> None:
        """Ensure we have an authenticated web session."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout),
                follow_redirects=True,
            )

        # Login to web interface
        encoded_pwd = zyxel_encode_password(self.config.get_password())
        login_data = f"username={self.config.username}&password={encoded_pwd}&login=true;"

        resp = await self._http.post(
            f"{self._base_url}/cgi-bin/dispatcher.cgi",
            content=login_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        auth_id = resp.text.strip()

        # Verify login
        await asyncio.sleep(0.5)
        check_resp = await self._http.post(
            f"{self._base_url}/cgi-bin/dispatcher.cgi",
            content=f"authId={auth_id}&login_chk=true",
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        if "OK" not in check_resp.text:
            raise ConnectionError("Web login failed")

        logger.info(f"Web session established for {self.device_id}")

    async def _get_xssid(self, cmd: int) -> str:
        """Get XSSID token from a page."""
        await self._ensure_web_session()
        if not self._http:
            raise ConnectionError("HTTP session not established")
        resp = await self._http.get(f"{self._base_url}/cgi-bin/dispatcher.cgi?cmd={cmd}")
        match = re.search(r'name="XSSID"\s+value="([^"]+)"', resp.text)
        if match:
            return match.group(1)
        raise ValueError("Could not find XSSID token")

    async def disconnect(self) -> None:
        """Disconnect from Zyxel switch."""
        if self._ssh:
            self._ssh.close()
            self._ssh = None
        if self._http:
            await self._http.aclose()
            self._http = None
        self._connected = False
        logger.info(f"Disconnected from {self.device_id}")

    async def check_health(self) -> DeviceStatus:
        """Check device health via SSH."""
        try:
            if not self._connected:
                await self.connect()

            success, output = await self.execute("show version")
            uptime = None
            version = None

            if success:
                for line in output.split("\n"):
                    if "System Up Time" in line:
                        uptime = line.split(":", 1)[-1].strip()
                    if "Firmware Version" in line:
                        version = line.split(":", 1)[-1].strip()

            return DeviceStatus(
                reachable=True,
                uptime=uptime,
                firmware_version=version,
            )
        except Exception as e:
            return DeviceStatus(reachable=False, error=str(e))

    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute SSH command."""
        if not self._ssh:
            raise ConnectionError("Not connected")

        ssh = self._ssh  # Local reference for type narrowing
        loop = asyncio.get_event_loop()

        def _exec():
            # Get interactive shell to handle the "Press Enter" prompt
            shell = ssh.invoke_shell()
            import time
            time.sleep(1)
            shell.recv(65535)  # Clear banner

            shell.send(b"\n")  # Press enter
            time.sleep(0.5)
            shell.recv(65535)  # Clear prompt

            shell.send(f"{command}\n".encode())
            time.sleep(2)

            output = b""
            while shell.recv_ready():
                output += shell.recv(65535)
                time.sleep(0.2)

            shell.close()
            return output.decode("utf-8", errors="ignore")

        output = await loop.run_in_executor(None, _exec)

        # Clean output
        lines = output.split("\n")
        clean_lines = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith("GS1900#") and line != command:
                clean_lines.append(line)

        return True, "\n".join(clean_lines)

    async def execute_config_mode(self, commands: list[str]) -> tuple[bool, str]:
        """Execute config via web interface (SSH is read-only)."""
        return False, "Use specific configuration methods for Zyxel"

    async def get_running_config(self) -> str:
        """Get running configuration via SSH."""
        success, output = await self.execute("show running-config")
        return output if success else ""

    async def get_vlans(self) -> list[VLANConfig]:
        """Get VLAN configurations via SSH.

        SSH output format:
          VID  |     VLAN Name    |        Untagged Ports        |        Tagged Ports          |  Type
        -------+------------------+------------------------------+------------------------------+---------
             1 |          default |                  1-26,lag1-8 |                          --- | Default
        """
        success, output = await self.execute("show vlan")
        if not success:
            return []

        vlans = []
        for line in output.split("\n"):
            line = line.strip()

            # Skip header and separator lines
            if not line or line.startswith("VID") or line.startswith("---"):
                continue

            # Parse: VID | Name | Untagged | Tagged | Type
            # Example: "1 |          default |                  1-26,lag1-8 |                          --- | Default"
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                try:
                    vlan_id = int(parts[0])
                    vlan_name = parts[1]
                    untagged = self._parse_port_list(parts[2])
                    tagged = self._parse_port_list(parts[3])

                    vlans.append(VLANConfig(
                        id=vlan_id,
                        name=vlan_name,
                        untagged_ports=untagged,
                        tagged_ports=tagged,
                    ))
                except (ValueError, IndexError) as e:
                    logger.debug(f"Failed to parse VLAN line '{line}': {e}")
                    continue

        return vlans

    def _parse_port_list(self, text: str) -> list[str]:
        """Parse Zyxel port list notation.

        Input: "1-4,7,10-12,lag1-2"
        Output: ["1", "2", "3", "4", "7", "10", "11", "12", "lag1", "lag2"]
        """
        ports = []
        text = text.strip()
        if text == "---" or not text:
            return ports

        for part in text.split(","):
            part = part.strip()
            if not part:
                continue

            # Handle ranges like "1-4" or "lag1-2"
            if "-" in part:
                # Check if it's a lag range
                if part.startswith("lag"):
                    match = re.match(r"lag(\d+)-(\d+)", part)
                    if match:
                        start, end = int(match.group(1)), int(match.group(2))
                        for i in range(start, end + 1):
                            ports.append(f"lag{i}")
                else:
                    # Regular port range
                    try:
                        start, end = part.split("-")
                        for i in range(int(start), int(end) + 1):
                            ports.append(str(i))
                    except ValueError:
                        ports.append(part)
            else:
                ports.append(part)

        return ports

    async def get_ports(self) -> list[PortConfig]:
        """Get port configurations via SSH."""
        ports = []

        # Get port 1 as sample to understand format
        for port_num in range(1, 27):  # 24 + 2 SFP
            success, output = await self.execute(f"show interfaces {port_num}")
            if not success:
                continue

            # Parse output
            enabled = "is up" in output.lower() or "is down" in output.lower()
            # Note: link_up = "is up" in output.lower() - could be used for link status
            speed = None
            duplex = None

            # Extract speed/duplex
            speed_match = re.search(r"(\d+G?)-speed|speed.*?(\d+G?)", output, re.I)
            if speed_match:
                speed = speed_match.group(1) or speed_match.group(2)

            ports.append(PortConfig(
                name=str(port_num),
                enabled=enabled,
                speed=speed,
                duplex=duplex,
            ))

            # Only get first few ports for speed
            if port_num >= 5:
                break

        return ports

    async def create_vlan(self, vlan: VLANConfig) -> tuple[bool, str]:
        """Create a VLAN via web interface."""
        try:
            xssid = await self._get_xssid(self.CMD_VLAN_ADD)
            if not self._http:
                raise ConnectionError("HTTP session not established")

            form_data = {
                "XSSID": xssid,
                "vlanlist": str(vlan.id),
                "vlanAction": "0",
                "name": vlan.name or f"VLAN{vlan.id}",
                "cmd": str(self.CMD_VLAN_ADD_SUBMIT),
                "sysSubmit": "Apply",
            }

            resp = await self._http.post(
                f"{self._base_url}/cgi-bin/dispatcher.cgi",
                data=form_data,
            )

            if resp.status_code == 200:
                return True, f"Created VLAN {vlan.id}"
            return False, f"Failed: HTTP {resp.status_code}"

        except Exception as e:
            return False, str(e)

    async def delete_vlan(self, vlan_id: int) -> tuple[bool, str]:
        """Delete a VLAN via web interface."""
        # Zyxel uses a different flow for deletion - typically via checkbox + delete button
        # This would need to be implemented based on the specific page structure
        return False, "VLAN deletion not yet implemented for Zyxel"

    async def configure_port(self, port: PortConfig) -> tuple[bool, str]:
        """Configure a port via web interface.

        Configures port VLAN settings including:
        - PVID (native VLAN)
        - VLAN trunk mode
        - VLAN membership (tagged/untagged)
        """
        try:
            await self._ensure_web_session()
            results = []

            # Get port index (0-based for form fields)
            try:
                port_idx = int(port.name) - 1
                if port_idx < 0 or port_idx > 25:  # Ports 1-26
                    return False, f"Invalid port number: {port.name}"
            except ValueError:
                # Handle LAG ports
                if port.name.startswith("lag"):
                    lag_num = int(port.name[3:])
                    port_idx = 25 + lag_num  # lag1 = index 26, etc.
                else:
                    return False, f"Invalid port name: {port.name}"

            # Configure PVID and trunk mode via CMD_PORT_VLAN
            if port.native_vlan is not None or port.vlan_mode:
                xssid = await self._get_xssid(self.CMD_PORT_VLAN)
                if not self._http:
                    raise ConnectionError("HTTP session not established")

                # Build form data - only modify the target port
                form_data = {
                    "XSSID": xssid,
                    "cmd": str(self.CMD_PORT_VLAN_SUBMIT),
                    "port": port.name,  # Select this port
                }

                # Set PVID if specified
                if port.native_vlan is not None:
                    form_data["pvid"] = str(port.native_vlan)

                # Set trunk mode (1 = enabled, 0 = disabled)
                if port.vlan_mode == "trunk":
                    form_data["trunk"] = "1"
                elif port.vlan_mode == "access":
                    form_data["trunk"] = "0"

                resp = await self._http.post(
                    f"{self._base_url}/cgi-bin/dispatcher.cgi",
                    data=form_data,
                )

                if resp.status_code == 200:
                    results.append(f"Port {port.name} PVID/trunk configured")
                else:
                    results.append(f"Port settings failed: HTTP {resp.status_code}")

            # Configure VLAN membership
            # For access ports: set untagged membership on native VLAN
            # For trunk ports: set tagged membership on allowed VLANs
            if port.vlan_mode == "access" and port.native_vlan is not None:
                # Access port should be untagged member of native VLAN
                success, msg = await self._set_port_vlan_membership(
                    port.name,
                    port_idx,
                    port.native_vlan,
                    self.MEMBERSHIP_UNTAGGED
                )
                results.append(msg)
            elif port.allowed_vlans:
                # Trunk/hybrid port - set membership for specified VLANs
                for vlan_id in port.allowed_vlans:
                    success, msg = await self._set_port_vlan_membership(
                        port.name,
                        port_idx,
                        vlan_id,
                        self.MEMBERSHIP_TAGGED if port.vlan_mode == "trunk" else self.MEMBERSHIP_UNTAGGED
                    )
                    results.append(msg)

            return True, "; ".join(results) if results else f"Port {port.name} configured"

        except Exception as e:
            logger.error(f"Failed to configure port {port.name}: {e}")
            return False, str(e)

    async def _set_port_vlan_membership(
        self,
        port_name: str,
        port_idx: int,
        vlan_id: int,
        membership: int
    ) -> tuple[bool, str]:
        """Set a port's membership for a specific VLAN.

        Args:
            port_name: Port name for logging
            port_idx: 0-based port index
            vlan_id: VLAN ID to configure
            membership: MEMBERSHIP_EXCLUDED/FORBIDDEN/TAGGED/UNTAGGED
        """
        try:
            await self._ensure_web_session()
            if not self._http:
                raise ConnectionError("HTTP session not established")
            # Get the VLAN membership page for this specific VLAN
            resp = await self._http.get(
                f"{self._base_url}/cgi-bin/dispatcher.cgi?cmd={self.CMD_VLAN_MEMBERSHIP}&vid={vlan_id}"
            )
            page = resp.text

            # Extract XSSID from page
            xssid_match = re.search(r'name="XSSID"\s+value="([^"]+)"', page)
            if not xssid_match:
                return False, "Could not find XSSID token"
            xssid = xssid_match.group(1)

            # Extract current membership values from hidden vlanMode fields
            vlan_modes = {}
            for match in re.finditer(r'name="vlanMode_(\d+)"\s+value="(\d+)"', page):
                idx, val = int(match.group(1)), match.group(2)
                vlan_modes[idx] = val

            # Also check for checked radio buttons to get actual current state
            # Pattern: <input ... name="membership_X" ... value="Y" ... checked
            for match in re.finditer(
                r'name="membership_(\d+)"[^>]*value="(\d+)"[^>]*checked',
                page,
                re.IGNORECASE
            ):
                idx, val = int(match.group(1)), match.group(2)
                vlan_modes[idx] = val

            # Build form data - preserve ALL existing values
            form_data = {
                "XSSID": xssid,
                "cmd": str(self.CMD_VLAN_MEMBERSHIP_SUBMIT),
                "vid": str(vlan_id),
            }

            # Set all port membership values, only changing the target port
            for idx in range(34):  # 26 ports + 8 LAGs
                if idx == port_idx:
                    # Set the new membership for target port
                    form_data[f"vlanMode_{idx}"] = str(membership)
                    form_data[f"membership_{idx}"] = str(membership)
                else:
                    # Preserve existing membership (default to excluded if unknown)
                    current = vlan_modes.get(idx, "0")
                    form_data[f"vlanMode_{idx}"] = current
                    form_data[f"membership_{idx}"] = current

            resp = await self._http.post(
                f"{self._base_url}/cgi-bin/dispatcher.cgi",
                data=form_data,
            )

            if resp.status_code == 200:
                membership_name = {0: "excluded", 1: "forbidden", 2: "tagged", 3: "untagged"}
                return True, f"Port {port_name} set to {membership_name.get(membership, '?')} on VLAN {vlan_id}"
            return False, f"VLAN membership failed: HTTP {resp.status_code}"

        except Exception as e:
            return False, f"VLAN membership error: {e}"

    async def save_config(self) -> tuple[bool, str]:
        """Save configuration - Zyxel typically auto-saves."""
        return True, "Zyxel auto-saves configuration changes"
