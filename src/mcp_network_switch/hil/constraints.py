"""HIL constraint validation and enforcement.

When HIL mode is enabled, ALL VLAN operations are validated against:
1. VLAN ID must be the HIL test VLAN (999)
2. Device must be in the allowed list
3. Ports must be in the device's allowed port list
4. Protected VLANs cannot be modified
"""
import logging
from typing import Optional

from .mode import get_hil_config, is_hil_enabled

logger = logging.getLogger(__name__)


class HILConstraintError(Exception):
    """Raised when an operation violates HIL constraints."""

    def __init__(self, message: str, constraint: str, attempted_value: str = ""):
        self.message = message
        self.constraint = constraint
        self.attempted_value = attempted_value
        super().__init__(f"HIL CONSTRAINT VIOLATION [{constraint}]: {message}")


def validate_hil_operation(
    operation: str,
    device_host: str,
    vlan_id: Optional[int] = None,
    ports: Optional[list[str]] = None,
    device_id: Optional[str] = None,
) -> None:
    """Validate an operation against HIL constraints.

    Raises HILConstraintError if the operation violates any constraint.

    Args:
        operation: The operation being performed (create_vlan, delete_vlan, etc.)
        device_host: The device IP address
        vlan_id: The VLAN ID being operated on (if applicable)
        ports: The ports being modified (if applicable)
        device_id: The device identifier (e.g., "lab-brocade")
    """
    if not is_hil_enabled():
        return  # No constraints when HIL mode is disabled

    config = get_hil_config()

    # 1. Validate device is allowed
    if device_host not in config.allowed_devices:
        raise HILConstraintError(
            f"Device {device_host} is not in HIL allowed list: {config.allowed_devices}",
            constraint="ALLOWED_DEVICES",
            attempted_value=device_host,
        )

    # 2. Validate VLAN ID
    if vlan_id is not None:
        # Check if it's the HIL test VLAN
        if vlan_id != config.vlan_id:
            raise HILConstraintError(
                f"Only VLAN {config.vlan_id} operations permitted in HIL mode. "
                f"Attempted: VLAN {vlan_id}",
                constraint="HIL_VLAN_ONLY",
                attempted_value=str(vlan_id),
            )

        # Check if it's a protected VLAN
        if vlan_id in config.protected_vlans:
            raise HILConstraintError(
                f"VLAN {vlan_id} is protected and cannot be modified",
                constraint="PROTECTED_VLAN",
                attempted_value=str(vlan_id),
            )

    # 3. Validate ports (if device_id is provided and we have specs)
    if ports and device_id and device_id in config.device_specs:
        spec = config.device_specs[device_id]
        allowed_ports = {spec.access_port, spec.trunk_port}

        for port in ports:
            if port not in allowed_ports:
                raise HILConstraintError(
                    f"Port {port} is not in HIL allowed ports for {device_id}: {allowed_ports}",
                    constraint="ALLOWED_PORTS",
                    attempted_value=port,
                )

    # 4. Validate port count
    if ports and len(ports) > config.max_ports_per_device:
        raise HILConstraintError(
            f"Too many ports ({len(ports)}) - max {config.max_ports_per_device} per device",
            constraint="MAX_PORTS",
            attempted_value=str(len(ports)),
        )

    logger.debug(
        f"HIL validation passed: {operation} on {device_host} "
        f"(vlan={vlan_id}, ports={ports})"
    )


def validate_vlan_create(device_host: str, vlan_id: int, device_id: Optional[str] = None) -> None:
    """Validate VLAN creation in HIL mode."""
    validate_hil_operation(
        operation="create_vlan",
        device_host=device_host,
        vlan_id=vlan_id,
        device_id=device_id,
    )


def validate_vlan_delete(device_host: str, vlan_id: int, device_id: Optional[str] = None) -> None:
    """Validate VLAN deletion in HIL mode."""
    validate_hil_operation(
        operation="delete_vlan",
        device_host=device_host,
        vlan_id=vlan_id,
        device_id=device_id,
    )


def validate_port_config(
    device_host: str,
    ports: list[str],
    vlan_id: Optional[int] = None,
    device_id: Optional[str] = None,
) -> None:
    """Validate port configuration in HIL mode."""
    validate_hil_operation(
        operation="configure_port",
        device_host=device_host,
        vlan_id=vlan_id,
        ports=ports,
        device_id=device_id,
    )
