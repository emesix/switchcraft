"""Command generator for creating device-specific command batches.

Generates optimized command sequences from diff results.
"""
from collections import defaultdict

from .schema import (
    DiffResult,
    VLANChange,
    PortChange,
    ChangeType,
    CommandPlan,
)


class CommandGenerator:
    """Generate device-specific command batches from diff results."""

    def generate(
        self,
        device_type: str,
        diff: DiffResult,
        save_config: bool = True
    ) -> CommandPlan:
        """
        Generate command plan from diff.

        Args:
            device_type: Type of device (brocade, openwrt, zyxel)
            diff: Diff result with changes to apply
            save_config: Whether to include save/write command

        Returns:
            CommandPlan with all commands
        """
        if device_type == "brocade":
            return self._generate_brocade(diff, save_config)
        elif device_type == "openwrt":
            return self._generate_openwrt(diff, save_config)
        else:
            raise ValueError(f"Unsupported device type: {device_type}")

    def _generate_brocade(
        self,
        diff: DiffResult,
        save_config: bool
    ) -> CommandPlan:
        """Generate Brocade-specific commands."""
        plan = CommandPlan()

        # Pre-commands: Handle known blockers
        # Detect ports that might have dual-mode issues
        for change in diff.vlan_changes:
            if change.change_type == ChangeType.MODIFY:
                # Ports being removed from tagged might need dual-mode disabled
                for port in change.ports_to_remove_tagged:
                    # Add dual-mode disable as precaution
                    plan.pre_commands.extend([
                        f"interface ethe {port}",
                        "no dual-mode",
                        "exit",
                    ])

        # Main commands: VLAN changes
        for change in diff.vlan_changes:
            plan.main_commands.extend(
                self._brocade_vlan_commands(change)
            )

        # Port configuration changes
        for change in diff.port_changes:
            plan.main_commands.extend(
                self._brocade_port_commands(change)
            )

        # Post-commands: Save config
        if save_config and plan.main_commands:
            plan.post_commands.append("write memory")

        # Generate rollback commands (reverse order)
        plan.rollback_commands = self._generate_brocade_rollback(diff)

        return plan

    def _brocade_vlan_commands(self, change: VLANChange) -> list[str]:
        """Generate Brocade commands for a VLAN change."""
        commands = []

        if change.change_type == ChangeType.CREATE:
            # Create VLAN with name
            vlan_name = change.desired_name or f"VLAN{change.vlan_id}"
            commands.append(f"vlan {change.vlan_id} name {vlan_name} by port")

            # Add untagged ports (grouped by module)
            for port_spec in self._group_ports_by_module(change.ports_to_add_untagged):
                commands.append(f"untagged ethe {port_spec}")

            # Add tagged ports (grouped by module)
            for port_spec in self._group_ports_by_module(change.ports_to_add_tagged):
                commands.append(f"tagged ethe {port_spec}")

            commands.append("exit")

        elif change.change_type == ChangeType.DELETE:
            commands.append(f"no vlan {change.vlan_id}")

        elif change.change_type == ChangeType.MODIFY:
            commands.append(f"vlan {change.vlan_id}")

            # Remove ports first (order matters!)
            for port_spec in self._group_ports_by_module(change.ports_to_remove_untagged):
                commands.append(f"no untagged ethe {port_spec}")

            for port_spec in self._group_ports_by_module(change.ports_to_remove_tagged):
                commands.append(f"no tagged ethe {port_spec}")

            # Then add ports
            for port_spec in self._group_ports_by_module(change.ports_to_add_untagged):
                commands.append(f"untagged ethe {port_spec}")

            for port_spec in self._group_ports_by_module(change.ports_to_add_tagged):
                commands.append(f"tagged ethe {port_spec}")

            commands.append("exit")

        return commands

    def _brocade_port_commands(self, change: PortChange) -> list[str]:
        """Generate Brocade commands for a port change."""
        commands = []

        commands.append(f"interface ethe {change.port_name}")

        if change.enabled is not None:
            if change.enabled:
                commands.append("enable")
            else:
                commands.append("disable")

        if change.description:
            commands.append(f'port-name "{change.description}"')

        if change.speed:
            if change.speed == "auto":
                commands.append("speed-duplex auto")
            elif change.speed == "10G":
                commands.append("speed-duplex 10g-full")
            elif change.speed == "1G":
                commands.append("speed-duplex 1000-full")
            elif change.speed == "100M":
                commands.append("speed-duplex 100-full")

        commands.append("exit")

        return commands

    def _group_ports_by_module(self, ports: list[str]) -> list[str]:
        """
        Group ports by module for Brocade commands.

        Brocade cannot accept port ranges spanning different modules.
        Returns separate port specs per module.

        Example:
            ["1/1/1", "1/1/2", "1/2/1", "1/2/2"]
            -> ["1/1/1 to 1/1/2", "1/2/1 to 1/2/2"]
        """
        if not ports:
            return []

        # Parse ports into (unit, module, port) tuples
        parsed: list[tuple[int, int, int, str]] = []
        for p in ports:
            try:
                parts = p.split("/")
                if len(parts) == 3:
                    parsed.append((int(parts[0]), int(parts[1]), int(parts[2]), p))
            except (ValueError, IndexError):
                # Keep original string for non-standard formats
                parsed.append((0, 0, 0, p))

        # Sort by unit, module, port
        parsed.sort(key=lambda x: (x[0], x[1], x[2]))

        # Group by (unit, module)
        module_groups: dict[tuple[int, int], list[tuple[int, str]]] = defaultdict(list)
        for unit, module, port_num, port_str in parsed:
            module_groups[(unit, module)].append((port_num, port_str))

        # Build ranges per module
        result = []
        for (unit, module), port_list in sorted(module_groups.items()):
            ranges = []
            i = 0
            while i < len(port_list):
                port_num, port_str = port_list[i]
                start = port_str
                end = port_str

                # Find contiguous ports
                j = i + 1
                while j < len(port_list):
                    next_num, next_str = port_list[j]
                    prev_num, _ = port_list[j - 1]
                    if next_num == prev_num + 1:
                        end = next_str
                        j += 1
                    else:
                        break

                ranges.append(f"{start} to {end}")
                i = j

            result.append(" ".join(ranges))

        return result

    def _generate_brocade_rollback(self, diff: DiffResult) -> list[str]:
        """Generate rollback commands for Brocade (reverse of changes)."""
        commands = []

        # Reverse VLAN changes
        for change in reversed(diff.vlan_changes):
            if change.change_type == ChangeType.CREATE:
                # Rollback: delete the VLAN
                commands.append(f"no vlan {change.vlan_id}")

            elif change.change_type == ChangeType.DELETE:
                # Rollback: recreate the VLAN (if we knew the original state)
                # For MVP, we just note this is not possible
                commands.append(f"! Cannot rollback VLAN {change.vlan_id} deletion")

            elif change.change_type == ChangeType.MODIFY:
                commands.append(f"vlan {change.vlan_id}")
                # Reverse: remove what we added, add what we removed
                for port_spec in self._group_ports_by_module(change.ports_to_add_untagged):
                    commands.append(f"no untagged ethe {port_spec}")
                for port_spec in self._group_ports_by_module(change.ports_to_add_tagged):
                    commands.append(f"no tagged ethe {port_spec}")
                for port_spec in self._group_ports_by_module(change.ports_to_remove_untagged):
                    commands.append(f"untagged ethe {port_spec}")
                for port_spec in self._group_ports_by_module(change.ports_to_remove_tagged):
                    commands.append(f"tagged ethe {port_spec}")
                commands.append("exit")

        return commands

    def _generate_openwrt(
        self,
        diff: DiffResult,
        save_config: bool
    ) -> CommandPlan:
        """Generate OpenWrt-specific commands (UCI)."""
        # TODO: Implement OpenWrt command generation
        # OpenWrt uses UCI commands like:
        #   uci set network.vlan100=bridge-vlan
        #   uci set network.vlan100.vlan='100'
        #   uci add_list network.vlan100.ports='lan1:u'
        #   uci commit network
        #   /etc/init.d/network reload

        plan = CommandPlan()
        plan.main_commands.append("# OpenWrt command generation not yet implemented")
        return plan
