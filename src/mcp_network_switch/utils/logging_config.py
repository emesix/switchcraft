"""Logging configuration for Switchcraft MCP server.

Provides configurable logging with:
- File-based logging with rotation
- Console output for real-time debugging
- Performance timing decorators for efficiency analysis
- Structured context (device_id, operation type)

Environment Variables:
    SWITCHCRAFT_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: INFO)
    SWITCHCRAFT_LOG_FILE: Path to log file (default: ~/.switchcraft/switchcraft.log)
    SWITCHCRAFT_LOG_MAX_SIZE: Max log file size in MB (default: 10)
    SWITCHCRAFT_LOG_BACKUPS: Number of backup files to keep (default: 5)

Usage:
    from mcp_network_switch.utils.logging_config import setup_logging, timed

    setup_logging()  # Call once at startup

    @timed("connect")
    async def connect(self):
        ...

    # Or use context manager for sections:
    async with timed_section("batch_execute", device_id="brocade-core"):
        ...
"""
import asyncio
import functools
import logging
import os
import time
from contextlib import contextmanager, asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Callable, Any

# Performance logger - separate from main logger for easy filtering
perf_logger = logging.getLogger("switchcraft.perf")
main_logger = logging.getLogger("switchcraft")


def get_log_level() -> int:
    """Get log level from environment."""
    level_str = os.environ.get("SWITCHCRAFT_LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_str, logging.INFO)


def get_log_file() -> Path:
    """Get log file path from environment."""
    default_path = Path.home() / ".switchcraft" / "switchcraft.log"
    path_str = os.environ.get("SWITCHCRAFT_LOG_FILE", str(default_path))
    return Path(path_str)


def setup_logging() -> None:
    """Configure logging for the application.

    Sets up:
    - Console handler (INFO+ by default, respects SWITCHCRAFT_LOG_LEVEL)
    - File handler with rotation (DEBUG level - captures everything)
    - Performance logger for timing metrics
    """
    log_level = get_log_level()
    log_file = get_log_file()
    max_size_mb = int(os.environ.get("SWITCHCRAFT_LOG_MAX_SIZE", "10"))
    backup_count = int(os.environ.get("SWITCHCRAFT_LOG_BACKUPS", "5"))

    # Create log directory if needed
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Main format: timestamp - logger - level - message
    main_format = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(name)-25s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Performance format: focused on timing
    perf_format = logging.Formatter(
        "%(asctime)s.%(msecs)03d | PERF | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler - respects configured level
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(main_format)

    # File handler - captures DEBUG and above (everything)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(main_format)

    # Performance file handler - separate file for easy analysis
    perf_log_file = log_file.parent / "switchcraft-perf.log"
    perf_handler = RotatingFileHandler(
        perf_log_file,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding="utf-8"
    )
    perf_handler.setLevel(logging.DEBUG)
    perf_handler.setFormatter(perf_format)

    # Configure root switchcraft logger
    root_logger = logging.getLogger("switchcraft")
    root_logger.setLevel(logging.DEBUG)  # Capture all, handlers filter
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Configure mcp_network_switch logger to use switchcraft
    mcp_logger = logging.getLogger("mcp_network_switch")
    mcp_logger.setLevel(logging.DEBUG)
    mcp_logger.addHandler(console_handler)
    mcp_logger.addHandler(file_handler)

    # Configure performance logger
    perf_logger.setLevel(logging.DEBUG)
    perf_logger.addHandler(perf_handler)
    perf_logger.addHandler(console_handler)  # Also show in console

    # Log startup
    root_logger.info(f"Logging initialized: level={logging.getLevelName(log_level)}, file={log_file}")
    perf_logger.info(f"Performance logging to: {perf_log_file}")


def timed(operation: str, device_id: Optional[str] = None):
    """Decorator to log execution time of sync/async functions.

    Args:
        operation: Name of the operation (e.g., "connect", "execute", "batch")
        device_id: Optional device identifier (can also be inferred from self.device_id)

    Usage:
        @timed("connect")
        async def connect(self):
            ...

        @timed("health_check", device_id="brocade-core")
        def check():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            # Try to get device_id from self if not provided
            dev_id = device_id
            if dev_id is None and args and hasattr(args[0], 'device_id'):
                dev_id = args[0].device_id

            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                elapsed = (time.perf_counter() - start) * 1000  # ms
                perf_logger.info(
                    f"{operation:20s} | {dev_id or 'N/A':15s} | {elapsed:8.2f}ms | OK"
                )
                return result
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                perf_logger.warning(
                    f"{operation:20s} | {dev_id or 'N/A':15s} | {elapsed:8.2f}ms | FAIL: {e}"
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            dev_id = device_id
            if dev_id is None and args and hasattr(args[0], 'device_id'):
                dev_id = args[0].device_id

            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = (time.perf_counter() - start) * 1000
                perf_logger.info(
                    f"{operation:20s} | {dev_id or 'N/A':15s} | {elapsed:8.2f}ms | OK"
                )
                return result
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                perf_logger.warning(
                    f"{operation:20s} | {dev_id or 'N/A':15s} | {elapsed:8.2f}ms | FAIL: {e}"
                )
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


@asynccontextmanager
async def timed_section(operation: str, device_id: Optional[str] = None, **extra):
    """Async context manager for timing code sections.

    Args:
        operation: Name of the operation
        device_id: Device identifier
        **extra: Additional context to log

    Usage:
        async with timed_section("vlan_create", device_id="brocade-core", vlan_id=100):
            await device.create_vlan(...)
    """
    start = time.perf_counter()
    extra_str = " | ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""

    try:
        yield
        elapsed = (time.perf_counter() - start) * 1000
        msg = f"{operation:20s} | {device_id or 'N/A':15s} | {elapsed:8.2f}ms | OK"
        if extra_str:
            msg += f" | {extra_str}"
        perf_logger.info(msg)
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        msg = f"{operation:20s} | {device_id or 'N/A':15s} | {elapsed:8.2f}ms | FAIL: {e}"
        if extra_str:
            msg += f" | {extra_str}"
        perf_logger.warning(msg)
        raise


@contextmanager
def timed_section_sync(operation: str, device_id: Optional[str] = None, **extra):
    """Sync context manager for timing code sections."""
    start = time.perf_counter()
    extra_str = " | ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""

    try:
        yield
        elapsed = (time.perf_counter() - start) * 1000
        msg = f"{operation:20s} | {device_id or 'N/A':15s} | {elapsed:8.2f}ms | OK"
        if extra_str:
            msg += f" | {extra_str}"
        perf_logger.info(msg)
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        msg = f"{operation:20s} | {device_id or 'N/A':15s} | {elapsed:8.2f}ms | FAIL: {e}"
        if extra_str:
            msg += f" | {extra_str}"
        perf_logger.warning(msg)
        raise


class PerfStats:
    """Collect and report performance statistics.

    Usage:
        stats = PerfStats()
        stats.record("connect", 150.5)
        stats.record("connect", 145.2)
        stats.record("execute", 50.3)
        print(stats.summary())
    """

    def __init__(self):
        self._data: dict[str, list[float]] = {}

    def record(self, operation: str, duration_ms: float) -> None:
        """Record a timing measurement."""
        if operation not in self._data:
            self._data[operation] = []
        self._data[operation].append(duration_ms)

    def summary(self) -> str:
        """Generate summary statistics."""
        lines = ["Performance Summary", "=" * 60]

        for op, times in sorted(self._data.items()):
            if not times:
                continue
            count = len(times)
            total = sum(times)
            avg = total / count
            min_t = min(times)
            max_t = max(times)

            lines.append(
                f"{op:20s} | count={count:4d} | "
                f"avg={avg:8.2f}ms | min={min_t:8.2f}ms | max={max_t:8.2f}ms"
            )

        return "\n".join(lines)

    def clear(self) -> None:
        """Clear all recorded data."""
        self._data.clear()


# Global stats instance for convenience
global_stats = PerfStats()
