"""Configuration Store for managing desired state configurations.

Handles:
- Reading/writing YAML configuration files
- Directory structure initialization
- Config versioning and checksums
- Snapshot management
"""
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# Default config directory
DEFAULT_CONFIG_DIR = Path.home() / ".switchcraft"


@dataclass
class StoredConfig:
    """A stored configuration with metadata."""
    device_id: str
    config: dict[str, Any]
    version: int = 1
    checksum: str = ""
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    source: str = "manual"  # manual, auto_save, profile, sync

    def to_yaml(self) -> str:
        """Convert to YAML string with metadata header."""
        header = {
            "device_id": self.device_id,
            "version": self.version,
            "checksum": self.checksum,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
            "source": self.source,
        }

        # Merge header with config
        full_config = {**header, **self.config}

        return yaml.dump(full_config, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, yaml_str: str, device_id: str) -> "StoredConfig":
        """Parse from YAML string."""
        data = yaml.safe_load(yaml_str) or {}

        # Extract metadata
        version = data.pop("version", 1)
        checksum = data.pop("checksum", "")
        updated_at_str = data.pop("updated_at", None)
        updated_by = data.pop("updated_by", None)
        source = data.pop("source", "manual")
        data.pop("device_id", None)  # Remove if present

        updated_at = None
        if updated_at_str:
            try:
                updated_at = datetime.fromisoformat(updated_at_str)
            except (ValueError, TypeError):
                pass

        return cls(
            device_id=device_id,
            config=data,
            version=version,
            checksum=checksum,
            updated_at=updated_at,
            updated_by=updated_by,
            source=source,
        )


@dataclass
class DriftItem:
    """A single drift item between desired and actual state."""
    category: str  # 'vlan', 'port', 'setting'
    item_id: str   # VLAN ID, port name, setting name
    drift_type: str  # 'missing', 'extra', 'modified'
    expected: Any = None
    actual: Any = None
    details: str = ""


@dataclass
class DriftReport:
    """Drift report comparing desired vs actual state."""
    device_id: str
    checked_at: datetime
    in_sync: bool
    items: list[DriftItem] = field(default_factory=list)

    @property
    def drift_count(self) -> int:
        return len(self.items)

    def summary(self) -> str:
        """Human-readable summary."""
        if self.in_sync:
            return f"{self.device_id}: ✅ IN SYNC"

        lines = [f"{self.device_id}: ⚠️ DRIFT ({self.drift_count} issues)"]
        for item in self.items[:5]:  # Show first 5
            lines.append(f"  - {item.category} {item.item_id}: {item.drift_type}")
        if self.drift_count > 5:
            lines.append(f"  ... and {self.drift_count - 5} more")
        return "\n".join(lines)


class ConfigStore:
    """
    Manages configuration storage and retrieval.

    Directory structure:
        ~/.switchcraft/
        ├── configs/
        │   ├── desired/          # Current desired state per device
        │   ├── profiles/         # Named configuration profiles
        │   ├── network/          # Network-wide definitions
        │   └── snapshots/        # Point-in-time snapshots
        └── state/
            ├── last_known/       # Last fetched actual state
            └── drift_reports/    # Drift detection reports
    """

    def __init__(self, base_dir: Optional[Path] = None, git_enabled: bool = True):
        """
        Initialize the config store.

        Args:
            base_dir: Base directory for configs (default: ~/.switchcraft)
            git_enabled: Enable git versioning for configs (default: True)
        """
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_CONFIG_DIR
        self.git_enabled = git_enabled
        self._git_manager = None
        self._ensure_directories()

        # Initialize git repo early (before any files are written)
        # This ensures the initial commit is empty and subsequent saves
        # create proper commits
        if self.git_enabled:
            _ = self.git  # Trigger git init

    @property
    def git(self):
        """Get or create GitManager for the configs directory."""
        if self._git_manager is None and self.git_enabled:
            from .git_manager import GitManager
            self._git_manager = GitManager(self.configs_dir)
            # Initialize git repo if not already done
            self._git_manager.init()
        return self._git_manager

    @property
    def configs_dir(self) -> Path:
        """Root of the configs directory (git repo root)."""
        return self.base_dir / "configs"

    def _ensure_directories(self) -> None:
        """Create directory structure if it doesn't exist."""
        dirs = [
            self.base_dir / "configs" / "desired",
            self.base_dir / "configs" / "profiles",
            self.base_dir / "configs" / "network",
            self.base_dir / "configs" / "snapshots",
            self.base_dir / "state" / "last_known",
            self.base_dir / "state" / "drift_reports",
        ]

        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Config store initialized at {self.base_dir}")

    @property
    def desired_dir(self) -> Path:
        return self.base_dir / "configs" / "desired"

    @property
    def profiles_dir(self) -> Path:
        return self.base_dir / "configs" / "profiles"

    @property
    def network_dir(self) -> Path:
        return self.base_dir / "configs" / "network"

    @property
    def snapshots_dir(self) -> Path:
        return self.base_dir / "configs" / "snapshots"

    @property
    def last_known_dir(self) -> Path:
        return self.base_dir / "state" / "last_known"

    @property
    def drift_reports_dir(self) -> Path:
        return self.base_dir / "state" / "drift_reports"

    # === Desired State Management ===

    def get_desired_config(self, device_id: str) -> Optional[StoredConfig]:
        """
        Get the desired configuration for a device.

        Returns None if no desired config exists.
        """
        config_path = self.desired_dir / f"{device_id}.yaml"

        if not config_path.exists():
            return None

        try:
            content = config_path.read_text()
            return StoredConfig.from_yaml(content, device_id)
        except Exception as e:
            logger.error(f"Failed to read config for {device_id}: {e}")
            return None

    def save_desired_config(
        self,
        device_id: str,
        config: dict[str, Any],
        source: str = "manual",
        updated_by: Optional[str] = None,
        commit_message: Optional[str] = None,
    ) -> StoredConfig:
        """
        Save a desired configuration for a device.

        Args:
            device_id: Device identifier
            config: Configuration dict (vlans, ports, settings)
            source: Source of the change (manual, auto_save, profile, sync)
            updated_by: User/system that made the change
            commit_message: Custom commit message (auto-generated if None)

        Returns:
            StoredConfig with metadata
        """
        # Get existing version or start at 1
        existing = self.get_desired_config(device_id)
        version = (existing.version + 1) if existing else 1

        # Compute checksum
        config_str = json.dumps(config, sort_keys=True)
        checksum = f"sha256:{hashlib.sha256(config_str.encode()).hexdigest()[:16]}"

        stored = StoredConfig(
            device_id=device_id,
            config=config,
            version=version,
            checksum=checksum,
            updated_at=datetime.now(timezone.utc),
            updated_by=updated_by,
            source=source,
        )

        # Write to file
        config_path = self.desired_dir / f"{device_id}.yaml"
        config_path.write_text(stored.to_yaml())

        logger.info(f"Saved desired config for {device_id} (v{version})")

        # Auto-commit if git is enabled
        if self.git_enabled and self.git:
            if commit_message is None:
                commit_message = f"[{device_id}] Config updated (v{version})"
            self.git.commit(
                message=commit_message,
                files=[f"desired/{device_id}.yaml"],
                author=updated_by,
            )

        return stored

    def list_desired_configs(self) -> list[str]:
        """List all device IDs with desired configs."""
        return [
            p.stem for p in self.desired_dir.glob("*.yaml")
        ]

    def delete_desired_config(self, device_id: str) -> bool:
        """Delete a desired configuration."""
        config_path = self.desired_dir / f"{device_id}.yaml"
        if config_path.exists():
            config_path.unlink()
            logger.info(f"Deleted desired config for {device_id}")
            return True
        return False

    # === Last Known State Management ===

    def save_last_known(
        self,
        device_id: str,
        vlans: list[dict],
        ports: list[dict],
    ) -> None:
        """
        Save the last known actual state from a device.

        This is cached state from the last fetch, used for quick drift checks.
        """
        state = {
            "device_id": device_id,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "vlans": {v["id"]: v for v in vlans},
            "ports": {p["name"]: p for p in ports},
        }

        state_path = self.last_known_dir / f"{device_id}.yaml"
        state_path.write_text(yaml.dump(state, default_flow_style=False))

        logger.debug(f"Saved last known state for {device_id}")

    def get_last_known(self, device_id: str) -> Optional[dict]:
        """Get the last known state for a device."""
        state_path = self.last_known_dir / f"{device_id}.yaml"

        if not state_path.exists():
            return None

        try:
            return yaml.safe_load(state_path.read_text())
        except Exception as e:
            logger.error(f"Failed to read last known state for {device_id}: {e}")
            return None

    # === Snapshot Management ===

    def create_snapshot(
        self,
        name: Optional[str] = None,
        device_ids: Optional[list[str]] = None,
    ) -> str:
        """
        Create a snapshot of current desired configs.

        Args:
            name: Optional snapshot name (default: timestamp)
            device_ids: Specific devices to snapshot (default: all)

        Returns:
            Snapshot name/path
        """
        if name is None:
            name = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        snapshot_path = self.snapshots_dir / name
        snapshot_path.mkdir(exist_ok=True)

        # Copy desired configs
        if device_ids is None:
            device_ids = self.list_desired_configs()

        for device_id in device_ids:
            src = self.desired_dir / f"{device_id}.yaml"
            if src.exists():
                dst = snapshot_path / f"{device_id}.yaml"
                shutil.copy2(src, dst)

        logger.info(f"Created snapshot '{name}' with {len(device_ids)} configs")
        return name

    def list_snapshots(self) -> list[str]:
        """List all snapshots."""
        return sorted([
            p.name for p in self.snapshots_dir.iterdir()
            if p.is_dir()
        ], reverse=True)

    def restore_snapshot(
        self,
        name: str,
        device_ids: Optional[list[str]] = None,
    ) -> list[str]:
        """
        Restore configs from a snapshot.

        Args:
            name: Snapshot name
            device_ids: Specific devices to restore (default: all in snapshot)

        Returns:
            List of restored device IDs
        """
        snapshot_path = self.snapshots_dir / name

        if not snapshot_path.exists():
            raise ValueError(f"Snapshot '{name}' not found")

        # Find configs in snapshot
        if device_ids is None:
            device_ids = [p.stem for p in snapshot_path.glob("*.yaml")]

        restored = []
        for device_id in device_ids:
            src = snapshot_path / f"{device_id}.yaml"
            if src.exists():
                dst = self.desired_dir / f"{device_id}.yaml"
                shutil.copy2(src, dst)
                restored.append(device_id)

        logger.info(f"Restored {len(restored)} configs from snapshot '{name}'")
        return restored

    # === Drift Detection ===

    def calculate_drift(
        self,
        device_id: str,
        actual_vlans: list[dict],
        actual_ports: list[dict],
    ) -> DriftReport:
        """
        Calculate drift between desired and actual state.

        Args:
            device_id: Device identifier
            actual_vlans: Current VLANs from device
            actual_ports: Current ports from device

        Returns:
            DriftReport with all differences
        """
        desired = self.get_desired_config(device_id)
        items = []

        if desired is None:
            # No desired config = no drift (unmanaged)
            return DriftReport(
                device_id=device_id,
                checked_at=datetime.now(timezone.utc),
                in_sync=True,
                items=[],
            )

        # Build lookup maps
        actual_vlan_map = {v["id"]: v for v in actual_vlans}
        actual_port_map = {p["name"]: p for p in actual_ports}

        desired_vlans = desired.config.get("vlans", {})
        desired_ports = desired.config.get("ports", {})

        # Check VLANs
        for vlan_id, desired_vlan in desired_vlans.items():
            vlan_id = int(vlan_id)
            actual_vlan = actual_vlan_map.get(vlan_id)

            if actual_vlan is None:
                items.append(DriftItem(
                    category="vlan",
                    item_id=str(vlan_id),
                    drift_type="missing",
                    expected=desired_vlan,
                    actual=None,
                    details=f"VLAN {vlan_id} expected but not found",
                ))
            else:
                # Check port membership
                drift = self._check_vlan_drift(vlan_id, desired_vlan, actual_vlan)
                items.extend(drift)

        # Check for unexpected VLANs (optional - skip VLAN 1)
        for vlan_id, actual_vlan in actual_vlan_map.items():
            if vlan_id not in [int(v) for v in desired_vlans.keys()]:
                if vlan_id != 1:  # Don't flag default VLAN
                    items.append(DriftItem(
                        category="vlan",
                        item_id=str(vlan_id),
                        drift_type="extra",
                        expected=None,
                        actual=actual_vlan,
                        details=f"VLAN {vlan_id} exists but not in desired config",
                    ))

        # Check ports
        for port_name, desired_port in desired_ports.items():
            actual_port = actual_port_map.get(port_name)

            if actual_port is None:
                items.append(DriftItem(
                    category="port",
                    item_id=port_name,
                    drift_type="missing",
                    expected=desired_port,
                    actual=None,
                    details=f"Port {port_name} not found",
                ))
            else:
                drift = self._check_port_drift(port_name, desired_port, actual_port)
                items.extend(drift)

        # Save last known state
        self.save_last_known(device_id, actual_vlans, actual_ports)

        report = DriftReport(
            device_id=device_id,
            checked_at=datetime.now(timezone.utc),
            in_sync=len(items) == 0,
            items=items,
        )

        # Save drift report
        self._save_drift_report(report)

        return report

    def _check_vlan_drift(
        self,
        vlan_id: int,
        desired: dict,
        actual: dict,
    ) -> list[DriftItem]:
        """Check for drift in a single VLAN's configuration."""
        items = []

        # Check untagged ports
        desired_untagged = set(self._expand_ports(desired.get("untagged_ports", [])))
        actual_untagged = set(actual.get("untagged_ports", []))

        missing_untagged = desired_untagged - actual_untagged
        extra_untagged = actual_untagged - desired_untagged

        if missing_untagged:
            items.append(DriftItem(
                category="vlan",
                item_id=str(vlan_id),
                drift_type="modified",
                expected=list(desired_untagged),
                actual=list(actual_untagged),
                details=f"Missing untagged ports: {', '.join(sorted(missing_untagged))}",
            ))

        if extra_untagged and desired_untagged:  # Only flag if we're managing this VLAN's ports
            items.append(DriftItem(
                category="vlan",
                item_id=str(vlan_id),
                drift_type="modified",
                expected=list(desired_untagged),
                actual=list(actual_untagged),
                details=f"Extra untagged ports: {', '.join(sorted(extra_untagged))}",
            ))

        # Check tagged ports
        desired_tagged = set(self._expand_ports(desired.get("tagged_ports", [])))
        actual_tagged = set(actual.get("tagged_ports", []))

        missing_tagged = desired_tagged - actual_tagged
        if missing_tagged:
            items.append(DriftItem(
                category="vlan",
                item_id=str(vlan_id),
                drift_type="modified",
                expected=list(desired_tagged),
                actual=list(actual_tagged),
                details=f"Missing tagged ports: {', '.join(sorted(missing_tagged))}",
            ))

        return items

    def _check_port_drift(
        self,
        port_name: str,
        desired: dict,
        actual: dict,
    ) -> list[DriftItem]:
        """Check for drift in a single port's configuration."""
        items = []

        # Check enabled state
        if "enabled" in desired:
            actual_enabled = actual.get("enabled", True)
            if desired["enabled"] != actual_enabled:
                items.append(DriftItem(
                    category="port",
                    item_id=port_name,
                    drift_type="modified",
                    expected={"enabled": desired["enabled"]},
                    actual={"enabled": actual_enabled},
                    details=f"Port enabled: expected {desired['enabled']}, actual {actual_enabled}",
                ))

        return items

    def _expand_ports(self, ports: list) -> list[str]:
        """Expand port ranges like 1/1/1-4 to individual ports."""
        if isinstance(ports, str):
            ports = [ports]

        expanded = []
        for port in ports:
            if "-" in str(port) and "/" in str(port):
                # Try to expand range
                try:
                    parts = str(port).rsplit("-", 1)
                    base = parts[0]
                    end = int(parts[1])
                    base_parts = base.rsplit("/", 1)
                    prefix = base_parts[0]
                    start = int(base_parts[1])
                    for i in range(start, end + 1):
                        expanded.append(f"{prefix}/{i}")
                except (ValueError, IndexError):
                    expanded.append(str(port))
            else:
                expanded.append(str(port))

        return expanded

    def _save_drift_report(self, report: DriftReport) -> None:
        """Save a drift report to file."""
        filename = f"{report.checked_at.strftime('%Y-%m-%dT%H:%M:%S')}_{report.device_id}.json"
        report_path = self.drift_reports_dir / filename

        data = {
            "device_id": report.device_id,
            "checked_at": report.checked_at.isoformat(),
            "in_sync": report.in_sync,
            "drift_count": report.drift_count,
            "items": [
                {
                    "category": item.category,
                    "item_id": item.item_id,
                    "drift_type": item.drift_type,
                    "expected": item.expected,
                    "actual": item.actual,
                    "details": item.details,
                }
                for item in report.items
            ],
        }

        report_path.write_text(json.dumps(data, indent=2))

    # === Profiles ===

    def list_profiles(self) -> list[str]:
        """List available profiles."""
        return [p.stem for p in self.profiles_dir.glob("*.yaml")]

    def get_profile(self, name: str) -> Optional[dict]:
        """Get a profile by name."""
        profile_path = self.profiles_dir / f"{name}.yaml"
        if not profile_path.exists():
            return None

        return yaml.safe_load(profile_path.read_text())

    def save_profile(self, name: str, config: dict) -> None:
        """Save a profile."""
        profile_path = self.profiles_dir / f"{name}.yaml"
        profile_path.write_text(yaml.dump(config, default_flow_style=False))
        logger.info(f"Saved profile '{name}'")

    # === Network-Wide Config ===

    def get_network_vlans(self) -> Optional[dict]:
        """Get network-wide VLAN definitions."""
        vlans_path = self.network_dir / "vlans.yaml"
        if not vlans_path.exists():
            return None

        return yaml.safe_load(vlans_path.read_text())

    def save_network_vlans(self, config: dict) -> None:
        """Save network-wide VLAN definitions."""
        vlans_path = self.network_dir / "vlans.yaml"
        vlans_path.write_text(yaml.dump(config, default_flow_style=False))
        logger.info("Saved network-wide VLAN config")

    # === Git History & Versioning ===

    def get_config_history(
        self,
        device_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Get version history for configs.

        Args:
            device_id: Filter by device (optional)
            limit: Maximum commits to return

        Returns:
            List of commit info dicts
        """
        if not self.git_enabled or not self.git:
            return []

        file_path = f"desired/{device_id}.yaml" if device_id else None
        commits = self.git.get_history(file_path=file_path, limit=limit)

        return [
            {
                "hash": c.hash,
                "short_hash": c.short_hash,
                "author": c.author,
                "date": c.date.isoformat(),
                "message": c.message,
            }
            for c in commits
        ]

    def get_config_at_revision(
        self,
        device_id: str,
        revision: str = "HEAD",
    ) -> Optional[StoredConfig]:
        """
        Get a device config at a specific git revision.

        Args:
            device_id: Device identifier
            revision: Git revision (commit hash, HEAD~1, etc.)

        Returns:
            StoredConfig or None if not found
        """
        if not self.git_enabled or not self.git:
            return None

        file_path = f"desired/{device_id}.yaml"
        content = self.git.get_file_at_revision(file_path, revision)

        if content is None:
            return None

        return StoredConfig.from_yaml(content, device_id)

    def restore_config_from_revision(
        self,
        device_id: str,
        revision: str,
        commit: bool = True,
    ) -> Optional[StoredConfig]:
        """
        Restore a device config from a git revision.

        Args:
            device_id: Device identifier
            revision: Git revision to restore from
            commit: Auto-commit the restore (default: True)

        Returns:
            The restored StoredConfig or None if failed
        """
        if not self.git_enabled or not self.git:
            return None

        # Get the config at the revision
        old_config = self.get_config_at_revision(device_id, revision)
        if old_config is None:
            return None

        # Save as current desired config
        commit_msg = f"[{device_id}] Restored from {revision}" if commit else None
        return self.save_desired_config(
            device_id=device_id,
            config=old_config.config,
            source="restore",
            updated_by="git-restore",
            commit_message=commit_msg if commit else None,
        )

    def diff_config_revisions(
        self,
        device_id: str,
        revision1: str = "HEAD~1",
        revision2: str = "HEAD",
    ) -> str:
        """
        Get diff between two revisions of a config.

        Args:
            device_id: Device identifier
            revision1: First revision (older)
            revision2: Second revision (newer)

        Returns:
            Diff output as string
        """
        if not self.git_enabled or not self.git:
            return ""

        file_path = f"desired/{device_id}.yaml"
        return self.git.diff(file_path, revision1, revision2)
