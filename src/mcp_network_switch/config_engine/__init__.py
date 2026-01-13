"""Config Engine - Declarative network configuration management.

The Config Engine enables declarative configuration of network devices:
- Send desired state, not individual commands
- Automatic validation and diff calculation
- Optimized command batching
- Error detection and reporting

Usage:
    from mcp_network_switch.config_engine import ConfigEngine

    engine = ConfigEngine(inventory)
    result = await engine.apply_config({
        "device": "brocade-core",
        "vlans": {
            100: {
                "name": "Production",
                "untagged_ports": ["1/1/1", "1/1/2", "1/2/1"]
            }
        }
    }, dry_run=True)
"""

from .engine import ConfigEngine
from .schema import (
    DesiredState,
    VLANDesiredState,
    PortDesiredState,
    VLANAction,
    ValidationResult,
    DiffResult,
    VLANChange,
    PortChange,
    ChangeType,
    CommandPlan,
    ExecuteOptions,
    ExecuteResult,
)
from .parser import ConfigParser, ParseError, compute_checksum
from .validator import ConfigValidator
from .diff import DiffEngine, summarize_diff
from .generator import CommandGenerator
from .executor import ConfigExecutor

__all__ = [
    # Main engine
    "ConfigEngine",
    # Schema classes
    "DesiredState",
    "VLANDesiredState",
    "PortDesiredState",
    "VLANAction",
    "ValidationResult",
    "DiffResult",
    "VLANChange",
    "PortChange",
    "ChangeType",
    "CommandPlan",
    "ExecuteOptions",
    "ExecuteResult",
    # Parser
    "ConfigParser",
    "ParseError",
    "compute_checksum",
    # Components (for advanced use)
    "ConfigValidator",
    "DiffEngine",
    "summarize_diff",
    "CommandGenerator",
    "ConfigExecutor",
]
