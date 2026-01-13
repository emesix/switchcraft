"""Device inventory management from YAML configuration."""
import logging
from pathlib import Path
from typing import Optional

import yaml

from ..devices import create_device, NetworkDevice

logger = logging.getLogger(__name__)


class DeviceInventory:
    """Manages the device inventory loaded from YAML config.

    Supports device groups for fleet management:

    ```yaml
    groups:
      access-points:
        - ap-living-room
        - ap-bedroom
        - ap-office
      switches:
        - brocade-core
        - zyxel-frontend
    ```
    """

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

        # Validate groups reference valid devices
        self._validate_groups()

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

    # === Group Management ===

    def _validate_groups(self) -> None:
        """Validate that all group members reference valid devices."""
        groups = self._config.get("groups", {})
        devices = self._config.get("devices", {})

        for group_name, members in groups.items():
            if not isinstance(members, list):
                logger.warning(f"Group '{group_name}' should be a list of device IDs")
                continue
            for device_id in members:
                if device_id not in devices:
                    logger.warning(
                        f"Group '{group_name}' references unknown device: {device_id}"
                    )

    def get_groups(self) -> dict[str, list[str]]:
        """Get all defined groups and their members.

        Returns:
            Dict mapping group names to lists of device IDs
        """
        return dict(self._config.get("groups", {}))

    def get_group_names(self) -> list[str]:
        """Get list of all group names."""
        return list(self._config.get("groups", {}).keys())

    def get_group_members(self, group_name: str) -> list[str]:
        """Get device IDs in a group.

        Args:
            group_name: Name of the group

        Returns:
            List of device IDs in the group

        Raises:
            KeyError: If group doesn't exist
        """
        groups = self._config.get("groups", {})
        if group_name not in groups:
            raise KeyError(f"Unknown group: {group_name}")
        return list(groups[group_name])

    def get_devices_in_group(self, group_name: str) -> list[NetworkDevice]:
        """Get device instances for all members of a group.

        Args:
            group_name: Name of the group

        Returns:
            List of NetworkDevice instances
        """
        device_ids = self.get_group_members(group_name)
        return [self.get_device(device_id) for device_id in device_ids]

    def get_group_info(self, group_name: str) -> dict:
        """Get detailed info about a group.

        Returns dict with:
            - name: Group name
            - members: List of device IDs
            - member_count: Number of devices
            - device_types: Unique device types in group
        """
        members = self.get_group_members(group_name)
        device_types = set()

        for device_id in members:
            try:
                config = self.get_device_config(device_id)
                device_types.add(config.get("type", "unknown"))
            except KeyError:
                pass

        return {
            "name": group_name,
            "members": members,
            "member_count": len(members),
            "device_types": sorted(device_types),
        }

    def is_device_in_group(self, device_id: str, group_name: str) -> bool:
        """Check if a device is a member of a group."""
        try:
            members = self.get_group_members(group_name)
            return device_id in members
        except KeyError:
            return False

    def get_device_groups(self, device_id: str) -> list[str]:
        """Get all groups a device belongs to."""
        groups = []
        for group_name, members in self._config.get("groups", {}).items():
            if device_id in members:
                groups.append(group_name)
        return groups
