"""Main Config Engine - orchestrates the full apply_config workflow.

Provides a single entry point for:
1. Parsing desired state
2. Validating configuration
3. Calculating diff against current state
4. Generating command batches
5. Executing with error handling
"""
import logging
from typing import Any, Optional

from ..config.inventory import DeviceInventory
from .schema import (
    DesiredState,
    ValidationResult,
    DiffResult,
    ExecuteOptions,
    ExecuteResult,
)
from .parser import ConfigParser
from .validator import ConfigValidator
from .diff import DiffEngine, summarize_diff
from .generator import CommandGenerator
from .executor import ConfigExecutor

logger = logging.getLogger(__name__)


class ConfigEngine:
    """
    Main Config Engine for applying desired state configurations.

    Usage:
        engine = ConfigEngine(inventory)
        result = await engine.apply_config(config_dict, dry_run=True)
    """

    def __init__(
        self,
        inventory: DeviceInventory,
        audit_log_path: Optional[str] = None
    ):
        """
        Initialize the Config Engine.

        Args:
            inventory: Device inventory for looking up devices
            audit_log_path: Path to audit log file (optional)
        """
        self.inventory = inventory
        self.parser = ConfigParser()
        self.diff_engine = DiffEngine()
        self.generator = CommandGenerator()
        self.executor = ConfigExecutor(audit_log_path)

    async def apply_config(
        self,
        config: dict[str, Any],
        dry_run: bool = False,
        audit_context: str = "",
        user: Optional[str] = None,
    ) -> ExecuteResult:
        """
        Apply a desired state configuration to a device.

        This is the main entry point. It:
        1. Parses the config into DesiredState
        2. Validates for logical errors
        3. Calculates diff against current state
        4. Generates optimized command batches
        5. Executes (or dry-runs) the changes

        Args:
            config: Desired state configuration dict
            dry_run: If True, preview changes without applying
            audit_context: Description for audit log
            user: User identifier for audit log

        Returns:
            ExecuteResult with success/failure and details
        """
        result = ExecuteResult(dry_run=dry_run)

        # Step 1: Parse
        logger.info("Parsing desired state configuration")
        try:
            desired = self.parser.parse(config)
        except Exception as e:
            result.error = f"Parse error: {e}"
            return result

        # Step 2: Validate
        logger.info(f"Validating configuration for device {desired.device_id}")
        device_config = self.inventory.get_device_config(desired.device_id)
        device_type = device_config.get("type", "unknown")

        validator = ConfigValidator(device_type)
        validation = validator.validate(desired)

        if not validation.valid:
            result.error = f"Validation failed: {'; '.join(validation.errors)}"
            result.error_context = "\n".join(validation.errors)
            return result

        # Add warnings to result
        if validation.warnings:
            result.recovery_attempts = [
                f"Warning: {w}" for w in validation.warnings
            ]

        # Step 3: Get device and calculate diff
        logger.info("Calculating diff against current state")
        device = self.inventory.get_device(desired.device_id)

        try:
            async with device:
                diff = await self.diff_engine.calculate(device, desired)
        except Exception as e:
            result.error = f"Failed to get current state: {e}"
            result.requires_ai_intervention = True
            return result

        # Check if any changes needed
        if diff.no_change:
            result.success = True
            result.changes_made = ["No changes needed - state already matches"]
            return result

        logger.info(f"Found {diff.total_changes} changes to apply")

        # Step 4: Generate commands
        logger.info("Generating command plan")
        try:
            plan = self.generator.generate(device_type, diff)
        except Exception as e:
            result.error = f"Command generation failed: {e}"
            result.requires_ai_intervention = True
            return result

        logger.info(
            f"Generated {plan.total_commands} commands "
            f"({len(plan.pre_commands)} pre, {len(plan.main_commands)} main, "
            f"{len(plan.post_commands)} post)"
        )

        # Step 5: Execute
        options = ExecuteOptions(
            dry_run=dry_run,
            audit_context=audit_context,
            user=user,
            stop_on_error=True,
            rollback_on_error=False,  # MVP: no auto-rollback yet
        )

        logger.info(f"{'DRY RUN: ' if dry_run else ''}Executing command plan")
        result = await self.executor.execute(device, plan, diff, options)

        return result

    def parse(self, config: dict[str, Any]) -> DesiredState:
        """Parse config dict to DesiredState (for external use)."""
        return self.parser.parse(config)

    def validate(self, desired: DesiredState) -> ValidationResult:
        """Validate a DesiredState (for external use)."""
        try:
            device_config = self.inventory.get_device_config(desired.device_id)
            device_type = device_config.get("type", "unknown")
        except KeyError:
            device_type = None

        validator = ConfigValidator(device_type)
        return validator.validate(desired)

    async def diff(self, desired: DesiredState) -> DiffResult:
        """Calculate diff for a DesiredState (for external use)."""
        device = self.inventory.get_device(desired.device_id)
        async with device:
            return await self.diff_engine.calculate(device, desired)

    async def preview(self, config: dict[str, Any]) -> str:
        """
        Preview changes without applying.

        Returns human-readable diff summary.
        """
        # Parse and validate
        desired = self.parser.parse(config)

        device_config = self.inventory.get_device_config(desired.device_id)
        device_type = device_config.get("type", "unknown")

        validator = ConfigValidator(device_type)
        validation = validator.validate(desired)

        if not validation.valid:
            return "Validation failed:\n" + "\n".join(validation.errors)

        # Calculate diff
        device = self.inventory.get_device(desired.device_id)
        async with device:
            diff = await self.diff_engine.calculate(device, desired)

        # Generate summary
        summary = summarize_diff(diff)

        # Add warnings
        if validation.warnings:
            summary += "\n\nWarnings:\n" + "\n".join(
                f"  - {w}" for w in validation.warnings
            )

        return summary
