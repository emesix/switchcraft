"""JT-COM S207CW-91TS (ONT-S207CW-91TSM) 9-port 2.5G managed switch handler.

Hardware: Realtek RTL8372N SoC with 8051 microcontroller core
- 9x 2.5GbE RJ45 ports (1 uplink + 8 downlink)
- Serial: 57600 8N1 via PL2303 USB adapter (/dev/ttyUSB0)
- Web UI: HTTP CGI at http://192.168.2.1 with MD5-cookie auth
- Firmware: V200.1.16 (not Linux-capable, 8051 core)
- MAC: D0:AA:5F:xx:xx:xx (Hangzhou Jingtang / JT-COM)

Transport architecture:
- Serial (UART): Boot interception, SPI flash access, low-level diagnostics
- HTTP (CGI): Configuration management, port/VLAN/MAC operations

Boot sequence:
1. Bootloader V0.2 prints "Loader start V0.2"
2. Options: [v] SPI flash viewer, [ESC] firmware download
3. Normal boot proceeds to "Key is wrong" auth prompt
4. Web UI available after full boot (~15s)

Config backup format:
- Binary download from config_back.cgi?cmd=conf_backup
- Passwords XOR-encrypted with key = 0x5a + byte_index
"""
import asyncio
import hashlib
import logging
import re
import time
from typing import Optional

import httpx
import serial

from .base import NetworkDevice, DeviceConfig, DeviceStatus, VLANConfig, PortConfig
from ..utils.connection import with_retry
from ..utils.logging_config import timed

logger = logging.getLogger(__name__)


class JTComSerial:
    """Serial transport for JT-COM switch UART interface.

    Manages persistent connection to /dev/ttyUSB0 at 57600 baud.
    Handles boot interception, SPI flash viewer access, and auth challenges.
    """

    BOOT_MARKER = "Loader start"
    AUTH_PROMPT = "Key is wrong"
    SPI_PROMPT = "SPI>"

    def __init__(self, port: str = "/dev/ttyUSB0", baud: int = 57600, timeout: float = 2):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self._boot_captured = False

    def connect(self) -> None:
        """Open serial connection."""
        if self._serial and self._serial.is_open:
            return
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
        )
        logger.info(f"Serial connected: {self.port} @ {self.baud}")

    def close(self) -> None:
        """Close serial connection."""
        if self._serial and self._serial.is_open:
            self._serial.close()
            self._serial = None
            logger.info("Serial disconnected")

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def _ensure_connected(self) -> serial.Serial:
        """Return open serial port, reconnecting if needed."""
        if not self.is_open:
            self.connect()
        assert self._serial is not None
        return self._serial

    def write(self, data: str) -> None:
        """Send string data with CR+LF line ending."""
        ser = self._ensure_connected()
        ser.write(f"{data}\r\n".encode("ascii"))
        ser.flush()

    def write_raw(self, data: bytes) -> None:
        """Send raw bytes (for boot key presses)."""
        ser = self._ensure_connected()
        ser.write(data)
        ser.flush()

    def read_until(self, marker: str, timeout: float = 10) -> str:
        """Read serial output until marker string appears or timeout."""
        ser = self._ensure_connected()
        old_timeout = ser.timeout
        ser.timeout = 0.1  # Short reads for responsiveness
        output = ""
        start = time.monotonic()

        try:
            while time.monotonic() - start < timeout:
                chunk = ser.read(1024)
                if chunk:
                    output += chunk.decode("ascii", errors="replace")
                    if marker in output:
                        break
        finally:
            ser.timeout = old_timeout

        return output

    def read_available(self, timeout: float = 1) -> str:
        """Read whatever is currently available on the serial port."""
        ser = self._ensure_connected()
        old_timeout = ser.timeout
        ser.timeout = timeout

        try:
            data = ser.read(4096)
            return data.decode("ascii", errors="replace")
        finally:
            ser.timeout = old_timeout

    def drain(self) -> str:
        """Read and discard all pending input, return what was drained."""
        ser = self._ensure_connected()
        old_timeout = ser.timeout
        ser.timeout = 0.1
        drained = ""

        try:
            while True:
                chunk = ser.read(4096)
                if not chunk:
                    break
                drained += chunk.decode("ascii", errors="replace")
        finally:
            ser.timeout = old_timeout

        return drained

    def enter_spi_viewer(self, reboot_timeout: float = 60) -> str:
        """Intercept boot and send 'v' to enter SPI flash viewer.

        Must be called during device boot (reboot device first).
        The bootloader window is extremely brief (~1s), so we spam 'v'
        at 50ms intervals starting before the banner appears.

        Returns:
            SPI viewer banner output
        """
        import threading

        logger.info("Waiting for bootloader to enter SPI viewer...")
        self.drain()

        # Spam 'v' at 20Hz to catch the brief bootloader window
        stop_spam = threading.Event()

        def _spam_v():
            ser = self._ensure_connected()
            while not stop_spam.is_set():
                ser.write(b"v")
                ser.flush()
                time.sleep(0.05)

        spam_thread = threading.Thread(target=_spam_v, daemon=True)
        spam_thread.start()

        # Read until we see the SPI viewer banner or normal boot
        output = ""
        start = time.monotonic()
        try:
            while time.monotonic() - start < reboot_timeout:
                chunk = self.read_available(timeout=0.2)
                if chunk:
                    output += chunk
                    if self.SPI_PROMPT in output or "SPI FLASH VIEWER" in output:
                        logger.info("SPI viewer mode entered successfully")
                        return output
                    if "RunTime Kernel Starting" in output:
                        raise RuntimeError(
                            "Missed bootloader window — device booted normally"
                        )
        finally:
            stop_spam.set()
            spam_thread.join(timeout=1)

        raise TimeoutError(f"Bootloader not detected within {reboot_timeout}s")

    def spi_read(self, address: int, length: int = 256) -> str:
        """Read from SPI flash using viewer 'r' command.

        Args:
            address: SPI flash address to read from
            length: Number of bytes to read

        Returns:
            Hex dump output from SPI viewer
        """
        self.drain()
        # SPI viewer command format: r <addr> <len>
        self.write(f"r {address:x} {length:x}")
        return self.read_until(self.SPI_PROMPT, timeout=10)


class JTComWeb:
    """HTTP/CGI transport for JT-COM switch web interface.

    Authenticates using MD5(username+password) cookie scheme.
    Parses HTML responses from CGI endpoints.
    """

    def __init__(self, host: str, username: str, password: str, timeout: float = 10):
        self.base_url = f"http://{host}"
        self.username = username
        self.password = password
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._authenticated = False

    async def connect(self) -> None:
        """Create HTTP client and authenticate."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=False,
            headers={"Referer": f"{self.base_url}/"},
        )
        await self._authenticate()

    async def _authenticate(self) -> None:
        """Authenticate via login form POST + MD5 cookie.

        The JT-COM web UI requires:
        1. POST to /login.cgi with username, password, Response (MD5 hash)
        2. Cookie set to admin=MD5(username+password)
        3. Referer header on ALL subsequent requests (or CGI pages return 404)
        """
        if not self._client:
            raise ConnectionError("HTTP client not initialized")

        md5_hash = hashlib.md5(
            f"{self.username}{self.password}".encode()
        ).hexdigest()

        self._client.cookies.set(self.username, md5_hash)

        # POST login form to create server-side session
        await self._client.post(
            f"{self.base_url}/login.cgi",
            data={
                "username": self.username,
                "password": self.password,
                "Response": md5_hash,
                "language": "EN",
            },
        )

        self._authenticated = True
        logger.info(f"Web session established for {self.base_url}")

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            self._authenticated = False

    def _ensure_client(self) -> httpx.AsyncClient:
        if not self._client or not self._authenticated:
            raise ConnectionError("Not authenticated to web interface")
        return self._client

    async def get(self, path: str, params: Optional[dict] = None) -> str:
        """GET a CGI page and return the response text."""
        client = self._ensure_client()
        url = f"{self.base_url}/{path}"
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.text

    async def post(self, path: str, data: dict) -> str:
        """POST to a CGI endpoint and return the response text."""
        client = self._ensure_client()
        url = f"{self.base_url}/{path}"
        resp = await client.post(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.text

    async def get_binary(self, path: str, params: Optional[dict] = None) -> bytes:
        """GET a binary response (for config backup download)."""
        client = self._ensure_client()
        url = f"{self.base_url}/{path}"
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.content

    async def check_auth(self) -> bool:
        """Verify authentication by fetching the info page."""
        try:
            html = await self.get("info.cgi")
            # If we get redirected to login or get empty response, auth failed
            return "Port" in html or "MAC" in html or "info" in html.lower()
        except Exception as e:
            logger.warning(f"Auth check failed: {e}")
            return False


class JTComDevice(NetworkDevice):
    """JT-COM S207CW-91TS managed switch handler.

    Dual-transport device:
    - Serial (UART) for boot interception, SPI flash access, diagnostics
    - HTTP (CGI) for configuration management (VLANs, ports, MACs)

    The 8051-based firmware has no CLI shell — all management is via
    the web CGI interface. Serial is only useful during boot or for
    low-level flash operations.
    """

    # Port mapping: hardware ports 0-8, displayed as Port 1-9
    PORT_COUNT = 9

    def __init__(self, device_id: str, config: DeviceConfig):
        super().__init__(device_id, config)
        # Serial transport config from config_paths
        serial_port = config.config_paths.get("serial_port", "/dev/ttyUSB0")
        serial_baud = int(config.config_paths.get("serial_baud", 57600))
        self._serial = JTComSerial(port=serial_port, baud=serial_baud)
        self._web: Optional[JTComWeb] = None

    def _init_web(self) -> JTComWeb:
        """Lazily initialize web transport."""
        if self._web is None:
            self._web = JTComWeb(
                host=self.config.host,
                username=self.config.username,
                password=self.config.get_password(),
            )
        return self._web

    @with_retry(max_attempts=3, min_wait=2, max_wait=10)
    @timed("connect")
    async def connect(self) -> bool:
        """Connect to JT-COM switch via web interface.

        Serial is connected on-demand for boot/SPI operations.
        """
        logger.info(f"Connecting to JT-COM {self.device_id} at {self.host}")
        web = self._init_web()
        await web.connect()

        if not await web.check_auth():
            raise ConnectionError("Web authentication failed")

        self._connected = True
        logger.info(f"Connected to {self.device_id} via HTTP")
        return True

    async def disconnect(self) -> None:
        """Disconnect all transports."""
        if self._web:
            await self._web.close()
            self._web = None
        if self._serial.is_open:
            self._serial.close()
        self._connected = False
        logger.info(f"Disconnected from {self.device_id}")

    @timed("check_health")
    async def check_health(self) -> DeviceStatus:
        """Check device health via web interface."""
        try:
            if not self._connected:
                await self.connect()

            web = self._init_web()
            html = await web.get("info.cgi")

            version = None
            port_count = self.PORT_COUNT
            active_ports = 0

            # Parse device info from info.cgi HTML
            ver_match = re.search(r"[Ff]irmware.*?[Vv]ersion.*?>(V[\d.]+)<", html)
            if ver_match:
                version = ver_match.group(1)

            mac_match = re.search(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", html)
            if mac_match:
                _ = mac_match.group(0)  # MAC available but not used in status

            # Count active ports from link status indicators
            active_ports = len(re.findall(r"(?:link[_ ]?up|1000M|2500M|100M)", html, re.I))

            return DeviceStatus(
                reachable=True,
                firmware_version=version,
                port_count=port_count,
                active_ports=active_ports,
            )
        except Exception as e:
            return DeviceStatus(reachable=False, error=str(e))

    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute a 'command' — maps to web CGI requests.

        Since the 8051 firmware has no CLI, commands are mapped to
        CGI page fetches. Supported pseudo-commands:
        - show info → info.cgi
        - show ports → info.cgi (port table)
        - show vlans → vlan.cgi?page=static
        - show mac → mac.cgi
        """
        web = self._init_web()
        cmd = command.strip().lower()

        try:
            if cmd in ("show info", "show version"):
                html = await web.get("info.cgi")
                return True, self._strip_html(html)
            elif cmd in ("show ports", "show interfaces"):
                html = await web.get("info.cgi")
                return True, self._strip_html(html)
            elif cmd in ("show vlans", "show vlan"):
                html = await web.get("vlan.cgi", params={"page": "static"})
                return True, self._strip_html(html)
            elif cmd in ("show mac", "show mac-address-table"):
                html = await web.get("mac.cgi")
                return True, self._strip_html(html)
            else:
                return False, f"Unknown command: {command} (8051 firmware has no CLI)"
        except Exception as e:
            return False, str(e)

    async def execute_config_mode(self, commands: list[str]) -> tuple[bool, str]:
        """Not applicable — use specific methods (create_vlan, configure_port, etc)."""
        return False, "JT-COM 8051 firmware has no CLI config mode. Use specific config methods."

    @timed("get_running_config")
    async def get_running_config(self) -> str:
        """Download binary config backup from device.

        Returns base64-encoded binary config (not human-readable).
        For actual settings, use get_vlans() and get_ports().
        """
        import base64

        web = self._init_web()
        try:
            config_bytes = await web.get_binary(
                "config_back.cgi", params={"cmd": "conf_backup"}
            )
            # Return as base64 since config is binary
            b64 = base64.b64encode(config_bytes).decode("ascii")
            return f"[binary config backup, {len(config_bytes)} bytes]\n{b64}"
        except Exception as e:
            logger.error(f"Config backup failed: {e}")
            return ""

    @timed("get_vlans")
    async def get_vlans(self) -> list[VLANConfig]:
        """Get VLAN configurations from vlan.cgi.

        Parses the static VLAN table HTML which contains:
        - VLAN ID
        - VLAN name
        - Member ports (tagged/untagged)
        """
        web = self._init_web()
        html = await web.get("vlan.cgi", params={"page": "static"})

        vlans = []
        # Parse VLAN table rows — typical format:
        # <tr><td>1</td><td>default</td><td>1-9</td><td>-</td>...</tr>
        rows = re.findall(
            r"<tr[^>]*>\s*<td[^>]*>(\d+)</td>\s*<td[^>]*>(.*?)</td>"
            r"\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
            html, re.DOTALL,
        )

        for vid_str, name, untagged_str, tagged_str in rows:
            try:
                vid = int(vid_str.strip())
            except ValueError:
                continue

            name = self._strip_html(name).strip()
            untagged = self._parse_port_list(untagged_str)
            tagged = self._parse_port_list(tagged_str)

            vlans.append(VLANConfig(
                id=vid,
                name=name,
                untagged_ports=untagged,
                tagged_ports=tagged,
            ))

        return vlans

    @timed("get_ports")
    async def get_ports(self) -> list[PortConfig]:
        """Get port status from info.cgi.

        Parses port status table showing link state, speed, duplex.
        """
        web = self._init_web()
        html = await web.get("info.cgi")

        ports = []
        # Parse port status — look for patterns like:
        # Port 1: Link Up, 2500M, Full Duplex
        # Or table rows with port info
        port_rows = re.findall(
            r"<tr[^>]*>\s*<td[^>]*>\s*(\d+)\s*</td>"
            r"\s*<td[^>]*>(.*?)</td>"   # link status
            r"\s*<td[^>]*>(.*?)</td>"   # speed
            r"\s*<td[^>]*>(.*?)</td>",  # duplex
            html, re.DOTALL,
        )

        for port_num, link, speed, duplex in port_rows:
            link_text = self._strip_html(link).strip().lower()
            speed_text = self._strip_html(speed).strip()
            duplex_text = self._strip_html(duplex).strip().lower()

            is_up = "up" in link_text or "link" in link_text

            ports.append(PortConfig(
                name=f"Port{port_num}",
                enabled=True,
                speed=speed_text if is_up else None,
                duplex=duplex_text if is_up and duplex_text else None,
                description="UP" if is_up else "DOWN",
            ))

        # If HTML parsing didn't find structured table, create basic port list
        if not ports:
            for i in range(1, self.PORT_COUNT + 1):
                ports.append(PortConfig(
                    name=f"Port{i}",
                    enabled=True,
                    description="unknown",
                ))

        return ports

    @timed("create_vlan")
    async def create_vlan(self, vlan: VLANConfig) -> tuple[bool, str]:
        """Create a VLAN via web interface POST to vlan.cgi."""
        if vlan.id < 1 or vlan.id > 4094:
            return False, f"Invalid VLAN ID {vlan.id}"

        web = self._init_web()
        try:
            data = {
                "vid": str(vlan.id),
                "vname": vlan.name or f"VLAN{vlan.id}",
                "cmd": "add",
            }

            # Add member ports if specified
            all_ports = set()
            for p in vlan.untagged_ports:
                num = self._extract_port_num(p)
                if num is not None:
                    all_ports.add(num)
                    data[f"utg{num}"] = "1"  # untagged

            for p in vlan.tagged_ports:
                num = self._extract_port_num(p)
                if num is not None:
                    all_ports.add(num)
                    data[f"tg{num}"] = "1"  # tagged

            for num in all_ports:
                data[f"mbr{num}"] = "1"  # member

            resp = await web.post("vlan.cgi", data)

            if "error" in resp.lower():
                return False, f"VLAN creation failed: {self._strip_html(resp)[:200]}"

            return True, f"Created VLAN {vlan.id} ({vlan.name})"
        except Exception as e:
            return False, str(e)

    @timed("delete_vlan")
    async def delete_vlan(self, vlan_id: int) -> tuple[bool, str]:
        """Delete a VLAN via web interface."""
        if vlan_id == 1:
            return False, "Cannot delete default VLAN 1"

        web = self._init_web()
        try:
            resp = await web.post("vlan.cgi", {
                "vid": str(vlan_id),
                "cmd": "del",
            })

            if "error" in resp.lower():
                return False, f"VLAN deletion failed: {self._strip_html(resp)[:200]}"

            return True, f"Deleted VLAN {vlan_id}"
        except Exception as e:
            return False, str(e)

    @timed("configure_port")
    async def configure_port(self, port: PortConfig) -> tuple[bool, str]:
        """Configure port settings via web interface POST to port.cgi."""
        web = self._init_web()
        port_num = self._extract_port_num(port.name)
        if port_num is None:
            return False, f"Invalid port name: {port.name}"

        try:
            data: dict[str, str] = {"port": str(port_num)}

            if port.speed:
                data["speed"] = port.speed
            if port.duplex:
                data["duplex"] = port.duplex
            if not port.enabled:
                data["state"] = "disable"
            else:
                data["state"] = "enable"

            resp = await web.post("port.cgi", data)

            if "error" in resp.lower():
                return False, f"Port config failed: {self._strip_html(resp)[:200]}"

            return True, f"Configured {port.name}"
        except Exception as e:
            return False, str(e)

    @timed("save_config")
    async def save_config(self) -> tuple[bool, str]:
        """Save running config to flash via save.cgi."""
        web = self._init_web()
        try:
            await web.post("save.cgi", {"cmd": "save"})
            return True, "Configuration saved"
        except Exception as e:
            return False, f"Save failed: {e}"

    # --- Serial/boot operations (not part of NetworkDevice ABC) ---

    async def reboot(self) -> tuple[bool, str]:
        """Reboot the switch via web interface."""
        web = self._init_web()
        try:
            await web.post("reboot.cgi", {"cmd": "reboot"})
            self._connected = False
            return True, "Reboot initiated"
        except Exception as e:
            # Connection may drop during reboot — that's expected
            if "closed" in str(e).lower() or "reset" in str(e).lower():
                self._connected = False
                return True, "Reboot initiated (connection dropped as expected)"
            return False, f"Reboot failed: {e}"

    async def enter_spi_viewer(self) -> tuple[bool, str]:
        """Reboot and intercept boot to enter SPI flash viewer.

        Must have serial connected. Will reboot the device via web,
        then catch the bootloader via serial to enter SPI mode.
        """
        loop = asyncio.get_event_loop()

        # Reboot via web
        await self.reboot()

        # Wait for bootloader on serial
        try:
            output = await loop.run_in_executor(
                None, self._serial.enter_spi_viewer, 30
            )
            return True, output
        except Exception as e:
            return False, f"SPI viewer entry failed: {e}"

    async def dump_firmware(self, address: int = 0, length: int = 0x1000) -> tuple[bool, str]:
        """Read firmware from SPI flash (must be in SPI viewer mode).

        Args:
            address: Start address in SPI flash
            length: Number of bytes to read
        """
        loop = asyncio.get_event_loop()
        try:
            output = await loop.run_in_executor(
                None, self._serial.spi_read, address, length
            )
            return True, output
        except Exception as e:
            return False, f"SPI read failed: {e}"

    async def serial_connect(self) -> tuple[bool, str]:
        """Open the serial port for monitoring."""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._serial.connect)
            return True, f"Serial connected: {self._serial.port}"
        except Exception as e:
            return False, f"Serial connection failed: {e}"

    async def serial_read(self, timeout: float = 2) -> tuple[bool, str]:
        """Read available serial output."""
        loop = asyncio.get_event_loop()
        try:
            output = await loop.run_in_executor(
                None, self._serial.read_available, timeout
            )
            return True, output or "(no data)"
        except Exception as e:
            return False, str(e)

    # --- Helpers ---

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags, returning plain text."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _extract_port_num(port_name: str) -> Optional[int]:
        """Extract port number from various formats.

        Accepts: "Port1", "port1", "1", "Port 1"
        Returns: 1 (or None if unparseable)
        """
        match = re.search(r"(\d+)", str(port_name))
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_port_list(text: str) -> list[str]:
        """Parse port list from HTML table cell.

        Input: "1-4,7,9" or "1,2,3" or "-"
        Output: ["Port1", "Port2", "Port3", "Port4", "Port7", "Port9"]
        """
        text = re.sub(r"<[^>]+>", "", text).strip()
        if not text or text == "-" or text == "--":
            return []

        ports = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    start, end = part.split("-", 1)
                    for i in range(int(start), int(end) + 1):
                        ports.append(f"Port{i}")
                except ValueError:
                    ports.append(f"Port{part}")
            else:
                try:
                    ports.append(f"Port{int(part)}")
                except ValueError:
                    pass

        return ports

    @staticmethod
    def decode_config_password(encrypted: bytes, offset: int = 0) -> str:
        """Decode XOR-encrypted password from config backup.

        The config backup uses XOR with key = 0x5a + byte_index.
        """
        result = []
        for i, byte in enumerate(encrypted):
            key = (0x5A + offset + i) & 0xFF
            result.append(byte ^ key)
        return bytes(result).decode("ascii", errors="replace").rstrip("\x00")
