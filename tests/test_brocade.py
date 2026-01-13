"""Tests for Brocade device handler."""
import pytest
from mcp_network_switch.devices.brocade import BrocadeDevice
from mcp_network_switch.devices.base import DeviceConfig


class TestBrocadePortParsing:
    """Tests for Brocade port parsing and formatting."""

    @pytest.fixture
    def device(self):
        """Create a Brocade device for testing."""
        config = DeviceConfig(
            type="brocade",
            name="Test Brocade",
            host="192.168.1.1",
            protocol="telnet",
            port=23,
            username="admin",
            password="test",
        )
        return BrocadeDevice("test-brocade", config)

    def test_parse_port_line_untagged_m1(self, device):
        """Parse untagged ports from module 1."""
        line = " Untagged Ports: (U1/M1)   1   2   3   4"
        module, ports = device._parse_port_line(line, "Untagged Ports:")
        assert module == 1
        assert ports == ["1/1/1", "1/1/2", "1/1/3", "1/1/4"]

    def test_parse_port_line_tagged_m2(self, device):
        """Parse tagged ports from module 2 (10G)."""
        line = " Tagged Ports: (U1/M2)   1   2"
        module, ports = device._parse_port_line(line, "Tagged Ports:")
        assert module == 2
        assert ports == ["1/2/1", "1/2/2"]

    def test_parse_port_line_none(self, device):
        """Parse 'None' port line."""
        line = " Untagged Ports: None"
        module, ports = device._parse_port_line(line, "Untagged Ports:")
        assert ports == []

    def test_parse_port_line_empty(self, device):
        """Parse empty port line."""
        line = " Tagged Ports: "
        module, ports = device._parse_port_line(line, "Tagged Ports:")
        assert ports == []

    def test_format_port_range_single(self, device):
        """Format single port."""
        result = device._format_port_range(["1/1/1"])
        assert result == "1/1/1 to 1/1/1"

    def test_format_port_range_contiguous(self, device):
        """Format contiguous port range."""
        result = device._format_port_range(["1/1/1", "1/1/2", "1/1/3", "1/1/4"])
        assert result == "1/1/1 to 1/1/4"

    def test_format_port_range_non_contiguous(self, device):
        """Format non-contiguous ports."""
        result = device._format_port_range(["1/1/1", "1/1/3", "1/1/5"])
        assert "1/1/1 to 1/1/1" in result
        assert "1/1/3 to 1/1/3" in result
        assert "1/1/5 to 1/1/5" in result

    def test_format_port_range_mixed_modules(self, device):
        """Format ports across different modules."""
        result = device._format_port_range(["1/1/1", "1/1/2", "1/2/1", "1/2/2"])
        assert "1/1/1 to 1/1/2" in result
        assert "1/2/1 to 1/2/2" in result

    def test_format_port_ranges_by_module_single_module(self, device):
        """Single module returns single entry."""
        result = device._format_port_ranges_by_module(["1/1/1", "1/1/2", "1/1/3"])
        assert result == ["1/1/1 to 1/1/3"]

    def test_format_port_ranges_by_module_two_modules(self, device):
        """Two modules return two separate entries."""
        result = device._format_port_ranges_by_module(["1/1/1", "1/1/2", "1/2/1", "1/2/2"])
        assert len(result) == 2
        assert "1/1/1 to 1/1/2" in result
        assert "1/2/1 to 1/2/2" in result

    def test_format_port_ranges_by_module_empty(self, device):
        """Empty list returns empty list."""
        result = device._format_port_ranges_by_module([])
        assert result == []

    def test_format_port_ranges_by_module_preserves_order(self, device):
        """Module 1 comes before module 2 in output."""
        result = device._format_port_ranges_by_module(["1/2/1", "1/1/1"])
        assert result[0] == "1/1/1 to 1/1/1"
        assert result[1] == "1/2/1 to 1/2/1"

    def test_format_port_range_empty(self, device):
        """Format empty port list."""
        result = device._format_port_range([])
        assert result == ""

    def test_format_port_range_unsorted(self, device):
        """Ports are sorted before formatting."""
        result = device._format_port_range(["1/1/4", "1/1/1", "1/1/3", "1/1/2"])
        assert result == "1/1/1 to 1/1/4"


class TestBrocadeErrorDetection:
    """Tests for Brocade error detection logic."""

    @pytest.fixture
    def device(self):
        """Create a Brocade device for testing."""
        config = DeviceConfig(
            type="brocade",
            name="Test Brocade",
            host="192.168.1.1",
            protocol="telnet",
            port=23,
            username="admin",
            password="test",
        )
        return BrocadeDevice("test-brocade", config)

    def test_real_error_detected(self, device):
        """Real errors should still be detected."""
        output = "Invalid input -> 1/2/1 to 1/2/4\nType ? for a list"
        error = device._has_error(output)
        assert error is not None
        assert "Invalid input" in error

    def test_already_member_not_error(self, device):
        """'already a member' is informational, not an error."""
        output = "Port(s) ethe 1/2/1 are already a member of VLAN 254"
        error = device._has_error(output)
        assert error is None

    def test_added_port_not_error(self, device):
        """'Added untagged port' is success, not an error."""
        output = "Added untagged port(s) ethe 1/2/3 to port-vlan 254."
        error = device._has_error(output)
        assert error is None

    def test_removed_port_not_error(self, device):
        """'Removed tagged port' is success, not an error."""
        output = "Removed tagged port(s) ethe 1/2/1 from port-vlan 254."
        error = device._has_error(output)
        assert error is None

    def test_please_disable_is_error(self, device):
        """'Please disable dual mode' is a real error."""
        output = "Please disable dual mode for port ethe 1/2/1 before removing the ports from the VLAN."
        error = device._has_error(output)
        assert error is not None
        assert "Please disable" in error

    def test_mixed_output_with_info(self, device):
        """Mixed output with info pattern should not report error."""
        output = """vlan 254
Port(s) ethe 1/2/1 are already a member of VLAN 254
exit"""
        error = device._has_error(output)
        assert error is None

    def test_command_not_found_is_error(self, device):
        """Command not found is an error."""
        output = "Error: command not found"
        error = device._has_error(output)
        assert error is not None
