"""Normalized configuration schema for cross-device consistency."""
from dataclasses import dataclass, field, asdict
from typing import Optional
import json


@dataclass
class NormalizedVLAN:
    """Normalized VLAN representation across all switch types."""
    id: int
    name: str = ""
    description: str = ""
    tagged_ports: list[str] = field(default_factory=list)
    untagged_ports: list[str] = field(default_factory=list)
    # L3 info (if applicable)
    ip_address: Optional[str] = None
    ip_mask: Optional[str] = None
    gateway: Optional[str] = None


@dataclass
class NormalizedPort:
    """Normalized port representation."""
    # Canonical name (e.g., "1", "1/1/1", "port1" -> normalized to "1")
    id: str
    # Original device-specific name
    original_name: str = ""
    enabled: bool = True
    link_up: bool = False
    speed: Optional[str] = None  # "100M", "1G", "10G", "auto"
    duplex: Optional[str] = None  # "full", "half", "auto"
    # VLAN mode
    mode: str = "access"  # "access", "trunk", "hybrid"
    access_vlan: Optional[int] = None
    native_vlan: Optional[int] = None
    allowed_vlans: list[int] = field(default_factory=list)
    # PoE (if supported)
    poe_enabled: Optional[bool] = None
    poe_power_mw: Optional[int] = None
    # Description
    description: str = ""


@dataclass
class NetworkConfig:
    """Complete normalized network configuration."""
    device_id: str
    device_type: str
    device_name: str
    vlans: list[NormalizedVLAN] = field(default_factory=list)
    ports: list[NormalizedPort] = field(default_factory=list)
    # Raw config for reference
    raw_config: str = ""
    # Metadata
    retrieved_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "NetworkConfig":
        vlans = [NormalizedVLAN(**v) for v in data.pop("vlans", [])]
        ports = [NormalizedPort(**p) for p in data.pop("ports", [])]
        return cls(vlans=vlans, ports=ports, **data)


def normalize_port_name(name: str, device_type: str) -> str:
    """Normalize port names across different devices.

    Brocade: 1/1/1 -> "1-1-1"
    ONTI: port0 -> "0"
    Zyxel: 1 -> "1"
    """
    import re

    # Remove common prefixes
    name = re.sub(r"^(port|eth|ethernet|ge|gi|fa)\s*", "", name, flags=re.I)

    # Normalize Brocade format
    if "/" in name:
        parts = name.split("/")
        return "-".join(parts)

    # Just digits
    if name.isdigit():
        return name

    # Extract digits
    match = re.search(r"(\d+)", name)
    if match:
        return match.group(1)

    return name


def normalize_config(
    device_id: str,
    device_type: str,
    device_name: str,
    vlans: list,
    ports: list,
    raw_config: str = "",
) -> NetworkConfig:
    """Create a normalized NetworkConfig from device-specific data."""
    from datetime import datetime

    normalized_vlans = []
    for v in vlans:
        if hasattr(v, "id"):
            normalized_vlans.append(NormalizedVLAN(
                id=v.id,
                name=v.name,
                description=getattr(v, "description", ""),
                tagged_ports=[normalize_port_name(p, device_type) for p in v.tagged_ports],
                untagged_ports=[normalize_port_name(p, device_type) for p in v.untagged_ports],
                ip_address=getattr(v, "ip_address", None),
                ip_mask=getattr(v, "ip_mask", None),
            ))
        elif isinstance(v, dict):
            normalized_vlans.append(NormalizedVLAN(**v))

    normalized_ports = []
    for p in ports:
        if hasattr(p, "name"):
            normalized_ports.append(NormalizedPort(
                id=normalize_port_name(p.name, device_type),
                original_name=p.name,
                enabled=p.enabled,
                speed=getattr(p, "speed", None),
                duplex=getattr(p, "duplex", None),
                mode=getattr(p, "vlan_mode", "access"),
                access_vlan=getattr(p, "native_vlan", None),
                native_vlan=getattr(p, "native_vlan", None),
                allowed_vlans=getattr(p, "allowed_vlans", []),
                poe_enabled=getattr(p, "poe_enabled", None),
                description=getattr(p, "description", ""),
            ))
        elif isinstance(p, dict):
            normalized_ports.append(NormalizedPort(**p))

    return NetworkConfig(
        device_id=device_id,
        device_type=device_type,
        device_name=device_name,
        vlans=normalized_vlans,
        ports=normalized_ports,
        raw_config=raw_config,
        retrieved_at=datetime.now().isoformat(),
    )


@dataclass
class ConfigDiff:
    """Difference between two configurations."""
    device_id: str
    changes: list[dict] = field(default_factory=list)

    def add_change(self, change_type: str, item_type: str, item_id: str, details: dict):
        self.changes.append({
            "type": change_type,  # "added", "removed", "modified"
            "item_type": item_type,  # "vlan", "port"
            "item_id": item_id,
            "details": details,
        })

    def has_changes(self) -> bool:
        return len(self.changes) > 0

    def to_text(self) -> str:
        if not self.changes:
            return "No changes detected"

        lines = [f"Configuration diff for {self.device_id}:"]
        for change in self.changes:
            prefix = {"added": "+", "removed": "-", "modified": "~"}.get(change["type"], "?")
            lines.append(f"  {prefix} {change['item_type']} {change['item_id']}: {change['details']}")
        return "\n".join(lines)


def diff_configs(expected: NetworkConfig, actual: NetworkConfig) -> ConfigDiff:
    """Compare expected vs actual configuration."""
    diff = ConfigDiff(device_id=actual.device_id)

    # Compare VLANs
    expected_vlans = {v.id: v for v in expected.vlans}
    actual_vlans = {v.id: v for v in actual.vlans}

    for vlan_id, exp_vlan in expected_vlans.items():
        if vlan_id not in actual_vlans:
            diff.add_change("removed", "vlan", str(vlan_id), {"expected": exp_vlan.name})
        else:
            act_vlan = actual_vlans[vlan_id]
            if set(exp_vlan.tagged_ports) != set(act_vlan.tagged_ports):
                diff.add_change("modified", "vlan", str(vlan_id), {
                    "field": "tagged_ports",
                    "expected": exp_vlan.tagged_ports,
                    "actual": act_vlan.tagged_ports,
                })
            if set(exp_vlan.untagged_ports) != set(act_vlan.untagged_ports):
                diff.add_change("modified", "vlan", str(vlan_id), {
                    "field": "untagged_ports",
                    "expected": exp_vlan.untagged_ports,
                    "actual": act_vlan.untagged_ports,
                })

    for vlan_id in actual_vlans:
        if vlan_id not in expected_vlans:
            diff.add_change("added", "vlan", str(vlan_id), {"actual": actual_vlans[vlan_id].name})

    # Compare ports
    expected_ports = {p.id: p for p in expected.ports}
    actual_ports = {p.id: p for p in actual.ports}

    for port_id, exp_port in expected_ports.items():
        if port_id in actual_ports:
            act_port = actual_ports[port_id]
            if exp_port.enabled != act_port.enabled:
                diff.add_change("modified", "port", port_id, {
                    "field": "enabled",
                    "expected": exp_port.enabled,
                    "actual": act_port.enabled,
                })

    return diff
