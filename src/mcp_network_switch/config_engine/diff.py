"""Diff engine for calculating changes between desired and current state.

Computes the minimal set of changes needed to reach the desired state.
"""
from typing import Optional

from ..devices.base import NetworkDevice, VLANConfig, PortConfig
from .schema import (
    DesiredState,
    VLANDesiredState,
    VLANAction,
    PortDesiredState,
    DiffResult,
    VLANChange,
    PortChange,
    ChangeType,
)


class DiffEngine:
    """Calculate differences between desired and current state."""

    async def calculate(
        self,
        device: NetworkDevice,
        desired: DesiredState
    ) -> DiffResult:
        """
        Calculate diff between desired state and current device state.

        Args:
            device: Connected network device
            desired: Desired state configuration

        Returns:
            DiffResult with all changes needed
        """
        # Fetch current state from device
        current_vlans = await device.get_vlans()
        current_ports = await device.get_ports()

        # Build lookup maps
        current_vlan_map = {v.id: v for v in current_vlans}
        current_port_map = {p.name: p for p in current_ports}

        result = DiffResult()

        # Calculate VLAN changes
        for vlan_id, desired_vlan in desired.vlans.items():
            current_vlan = current_vlan_map.get(vlan_id)
            change = self._diff_vlan(vlan_id, desired_vlan, current_vlan)
            if change:
                result.vlan_changes.append(change)

        # Calculate port changes
        for port_name, desired_port in desired.ports.items():
            current_port = current_port_map.get(port_name)
            change = self._diff_port(port_name, desired_port, current_port)
            if change:
                result.port_changes.append(change)

        return result

    def _diff_vlan(
        self,
        vlan_id: int,
        desired: VLANDesiredState,
        current: Optional[VLANConfig]
    ) -> Optional[VLANChange]:
        """
        Calculate changes needed for a single VLAN.

        Returns None if no changes needed.
        """
        # Handle deletion
        if desired.action == VLANAction.ABSENT:
            if current:
                return VLANChange(
                    vlan_id=vlan_id,
                    change_type=ChangeType.DELETE,
                    current_name=current.name,
                )
            else:
                # VLAN doesn't exist, nothing to delete
                return None

        # Handle create/ensure
        if not current:
            # VLAN doesn't exist, create it
            return VLANChange(
                vlan_id=vlan_id,
                change_type=ChangeType.CREATE,
                desired_name=desired.name,
                ports_to_add_untagged=desired.untagged_ports.copy(),
                ports_to_add_tagged=desired.tagged_ports.copy(),
            )

        # VLAN exists, check for modifications
        change = VLANChange(
            vlan_id=vlan_id,
            change_type=ChangeType.NO_CHANGE,
            current_name=current.name,
            desired_name=desired.name,
        )

        # Compare untagged ports
        current_untagged = set(current.untagged_ports)
        desired_untagged = set(desired.untagged_ports)

        change.ports_to_add_untagged = list(desired_untagged - current_untagged)
        change.ports_to_remove_untagged = list(current_untagged - desired_untagged)

        # Compare tagged ports
        current_tagged = set(current.tagged_ports)
        desired_tagged = set(desired.tagged_ports)

        change.ports_to_add_tagged = list(desired_tagged - current_tagged)
        change.ports_to_remove_tagged = list(current_tagged - desired_tagged)

        # Check if any changes needed
        has_changes = (
            change.ports_to_add_untagged or
            change.ports_to_remove_untagged or
            change.ports_to_add_tagged or
            change.ports_to_remove_tagged or
            (desired.name and desired.name != current.name)
        )

        if has_changes:
            change.change_type = ChangeType.MODIFY
            return change

        return None  # No changes needed

    def _diff_port(
        self,
        port_name: str,
        desired: PortDesiredState,
        current: Optional[PortConfig]
    ) -> Optional[PortChange]:
        """
        Calculate changes needed for a single port.

        Returns None if no changes needed.
        """
        change = PortChange(
            port_name=port_name,
            change_type=ChangeType.NO_CHANGE,
        )

        has_changes = False

        # Check enabled state
        if desired.enabled is not None:
            current_enabled = current.enabled if current else True
            if desired.enabled != current_enabled:
                change.enabled = desired.enabled
                has_changes = True

        # Check description
        if desired.description is not None:
            current_desc = current.description if current else ""
            if desired.description != current_desc:
                change.description = desired.description
                has_changes = True

        # Check speed
        if desired.speed is not None:
            current_speed = current.speed if current else "auto"
            if desired.speed != current_speed:
                change.speed = desired.speed
                has_changes = True

        if has_changes:
            change.change_type = ChangeType.MODIFY
            return change

        return None


def summarize_diff(diff: DiffResult) -> str:
    """
    Create a human-readable summary of a diff.

    Useful for dry-run output and logging.
    """
    lines = []

    if diff.no_change:
        return "No changes needed - current state matches desired state"

    lines.append(f"Changes to apply ({diff.total_changes} total):")
    lines.append("")

    # VLAN changes
    for change in diff.vlan_changes:
        if change.change_type == ChangeType.CREATE:
            lines.append(f"  [+] Create VLAN {change.vlan_id}")
            if change.desired_name:
                lines.append(f"      Name: {change.desired_name}")
            if change.ports_to_add_untagged:
                lines.append(f"      Untagged: {', '.join(change.ports_to_add_untagged)}")
            if change.ports_to_add_tagged:
                lines.append(f"      Tagged: {', '.join(change.ports_to_add_tagged)}")

        elif change.change_type == ChangeType.DELETE:
            lines.append(f"  [-] Delete VLAN {change.vlan_id}")
            if change.current_name:
                lines.append(f"      (was: {change.current_name})")

        elif change.change_type == ChangeType.MODIFY:
            lines.append(f"  [~] Modify VLAN {change.vlan_id}")
            if change.ports_to_add_untagged:
                lines.append(f"      Add untagged: {', '.join(change.ports_to_add_untagged)}")
            if change.ports_to_remove_untagged:
                lines.append(f"      Remove untagged: {', '.join(change.ports_to_remove_untagged)}")
            if change.ports_to_add_tagged:
                lines.append(f"      Add tagged: {', '.join(change.ports_to_add_tagged)}")
            if change.ports_to_remove_tagged:
                lines.append(f"      Remove tagged: {', '.join(change.ports_to_remove_tagged)}")

    # Port changes
    for change in diff.port_changes:
        lines.append(f"  [~] Configure port {change.port_name}")
        if change.enabled is not None:
            lines.append(f"      Enabled: {change.enabled}")
        if change.description:
            lines.append(f"      Description: {change.description}")
        if change.speed:
            lines.append(f"      Speed: {change.speed}")

    return "\n".join(lines)
