"""Connection stability utilities with retry logic and health checks."""
import asyncio
import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


# Common network exceptions to retry on
RETRYABLE_EXCEPTIONS = (
    ConnectionRefusedError,
    ConnectionResetError,
    TimeoutError,
    OSError,
    EOFError,
)


def with_retry(
    max_attempts: int = 3,
    min_wait: float = 1,
    max_wait: float = 10,
    exceptions: tuple = RETRYABLE_EXCEPTIONS,
) -> Callable:
    """Decorator factory for retry logic with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)
        exceptions: Tuple of exception types to retry on
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(exceptions),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            return await func(*args, **kwargs)  # type: ignore[misc]

        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(exceptions),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            return func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper

    return decorator


class ConnectionManager:
    """Manages device connections with health checks and reconnection logic."""

    def __init__(self, config: dict):
        self.config = config
        self._connections: dict[str, Any] = {}
        self._health_status: dict[str, bool] = {}
        self._lock = asyncio.Lock()

    async def get_connection(self, device_id: str) -> Any:
        """Get or create a connection to a device."""
        async with self._lock:
            if device_id in self._connections:
                conn = self._connections[device_id]
                if await self._check_health(device_id, conn):
                    return conn
                # Connection unhealthy, remove it
                await self._close_connection(device_id)

            # Create new connection
            conn = await self._create_connection(device_id)
            self._connections[device_id] = conn
            self._health_status[device_id] = True
            return conn

    async def _create_connection(self, device_id: str) -> Any:
        """Create a new connection to a device."""
        # This is implemented by device-specific handlers
        raise NotImplementedError("Subclass must implement _create_connection")

    async def _check_health(self, device_id: str, conn: Any) -> bool:
        """Check if a connection is still healthy."""
        # Device-specific health check
        return self._health_status.get(device_id, False)

    async def _close_connection(self, device_id: str) -> None:
        """Close and remove a connection."""
        if device_id in self._connections:
            conn = self._connections.pop(device_id)
            self._health_status.pop(device_id, None)
            try:
                if hasattr(conn, "close"):
                    if asyncio.iscoroutinefunction(conn.close):
                        await conn.close()
                    else:
                        conn.close()
            except Exception as e:
                logger.warning(f"Error closing connection to {device_id}: {e}")

    async def close_all(self) -> None:
        """Close all managed connections."""
        for device_id in list(self._connections.keys()):
            await self._close_connection(device_id)

    def mark_unhealthy(self, device_id: str) -> None:
        """Mark a connection as unhealthy for reconnection."""
        self._health_status[device_id] = False


class CommandResult:
    """Result of a command execution on a device."""

    def __init__(
        self,
        success: bool,
        output: str = "",
        error: str = "",
        device_id: str = "",
        command: str = "",
    ):
        self.success = success
        self.output = output
        self.error = error
        self.device_id = device_id
        self.command = command

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "device_id": self.device_id,
            "command": self.command,
        }

    def __repr__(self) -> str:
        status = "OK" if self.success else "FAILED"
        return f"CommandResult({status}, device={self.device_id})"
