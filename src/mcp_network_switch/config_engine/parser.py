"""Parser for desired state configuration.

Converts dict/YAML input to strongly-typed DesiredState objects.
"""
import hashlib
import json
from typing import Any

from .schema import (
    DesiredState,
    VLANDesiredState,
    VLANAction,
    PortDesiredState,
    IPInterface,
)


class ParseError(Exception):
    """Error parsing desired state configuration."""
    pass


class ConfigParser:
    """Parse desired state from dict/YAML format."""

    def parse(self, config: dict[str, Any]) -> DesiredState:
        """
        Parse a configuration dict into a DesiredState object.

        Args:
            config: Dict with device_id, vlans, ports, etc.

        Returns:
            DesiredState object

        Raises:
            ParseError: If config is invalid
        """
        # Required field
        device_id = config.get("device_id") or config.get("device")
        if not device_id:
            raise ParseError("Missing required field: device_id or device")

        # Optional fields with defaults
        version = config.get("version", 1)
        checksum = config.get("checksum")
        mode = config.get("mode", "patch")

        if mode not in ("full", "patch"):
            raise ParseError(f"Invalid mode: {mode}. Must be 'full' or 'patch'")

        # Parse VLANs
        vlans = self._parse_vlans(config.get("vlans", {}))

        # Parse ports
        ports = self._parse_ports(config.get("ports", {}))

        # Parse settings
        settings = config.get("settings", {})

        return DesiredState(
            device_id=device_id,
            version=version,
            checksum=checksum,
            mode=mode,
            vlans=vlans,
            ports=ports,
            settings=settings,
        )

    def _parse_vlans(
        self,
        vlans_config: dict[str | int, Any]
    ) -> dict[int, VLANDesiredState]:
        """Parse VLAN configurations."""
        vlans = {}

        for vlan_id, vlan_config in vlans_config.items():
            # Convert string VLAN ID to int
            try:
                vlan_id_int = int(vlan_id)
            except (ValueError, TypeError):
                raise ParseError(f"Invalid VLAN ID: {vlan_id}")

            vlans[vlan_id_int] = self._parse_single_vlan(vlan_id_int, vlan_config)

        return vlans

    def _parse_single_vlan(
        self,
        vlan_id: int,
        config: dict[str, Any] | None
    ) -> VLANDesiredState:
        """Parse a single VLAN configuration."""
        if config is None:
            config = {}

        # Parse action
        action_str = config.get("action", "ensure")
        try:
            action = VLANAction(action_str)
        except ValueError:
            raise ParseError(
                f"Invalid action for VLAN {vlan_id}: {action_str}. "
                f"Must be 'ensure' or 'absent'"
            )

        # Parse ports (expand ranges if needed)
        untagged_ports = self._expand_port_list(
            config.get("untagged_ports", [])
        )
        tagged_ports = self._expand_port_list(
            config.get("tagged_ports", [])
        )

        # Parse IP interface
        ip_interface = None
        ip_config = config.get("ip_interface")
        if ip_config:
            ip_interface = IPInterface(
                address=ip_config.get("address", ""),
                mask=ip_config.get("mask", ""),
            )

        return VLANDesiredState(
            id=vlan_id,
            action=action,
            name=config.get("name"),
            untagged_ports=untagged_ports,
            tagged_ports=tagged_ports,
            ip_interface=ip_interface,
        )

    def _parse_ports(
        self,
        ports_config: dict[str, Any]
    ) -> dict[str, PortDesiredState]:
        """Parse port configurations."""
        ports = {}

        for port_name, port_config in ports_config.items():
            ports[port_name] = self._parse_single_port(port_name, port_config)

        return ports

    def _parse_single_port(
        self,
        port_name: str,
        config: dict[str, Any] | None
    ) -> PortDesiredState:
        """Parse a single port configuration."""
        if config is None:
            config = {}

        return PortDesiredState(
            name=port_name,
            enabled=config.get("enabled"),
            description=config.get("description"),
            speed=config.get("speed"),
        )

    def _expand_port_list(self, ports: list[str] | str) -> list[str]:
        """
        Expand port list, handling ranges like "1/1/1-4".

        Examples:
            ["1/1/1", "1/1/2"] -> ["1/1/1", "1/1/2"]
            ["1/1/1-4"] -> ["1/1/1", "1/1/2", "1/1/3", "1/1/4"]
            "1/1/1-4" -> ["1/1/1", "1/1/2", "1/1/3", "1/1/4"]
        """
        # Handle single string input
        if isinstance(ports, str):
            ports = [ports]

        expanded = []
        for port in ports:
            if "-" in port and "/" in port:
                # Might be a range like "1/1/1-4"
                expanded.extend(self._expand_port_range(port))
            else:
                expanded.append(port)

        return expanded

    def _expand_port_range(self, port_spec: str) -> list[str]:
        """
        Expand a port range like "1/1/1-4" to ["1/1/1", "1/1/2", "1/1/3", "1/1/4"].

        Also handles full ranges like "1/1/1-1/1/4".
        """
        # Check for simple range: "1/1/1-4"
        if port_spec.count("-") == 1:
            parts = port_spec.rsplit("-", 1)
            base = parts[0]  # "1/1/1"
            end = parts[1]   # "4"

            # Check if it's a full port spec or just a number
            if "/" in end:
                # Full range like "1/1/1-1/1/4"
                return self._expand_full_range(parts[0], end)
            else:
                # Simple range like "1/1/1-4"
                try:
                    end_num = int(end)
                    base_parts = base.rsplit("/", 1)
                    prefix = base_parts[0]  # "1/1"
                    start_num = int(base_parts[1])

                    return [
                        f"{prefix}/{i}"
                        for i in range(start_num, end_num + 1)
                    ]
                except (ValueError, IndexError):
                    # Can't parse, return as-is
                    return [port_spec]

        # Not a range, return as-is
        return [port_spec]

    def _expand_full_range(self, start: str, end: str) -> list[str]:
        """Expand a full range like 1/1/1 to 1/1/4."""
        try:
            start_parts = start.split("/")
            end_parts = end.split("/")

            if len(start_parts) != 3 or len(end_parts) != 3:
                return [start, end]

            # Must be same unit/module
            if start_parts[0] != end_parts[0] or start_parts[1] != end_parts[1]:
                return [start, end]

            prefix = f"{start_parts[0]}/{start_parts[1]}"
            start_port = int(start_parts[2])
            end_port = int(end_parts[2])

            return [
                f"{prefix}/{i}"
                for i in range(start_port, end_port + 1)
            ]
        except (ValueError, IndexError):
            return [start, end]


def compute_checksum(config: dict[str, Any]) -> str:
    """
    Compute SHA256 checksum of a config dict.

    Useful for integrity verification.
    """
    # Remove existing checksum field for computation
    config_copy = {k: v for k, v in config.items() if k != "checksum"}

    # Serialize deterministically
    config_str = json.dumps(config_copy, sort_keys=True, separators=(",", ":"))

    # Compute hash
    hash_bytes = hashlib.sha256(config_str.encode()).hexdigest()

    return f"sha256:{hash_bytes[:16]}"  # Short hash for readability
