"""Tests for configuration schema and normalization."""
import pytest
from mcp_network_switch.config.schema import (
    NormalizedVLAN,
    NormalizedPort,
    NetworkConfig,
    ConfigDiff,
    normalize_port_name,
    normalize_config,
    diff_configs,
)
from mcp_network_switch.devices.base import VLANConfig, PortConfig


class TestNormalizePortName:
    """Tests for port name normalization."""

    def test_normalize_simple_number(self):
        """Simple port number stays as-is."""
        assert normalize_port_name("1", "zyxel") == "1"
        assert normalize_port_name("24", "zyxel") == "24"

    def test_normalize_brocade_format(self):
        """Brocade 1/1/1 format gets normalized to 1-1-1."""
        assert normalize_port_name("1/1/1", "brocade") == "1-1-1"
        assert normalize_port_name("1/2/4", "brocade") == "1-2-4"

    def test_normalize_with_prefix(self):
        """Port prefixes like 'port', 'eth' are removed."""
        assert normalize_port_name("port0", "onti") == "0"
        assert normalize_port_name("port5", "onti") == "5"
        assert normalize_port_name("eth0", "generic") == "0"
        assert normalize_port_name("ethernet1", "generic") == "1"
        assert normalize_port_name("ge0/0/1", "generic") == "0-0-1"
        assert normalize_port_name("gi1", "generic") == "1"

    def test_normalize_preserves_unknown(self):
        """Unknown formats are preserved."""
        assert normalize_port_name("some-port", "generic") == "some-port"


class TestVLANConfig:
    """Tests for VLAN configuration dataclass."""

    def test_vlan_config_defaults(self):
        """VLANConfig has sensible defaults."""
        vlan = VLANConfig(id=100)
        assert vlan.id == 100
        assert vlan.name == ""
        assert vlan.tagged_ports == []
        assert vlan.untagged_ports == []

    def test_vlan_config_full(self):
        """VLANConfig can be fully populated."""
        vlan = VLANConfig(
            id=200,
            name="Servers",
            tagged_ports=["1/1/1", "1/1/2"],
            untagged_ports=["1/1/5", "1/1/6"],
            ip_address="192.168.200.1",
            ip_mask="255.255.255.0",
        )
        assert vlan.id == 200
        assert vlan.name == "Servers"
        assert len(vlan.tagged_ports) == 2
        assert len(vlan.untagged_ports) == 2


class TestNormalizeConfig:
    """Tests for config normalization."""

    def test_normalize_empty(self):
        """Empty config normalizes correctly."""
        config = normalize_config(
            device_id="test-device",
            device_type="generic",
            device_name="Test Device",
            vlans=[],
            ports=[],
        )
        assert config.device_id == "test-device"
        assert config.device_type == "generic"
        assert config.vlans == []
        assert config.ports == []
        assert config.retrieved_at != ""

    def test_normalize_with_vlans(self):
        """VLANs are normalized correctly."""
        vlans = [
            VLANConfig(id=100, name="Data", tagged_ports=["1/1/1"]),
            VLANConfig(id=200, name="Voice", untagged_ports=["1/1/2"]),
        ]
        config = normalize_config(
            device_id="test",
            device_type="brocade",
            device_name="Test",
            vlans=vlans,
            ports=[],
        )
        assert len(config.vlans) == 2
        assert config.vlans[0].id == 100
        assert config.vlans[0].name == "Data"
        # Port name should be normalized
        assert config.vlans[0].tagged_ports == ["1-1-1"]
        assert config.vlans[1].untagged_ports == ["1-1-2"]

    def test_normalize_with_ports(self):
        """Ports are normalized correctly."""
        ports = [
            PortConfig(name="1/1/1", enabled=True, speed="1G"),
            PortConfig(name="1/1/2", enabled=False),
        ]
        config = normalize_config(
            device_id="test",
            device_type="brocade",
            device_name="Test",
            vlans=[],
            ports=ports,
        )
        assert len(config.ports) == 2
        assert config.ports[0].id == "1-1-1"
        assert config.ports[0].original_name == "1/1/1"
        assert config.ports[0].enabled is True
        assert config.ports[0].speed == "1G"


class TestConfigDiff:
    """Tests for config diff functionality."""

    def test_no_changes(self):
        """Identical configs show no changes."""
        vlan1 = NormalizedVLAN(id=100, name="Test", tagged_ports=["1"])
        config1 = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test",
            vlans=[vlan1],
        )
        config2 = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test",
            vlans=[NormalizedVLAN(id=100, name="Test", tagged_ports=["1"])],
        )
        diff = diff_configs(config1, config2)
        assert not diff.has_changes()
        assert diff.to_text() == "No changes detected"

    def test_vlan_added(self):
        """New VLAN in actual shows as added."""
        expected = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test",
            vlans=[],
        )
        actual = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test",
            vlans=[NormalizedVLAN(id=100, name="NewVLAN")],
        )
        diff = diff_configs(expected, actual)
        assert diff.has_changes()
        assert len(diff.changes) == 1
        assert diff.changes[0]["type"] == "added"
        assert diff.changes[0]["item_id"] == "100"

    def test_vlan_removed(self):
        """VLAN in expected but not actual shows as removed."""
        expected = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test",
            vlans=[NormalizedVLAN(id=100, name="OldVLAN")],
        )
        actual = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test",
            vlans=[],
        )
        diff = diff_configs(expected, actual)
        assert diff.has_changes()
        assert len(diff.changes) == 1
        assert diff.changes[0]["type"] == "removed"

    def test_vlan_ports_modified(self):
        """Changed port membership shows as modified."""
        expected = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test",
            vlans=[NormalizedVLAN(id=100, name="Test", tagged_ports=["1", "2"])],
        )
        actual = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test",
            vlans=[NormalizedVLAN(id=100, name="Test", tagged_ports=["1", "3"])],
        )
        diff = diff_configs(expected, actual)
        assert diff.has_changes()
        assert any(c["type"] == "modified" for c in diff.changes)


class TestNetworkConfig:
    """Tests for NetworkConfig serialization."""

    def test_to_json(self):
        """Config can be serialized to JSON."""
        config = NetworkConfig(
            device_id="test",
            device_type="generic",
            device_name="Test Device",
            vlans=[NormalizedVLAN(id=100, name="Data")],
        )
        json_str = config.to_json()
        assert "test" in json_str
        assert "Data" in json_str
        assert "100" in json_str

    def test_from_dict(self):
        """Config can be deserialized from dict."""
        data = {
            "device_id": "test",
            "device_type": "generic",
            "device_name": "Test",
            "vlans": [{"id": 100, "name": "Data"}],
            "ports": [],
        }
        config = NetworkConfig.from_dict(data)
        assert config.device_id == "test"
        assert len(config.vlans) == 1
        assert config.vlans[0].id == 100
