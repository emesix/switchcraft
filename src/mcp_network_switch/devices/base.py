"""Base device abstraction for network switches."""
import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class DeviceConfig:
    """Configuration for a network device."""
    type: str
    name: str
    host: str
    protocol: str
    port: int
    username: str
    password: Optional[str] = None
    password_env: str = "NETWORK_PASSWORD"
    timeout: int = 30
    retries: int = 3
    retry_delay: float = 2
    # Device-specific options
    enable_password_required: bool = False
    verify_ssl: bool = True
    use_scp_workflow: bool = False
    config_paths: dict = field(default_factory=dict)

    def get_password(self) -> str:
        """Get password from config or environment variable."""
        if self.password:
            return self.password
        return os.environ.get(self.password_env, "")


@dataclass
class VLANConfig:
    """Normalized VLAN configuration."""
    id: int
    name: str = ""
    tagged_ports: list[str] = field(default_factory=list)
    untagged_ports: list[str] = field(default_factory=list)
    ip_address: Optional[str] = None
    ip_mask: Optional[str] = None
    description: str = ""


@dataclass
class PortConfig:
    """Normalized port configuration."""
    name: str
    enabled: bool = True
    speed: Optional[str] = None  # auto, 100M, 1G, 10G
    duplex: Optional[str] = None  # auto, full, half
    vlan_mode: str = "access"  # access, trunk, hybrid
    native_vlan: Optional[int] = None
    allowed_vlans: list[int] = field(default_factory=list)
    description: str = ""
    poe_enabled: Optional[bool] = None


@dataclass
class DeviceStatus:
    """Device health and status information."""
    reachable: bool
    uptime: Optional[str] = None
    firmware_version: Optional[str] = None
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    temperature: Optional[float] = None
    port_count: int = 0
    active_ports: int = 0
    error: Optional[str] = None


class NetworkDevice(ABC):
    """Abstract base class for network device handlers."""

    def __init__(self, device_id: str, config: DeviceConfig):
        self.device_id = device_id
        self.config = config
        self._connected = False
        self._connection: Any = None

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def host(self) -> str:
        return self.config.host

    @property
    def is_connected(self) -> bool:
        return self._connected

    # Connection management
    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the device."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the device."""
        pass

    @abstractmethod
    async def check_health(self) -> DeviceStatus:
        """Check device health and connectivity."""
        pass

    # Command execution
    @abstractmethod
    async def execute(self, command: str) -> tuple[bool, str]:
        """Execute a raw command on the device.

        Returns:
            Tuple of (success, output)
        """
        pass

    @abstractmethod
    async def execute_config_mode(self, commands: list[str]) -> tuple[bool, str]:
        """Execute commands in configuration mode.

        Returns:
            Tuple of (success, output)
        """
        pass

    # Configuration retrieval
    @abstractmethod
    async def get_running_config(self) -> str:
        """Get the current running configuration."""
        pass

    @abstractmethod
    async def get_vlans(self) -> list[VLANConfig]:
        """Get all VLAN configurations."""
        pass

    @abstractmethod
    async def get_ports(self) -> list[PortConfig]:
        """Get all port configurations."""
        pass

    # Configuration modification
    @abstractmethod
    async def create_vlan(self, vlan: VLANConfig) -> tuple[bool, str]:
        """Create or update a VLAN."""
        pass

    @abstractmethod
    async def delete_vlan(self, vlan_id: int) -> tuple[bool, str]:
        """Delete a VLAN."""
        pass

    @abstractmethod
    async def configure_port(self, port: PortConfig) -> tuple[bool, str]:
        """Configure a port."""
        pass

    @abstractmethod
    async def save_config(self) -> tuple[bool, str]:
        """Save running config to startup config."""
        pass

    # SCP-based workflow (optional, for devices that support it)
    async def download_config(self, remote_path: str, local_path: str) -> tuple[bool, str]:
        """Download config file via SCP."""
        return False, "SCP not supported on this device"

    async def upload_config(self, local_path: str, remote_path: str) -> tuple[bool, str]:
        """Upload config file via SCP."""
        return False, "SCP not supported on this device"

    async def reload_config(self) -> tuple[bool, str]:
        """Reload configuration after SCP upload."""
        return False, "Config reload not supported on this device"

    async def get_config_file(self, config_name: str) -> str:
        """Get config file contents by name (for devices that support it)."""
        raise NotImplementedError("get_config_file not supported on this device")

    async def put_config_file(self, config_name: str, content: str) -> tuple[bool, str]:
        """Put config file contents by name (for devices that support it)."""
        return False, "put_config_file not supported on this device"

    # Context manager support
    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
        return False
