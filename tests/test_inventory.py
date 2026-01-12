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


class TestDeviceInventoryNoConfig:
    """Tests for DeviceInventory when no config file exists."""

    def test_find_config_not_found(self):
        """FileNotFoundError raised when no config file found."""
        with pytest.raises(FileNotFoundError):
            # Use a non-existent path
            DeviceInventory("/nonexistent/path/devices.yaml")
        # The error should be from file reading, not find_config
