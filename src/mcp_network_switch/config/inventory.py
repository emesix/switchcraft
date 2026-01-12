"""Device inventory management from YAML configuration."""
from pathlib import Path
from typing import Optional

import yaml

from ..devices import create_device, NetworkDevice


class DeviceInventory:
    """Manages the device inventory loaded from YAML config."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self._find_config()
        self._config: dict = {}
        self._devices: dict[str, NetworkDevice] = {}
        self._load_config()

    def _find_config(self) -> str:
        """Find the devices.yaml config file."""
        search_paths = [
            Path.cwd() / "configs" / "devices.yaml",
            Path.cwd() / "devices.yaml",
            Path.home() / ".config" / "mcp-network-switch" / "devices.yaml",
            Path("/etc/mcp-network-switch/devices.yaml"),
        ]

        for path in search_paths:
            if path.exists():
                return str(path)

        raise FileNotFoundError(
            "Could not find devices.yaml. Create one in ./configs/devices.yaml"
        )

    def _load_config(self) -> None:
        """Load the YAML configuration."""
        with open(self.config_path) as f:
            self._config = yaml.safe_load(f)

        # Apply defaults
        defaults = self._config.get("defaults", {})
        for device_id, device_config in self._config.get("devices", {}).items():
            # Merge defaults
            for key, value in defaults.items():
                if key not in device_config:
                    device_config[key] = value

    def get_device_ids(self) -> list[str]:
        """Get all device IDs."""
        return list(self._config.get("devices", {}).keys())

    def get_device_config(self, device_id: str) -> dict:
        """Get raw config for a device."""
        devices = self._config.get("devices", {})
        if device_id not in devices:
            raise KeyError(f"Unknown device: {device_id}")
        return devices[device_id]

    def get_device(self, device_id: str) -> NetworkDevice:
        """Get or create a device instance."""
        if device_id not in self._devices:
            config = self.get_device_config(device_id)
            self._devices[device_id] = create_device(device_id, config)
        return self._devices[device_id]

    def get_all_devices(self) -> dict[str, NetworkDevice]:
        """Get all device instances."""
        for device_id in self.get_device_ids():
            self.get_device(device_id)
        return self._devices

    def get_devices_by_type(self, device_type: str) -> list[NetworkDevice]:
        """Get devices filtered by type."""
        result = []
        for device_id, config in self._config.get("devices", {}).items():
            if config.get("type") == device_type:
                result.append(self.get_device(device_id))
        return result

    def get_snmp_community(self, device_id: str) -> Optional[str]:
        """Get SNMP community for a device."""
        snmp_config = self._config.get("snmp", {}).get("communities", {})
        for community, devices in snmp_config.items():
            if device_id in devices:
                return community
        return None

    async def close_all(self) -> None:
        """Close all device connections."""
        for device in self._devices.values():
            if device.is_connected:
                await device.disconnect()
        self._devices.clear()
