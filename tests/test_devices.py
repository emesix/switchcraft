"""Tests for device base classes and utilities."""
from mcp_network_switch.devices.base import (
    DeviceConfig,
    VLANConfig,
    PortConfig,
    DeviceStatus,
)


class TestDeviceConfig:
    """Tests for DeviceConfig dataclass."""

    def test_basic_config(self):
        """Basic device config creation."""
        config = DeviceConfig(
            type="brocade",
            name="Core Switch",
            host="192.168.1.1",
            protocol="telnet",
            port=23,
            username="admin",
        )
        assert config.type == "brocade"
        assert config.name == "Core Switch"
        assert config.host == "192.168.1.1"
        assert config.protocol == "telnet"
        assert config.port == 23
        assert config.username == "admin"

    def test_defaults(self):
        """Default values are applied."""
        config = DeviceConfig(
            type="generic",
            name="Test",
            host="10.0.0.1",
            protocol="ssh",
            port=22,
            username="user",
        )
        assert config.password is None
        assert config.password_env == "NETWORK_PASSWORD"
        assert config.timeout == 30
        assert config.retries == 3
        assert config.retry_delay == 2
        assert config.enable_password_required is False
        assert config.verify_ssl is True
        assert config.use_scp_workflow is False
        assert config.config_paths == {}

    def test_get_password_from_config(self):
        """Password from config takes precedence."""
        config = DeviceConfig(
            type="test",
            name="Test",
            host="10.0.0.1",
            protocol="ssh",
            port=22,
            username="user",
            password="secret123",
        )
        assert config.get_password() == "secret123"

    def test_get_password_from_env(self, monkeypatch):
        """Password falls back to environment variable."""
        monkeypatch.setenv("NETWORK_PASSWORD", "env_secret")
        config = DeviceConfig(
            type="test",
            name="Test",
            host="10.0.0.1",
            protocol="ssh",
            port=22,
            username="user",
        )
        assert config.get_password() == "env_secret"

    def test_get_password_custom_env(self, monkeypatch):
        """Custom password_env variable is respected."""
        monkeypatch.setenv("CUSTOM_PWD", "custom_secret")
        config = DeviceConfig(
            type="test",
            name="Test",
            host="10.0.0.1",
            protocol="ssh",
            port=22,
            username="user",
            password_env="CUSTOM_PWD",
        )
        assert config.get_password() == "custom_secret"


class TestVLANConfig:
    """Tests for VLANConfig dataclass."""

    def test_minimal_vlan(self):
        """Minimal VLAN with just ID."""
        vlan = VLANConfig(id=100)
        assert vlan.id == 100
        assert vlan.name == ""
        assert vlan.tagged_ports == []
        assert vlan.untagged_ports == []
        assert vlan.ip_address is None
        assert vlan.ip_mask is None
        assert vlan.description == ""

    def test_full_vlan(self):
        """Fully specified VLAN."""
        vlan = VLANConfig(
            id=254,
            name="Management",
            tagged_ports=["1/1/1", "1/1/2"],
            untagged_ports=["1/1/10", "1/1/11"],
            ip_address="192.168.254.1",
            ip_mask="255.255.255.0",
            description="Management VLAN",
        )
        assert vlan.id == 254
        assert vlan.name == "Management"
        assert len(vlan.tagged_ports) == 2
        assert len(vlan.untagged_ports) == 2
        assert vlan.ip_address == "192.168.254.1"
        assert vlan.description == "Management VLAN"


class TestPortConfig:
    """Tests for PortConfig dataclass."""

    def test_minimal_port(self):
        """Minimal port with just name."""
        port = PortConfig(name="1/1/1")
        assert port.name == "1/1/1"
        assert port.enabled is True
        assert port.speed is None
        assert port.duplex is None
        assert port.vlan_mode == "access"
        assert port.native_vlan is None
        assert port.allowed_vlans == []
        assert port.description == ""
        assert port.poe_enabled is None

    def test_full_port(self):
        """Fully specified port."""
        port = PortConfig(
            name="1/1/5",
            enabled=True,
            speed="1G",
            duplex="full",
            vlan_mode="trunk",
            native_vlan=1,
            allowed_vlans=[100, 200, 300],
            description="Server uplink",
            poe_enabled=True,
        )
        assert port.name == "1/1/5"
        assert port.speed == "1G"
        assert port.duplex == "full"
        assert port.vlan_mode == "trunk"
        assert port.native_vlan == 1
        assert 100 in port.allowed_vlans
        assert port.poe_enabled is True


class TestDeviceStatus:
    """Tests for DeviceStatus dataclass."""

    def test_reachable_device(self):
        """Reachable device status."""
        status = DeviceStatus(
            reachable=True,
            uptime="10 days, 5:30:00",
            firmware_version="08.0.30uT7f3",
            port_count=28,
            active_ports=24,
        )
        assert status.reachable is True
        assert status.uptime == "10 days, 5:30:00"
        assert status.firmware_version == "08.0.30uT7f3"
        assert status.error is None

    def test_unreachable_device(self):
        """Unreachable device status with error."""
        status = DeviceStatus(
            reachable=False,
            error="Connection refused",
        )
        assert status.reachable is False
        assert status.error == "Connection refused"
        assert status.uptime is None
