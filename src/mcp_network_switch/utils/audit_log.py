"""Audit logging for configuration changes.

Provides enterprise-grade change tracking with:
- Timestamped entries for all config modifications
- Before/after state capture (for rollback)
- Structured JSON log format
- Separate audit log file
"""
import json
import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

# Create dedicated audit logger
audit_logger = logging.getLogger("switchcraft.audit")


def setup_audit_logging(log_dir: Optional[str] = None) -> None:
    """Configure audit logging to file.

    Args:
        log_dir: Directory for audit logs. Defaults to ~/.switchcraft/
    """
    if log_dir is None:
        log_dir = os.path.expanduser("~/.switchcraft")

    Path(log_dir).mkdir(parents=True, exist_ok=True)

    audit_file = os.path.join(log_dir, "audit.log")

    # Configure audit logger
    audit_logger.setLevel(logging.INFO)

    # Remove existing handlers
    audit_logger.handlers.clear()

    # File handler with rotation
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(
        audit_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10,
    )

    # Use JSON format for machine-readability
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(handler)

    # Don't propagate to root logger
    audit_logger.propagate = False


@dataclass
class ChangeRecord:
    """Record of a configuration change."""
    timestamp: str
    device_id: str
    operation: str  # create_vlan, delete_vlan, configure_port, etc.
    user: str  # For future auth integration
    dry_run: bool
    success: bool
    parameters: dict
    before_state: Optional[dict] = None
    after_state: Optional[dict] = None
    output: str = ""
    error: Optional[str] = None

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(asdict(self), indent=None)

    @classmethod
    def from_json(cls, json_str: str) -> "ChangeRecord":
        """Parse from JSON string."""
        data = json.loads(json_str)
        return cls(**data)


class ChangeTracker:
    """Track and log configuration changes."""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self._snapshots: dict[str, Any] = {}

    def snapshot(self, name: str, state: Any) -> None:
        """Capture a state snapshot for potential rollback.

        Args:
            name: Identifier for this snapshot (e.g., "vlans_before")
            state: The state to capture (will be JSON serialized)
        """
        self._snapshots[name] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state,
        }

    def get_snapshot(self, name: str) -> Optional[Any]:
        """Retrieve a previously captured snapshot."""
        snap = self._snapshots.get(name)
        return snap["state"] if snap else None

    def clear_snapshots(self) -> None:
        """Clear all snapshots (after successful commit)."""
        self._snapshots.clear()

    def log_change(
        self,
        operation: str,
        parameters: dict,
        success: bool,
        output: str = "",
        error: Optional[str] = None,
        dry_run: bool = False,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
    ) -> ChangeRecord:
        """Log a configuration change.

        Args:
            operation: The operation performed (e.g., "create_vlan")
            parameters: Parameters passed to the operation
            success: Whether the operation succeeded
            output: Command output or result message
            error: Error message if failed
            dry_run: Whether this was a dry-run (no actual changes)
            before_state: State before the change
            after_state: State after the change

        Returns:
            The ChangeRecord that was logged
        """
        record = ChangeRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            device_id=self.device_id,
            operation=operation,
            user="system",  # TODO: Integrate with auth
            dry_run=dry_run,
            success=success,
            parameters=parameters,
            before_state=before_state,
            after_state=after_state,
            output=output[:1000] if output else "",  # Truncate long output
            error=error,
        )

        # Write to audit log
        audit_logger.info(record.to_json())

        return record


def get_recent_changes(
    log_file: Optional[str] = None,
    device_id: Optional[str] = None,
    operation: Optional[str] = None,
    limit: int = 100,
) -> list[ChangeRecord]:
    """Read recent changes from audit log.

    Args:
        log_file: Path to audit log. Defaults to ~/.switchcraft/audit.log
        device_id: Filter by device ID
        operation: Filter by operation type
        limit: Maximum number of records to return

    Returns:
        List of ChangeRecords, most recent first
    """
    if log_file is None:
        log_file = os.path.expanduser("~/.switchcraft/audit.log")

    if not os.path.exists(log_file):
        return []

    records = []
    with open(log_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = ChangeRecord.from_json(line)

                # Apply filters
                if device_id and record.device_id != device_id:
                    continue
                if operation and record.operation != operation:
                    continue

                records.append(record)
            except (json.JSONDecodeError, TypeError):
                continue  # Skip malformed lines

    # Return most recent first, limited
    return list(reversed(records[-limit:]))


# Initialize audit logging on module load
setup_audit_logging()
