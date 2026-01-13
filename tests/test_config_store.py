"""Tests for the Configuration Store."""
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from mcp_network_switch.config_store import (
    ConfigStore,
    StoredConfig,
    DriftReport,
    DriftItem,
)


class TestStoredConfig:
    """Tests for StoredConfig dataclass."""

    def test_to_yaml(self):
        """Test serialization to YAML."""
        config = StoredConfig(
            device_id="test-device",
            config={"vlans": {100: {"name": "Test"}}},
            version=1,
            checksum="sha256:abc123",
            updated_at=datetime(2026, 1, 13, 10, 0, 0),
            updated_by="testuser",
            source="manual",
        )

        yaml_str = config.to_yaml()

        assert "device_id: test-device" in yaml_str
        assert "version: 1" in yaml_str
        assert "sha256:abc123" in yaml_str
        assert "vlans:" in yaml_str

    def test_from_yaml(self):
        """Test parsing from YAML."""
        yaml_str = """
device_id: test-device
version: 3
checksum: sha256:def456
updated_at: '2026-01-13T10:00:00'
updated_by: admin
source: sync
vlans:
  100:
    name: Production
"""

        config = StoredConfig.from_yaml(yaml_str, "test-device")

        assert config.device_id == "test-device"
        assert config.version == 3
        assert config.checksum == "sha256:def456"
        assert config.source == "sync"
        assert 100 in config.config["vlans"]

    def test_from_yaml_minimal(self):
        """Test parsing minimal YAML (no metadata)."""
        yaml_str = """
vlans:
  200:
    name: Guest
"""

        config = StoredConfig.from_yaml(yaml_str, "minimal-device")

        assert config.device_id == "minimal-device"
        assert config.version == 1  # Default
        assert config.source == "manual"  # Default
        assert 200 in config.config["vlans"]


class TestDriftReport:
    """Tests for DriftReport."""

    def test_in_sync_report(self):
        """Test drift report when in sync."""
        report = DriftReport(
            device_id="test",
            checked_at=datetime.now(timezone.utc),
            in_sync=True,
            items=[],
        )

        assert report.in_sync
        assert report.drift_count == 0
        assert "IN SYNC" in report.summary()

    def test_drift_report(self):
        """Test drift report with issues."""
        report = DriftReport(
            device_id="test",
            checked_at=datetime.now(timezone.utc),
            in_sync=False,
            items=[
                DriftItem(
                    category="vlan",
                    item_id="100",
                    drift_type="missing",
                    details="VLAN 100 expected but not found",
                ),
                DriftItem(
                    category="port",
                    item_id="1/1/1",
                    drift_type="modified",
                    details="Port enabled: expected True, actual False",
                ),
            ],
        )

        assert not report.in_sync
        assert report.drift_count == 2
        assert "DRIFT" in report.summary()
        assert "2 issues" in report.summary()


class TestConfigStore:
    """Tests for ConfigStore."""

    @pytest.fixture
    def temp_store(self, tmp_path):
        """Create a ConfigStore with a temporary directory."""
        return ConfigStore(base_dir=tmp_path)

    def test_directory_creation(self, tmp_path):
        """Test that directories are created on init."""
        store = ConfigStore(base_dir=tmp_path)

        assert (tmp_path / "configs" / "desired").exists()
        assert (tmp_path / "configs" / "profiles").exists()
        assert (tmp_path / "configs" / "network").exists()
        assert (tmp_path / "configs" / "snapshots").exists()
        assert (tmp_path / "state" / "last_known").exists()
        assert (tmp_path / "state" / "drift_reports").exists()

    def test_save_and_get_desired_config(self, temp_store):
        """Test saving and retrieving a desired config."""
        config = {
            "vlans": {100: {"name": "Test", "untagged_ports": ["1/1/1"]}},
            "ports": {"1/1/1": {"enabled": True}},
        }

        stored = temp_store.save_desired_config(
            device_id="test-device",
            config=config,
            source="manual",
            updated_by="testuser",
        )

        assert stored.version == 1
        assert stored.checksum.startswith("sha256:")
        assert stored.source == "manual"

        # Retrieve it back
        retrieved = temp_store.get_desired_config("test-device")

        assert retrieved is not None
        assert retrieved.device_id == "test-device"
        assert 100 in retrieved.config["vlans"]

    def test_save_increments_version(self, temp_store):
        """Test that saving multiple times increments version."""
        config = {"vlans": {100: {"name": "V1"}}}

        stored1 = temp_store.save_desired_config("test", config)
        assert stored1.version == 1

        config["vlans"][100]["name"] = "V2"
        stored2 = temp_store.save_desired_config("test", config)
        assert stored2.version == 2

        config["vlans"][100]["name"] = "V3"
        stored3 = temp_store.save_desired_config("test", config)
        assert stored3.version == 3

    def test_get_nonexistent_config(self, temp_store):
        """Test getting a config that doesn't exist."""
        result = temp_store.get_desired_config("nonexistent")
        assert result is None

    def test_list_desired_configs(self, temp_store):
        """Test listing all desired configs."""
        temp_store.save_desired_config("device-a", {"vlans": {}})
        temp_store.save_desired_config("device-b", {"vlans": {}})
        temp_store.save_desired_config("device-c", {"vlans": {}})

        configs = temp_store.list_desired_configs()

        assert len(configs) == 3
        assert "device-a" in configs
        assert "device-b" in configs
        assert "device-c" in configs

    def test_delete_desired_config(self, temp_store):
        """Test deleting a desired config."""
        temp_store.save_desired_config("to-delete", {"vlans": {}})

        assert temp_store.get_desired_config("to-delete") is not None

        result = temp_store.delete_desired_config("to-delete")

        assert result is True
        assert temp_store.get_desired_config("to-delete") is None

    def test_delete_nonexistent(self, temp_store):
        """Test deleting a config that doesn't exist."""
        result = temp_store.delete_desired_config("nonexistent")
        assert result is False

    def test_save_and_get_last_known(self, temp_store):
        """Test saving and retrieving last known state."""
        vlans = [
            {"id": 1, "name": "default", "untagged_ports": [], "tagged_ports": []},
            {"id": 100, "name": "Test", "untagged_ports": ["1/1/1"], "tagged_ports": []},
        ]
        ports = [
            {"name": "1/1/1", "enabled": True, "speed": "1G"},
        ]

        temp_store.save_last_known("test-device", vlans, ports)

        retrieved = temp_store.get_last_known("test-device")

        assert retrieved is not None
        assert retrieved["device_id"] == "test-device"
        assert 1 in retrieved["vlans"]
        assert 100 in retrieved["vlans"]
        assert "1/1/1" in retrieved["ports"]

    def test_calculate_drift_no_desired(self, temp_store):
        """Test drift calculation when no desired config exists."""
        # No desired config saved
        drift = temp_store.calculate_drift(
            "unmanaged-device",
            actual_vlans=[{"id": 100, "name": "Test"}],
            actual_ports=[],
        )

        # No desired = no drift (unmanaged)
        assert drift.in_sync is True
        assert drift.drift_count == 0

    def test_calculate_drift_missing_vlan(self, temp_store):
        """Test drift detection for missing VLAN."""
        # Save desired config with VLAN 100
        temp_store.save_desired_config(
            "test-device",
            {
                "vlans": {
                    100: {"name": "Expected", "untagged_ports": ["1/1/1"], "tagged_ports": []},
                }
            },
        )

        # Actual state has no VLANs
        drift = temp_store.calculate_drift(
            "test-device",
            actual_vlans=[],
            actual_ports=[],
        )

        assert not drift.in_sync
        assert drift.drift_count == 1
        assert drift.items[0].category == "vlan"
        assert drift.items[0].drift_type == "missing"

    def test_calculate_drift_extra_vlan(self, temp_store):
        """Test drift detection for extra VLAN."""
        # Save minimal desired config
        temp_store.save_desired_config(
            "test-device",
            {"vlans": {}},
        )

        # Actual has extra VLAN (not VLAN 1)
        drift = temp_store.calculate_drift(
            "test-device",
            actual_vlans=[{"id": 200, "name": "Extra", "untagged_ports": [], "tagged_ports": []}],
            actual_ports=[],
        )

        assert not drift.in_sync
        assert any(item.drift_type == "extra" for item in drift.items)

    def test_calculate_drift_in_sync(self, temp_store):
        """Test drift when device matches desired state."""
        # Save desired config
        temp_store.save_desired_config(
            "test-device",
            {
                "vlans": {
                    100: {"name": "Test", "untagged_ports": ["1/1/1"], "tagged_ports": []},
                }
            },
        )

        # Actual matches desired
        drift = temp_store.calculate_drift(
            "test-device",
            actual_vlans=[
                {"id": 100, "name": "Test", "untagged_ports": ["1/1/1"], "tagged_ports": []},
            ],
            actual_ports=[],
        )

        assert drift.in_sync
        assert drift.drift_count == 0

    def test_snapshot_create_and_list(self, temp_store):
        """Test creating and listing snapshots."""
        # Create some configs
        temp_store.save_desired_config("device-a", {"vlans": {100: {"name": "A"}}})
        temp_store.save_desired_config("device-b", {"vlans": {200: {"name": "B"}}})

        # Create snapshot
        snapshot_name = temp_store.create_snapshot(name="test-snapshot")

        assert snapshot_name == "test-snapshot"
        assert "test-snapshot" in temp_store.list_snapshots()

    def test_snapshot_restore(self, temp_store):
        """Test restoring from a snapshot."""
        # Create and save initial config
        temp_store.save_desired_config("device-a", {"vlans": {100: {"name": "Original"}}})

        # Create snapshot
        temp_store.create_snapshot(name="backup")

        # Modify config
        temp_store.save_desired_config("device-a", {"vlans": {100: {"name": "Modified"}}})

        # Verify modification
        modified = temp_store.get_desired_config("device-a")
        assert modified.config["vlans"][100]["name"] == "Modified"

        # Restore from snapshot
        restored = temp_store.restore_snapshot("backup")

        assert "device-a" in restored

        # Verify restoration
        restored_config = temp_store.get_desired_config("device-a")
        assert restored_config.config["vlans"][100]["name"] == "Original"

    def test_snapshot_restore_nonexistent(self, temp_store):
        """Test restoring from nonexistent snapshot."""
        with pytest.raises(ValueError) as exc:
            temp_store.restore_snapshot("nonexistent")

        assert "not found" in str(exc.value)

    def test_profiles(self, temp_store):
        """Test profile management."""
        profile = {
            "name": "Maintenance",
            "description": "Disable non-essential ports",
            "actions": [
                {"device": "brocade-core", "ports": {"1/1/11-24": {"enabled": False}}},
            ],
        }

        temp_store.save_profile("maintenance", profile)

        profiles = temp_store.list_profiles()
        assert "maintenance" in profiles

        retrieved = temp_store.get_profile("maintenance")
        assert retrieved["name"] == "Maintenance"

    def test_network_vlans(self, temp_store):
        """Test network-wide VLAN config."""
        network_vlans = {
            "vlans": {
                254: {"name": "Management", "apply_to": "all"},
                100: {"name": "Production", "apply_to": ["brocade-core"]},
            }
        }

        temp_store.save_network_vlans(network_vlans)

        retrieved = temp_store.get_network_vlans()

        assert retrieved is not None
        assert 254 in retrieved["vlans"]
        assert 100 in retrieved["vlans"]

    def test_expand_ports(self, temp_store):
        """Test port range expansion."""
        # Test via drift detection which uses _expand_ports internally
        temp_store.save_desired_config(
            "test-device",
            {
                "vlans": {
                    100: {
                        "name": "Test",
                        "untagged_ports": ["1/1/1-4"],  # Range
                        "tagged_ports": [],
                    },
                }
            },
        )

        # Actual has all 4 ports
        drift = temp_store.calculate_drift(
            "test-device",
            actual_vlans=[
                {
                    "id": 100,
                    "name": "Test",
                    "untagged_ports": ["1/1/1", "1/1/2", "1/1/3", "1/1/4"],
                    "tagged_ports": [],
                },
            ],
            actual_ports=[],
        )

        # Should be in sync - range expanded to match
        assert drift.in_sync
