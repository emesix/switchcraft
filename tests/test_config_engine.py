"""Tests for the Config Engine."""
import pytest
from mcp_network_switch.config_engine import (
    ConfigParser,
    ParseError,
    ConfigValidator,
    CommandGenerator,
    DesiredState,
    VLANDesiredState,
    VLANAction,
    DiffResult,
    VLANChange,
    ChangeType,
)


class TestConfigParser:
    """Tests for the ConfigParser."""

    def test_parse_minimal_config(self):
        """Parse minimal config with just device."""
        parser = ConfigParser()
        config = {"device": "brocade-core"}

        result = parser.parse(config)

        assert result.device_id == "brocade-core"
        assert result.version == 1
        assert result.mode == "patch"
        assert len(result.vlans) == 0

    def test_parse_config_with_vlans(self):
        """Parse config with VLAN definitions."""
        parser = ConfigParser()
        config = {
            "device": "brocade-core",
            "vlans": {
                100: {
                    "name": "Production",
                    "untagged_ports": ["1/1/1", "1/1/2"],
                    "tagged_ports": ["1/2/1"]
                }
            }
        }

        result = parser.parse(config)

        assert 100 in result.vlans
        vlan = result.vlans[100]
        assert vlan.name == "Production"
        assert vlan.untagged_ports == ["1/1/1", "1/1/2"]
        assert vlan.tagged_ports == ["1/2/1"]
        assert vlan.action == VLANAction.ENSURE

    def test_parse_vlan_absent(self):
        """Parse VLAN with absent action."""
        parser = ConfigParser()
        config = {
            "device": "brocade-core",
            "vlans": {
                999: {"action": "absent"}
            }
        }

        result = parser.parse(config)

        assert result.vlans[999].action == VLANAction.ABSENT

    def test_parse_port_range_expansion(self):
        """Port ranges like 1/1/1-4 should expand."""
        parser = ConfigParser()
        config = {
            "device": "brocade-core",
            "vlans": {
                100: {
                    "untagged_ports": ["1/1/1-4"]
                }
            }
        }

        result = parser.parse(config)

        assert result.vlans[100].untagged_ports == [
            "1/1/1", "1/1/2", "1/1/3", "1/1/4"
        ]

    def test_parse_missing_device_raises(self):
        """Missing device_id should raise ParseError."""
        parser = ConfigParser()

        with pytest.raises(ParseError) as exc:
            parser.parse({})

        assert "device_id" in str(exc.value).lower()

    def test_parse_string_vlan_id(self):
        """String VLAN IDs should be converted to int."""
        parser = ConfigParser()
        config = {
            "device": "brocade-core",
            "vlans": {
                "100": {"name": "Test"}
            }
        }

        result = parser.parse(config)

        assert 100 in result.vlans


class TestConfigValidator:
    """Tests for the ConfigValidator."""

    def test_valid_config(self):
        """Valid config should pass validation."""
        validator = ConfigValidator("brocade")
        desired = DesiredState(
            device_id="brocade-core",
            vlans={
                100: VLANDesiredState(
                    id=100,
                    name="Test",
                    untagged_ports=["1/1/1", "1/1/2"]
                )
            }
        )

        result = validator.validate(desired)

        assert result.valid
        assert len(result.errors) == 0

    def test_invalid_vlan_id_too_low(self):
        """VLAN ID 0 should fail validation."""
        validator = ConfigValidator("brocade")
        desired = DesiredState(
            device_id="brocade-core",
            vlans={
                0: VLANDesiredState(id=0)
            }
        )

        result = validator.validate(desired)

        assert not result.valid
        assert any("vlan" in e.lower() and "0" in e for e in result.errors)

    def test_invalid_vlan_id_too_high(self):
        """VLAN ID 4095 should fail validation."""
        validator = ConfigValidator("brocade")
        desired = DesiredState(
            device_id="brocade-core",
            vlans={
                4095: VLANDesiredState(id=4095)
            }
        )

        result = validator.validate(desired)

        assert not result.valid

    def test_cannot_delete_vlan_1(self):
        """VLAN 1 deletion should fail validation."""
        validator = ConfigValidator("brocade")
        desired = DesiredState(
            device_id="brocade-core",
            vlans={
                1: VLANDesiredState(id=1, action=VLANAction.ABSENT)
            }
        )

        result = validator.validate(desired)

        assert not result.valid
        assert any("cannot delete" in e.lower() for e in result.errors)

    def test_port_conflict_untagged(self):
        """Same port untagged in two VLANs should fail."""
        validator = ConfigValidator("brocade")
        desired = DesiredState(
            device_id="brocade-core",
            vlans={
                100: VLANDesiredState(id=100, untagged_ports=["1/1/1"]),
                200: VLANDesiredState(id=200, untagged_ports=["1/1/1"]),
            }
        )

        result = validator.validate(desired)

        assert not result.valid
        assert any("1/1/1" in e and "untagged" in e.lower() for e in result.errors)

    def test_empty_vlan_warning(self):
        """VLAN with no ports should generate warning."""
        validator = ConfigValidator("brocade")
        desired = DesiredState(
            device_id="brocade-core",
            vlans={
                100: VLANDesiredState(id=100, name="Empty")
            }
        )

        result = validator.validate(desired)

        assert result.valid  # Warnings don't fail validation
        assert any("no ports" in w.lower() for w in result.warnings)


class TestCommandGenerator:
    """Tests for the CommandGenerator."""

    def test_generate_create_vlan(self):
        """Generate commands to create a VLAN."""
        generator = CommandGenerator()
        diff = DiffResult(
            vlan_changes=[
                VLANChange(
                    vlan_id=100,
                    change_type=ChangeType.CREATE,
                    desired_name="Production",
                    ports_to_add_untagged=["1/1/1", "1/1/2"],
                    ports_to_add_tagged=["1/2/1"],
                )
            ]
        )

        plan = generator.generate("brocade", diff)

        assert "vlan 100 name Production by port" in plan.main_commands
        assert any("untagged ethe" in cmd for cmd in plan.main_commands)
        assert any("tagged ethe" in cmd for cmd in plan.main_commands)
        assert "exit" in plan.main_commands

    def test_generate_delete_vlan(self):
        """Generate commands to delete a VLAN."""
        generator = CommandGenerator()
        diff = DiffResult(
            vlan_changes=[
                VLANChange(
                    vlan_id=999,
                    change_type=ChangeType.DELETE,
                )
            ]
        )

        plan = generator.generate("brocade", diff)

        assert "no vlan 999" in plan.main_commands

    def test_generate_modify_vlan(self):
        """Generate commands to modify a VLAN."""
        generator = CommandGenerator()
        diff = DiffResult(
            vlan_changes=[
                VLANChange(
                    vlan_id=100,
                    change_type=ChangeType.MODIFY,
                    ports_to_add_untagged=["1/1/3"],
                    ports_to_remove_untagged=["1/1/1"],
                )
            ]
        )

        plan = generator.generate("brocade", diff)

        assert "vlan 100" in plan.main_commands
        # Remove comes before add
        remove_idx = next(
            i for i, cmd in enumerate(plan.main_commands)
            if "no untagged" in cmd
        )
        add_idx = next(
            i for i, cmd in enumerate(plan.main_commands)
            if "untagged ethe" in cmd and "no" not in cmd
        )
        assert remove_idx < add_idx

    def test_generate_groups_ports_by_module(self):
        """Ports should be grouped by module."""
        generator = CommandGenerator()
        diff = DiffResult(
            vlan_changes=[
                VLANChange(
                    vlan_id=100,
                    change_type=ChangeType.CREATE,
                    desired_name="Test",
                    ports_to_add_untagged=["1/1/1", "1/1/2", "1/2/1", "1/2/2"],
                )
            ]
        )

        plan = generator.generate("brocade", diff)

        # Should have two separate untagged commands (one per module)
        untagged_cmds = [cmd for cmd in plan.main_commands if "untagged ethe" in cmd]
        assert len(untagged_cmds) == 2

    def test_generate_includes_write_memory(self):
        """Generated plan should include write memory."""
        generator = CommandGenerator()
        diff = DiffResult(
            vlan_changes=[
                VLANChange(vlan_id=100, change_type=ChangeType.CREATE)
            ]
        )

        plan = generator.generate("brocade", diff, save_config=True)

        assert "write memory" in plan.post_commands

    def test_generate_rollback_commands(self):
        """Rollback commands should be generated."""
        generator = CommandGenerator()
        diff = DiffResult(
            vlan_changes=[
                VLANChange(vlan_id=100, change_type=ChangeType.CREATE)
            ]
        )

        plan = generator.generate("brocade", diff)

        # Rollback for create is delete
        assert "no vlan 100" in plan.rollback_commands
