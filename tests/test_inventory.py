"""Tests for device inventory management."""
import pytest
import tempfile
import os
from mcp_network_switch.config.inventory import DeviceInventory


class TestDeviceInventory:
    """Tests for DeviceInventory class."""

    @pytest.fixture
    def temp_config(self):
        """Create a temporary config file for testing."""
        config_content = """
defaults:
  password_env: "TEST_PASSWORD"
  timeout: 30
  retries: 3

devices:
  test-switch:
    type: brocade
    name: "Test Switch"
    host: 192.168.1.1
    protocol: telnet
    port: 23
    username: admin

  test-onti:
    type: onti
    name: "Test ONTI"
    host: 192.168.1.2
    protocol: ssh
    port: 22
    username: root
    use_scp_workflow: true
    config_paths:
      network: /etc/config/network
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            f.flush()
            yield f.name
        os.unlink(f.name)

    def test_load_config(self, temp_config):
        """Inventory loads config file correctly."""
        inv = DeviceInventory(temp_config)
        device_ids = inv.get_device_ids()
        assert "test-switch" in device_ids
        assert "test-onti" in device_ids

    def test_get_device_config(self, temp_config):
        """Can get raw device config."""
        inv = DeviceInventory(temp_config)
        config = inv.get_device_config("test-switch")
        assert config["type"] == "brocade"
        assert config["host"] == "192.168.1.1"
        assert config["username"] == "admin"
        # Defaults should be merged
        assert config["timeout"] == 30
        assert config["retries"] == 3

    def test_get_device_unknown(self, temp_config):
        """Unknown device raises KeyError."""
        inv = DeviceInventory(temp_config)
        with pytest.raises(KeyError) as exc_info:
            inv.get_device_config("nonexistent")
        assert "Unknown device" in str(exc_info.value)

    def test_get_device(self, temp_config, monkeypatch):
        """Can create device instances."""
        monkeypatch.setenv("TEST_PASSWORD", "secret")
        inv = DeviceInventory(temp_config)
        device = inv.get_device("test-switch")
        assert device.device_id == "test-switch"
        assert device.config.type == "brocade"

    def test_get_device_cached(self, temp_config, monkeypatch):
        """Device instances are cached."""
        monkeypatch.setenv("TEST_PASSWORD", "secret")
        inv = DeviceInventory(temp_config)
        device1 = inv.get_device("test-switch")
        device2 = inv.get_device("test-switch")
        assert device1 is device2

    def test_get_devices_by_type(self, temp_config, monkeypatch):
        """Can filter devices by type."""
        monkeypatch.setenv("TEST_PASSWORD", "secret")
        inv = DeviceInventory(temp_config)
        brocade_devices = inv.get_devices_by_type("brocade")
        assert len(brocade_devices) == 1
        assert brocade_devices[0].config.type == "brocade"

    def test_defaults_applied(self, temp_config):
        """Default values are applied to all devices."""
        inv = DeviceInventory(temp_config)
        switch_config = inv.get_device_config("test-switch")
        onti_config = inv.get_device_config("test-onti")
        # Both should have the default values
        assert switch_config["password_env"] == "TEST_PASSWORD"
        assert onti_config["password_env"] == "TEST_PASSWORD"
        assert switch_config["timeout"] == 30
        assert onti_config["timeout"] == 30

    def test_device_specific_overrides_defaults(self, temp_config):
        """Device-specific values override defaults."""
        inv = DeviceInventory(temp_config)
        onti_config = inv.get_device_config("test-onti")
        # use_scp_workflow is device-specific, should be preserved
        assert onti_config["use_scp_workflow"] is True


class TestDeviceGroups:
    """Tests for device group functionality."""

    @pytest.fixture
    def temp_config_with_groups(self):
        """Create a config file with groups."""
        config_content = """
defaults:
  password_env: "TEST_PASSWORD"
  timeout: 30

devices:
  switch-1:
    type: brocade
    host: 192.168.1.1
    protocol: telnet
    port: 23
    username: admin

  switch-2:
    type: brocade
    host: 192.168.1.2
    protocol: telnet
    port: 23
    username: admin

  ap-1:
    type: openwrt
    host: 192.168.1.10
    protocol: ssh
    port: 22
    username: root

  ap-2:
    type: openwrt
    host: 192.168.1.11
    protocol: ssh
    port: 22
    username: root

groups:
  switches:
    - switch-1
    - switch-2
  access-points:
    - ap-1
    - ap-2
  all-network:
    - switch-1
    - switch-2
    - ap-1
    - ap-2
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            f.flush()
            yield f.name
        os.unlink(f.name)

    def test_get_groups(self, temp_config_with_groups):
        """Can list all groups."""
        inv = DeviceInventory(temp_config_with_groups)
        groups = inv.get_groups()
        assert "switches" in groups
        assert "access-points" in groups
        assert "all-network" in groups

    def test_get_group_names(self, temp_config_with_groups):
        """Can get list of group names."""
        inv = DeviceInventory(temp_config_with_groups)
        names = inv.get_group_names()
        assert len(names) == 3
        assert "switches" in names

    def test_get_group_members(self, temp_config_with_groups):
        """Can get members of a group."""
        inv = DeviceInventory(temp_config_with_groups)
        members = inv.get_group_members("switches")
        assert len(members) == 2
        assert "switch-1" in members
        assert "switch-2" in members

    def test_get_group_members_unknown(self, temp_config_with_groups):
        """Unknown group raises KeyError."""
        inv = DeviceInventory(temp_config_with_groups)
        with pytest.raises(KeyError):
            inv.get_group_members("nonexistent")

    def test_get_group_info(self, temp_config_with_groups):
        """Can get detailed group info."""
        inv = DeviceInventory(temp_config_with_groups)
        info = inv.get_group_info("switches")
        assert info["name"] == "switches"
        assert info["member_count"] == 2
        assert "brocade" in info["device_types"]

    def test_is_device_in_group(self, temp_config_with_groups):
        """Can check group membership."""
        inv = DeviceInventory(temp_config_with_groups)
        assert inv.is_device_in_group("switch-1", "switches")
        assert not inv.is_device_in_group("ap-1", "switches")
        assert inv.is_device_in_group("ap-1", "access-points")

    def test_get_device_groups(self, temp_config_with_groups):
        """Can get all groups a device belongs to."""
        inv = DeviceInventory(temp_config_with_groups)
        groups = inv.get_device_groups("switch-1")
        assert "switches" in groups
        assert "all-network" in groups
        assert "access-points" not in groups

    def test_no_groups_defined(self):
        """Gracefully handles configs with no groups."""
        config_content = """
devices:
  test-switch:
    type: brocade
    host: 192.168.1.1
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            f.flush()
            inv = DeviceInventory(f.name)
        os.unlink(f.name)
        assert inv.get_groups() == {}
        assert inv.get_group_names() == []


class TestDeviceInventoryNoConfig:
    """Tests for DeviceInventory when no config file exists."""

    def test_find_config_not_found(self):
        """FileNotFoundError raised when no config file found."""
        with pytest.raises(FileNotFoundError):
            # Use a non-existent path
            DeviceInventory("/nonexistent/path/devices.yaml")
        # The error should be from file reading, not find_config
