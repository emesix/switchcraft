"""Tests for connection utilities."""
import pytest
import asyncio
from mcp_network_switch.utils.connection import (
    with_retry,
    ConnectionManager,
    CommandResult,
    RETRYABLE_EXCEPTIONS,
)


class TestWithRetry:
    """Tests for retry decorator."""

    @pytest.mark.asyncio
    async def test_async_success_no_retry(self):
        """Successful async function doesn't retry."""
        call_count = 0

        @with_retry(max_attempts=3)
        async def succeeding_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await succeeding_func()
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_retry_then_success(self):
        """Async function retries on failure then succeeds."""
        call_count = 0

        @with_retry(max_attempts=3, min_wait=0.01, max_wait=0.1)
        async def failing_then_succeeding():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionRefusedError("Connection refused")
            return "success"

        result = await failing_then_succeeding()
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_max_retries_exceeded(self):
        """Async function raises after max retries."""
        call_count = 0

        @with_retry(max_attempts=3, min_wait=0.01, max_wait=0.1)
        async def always_failing():
            nonlocal call_count
            call_count += 1
            raise TimeoutError("Always times out")

        with pytest.raises(TimeoutError):
            await always_failing()
        assert call_count == 3

    def test_sync_success_no_retry(self):
        """Successful sync function doesn't retry."""
        call_count = 0

        @with_retry(max_attempts=3)
        def succeeding_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = succeeding_func()
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_non_retryable_exception(self):
        """Non-retryable exceptions are not retried."""
        call_count = 0

        @with_retry(max_attempts=3, exceptions=(ConnectionRefusedError,))
        async def raising_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("Not retryable")

        with pytest.raises(ValueError):
            await raising_value_error()
        assert call_count == 1  # Only one attempt


class TestRetryableExceptions:
    """Tests for retryable exceptions list."""

    def test_connection_refused_is_retryable(self):
        """ConnectionRefusedError is retryable."""
        assert ConnectionRefusedError in RETRYABLE_EXCEPTIONS

    def test_timeout_is_retryable(self):
        """TimeoutError is retryable."""
        assert TimeoutError in RETRYABLE_EXCEPTIONS

    def test_connection_reset_is_retryable(self):
        """ConnectionResetError is retryable."""
        assert ConnectionResetError in RETRYABLE_EXCEPTIONS

    def test_os_error_is_retryable(self):
        """OSError is retryable."""
        assert OSError in RETRYABLE_EXCEPTIONS

    def test_eof_error_is_retryable(self):
        """EOFError is retryable."""
        assert EOFError in RETRYABLE_EXCEPTIONS


class TestCommandResult:
    """Tests for CommandResult class."""

    def test_successful_result(self):
        """Successful command result."""
        result = CommandResult(
            success=True,
            output="Command output",
            device_id="test-device",
            command="show version",
        )
        assert result.success is True
        assert result.output == "Command output"
        assert result.error == ""
        assert "OK" in repr(result)

    def test_failed_result(self):
        """Failed command result."""
        result = CommandResult(
            success=False,
            output="",
            error="Command failed",
            device_id="test-device",
            command="invalid command",
        )
        assert result.success is False
        assert result.error == "Command failed"
        assert "FAILED" in repr(result)

    def test_to_dict(self):
        """Result can be converted to dict."""
        result = CommandResult(
            success=True,
            output="test output",
            device_id="device1",
            command="test cmd",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == "test output"
        assert d["device_id"] == "device1"
        assert d["command"] == "test cmd"
