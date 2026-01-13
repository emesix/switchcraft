"""Executor for applying command plans to devices.

Handles execution with basic error detection and reporting.
Phase 2 will add auto-recovery.
"""
import logging
from datetime import datetime
from typing import Optional

from ..devices.base import NetworkDevice
from .schema import (
    CommandPlan,
    ExecuteOptions,
    ExecuteResult,
    AuditEntry,
)
from .diff import DiffResult

logger = logging.getLogger(__name__)


class ConfigExecutor:
    """Execute command plans on network devices."""

    def __init__(self, audit_log_path: Optional[str] = None):
        """
        Initialize executor.

        Args:
            audit_log_path: Path to audit log file (optional)
        """
        self.audit_log_path = audit_log_path

    async def execute(
        self,
        device: NetworkDevice,
        plan: CommandPlan,
        diff: DiffResult,
        options: ExecuteOptions
    ) -> ExecuteResult:
        """
        Execute a command plan on a device.

        Args:
            device: Connected network device
            plan: Command plan to execute
            diff: Original diff (for reporting)
            options: Execution options (dry_run, etc.)

        Returns:
            ExecuteResult with success/failure and details
        """
        result = ExecuteResult(dry_run=options.dry_run)

        # Create audit entry
        audit_entry = AuditEntry(
            timestamp=datetime.utcnow(),
            device_id=device.device_id,
            operation="apply_config",
            context=options.audit_context,
            user=options.user or "system",
        )

        try:
            # DRY RUN MODE
            if options.dry_run:
                return self._dry_run(plan, diff, result)

            # Connect to device
            async with device:
                # Execute pre-commands (one by one for safety)
                if plan.pre_commands:
                    logger.info(f"Executing {len(plan.pre_commands)} pre-commands")
                    success, output = await self._execute_commands(
                        device, plan.pre_commands, "pre"
                    )
                    result.commands_executed.extend(plan.pre_commands)
                    if not success:
                        result.success = False
                        result.error = f"Pre-command failed: {output}"
                        result.error_context = output
                        return result

                # Execute main commands as batch (faster)
                if plan.main_commands:
                    logger.info(f"Executing {len(plan.main_commands)} main commands")
                    success, output = await device.execute_config_batch(
                        plan.main_commands,
                        stop_on_error=options.stop_on_error
                    )
                    result.commands_executed.extend(plan.main_commands)

                    if not success:
                        result.success = False
                        result.error = "Main command batch failed"
                        result.error_context = output
                        result.requires_ai_intervention = True

                        # Attempt rollback if requested
                        if options.rollback_on_error and plan.rollback_commands:
                            await self._attempt_rollback(device, plan, result)

                        return result

                # Execute post-commands (save config, etc.)
                if plan.post_commands:
                    logger.info(f"Executing {len(plan.post_commands)} post-commands")
                    success, output = await self._execute_commands(
                        device, plan.post_commands, "post"
                    )
                    result.commands_executed.extend(plan.post_commands)

                    if not success:
                        # Post-command failure is not critical
                        logger.warning(f"Post-command failed: {output}")

                # Build changes_made list from diff
                result.changes_made = self._extract_changes(diff)
                result.success = True

        except Exception as e:
            logger.exception(f"Execution failed: {e}")
            result.success = False
            result.error = str(e)
            result.requires_ai_intervention = True

        finally:
            # Write audit log
            audit_entry.success = result.success
            audit_entry.changes = result.changes_made
            audit_entry.error = result.error
            await self._write_audit(audit_entry)

        return result

    def _dry_run(
        self,
        plan: CommandPlan,
        diff: DiffResult,
        result: ExecuteResult
    ) -> ExecuteResult:
        """Handle dry-run mode - preview without executing."""
        result.success = True
        result.dry_run = True

        # Show what would be executed
        all_commands = (
            plan.pre_commands +
            plan.main_commands +
            plan.post_commands
        )

        result.commands_executed = [
            f"[DRY-RUN] {cmd}" for cmd in all_commands
        ]

        # Extract changes that would be made
        result.changes_made = [
            f"[PREVIEW] {change}"
            for change in self._extract_changes(diff)
        ]

        return result

    async def _execute_commands(
        self,
        device: NetworkDevice,
        commands: list[str],
        phase: str
    ) -> tuple[bool, str]:
        """Execute a list of commands individually."""
        outputs = []

        for cmd in commands:
            try:
                success, output = await device.execute(cmd)
                outputs.append(output)
                if not success:
                    return False, f"{phase} command '{cmd}' failed: {output}"
            except Exception as e:
                return False, f"{phase} command '{cmd}' raised: {e}"

        return True, "\n".join(outputs)

    async def _attempt_rollback(
        self,
        device: NetworkDevice,
        plan: CommandPlan,
        result: ExecuteResult
    ) -> None:
        """Attempt to rollback changes after failure."""
        logger.warning("Attempting rollback after failure")

        if not plan.rollback_commands:
            logger.warning("No rollback commands available")
            return

        try:
            success, output = await device.execute_config_batch(
                plan.rollback_commands,
                stop_on_error=False  # Try all rollback commands
            )

            if success:
                result.rollback_performed = True
                result.recovery_attempts.append("Rollback successful")
                logger.info("Rollback completed successfully")
            else:
                result.recovery_attempts.append(f"Rollback failed: {output}")
                logger.error(f"Rollback failed: {output}")

        except Exception as e:
            result.recovery_attempts.append(f"Rollback exception: {e}")
            logger.exception(f"Rollback failed with exception: {e}")

    def _extract_changes(self, diff: DiffResult) -> list[str]:
        """Extract human-readable change descriptions from diff."""
        changes = []

        for change in diff.vlan_changes:
            if change.change_type.value == "create":
                changes.append(f"Created VLAN {change.vlan_id}")
            elif change.change_type.value == "delete":
                changes.append(f"Deleted VLAN {change.vlan_id}")
            elif change.change_type.value == "modify":
                parts = []
                if change.ports_to_add_untagged:
                    parts.append(f"added untagged: {', '.join(change.ports_to_add_untagged)}")
                if change.ports_to_remove_untagged:
                    parts.append(f"removed untagged: {', '.join(change.ports_to_remove_untagged)}")
                if change.ports_to_add_tagged:
                    parts.append(f"added tagged: {', '.join(change.ports_to_add_tagged)}")
                if change.ports_to_remove_tagged:
                    parts.append(f"removed tagged: {', '.join(change.ports_to_remove_tagged)}")
                changes.append(f"Modified VLAN {change.vlan_id}: {'; '.join(parts)}")

        for change in diff.port_changes:
            parts = []
            if change.enabled is not None:
                parts.append(f"enabled={change.enabled}")
            if change.description:
                parts.append(f"description={change.description}")
            if change.speed:
                parts.append(f"speed={change.speed}")
            changes.append(f"Configured port {change.port_name}: {', '.join(parts)}")

        return changes

    async def _write_audit(self, entry: AuditEntry) -> None:
        """Write audit entry to log file."""
        if not self.audit_log_path:
            return

        try:
            import json
            from pathlib import Path

            log_path = Path(self.audit_log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            log_entry = {
                "timestamp": entry.timestamp.isoformat(),
                "device_id": entry.device_id,
                "operation": entry.operation,
                "context": entry.context,
                "user": entry.user,
                "success": entry.success,
                "changes": entry.changes,
                "error": entry.error,
            }

            with open(log_path, "a") as f:
                f.write(json.dumps(log_entry) + "\n")

        except Exception as e:
            logger.warning(f"Failed to write audit log: {e}")
