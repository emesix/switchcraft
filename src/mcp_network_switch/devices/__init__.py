"""Device handlers for different switch types."""
from .base import NetworkDevice, DeviceConfig
from .brocade import BrocadeDevice
from .onti import ONTIDevice
from .zyxel import ZyxelDevice

__all__ = [
    "NetworkDevice",
    "DeviceConfig",
    "BrocadeDevice",
    "ONTIDevice",
    "ZyxelDevice",
]

# Device type registry
DEVICE_TYPES = {
    "brocade": BrocadeDevice,
    "onti": ONTIDevice,
    "zyxel": ZyxelDevice,
}


def create_device(device_id: str, config: dict) -> NetworkDevice:
    """Factory function to create device instances."""
    device_type = config.get("type", "").lower()
    if device_type not in DEVICE_TYPES:
        raise ValueError(f"Unknown device type: {device_type}")

    device_class = DEVICE_TYPES[device_type]
    return device_class(device_id, DeviceConfig(**config))
