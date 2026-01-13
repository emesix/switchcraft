#!/usr/bin/env python3
"""HIL test CLI runner.

Usage:
    python -m mcp_network_switch.hil.cli [--spec SPEC] [--devices DEVICES] [--artifacts DIR]

Environment variables:
    SWITCHCRAFT_HIL_MODE=1      Enable HIL mode constraints
    SWITCHCRAFT_HIL_VLAN=999    Override test VLAN (default: 999)
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .mode import HILConfig
from .runner import HILRunner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_default_paths() -> tuple[Path, Path, Path]:
    """Get default paths relative to project root."""
    # Find project root (contains pyproject.toml or src/)
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists() or (parent / "src").exists():
            project_root = parent
            break
    else:
        project_root = Path.cwd()

    spec_path = project_root / "tests" / "hil_spec.yaml"
    lab_devices_path = project_root / "configs" / "devices.lab.yaml"
    artifacts_base = project_root / "artifacts"

    return spec_path, lab_devices_path, artifacts_base


def main() -> int:
    """Main entry point for HIL CLI."""
    spec_default, devices_default, artifacts_default = get_default_paths()

    parser = argparse.ArgumentParser(
        description="Run HIL (Hardware-in-the-Loop) tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run with defaults
    python -m mcp_network_switch.hil.cli

    # Run with custom spec
    python -m mcp_network_switch.hil.cli --spec custom_spec.yaml

    # Run single device
    python -m mcp_network_switch.hil.cli --device lab-brocade

Environment:
    NETWORK_PASSWORD    Device credentials
    SWITCHCRAFT_HIL_MODE=1    Enable HIL constraints (auto-enabled)
""",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=spec_default,
        help=f"HIL spec file (default: {spec_default})",
    )
    parser.add_argument(
        "--devices",
        type=Path,
        default=devices_default,
        help=f"Lab devices file (default: {devices_default})",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=artifacts_default,
        help=f"Artifacts output directory (default: {artifacts_default})",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Run only specified device (e.g., lab-brocade)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate paths
    if not args.spec.exists():
        logger.error(f"HIL spec file not found: {args.spec}")
        return 1

    if not args.devices.exists():
        logger.error(f"Lab devices file not found: {args.devices}")
        return 1

    # Load configuration
    logger.info("=" * 60)
    logger.info("HIL (Hardware-in-the-Loop) Test Runner")
    logger.info("=" * 60)
    logger.info(f"Spec file: {args.spec}")
    logger.info(f"Devices file: {args.devices}")
    logger.info(f"Artifacts dir: {args.artifacts}")

    config = HILConfig.from_spec_file(args.spec)

    # Filter to single device if specified
    if args.device:
        if args.device not in config.device_specs:
            logger.error(f"Unknown device: {args.device}")
            logger.error(f"Available devices: {list(config.device_specs.keys())}")
            return 1
        config.device_specs = {args.device: config.device_specs[args.device]}

    logger.info(f"Test VLAN: {config.vlan_id}")
    logger.info(f"Devices: {list(config.device_specs.keys())}")
    logger.info("=" * 60)

    # Run tests
    runner = HILRunner(config, args.devices, args.artifacts)

    try:
        result = asyncio.run(runner.run_all())
    except KeyboardInterrupt:
        logger.warning("HIL test interrupted by user")
        return 130
    except Exception as e:
        logger.exception(f"HIL test failed with exception: {e}")
        return 1

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("HIL TEST RESULTS")
    logger.info("=" * 60)

    for device_result in result.devices:
        status = "PASS" if device_result.success else "FAIL"
        logger.info(f"  {device_result.device_id}: {status}")
        for stage in device_result.stages:
            stage_status = "OK" if stage.success else "FAIL"
            logger.info(f"    {stage.stage.value}: {stage_status} ({stage.duration_ms:.0f}ms)")
            if stage.error:
                logger.info(f"      Error: {stage.error}")

    logger.info("")
    summary = result.to_dict()["summary"]
    logger.info(f"Total: {summary['total_devices']} devices")
    logger.info(f"Passed: {summary['passed']}")
    logger.info(f"Failed: {summary['failed']}")
    logger.info(f"Artifacts: {result.artifacts_dir}")
    logger.info("=" * 60)

    if result.success:
        logger.info("HIL TESTS PASSED")
        return 0
    else:
        logger.error("HIL TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
