"""HIL mode configuration and enforcement.

Environment variables:
- SWITCHCRAFT_HIL_MODE: Set to "1" to enable HIL mode
- SWITCHCRAFT_HIL_VLAN: Override test VLAN (default: 999)
- SWITCHCRAFT_HIL_ALLOWED_DEVICES: Comma-separated device IPs
"""
import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Default HIL VLAN - chosen for compatibility with older hardware
DEFAULT_HIL_VLAN = 999

# Default allowed devices (lab network)
DEFAULT_ALLOWED_DEVICES = [
    "192.168.254.2",
    "192.168.254.3",
    "192.168.254.4",
]


@dataclass
class HILDeviceSpec:
    """Per-device HIL configuration."""
    host: str
    access_port: str
    trunk_port: str


@dataclass
class HILConfig:
    """HIL mode configuration."""
    enabled: bool = False
    vlan_id: int = DEFAULT_HIL_VLAN
    vlan_name: str = "HIL-TEST-999"
    allowed_devices: list[str] = field(default_factory=lambda: DEFAULT_ALLOWED_DEVICES.copy())
    device_specs: dict[str, HILDeviceSpec] = field(default_factory=dict)
    protected_vlans: list[int] = field(default_factory=lambda: [1, 254])
    max_ports_per_device: int = 2

    @classmethod
    def from_env(cls) -> "HILConfig":
        """Load HIL config from environment variables."""
        enabled = os.environ.get("SWITCHCRAFT_HIL_MODE", "0") == "1"
        vlan_id = int(os.environ.get("SWITCHCRAFT_HIL_VLAN", str(DEFAULT_HIL_VLAN)))

        devices_str = os.environ.get("SWITCHCRAFT_HIL_ALLOWED_DEVICES", "")
        if devices_str:
            allowed_devices = [d.strip() for d in devices_str.split(",")]
        else:
            allowed_devices = DEFAULT_ALLOWED_DEVICES.copy()

        return cls(
            enabled=enabled,
            vlan_id=vlan_id,
            allowed_devices=allowed_devices,
        )

    @classmethod
    def from_spec_file(cls, spec_path: Path) -> "HILConfig":
        """Load HIL config from hil_spec.yaml."""
        if not spec_path.exists():
            logger.warning(f"HIL spec file not found: {spec_path}")
            return cls()

        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        device_specs = {}
        for device_id, device_config in spec.get("devices", {}).items():
            device_specs[device_id] = HILDeviceSpec(
                host=device_config.get("host", ""),
                access_port=device_config.get("access_port", ""),
                trunk_port=device_config.get("trunk_port", ""),
            )

        constraints = spec.get("constraints", {})

        return cls(
            enabled=True,  # If loading from spec, HIL is enabled
            vlan_id=spec.get("vlan_id", DEFAULT_HIL_VLAN),
            vlan_name=spec.get("vlan_name", "HIL-TEST-999"),
            allowed_devices=[d.host for d in device_specs.values()],
            device_specs=device_specs,
            protected_vlans=constraints.get("protected_vlans", [1, 254]),
            max_ports_per_device=constraints.get("max_ports_per_device", 2),
        )


class HILMode:
    """Singleton for HIL mode state."""

    _instance: Optional["HILMode"] = None
    _config: Optional[HILConfig] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def initialize(cls, config: Optional[HILConfig] = None) -> "HILMode":
        """Initialize HIL mode with configuration."""
        instance = cls()
        if config:
            cls._config = config
        else:
            cls._config = HILConfig.from_env()

        if cls._config.enabled:
            logger.warning(
                f"HIL MODE ENABLED - Only VLAN {cls._config.vlan_id} operations permitted"
            )
            logger.warning(f"Allowed devices: {cls._config.allowed_devices}")

        return instance

    @classmethod
    def get_config(cls) -> HILConfig:
        """Get current HIL configuration."""
        if cls._config is None:
            cls._config = HILConfig.from_env()
        return cls._config

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if HIL mode is enabled."""
        return cls.get_config().enabled

    @classmethod
    def reset(cls) -> None:
        """Reset HIL mode (for testing)."""
        cls._config = None


def is_hil_enabled() -> bool:
    """Check if HIL mode is enabled."""
    return HILMode.is_enabled()


def get_hil_config() -> HILConfig:
    """Get current HIL configuration."""
    return HILMode.get_config()
