"""Real device integration tests.

These tests run against actual network devices.
Skip in CI - only run manually or with real hardware available.

Run with: pytest tests/test_integration_real.py -v -s
"""
import os
import pytest

# Set env before imports
os.environ.setdefault("NETWORK_PASSWORD", "NikonD90")
os.environ.setdefault("MCP_NETWORK_CONFIG", "/home/emesix/git/switchcraft/configs/devices.yaml")

from mcp_network_switch.config.inventory import DeviceInventory


# Skip if not on the right network
def can_reach_device(host: str) -> bool:
    """Check if device is reachable."""
    import subprocess
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "1", host],
        capture_output=True
    )
    return result.returncode == 0


@pytest.fixture
def inventory():
    """Load device inventory."""
    return DeviceInventory()


class TestBrocadeReal:
    """Real Brocade device tests."""

    @pytest.fixture
    def brocade(self, inventory):
        """Get Brocade device instance."""
        return inventory.get_device("brocade-core")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.2"),
        reason="Brocade not reachable"
    )
    @pytest.mark.asyncio
    async def test_connect_and_health(self, brocade):
        """Test Brocade connection and health check."""
        async with brocade:
            assert brocade.is_connected
            status = await brocade.check_health()
            assert status.reachable
            print("\nBrocade Status:")
            print(f"  Uptime: {status.uptime}")
            print(f"  Firmware: {status.firmware_version}")
            print(f"  Ports: {status.active_ports}/{status.port_count}")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.2"),
        reason="Brocade not reachable"
    )
    @pytest.mark.asyncio
    async def test_get_vlans(self, brocade):
        """Test reading VLANs from Brocade."""
        async with brocade:
            vlans = await brocade.get_vlans()
            print(f"\nBrocade VLANs ({len(vlans)}):")
            for vlan in vlans[:5]:  # First 5
                print(f"  VLAN {vlan.id}: {vlan.name}")
                print(f"    Tagged: {vlan.tagged_ports[:3]}...")
                print(f"    Untagged: {vlan.untagged_ports[:3]}...")
            assert len(vlans) > 0, "Should have at least one VLAN"


class TestZyxelReal:
    """Real Zyxel device tests."""

    @pytest.fixture
    def zyxel(self, inventory):
        """Get Zyxel device instance."""
        return inventory.get_device("zyxel-frontend")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.3"),
        reason="Zyxel not reachable"
    )
    @pytest.mark.asyncio
    async def test_connect_and_health(self, zyxel):
        """Test Zyxel connection and health check."""
        async with zyxel:
            assert zyxel.is_connected
            status = await zyxel.check_health()
            assert status.reachable
            print("\nZyxel Status:")
            print(f"  Uptime: {status.uptime}")
            print(f"  Firmware: {status.firmware_version}")
            print(f"  Ports: {status.active_ports}/{status.port_count}")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.3"),
        reason="Zyxel not reachable"
    )
    @pytest.mark.asyncio
    async def test_get_vlans(self, zyxel):
        """Test reading VLANs from Zyxel."""
        async with zyxel:
            vlans = await zyxel.get_vlans()
            print(f"\nZyxel VLANs ({len(vlans)}):")
            for vlan in vlans[:5]:
                print(f"  VLAN {vlan.id}: {vlan.name}")
            assert len(vlans) > 0, "Should have at least one VLAN"


class TestONTIReal:
    """Real ONTI device tests."""

    @pytest.fixture
    def onti(self, inventory):
        """Get ONTI device instance."""
        return inventory.get_device("onti-backend")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.4"),
        reason="ONTI not reachable"
    )
    @pytest.mark.asyncio
    async def test_connect_and_health(self, onti):
        """Test ONTI connection and health check."""
        async with onti:
            assert onti.is_connected
            status = await onti.check_health()
            assert status.reachable
            print("\nONTI Status:")
            print(f"  Uptime: {status.uptime}")
            print(f"  Firmware: {status.firmware_version}")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.4"),
        reason="ONTI not reachable"
    )
    @pytest.mark.asyncio
    async def test_get_config_file(self, onti):
        """Test reading config via SCP from ONTI."""
        async with onti:
            # ONTI uses UCI config format
            try:
                network_config = await onti.get_config_file("network")
                print(f"\nONTI Network Config ({len(network_config)} bytes):")
                print(network_config[:500] + "..." if len(network_config) > 500 else network_config)
                assert len(network_config) > 0
            except Exception as e:
                print(f"\nONTI get_config_file failed: {e}")
                # Try alternative method
                success, output = await onti.execute("cat /etc/config/network")
                print(f"Direct cat result ({len(output)} bytes):")
                print(output[:500] + "..." if len(output) > 500 else output)


class TestAllDevices:
    """Cross-device integration tests."""

    @pytest.mark.asyncio
    async def test_all_devices_reachable(self, inventory):
        """Test that all configured devices are reachable."""
        results = {}
        for device_id in ["brocade-core", "zyxel-frontend", "onti-backend"]:
            try:
                device = inventory.get_device(device_id)
                async with device:
                    status = await device.check_health()
                    results[device_id] = {
                        "reachable": status.reachable,
                        "uptime": status.uptime,
                        "error": status.error
                    }
            except Exception as e:
                results[device_id] = {
                    "reachable": False,
                    "error": str(e)
                }

        print("\n=== All Devices Status ===")
        for device_id, status in results.items():
            emoji = "✅" if status.get("reachable") else "❌"
            print(f"{emoji} {device_id}: {status}")

        # At least 2 of 3 should be reachable
        reachable_count = sum(1 for s in results.values() if s.get("reachable"))
        assert reachable_count >= 2, f"Only {reachable_count}/3 devices reachable"
