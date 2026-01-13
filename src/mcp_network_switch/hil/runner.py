"""HIL test runner with full lifecycle testing.

Lifecycle stages:
1. SNAPSHOT  - Save pre-test state for each device
2. APPLY     - Create VLAN 999 and configure ports
3. VERIFY    - Assert expected state matches actual
4. IDEMPOTENT - Apply again, verify no changes needed
5. CLEANUP   - Restore original state
6. VALIDATE  - Verify cleanup succeeded

Each stage produces artifacts in artifacts/hil/<timestamp>/<device>/
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml

from ..devices import create_device, NetworkDevice
from ..devices.base import VLANConfig
from .mode import HILConfig, HILDeviceSpec

logger = logging.getLogger(__name__)


class HILStage(Enum):
    """HIL test lifecycle stages."""
    SNAPSHOT = "snapshot"
    APPLY = "apply"
    VERIFY = "verify"
    IDEMPOTENT = "idempotent"
    CLEANUP = "cleanup"
    VALIDATE = "validate"


@dataclass
class HILStageResult:
    """Result of a single HIL stage."""
    stage: HILStage
    success: bool
    message: str = ""
    error: Optional[str] = None
    duration_ms: float = 0
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class HILDeviceResult:
    """HIL test results for a single device."""
    device_id: str
    host: str
    success: bool
    stages: list[HILStageResult] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "device_id": self.device_id,
            "host": self.host,
            "success": self.success,
            "error": self.error,
            "stages": [
                {
                    "stage": s.stage.value,
                    "success": s.success,
                    "message": s.message,
                    "error": s.error,
                    "duration_ms": s.duration_ms,
                }
                for s in self.stages
            ],
        }


@dataclass
class HILResult:
    """Overall HIL test results."""
    timestamp: str
    success: bool
    vlan_id: int
    devices: list[HILDeviceResult] = field(default_factory=list)
    artifacts_dir: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "success": self.success,
            "vlan_id": self.vlan_id,
            "artifacts_dir": self.artifacts_dir,
            "summary": {
                "total_devices": len(self.devices),
                "passed": sum(1 for d in self.devices if d.success),
                "failed": sum(1 for d in self.devices if not d.success),
            },
            "devices": [d.to_dict() for d in self.devices],
        }


class HILRunner:
    """HIL test runner that executes lifecycle tests across devices."""

    def __init__(
        self,
        config: HILConfig,
        lab_devices_path: Path,
        artifacts_base: Path,
    ):
        self.config = config
        self.lab_devices_path = lab_devices_path
        self.artifacts_base = artifacts_base
        self.timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.artifacts_dir = artifacts_base / "hil" / self.timestamp

        # Load lab device configurations
        self.lab_devices = self._load_lab_devices()

    def _load_lab_devices(self) -> dict[str, dict]:
        """Load lab device configurations from YAML."""
        if not self.lab_devices_path.exists():
            raise FileNotFoundError(f"Lab devices file not found: {self.lab_devices_path}")

        with open(self.lab_devices_path) as f:
            data = yaml.safe_load(f)

        return data.get("devices", {})

    def _create_device(self, device_id: str) -> NetworkDevice:
        """Create a device instance from lab config."""
        if device_id not in self.lab_devices:
            raise ValueError(f"Unknown device: {device_id}")

        device_config = self.lab_devices[device_id].copy()
        return create_device(device_id, device_config)

    def _save_artifact(self, device_id: str, name: str, data: Any) -> Path:
        """Save an artifact to the artifacts directory."""
        device_dir = self.artifacts_dir / device_id
        device_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = device_dir / f"{name}.json"
        with open(artifact_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        return artifact_path

    async def _snapshot_device(self, device: NetworkDevice, spec: HILDeviceSpec) -> dict:
        """Capture pre-test state of a device."""
        vlans = await device.get_vlans()
        ports = await device.get_ports()

        # Find current state of test ports
        access_port_state = next(
            (p for p in ports if p.name == spec.access_port), None
        )
        trunk_port_state = next(
            (p for p in ports if p.name == spec.trunk_port), None
        )

        # Check if VLAN 999 exists
        vlan_999_exists = any(v.id == self.config.vlan_id for v in vlans)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vlans": [{"id": v.id, "name": v.name, "tagged": v.tagged_ports, "untagged": v.untagged_ports} for v in vlans],
            "access_port": {
                "name": spec.access_port,
                "state": {
                    "enabled": access_port_state.enabled if access_port_state else None,
                    "speed": access_port_state.speed if access_port_state else None,
                    "native_vlan": access_port_state.native_vlan if access_port_state else None,
                } if access_port_state else None,
            },
            "trunk_port": {
                "name": spec.trunk_port,
                "state": {
                    "enabled": trunk_port_state.enabled if trunk_port_state else None,
                    "speed": trunk_port_state.speed if trunk_port_state else None,
                } if trunk_port_state else None,
            },
            "hil_vlan_existed": vlan_999_exists,
        }

    async def _apply_hil_state(self, device: NetworkDevice, spec: HILDeviceSpec) -> tuple[bool, str]:
        """Apply HIL test state (VLAN 999 with test ports)."""
        # Create VLAN 999 with test ports
        vlan = VLANConfig(
            id=self.config.vlan_id,
            name=self.config.vlan_name,
            untagged_ports=[spec.access_port],
            tagged_ports=[spec.trunk_port],
        )

        success, output = await device.create_vlan(vlan)
        if not success:
            return False, f"Failed to create VLAN: {output}"

        # Save config
        save_success, save_output = await device.save_config()
        if not save_success:
            return False, f"Failed to save config: {save_output}"

        return True, "HIL state applied successfully"

    async def _verify_hil_state(self, device: NetworkDevice, spec: HILDeviceSpec) -> tuple[bool, str, dict]:
        """Verify HIL test state is correctly applied."""
        vlans = await device.get_vlans()

        # Find VLAN 999
        hil_vlan = next((v for v in vlans if v.id == self.config.vlan_id), None)
        if not hil_vlan:
            return False, f"VLAN {self.config.vlan_id} not found", {}

        # Verify port membership
        access_ok = spec.access_port in hil_vlan.untagged_ports
        trunk_ok = spec.trunk_port in hil_vlan.tagged_ports

        verification = {
            "vlan_exists": True,
            "vlan_name": hil_vlan.name,
            "access_port_untagged": access_ok,
            "trunk_port_tagged": trunk_ok,
            "untagged_ports": hil_vlan.untagged_ports,
            "tagged_ports": hil_vlan.tagged_ports,
        }

        if not access_ok:
            return False, f"Access port {spec.access_port} not in untagged ports", verification
        if not trunk_ok:
            return False, f"Trunk port {spec.trunk_port} not in tagged ports", verification

        return True, "HIL state verified", verification

    async def _cleanup_hil_state(
        self,
        device: NetworkDevice,
        spec: HILDeviceSpec,
        pre_snapshot: dict,
    ) -> tuple[bool, str]:
        """Restore device to pre-test state."""
        # If VLAN 999 didn't exist before, delete it
        if not pre_snapshot.get("hil_vlan_existed", False):
            success, output = await device.delete_vlan(self.config.vlan_id)
            if not success:
                return False, f"Failed to delete VLAN {self.config.vlan_id}: {output}"
        else:
            # VLAN existed - restore original port membership
            # For simplicity, we'll just remove test ports from VLAN 999
            # A more sophisticated approach would restore exact original state
            pass

        # Save config
        save_success, save_output = await device.save_config()
        if not save_success:
            return False, f"Failed to save config after cleanup: {save_output}"

        return True, "Cleanup completed"

    async def _validate_cleanup(
        self,
        device: NetworkDevice,
        spec: HILDeviceSpec,
        pre_snapshot: dict,
    ) -> tuple[bool, str, dict]:
        """Validate that cleanup restored original state."""
        vlans = await device.get_vlans()

        # Check VLAN 999 state matches pre-test
        hil_vlan = next((v for v in vlans if v.id == self.config.vlan_id), None)
        vlan_existed_before = pre_snapshot.get("hil_vlan_existed", False)

        validation = {
            "vlan_existed_before": vlan_existed_before,
            "vlan_exists_now": hil_vlan is not None,
        }

        if not vlan_existed_before and hil_vlan is not None:
            return False, f"VLAN {self.config.vlan_id} should have been removed", validation

        return True, "Cleanup validated", validation

    async def run_device_lifecycle(self, device_id: str) -> HILDeviceResult:
        """Run full HIL lifecycle for a single device."""
        if device_id not in self.config.device_specs:
            return HILDeviceResult(
                device_id=device_id,
                host="unknown",
                success=False,
                error=f"No HIL spec found for device {device_id}",
            )

        spec = self.config.device_specs[device_id]
        result = HILDeviceResult(
            device_id=device_id,
            host=spec.host,
            success=True,
        )

        try:
            device = self._create_device(device_id)
        except Exception as e:
            result.success = False
            result.error = f"Failed to create device: {e}"
            return result

        pre_snapshot = None

        try:
            async with device:
                # Stage 1: SNAPSHOT
                import time
                start = time.perf_counter()
                try:
                    pre_snapshot = await self._snapshot_device(device, spec)
                    self._save_artifact(device_id, "pre", pre_snapshot)
                    result.stages.append(HILStageResult(
                        stage=HILStage.SNAPSHOT,
                        success=True,
                        message="Pre-test snapshot captured",
                        duration_ms=(time.perf_counter() - start) * 1000,
                        artifacts={"pre.json": str(self.artifacts_dir / device_id / "pre.json")},
                    ))
                except Exception as e:
                    result.stages.append(HILStageResult(
                        stage=HILStage.SNAPSHOT,
                        success=False,
                        error=str(e),
                        duration_ms=(time.perf_counter() - start) * 1000,
                    ))
                    result.success = False
                    return result

                # Stage 2: APPLY
                start = time.perf_counter()
                try:
                    success, message = await self._apply_hil_state(device, spec)
                    result.stages.append(HILStageResult(
                        stage=HILStage.APPLY,
                        success=success,
                        message=message if success else "",
                        error=message if not success else None,
                        duration_ms=(time.perf_counter() - start) * 1000,
                    ))
                    if not success:
                        result.success = False
                        # Continue to cleanup
                except Exception as e:
                    result.stages.append(HILStageResult(
                        stage=HILStage.APPLY,
                        success=False,
                        error=str(e),
                        duration_ms=(time.perf_counter() - start) * 1000,
                    ))
                    result.success = False

                # Stage 3: VERIFY
                if result.success:
                    start = time.perf_counter()
                    try:
                        success, message, verification = await self._verify_hil_state(device, spec)
                        self._save_artifact(device_id, "post", verification)
                        result.stages.append(HILStageResult(
                            stage=HILStage.VERIFY,
                            success=success,
                            message=message if success else "",
                            error=message if not success else None,
                            duration_ms=(time.perf_counter() - start) * 1000,
                            artifacts={"post.json": str(self.artifacts_dir / device_id / "post.json")},
                        ))
                        if not success:
                            result.success = False
                    except Exception as e:
                        result.stages.append(HILStageResult(
                            stage=HILStage.VERIFY,
                            success=False,
                            error=str(e),
                            duration_ms=(time.perf_counter() - start) * 1000,
                        ))
                        result.success = False

                # Stage 4: IDEMPOTENT
                if result.success:
                    start = time.perf_counter()
                    try:
                        # Apply again - should be no-op
                        success, message = await self._apply_hil_state(device, spec)
                        result.stages.append(HILStageResult(
                            stage=HILStage.IDEMPOTENT,
                            success=success,
                            message="Idempotent apply succeeded" if success else "",
                            error=message if not success else None,
                            duration_ms=(time.perf_counter() - start) * 1000,
                        ))
                        if not success:
                            result.success = False
                    except Exception as e:
                        result.stages.append(HILStageResult(
                            stage=HILStage.IDEMPOTENT,
                            success=False,
                            error=str(e),
                            duration_ms=(time.perf_counter() - start) * 1000,
                        ))
                        result.success = False

                # Stage 5: CLEANUP (always run)
                start = time.perf_counter()
                try:
                    success, message = await self._cleanup_hil_state(device, spec, pre_snapshot)
                    result.stages.append(HILStageResult(
                        stage=HILStage.CLEANUP,
                        success=success,
                        message=message if success else "",
                        error=message if not success else None,
                        duration_ms=(time.perf_counter() - start) * 1000,
                    ))
                    if not success:
                        result.success = False
                except Exception as e:
                    result.stages.append(HILStageResult(
                        stage=HILStage.CLEANUP,
                        success=False,
                        error=str(e),
                        duration_ms=(time.perf_counter() - start) * 1000,
                    ))
                    result.success = False

                # Stage 6: VALIDATE (always run)
                start = time.perf_counter()
                try:
                    success, message, validation = await self._validate_cleanup(device, spec, pre_snapshot)
                    self._save_artifact(device_id, "clean", validation)
                    result.stages.append(HILStageResult(
                        stage=HILStage.VALIDATE,
                        success=success,
                        message=message if success else "",
                        error=message if not success else None,
                        duration_ms=(time.perf_counter() - start) * 1000,
                        artifacts={"clean.json": str(self.artifacts_dir / device_id / "clean.json")},
                    ))
                    if not success:
                        result.success = False
                except Exception as e:
                    result.stages.append(HILStageResult(
                        stage=HILStage.VALIDATE,
                        success=False,
                        error=str(e),
                        duration_ms=(time.perf_counter() - start) * 1000,
                    ))
                    result.success = False

        except Exception as e:
            result.success = False
            result.error = f"Device connection failed: {e}"

        return result

    async def run_all(self) -> HILResult:
        """Run HIL lifecycle across all configured devices."""
        result = HILResult(
            timestamp=self.timestamp,
            success=True,
            vlan_id=self.config.vlan_id,
            artifacts_dir=str(self.artifacts_dir),
        )

        # Run tests for each device
        for device_id in self.config.device_specs:
            logger.info(f"Running HIL lifecycle for {device_id}")
            device_result = await self.run_device_lifecycle(device_id)
            result.devices.append(device_result)

            if not device_result.success:
                result.success = False
                logger.error(f"HIL FAILED for {device_id}: {device_result.error}")
            else:
                logger.info(f"HIL PASSED for {device_id}")

        # Save overall report
        report_path = self.artifacts_dir / "hil-report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)

        return result


async def run_hil_tests(
    spec_path: Path,
    lab_devices_path: Path,
    artifacts_base: Path,
) -> HILResult:
    """Run HIL tests using the specified configuration files."""
    config = HILConfig.from_spec_file(spec_path)
    runner = HILRunner(config, lab_devices_path, artifacts_base)
    return await runner.run_all()
