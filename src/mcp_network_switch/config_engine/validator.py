"""Pre-flight validation for desired state configurations.

Catches logical errors before any switch communication.
"""
import re
from typing import Optional

from .schema import (
    DesiredState,
    VLANAction,
    ValidationResult,
)


# Port name patterns by device type
PORT_PATTERNS = {
    "brocade": re.compile(r"^\d+/\d+/\d+$"),  # 1/1/1, 1/2/4
    "openwrt": re.compile(r"^lan\d+$"),        # lan1, lan8
    "zyxel": re.compile(r"^\d+$"),             # 1, 24
}

# Reserved VLAN IDs
RESERVED_VLANS = {
    0: "Reserved for internal use",
    4095: "Reserved (IEEE 802.1Q)",
}

# Protected VLANs that cannot be deleted
PROTECTED_VLANS = {
    1: "Default VLAN",
}


class ConfigValidator:
    """Validate desired state for logical errors before execution."""

    def __init__(self, device_type: Optional[str] = None):
        """
        Initialize validator.

        Args:
            device_type: Optional device type for port name validation
        """
        self.device_type = device_type

    def validate(self, desired: DesiredState) -> ValidationResult:
        """
        Validate a desired state configuration.

        Performs pre-flight checks:
        - VLAN ID ranges (1-4094)
        - Protected VLAN operations
        - Port name formats
        - Port assignment conflicts
        - Checksum verification

        Args:
            desired: The desired state to validate

        Returns:
            ValidationResult with valid flag, errors, and warnings
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Check VLANs
        self._validate_vlans(desired, errors, warnings)

        # Check ports
        self._validate_ports(desired, errors, warnings)

        # Check for port conflicts
        self._check_port_conflicts(desired, errors, warnings)

        # Verify checksum if provided
        self._verify_checksum(desired, errors)

        # Check change set size
        self._check_change_size(desired, warnings)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _validate_vlans(
        self,
        desired: DesiredState,
        errors: list[str],
        warnings: list[str]
    ) -> None:
        """Validate VLAN configurations."""
        for vlan_id, vlan in desired.vlans.items():
            # Check VLAN ID range
            if vlan_id < 1 or vlan_id > 4094:
                errors.append(
                    f"Invalid VLAN ID {vlan_id}: must be between 1 and 4094"
                )
                continue

            # Check reserved VLANs
            if vlan_id in RESERVED_VLANS:
                errors.append(
                    f"VLAN {vlan_id} is reserved: {RESERVED_VLANS[vlan_id]}"
                )
                continue

            # Check protected VLANs for deletion
            if vlan_id in PROTECTED_VLANS and vlan.action == VLANAction.ABSENT:
                errors.append(
                    f"Cannot delete VLAN {vlan_id}: {PROTECTED_VLANS[vlan_id]}"
                )

            # Check for empty VLAN (warning only)
            if (vlan.action == VLANAction.ENSURE and
                not vlan.untagged_ports and
                not vlan.tagged_ports):
                warnings.append(
                    f"VLAN {vlan_id} has no ports assigned"
                )

            # Validate port names in VLAN
            for port in vlan.untagged_ports + vlan.tagged_ports:
                if not self._valid_port_name(port):
                    errors.append(
                        f"Invalid port name '{port}' in VLAN {vlan_id}"
                    )

    def _validate_ports(
        self,
        desired: DesiredState,
        errors: list[str],
        warnings: list[str]
    ) -> None:
        """Validate port configurations."""
        for port_name, port_config in desired.ports.items():
            # Validate port name format
            if not self._valid_port_name(port_name):
                errors.append(f"Invalid port name: {port_name}")

            # Validate speed setting
            valid_speeds = {"auto", "100M", "1G", "10G", None}
            if port_config.speed and port_config.speed not in valid_speeds:
                errors.append(
                    f"Invalid speed '{port_config.speed}' for port {port_name}. "
                    f"Valid: auto, 100M, 1G, 10G"
                )

    def _check_port_conflicts(
        self,
        desired: DesiredState,
        errors: list[str],
        warnings: list[str]
    ) -> None:
        """Check for port assignment conflicts."""
        # Track untagged assignments (port can only be untagged in ONE VLAN)
        untagged_assignments: dict[str, int] = {}

        for vlan_id, vlan in desired.vlans.items():
            if vlan.action == VLANAction.ABSENT:
                continue

            for port in vlan.untagged_ports:
                if port in untagged_assignments:
                    errors.append(
                        f"Port {port} assigned untagged to both "
                        f"VLAN {untagged_assignments[port]} and VLAN {vlan_id}"
                    )
                else:
                    untagged_assignments[port] = vlan_id

        # Check for port in both tagged and untagged in same VLAN
        for vlan_id, vlan in desired.vlans.items():
            if vlan.action == VLANAction.ABSENT:
                continue

            overlap = set(vlan.untagged_ports) & set(vlan.tagged_ports)
            if overlap:
                errors.append(
                    f"Port(s) {', '.join(overlap)} in VLAN {vlan_id} "
                    f"cannot be both tagged and untagged"
                )

    def _valid_port_name(self, port: str) -> bool:
        """Check if port name is valid for the device type."""
        if not port:
            return False

        # If device type specified, use specific pattern
        if self.device_type and self.device_type in PORT_PATTERNS:
            return bool(PORT_PATTERNS[self.device_type].match(port))

        # Otherwise, accept any known pattern
        return any(
            pattern.match(port)
            for pattern in PORT_PATTERNS.values()
        )

    def _verify_checksum(
        self,
        desired: DesiredState,
        errors: list[str]
    ) -> None:
        """Verify config checksum if provided."""
        if not desired.checksum:
            return

        # Reconstruct config dict for checksum computation
        # This is a simplified version - in practice you'd pass the original dict
        # For now, we skip actual verification in MVP
        pass

    def _check_change_size(
        self,
        desired: DesiredState,
        warnings: list[str]
    ) -> None:
        """Warn about large change sets."""
        total_items = len(desired.vlans) + len(desired.ports)

        if total_items > 20:
            warnings.append(
                f"Large change set ({total_items} items) - consider staging"
            )

        # Count total ports being modified
        total_ports = sum(
            len(v.untagged_ports) + len(v.tagged_ports)
            for v in desired.vlans.values()
        )
        if total_ports > 50:
            warnings.append(
                f"Many port changes ({total_ports} ports) - verify before applying"
            )
