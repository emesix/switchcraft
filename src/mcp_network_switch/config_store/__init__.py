"""Configuration Store package for managing desired state configurations.

This package provides:
- ConfigStore: Main class for reading/writing device configurations
- StoredConfig: A stored configuration with metadata
- DriftReport/DriftItem: Drift detection between desired and actual state

Directory structure managed:
    ~/.switchcraft/
    ├── configs/
    │   ├── desired/          # Current desired state per device
    │   ├── profiles/         # Named configuration profiles
    │   ├── network/          # Network-wide definitions
    │   └── snapshots/        # Point-in-time snapshots
    └── state/
        ├── last_known/       # Last fetched actual state
        └── drift_reports/    # Drift detection reports
"""

from .store import (
    ConfigStore,
    StoredConfig,
    DriftReport,
    DriftItem,
    DEFAULT_CONFIG_DIR,
)
from .git_manager import GitManager, CommitInfo, GitError

__all__ = [
    "ConfigStore",
    "StoredConfig",
    "DriftReport",
    "DriftItem",
    "DEFAULT_CONFIG_DIR",
    "GitManager",
    "CommitInfo",
    "GitError",
]
