"""Schema definitions for the Config Engine.

Defines the desired state format and all related dataclasses.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional


class VLANAction(str, Enum):
    """Action to take for a VLAN."""
    ENSURE = "ensure"   # Create if missing, update if different
    ABSENT = "absent"   # Delete if exists


class ChangeType(str, Enum):
    """Type of change in a diff."""
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    NO_CHANGE = "no_change"


@dataclass
class IPInterface:
    """IP interface configuration for a VLAN."""
    address: str
    mask: str


@dataclass
class VLANDesiredState:
    """Desired state for a single VLAN."""
    id: int
    action: VLANAction = VLANAction.ENSURE
    name: Optional[str] = None
    untagged_ports: list[str] = field(default_factory=list)
    tagged_ports: list[str] = field(default_factory=list)
    ip_interface: Optional[IPInterface] = None


@dataclass
class PortDesiredState:
    """Desired state for a single port."""
    name: str
    enabled: Optional[bool] = None
    description: Optional[str] = None
    speed: Optional[str] = None  # auto, 100M, 1G, 10G


@dataclass
class DesiredState:
    """Complete desired state for a device."""
    device_id: str
    version: int = 1
    checksum: Optional[str] = None
    mode: Literal["full", "patch"] = "patch"
    vlans: dict[int, VLANDesiredState] = field(default_factory=dict)
    ports: dict[str, PortDesiredState] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)


# --- Validation Results ---

@dataclass
class ValidationResult:
    """Result of config validation."""
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- Diff Results ---

@dataclass
class VLANChange:
    """A single VLAN change."""
    vlan_id: int
    change_type: ChangeType
    current_name: Optional[str] = None
    desired_name: Optional[str] = None
    ports_to_add_untagged: list[str] = field(default_factory=list)
    ports_to_remove_untagged: list[str] = field(default_factory=list)
    ports_to_add_tagged: list[str] = field(default_factory=list)
    ports_to_remove_tagged: list[str] = field(default_factory=list)


@dataclass
class PortChange:
    """A single port change."""
    port_name: str
    change_type: ChangeType
    enabled: Optional[bool] = None
    description: Optional[str] = None
    speed: Optional[str] = None


@dataclass
class DiffResult:
    """Result of diffing desired vs current state."""
    vlan_changes: list[VLANChange] = field(default_factory=list)
    port_changes: list[PortChange] = field(default_factory=list)

    @property
    def no_change(self) -> bool:
        """Check if there are any changes."""
        return (
            len(self.vlan_changes) == 0 and
            len(self.port_changes) == 0
        )

    @property
    def total_changes(self) -> int:
        """Total number of changes."""
        return len(self.vlan_changes) + len(self.port_changes)


# --- Command Plan ---

@dataclass
class CommandPlan:
    """Plan of commands to execute."""
    pre_commands: list[str] = field(default_factory=list)
    main_commands: list[str] = field(default_factory=list)
    post_commands: list[str] = field(default_factory=list)
    rollback_commands: list[str] = field(default_factory=list)

    @property
    def total_commands(self) -> int:
        """Total number of commands."""
        return (
            len(self.pre_commands) +
            len(self.main_commands) +
            len(self.post_commands)
        )


# --- Execution Results ---

@dataclass
class ExecuteOptions:
    """Options for config execution."""
    dry_run: bool = False
    stop_on_error: bool = True
    rollback_on_error: bool = False
    audit_context: str = ""
    user: Optional[str] = None


@dataclass
class ExecuteResult:
    """Result of config execution."""
    success: bool = False
    dry_run: bool = False
    changes_made: list[str] = field(default_factory=list)
    commands_executed: list[str] = field(default_factory=list)
    error: Optional[str] = None
    error_context: Optional[str] = None
    recovery_attempts: list[str] = field(default_factory=list)
    requires_ai_intervention: bool = False
    rollback_performed: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "dry_run": self.dry_run,
            "changes_made": self.changes_made,
            "commands_executed": self.commands_executed,
            "error": self.error,
            "error_context": self.error_context,
            "recovery_attempts": self.recovery_attempts,
            "requires_ai_intervention": self.requires_ai_intervention,
            "rollback_performed": self.rollback_performed,
        }


# --- Audit Entry ---

@dataclass
class AuditEntry:
    """Audit log entry for config changes."""
    timestamp: datetime
    device_id: str
    operation: str
    context: str = ""
    user: str = "system"
    success: bool = False
    changes: list[str] = field(default_factory=list)
    error: Optional[str] = None
    config_checksum: Optional[str] = None
