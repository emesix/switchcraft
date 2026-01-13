"""HIL (Hardware-in-the-Loop) testing module.

This module provides server-enforced safety constraints for HIL testing:
- Only VLAN 999 operations permitted
- Only allowlisted devices (192.168.254.2-4)
- Only designated ports per device
- Full lifecycle testing with rollback verification
"""
from .mode import HILMode, HILConfig, is_hil_enabled, get_hil_config
from .constraints import HILConstraintError, validate_hil_operation
from .runner import HILRunner, HILResult, HILStage

__all__ = [
    "HILMode",
    "HILConfig",
    "is_hil_enabled",
    "get_hil_config",
    "HILConstraintError",
    "validate_hil_operation",
    "HILRunner",
    "HILResult",
    "HILStage",
]
