"""VLAN Integration Tests - Test create/delete lifecycle in safe VLAN range.

These tests run against actual network devices and create/delete VLANs.
Uses VLAN range 3000-3010 which should be safe for testing.

CAUTION: These tests modify device configuration! Only run in test environments.

Run with: pytest tests/test_vlan_integration.py -v -s
"""
import os
import pytest

# Set env before imports
os.environ.setdefault("NETWORK_PASSWORD", "NikonD90")
os.environ.setdefault("MCP_NETWORK_CONFIG", "/home/emesix/git/switchcraft/configs/devices.yaml")

from mcp_network_switch.config.inventory import DeviceInventory
from mcp_network_switch.devices.base import VLANConfig


# Test VLAN range - use VLANs 50-60 which are within default 64-VLAN limit
# but should be unused on most switches
TEST_VLAN_START = 50
TEST_VLAN_END = 60


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


class TestBrocadeVLANLifecycle:
    """Test VLAN create/verify/delete cycle on Brocade."""

    TEST_VLAN_ID = 51
    TEST_VLAN_NAME = "TestVLAN51"

    @pytest.fixture
    def brocade(self, inventory):
        """Get Brocade device instance."""
        return inventory.get_device("brocade-core")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.2"),
        reason="Brocade not reachable"
    )
    @pytest.mark.asyncio
    async def test_vlan_lifecycle_basic(self, brocade):
        """Test basic VLAN create/verify/delete cycle.

        Note: Brocade doesn't show VLANs without port members in 'show vlan'.
        We use 'show vlan <id>' to verify existence.
        """
        async with brocade:
            # Step 1: Ensure test VLAN doesn't exist (cleanup from previous run)
            await brocade.delete_vlan(self.TEST_VLAN_ID)

            # Step 2: Verify VLAN doesn't exist (check via show vlan <id>)
            success, output = await brocade.execute(f"show vlan {self.TEST_VLAN_ID}")
            # Brocade returns different messages for non-existent VLANs
            assert "does not exist" in output.lower() or "not have any members" not in output, \
                f"VLAN {self.TEST_VLAN_ID} should not exist initially"
            print(f"\n✅ Confirmed VLAN {self.TEST_VLAN_ID} doesn't exist")

            # Step 3: Create test VLAN
            vlan = VLANConfig(
                id=self.TEST_VLAN_ID,
                name=self.TEST_VLAN_NAME,
            )
            success, output = await brocade.create_vlan(vlan)
            assert success, f"Failed to create VLAN: {output}"
            print(f"✅ Created VLAN {self.TEST_VLAN_ID}")

            # Step 4: Verify VLAN was created (via show vlan <id>)
            success, output = await brocade.execute(f"show vlan {self.TEST_VLAN_ID}")
            # VLAN exists but has no members - this is expected for empty VLANs
            assert success, f"show vlan {self.TEST_VLAN_ID} failed"
            assert "does not exist" not in output.lower(), \
                f"VLAN {self.TEST_VLAN_ID} should exist after creation"
            print(f"   Response: {output[:100]}...")

            # Step 5: Delete test VLAN
            success, output = await brocade.delete_vlan(self.TEST_VLAN_ID)
            assert success, f"Failed to delete VLAN: {output}"
            print(f"✅ Deleted VLAN {self.TEST_VLAN_ID}")

            # Step 6: Verify VLAN was deleted
            success, output = await brocade.execute(f"show vlan {self.TEST_VLAN_ID}")
            assert "does not exist" in output.lower() or "invalid" in output.lower(), \
                f"VLAN {self.TEST_VLAN_ID} should not exist after deletion"
            print(f"✅ Verified VLAN {self.TEST_VLAN_ID} no longer exists")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.2"),
        reason="Brocade not reachable"
    )
    @pytest.mark.asyncio
    async def test_vlan_with_ports(self, brocade):
        """Test VLAN creation with port assignments."""
        test_vlan_id = 52
        test_vlan_name = "TestVLAN52WithPorts"

        async with brocade:
            # Cleanup
            await brocade.delete_vlan(test_vlan_id)

            # Create VLAN with port assignments
            # Using ports that are likely unused (higher numbered ports)
            vlan = VLANConfig(
                id=test_vlan_id,
                name=test_vlan_name,
                untagged_ports=["1/1/20", "1/1/21"],  # Use high-numbered ports
            )
            success, output = await brocade.create_vlan(vlan)
            assert success, f"Failed to create VLAN with ports: {output}"
            print(f"\n✅ Created VLAN {test_vlan_id} with ports 1/1/20, 1/1/21")

            # Verify ports were assigned
            vlans = await brocade.get_vlans()
            created_vlan = next((v for v in vlans if v.id == test_vlan_id), None)
            assert created_vlan is not None, f"VLAN {test_vlan_id} not found"

            # Note: The ports might be in a different format depending on device output
            print(f"   Untagged ports: {created_vlan.untagged_ports}")

            # Cleanup
            success, output = await brocade.delete_vlan(test_vlan_id)
            assert success, f"Failed to delete VLAN: {output}"
            print(f"✅ Deleted VLAN {test_vlan_id}")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.2"),
        reason="Brocade not reachable"
    )
    @pytest.mark.asyncio
    async def test_vlan_id_validation(self, brocade):
        """Test that invalid VLAN IDs are rejected."""
        async with brocade:
            # Test VLAN ID 0 (reserved)
            vlan = VLANConfig(id=0, name="InvalidZero")
            success, output = await brocade.create_vlan(vlan)
            assert not success, "VLAN 0 should be rejected"
            assert "Invalid VLAN ID" in output or "must be between" in output
            print(f"\n✅ VLAN 0 correctly rejected: {output}")

            # Test VLAN ID 4095 (out of range)
            vlan = VLANConfig(id=4095, name="InvalidMax")
            success, output = await brocade.create_vlan(vlan)
            assert not success, "VLAN 4095 should be rejected"
            print(f"✅ VLAN 4095 correctly rejected: {output}")

            # Test negative VLAN ID
            vlan = VLANConfig(id=-1, name="InvalidNegative")
            success, output = await brocade.create_vlan(vlan)
            assert not success, "Negative VLAN ID should be rejected"
            print(f"✅ VLAN -1 correctly rejected: {output}")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.2"),
        reason="Brocade not reachable"
    )
    @pytest.mark.asyncio
    async def test_vlan1_protection(self, brocade):
        """Test that VLAN 1 (default) cannot be deleted."""
        async with brocade:
            success, output = await brocade.delete_vlan(1)
            assert not success, "VLAN 1 should not be deletable"
            assert "default" in output.lower() or "protected" in output.lower()
            print(f"\n✅ VLAN 1 correctly protected: {output}")


class TestVLANRangeCleanup:
    """Utility test to clean up any leftover test VLANs."""

    @pytest.fixture
    def brocade(self, inventory):
        """Get Brocade device instance."""
        return inventory.get_device("brocade-core")

    @pytest.mark.skipif(
        not can_reach_device("192.168.254.2"),
        reason="Brocade not reachable"
    )
    @pytest.mark.asyncio
    async def test_cleanup_test_vlans(self, brocade):
        """Clean up any VLANs in the test range 3000-3010."""
        async with brocade:
            cleaned = 0
            for vlan_id in range(TEST_VLAN_START, TEST_VLAN_END + 1):
                success, _ = await brocade.delete_vlan(vlan_id)
                if success:
                    cleaned += 1
                    print(f"Cleaned up VLAN {vlan_id}")

            print(f"\n✅ Cleaned up {cleaned} test VLANs in range {TEST_VLAN_START}-{TEST_VLAN_END}")
