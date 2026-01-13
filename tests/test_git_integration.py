"""Tests for Git integration in the Configuration Store."""
import pytest
from pathlib import Path
from datetime import datetime, timezone

from mcp_network_switch.config_store import (
    ConfigStore,
    GitManager,
    CommitInfo,
    GitError,
)


class TestGitManager:
    """Tests for GitManager."""

    @pytest.fixture
    def temp_repo(self, tmp_path):
        """Create a GitManager with a temporary directory."""
        return GitManager(tmp_path)

    def test_init_creates_repo(self, temp_repo):
        """Test git init creates a .git directory."""
        result = temp_repo.init()

        assert result is True  # Newly initialized
        assert temp_repo.is_initialized()
        assert (temp_repo.repo_path / ".git").exists()

    def test_init_idempotent(self, temp_repo):
        """Test git init is idempotent."""
        temp_repo.init()
        result = temp_repo.init()

        assert result is False  # Already initialized
        assert temp_repo.is_initialized()

    def test_commit_no_changes(self, temp_repo):
        """Test commit with no changes returns None."""
        temp_repo.init()

        result = temp_repo.commit("Empty commit")

        assert result is None  # Nothing to commit

    def test_commit_with_file(self, temp_repo):
        """Test commit with a file change."""
        temp_repo.init()

        # Create a file
        test_file = temp_repo.repo_path / "test.txt"
        test_file.write_text("Hello, World!")

        result = temp_repo.commit("Add test file")

        assert result is not None
        assert len(result) == 40  # Full SHA

    def test_get_history(self, temp_repo):
        """Test getting commit history."""
        temp_repo.init()

        # Create multiple commits
        for i in range(3):
            test_file = temp_repo.repo_path / f"file{i}.txt"
            test_file.write_text(f"Content {i}")
            temp_repo.commit(f"Add file {i}")

        history = temp_repo.get_history(limit=10)

        # Should have initial commit + 3 file commits
        assert len(history) >= 3
        assert all(isinstance(c, CommitInfo) for c in history)

    def test_get_file_at_revision(self, temp_repo):
        """Test retrieving file at specific revision."""
        temp_repo.init()

        test_file = temp_repo.repo_path / "config.yaml"

        # First version
        test_file.write_text("version: 1\nname: first")
        temp_repo.commit("Version 1")

        # Second version
        test_file.write_text("version: 2\nname: second")
        temp_repo.commit("Version 2")

        # Get first version
        content = temp_repo.get_file_at_revision("config.yaml", "HEAD~1")

        assert "version: 1" in content
        assert "first" in content

    def test_restore_file(self, temp_repo):
        """Test restoring file from revision."""
        temp_repo.init()

        test_file = temp_repo.repo_path / "data.txt"

        # First version
        test_file.write_text("Original content")
        temp_repo.commit("Original")

        # Modified
        test_file.write_text("Modified content")
        temp_repo.commit("Modified")

        assert "Modified" in test_file.read_text()

        # Restore
        result = temp_repo.restore_file("data.txt", "HEAD~1")

        assert result is True
        assert "Original" in test_file.read_text()

    def test_diff(self, temp_repo):
        """Test getting diff between revisions."""
        temp_repo.init()

        test_file = temp_repo.repo_path / "diff_test.txt"
        test_file.write_text("line1\nline2\n")
        temp_repo.commit("Initial")

        test_file.write_text("line1\nline2\nline3\n")
        temp_repo.commit("Added line3")

        diff = temp_repo.diff(revision1="HEAD~1", revision2="HEAD")

        assert "+line3" in diff

    def test_tag(self, temp_repo):
        """Test creating tags."""
        temp_repo.init()

        test_file = temp_repo.repo_path / "tagged.txt"
        test_file.write_text("content")
        temp_repo.commit("Initial commit")

        result = temp_repo.tag("v1.0", message="Version 1.0")

        assert result is True
        assert "v1.0" in temp_repo.list_tags()


class TestConfigStoreGitIntegration:
    """Tests for ConfigStore with git enabled."""

    @pytest.fixture
    def git_store(self, tmp_path):
        """Create a ConfigStore with git enabled."""
        return ConfigStore(base_dir=tmp_path, git_enabled=True)

    @pytest.fixture
    def no_git_store(self, tmp_path):
        """Create a ConfigStore with git disabled."""
        return ConfigStore(base_dir=tmp_path, git_enabled=False)

    def test_git_auto_init(self, git_store):
        """Test git is auto-initialized."""
        # Access git property to trigger init
        _ = git_store.git

        assert git_store.git.is_initialized()
        assert (git_store.configs_dir / ".git").exists()

    def test_git_disabled(self, no_git_store):
        """Test git can be disabled."""
        assert no_git_store.git is None

        # Save should work without git
        stored = no_git_store.save_desired_config(
            "test-device",
            {"vlans": {100: {"name": "Test"}}}
        )

        assert stored.version == 1

    def test_save_creates_commit(self, git_store):
        """Test saving config creates a git commit."""
        # Get history before
        history_before = git_store.get_config_history()
        count_before = len(history_before)

        # Save first config
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "First"}}}
        )

        # Get history after
        history_after = git_store.get_config_history()

        # Should have one more commit
        assert len(history_after) == count_before + 1

    def test_multiple_saves_create_history(self, git_store):
        """Test multiple saves create commit history."""
        # Make several changes
        for i in range(3):
            git_store.save_desired_config(
                "device-a",
                {"vlans": {100: {"name": f"Version {i+1}"}}}
            )

        history = git_store.get_config_history(device_id="device-a")

        # Should have commits for each version
        assert len(history) >= 3

    def test_get_config_at_revision(self, git_store):
        """Test retrieving config at a specific revision."""
        # Version 1
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "Original"}}}
        )

        # Version 2
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "Modified"}}}
        )

        # Get version 1
        old_config = git_store.get_config_at_revision("device-a", "HEAD~1")

        assert old_config is not None
        assert old_config.config["vlans"][100]["name"] == "Original"

    def test_restore_from_revision(self, git_store):
        """Test restoring config from a git revision."""
        # Version 1
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "Original"}}}
        )

        # Version 2
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "Modified"}}}
        )

        # Current should be Modified
        current = git_store.get_desired_config("device-a")
        assert current.config["vlans"][100]["name"] == "Modified"

        # Restore from HEAD~1
        restored = git_store.restore_config_from_revision("device-a", "HEAD~1")

        assert restored is not None
        # New current should be Original
        new_current = git_store.get_desired_config("device-a")
        assert new_current.config["vlans"][100]["name"] == "Original"
        # Version should have incremented
        assert new_current.version == 3

    def test_diff_revisions(self, git_store):
        """Test diffing between revisions."""
        # Version 1
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "Alpha"}}}
        )

        # Version 2
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "Beta"}}}
        )

        diff = git_store.diff_config_revisions(
            "device-a",
            revision1="HEAD~1",
            revision2="HEAD"
        )

        # Should show the name change
        assert "Alpha" in diff or "Beta" in diff

    def test_custom_commit_message(self, git_store):
        """Test saving with custom commit message."""
        custom_msg = "Custom: Added production VLAN"
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "Test"}}},
            commit_message=custom_msg
        )

        history = git_store.get_config_history()

        # Find the most recent commit (first in list)
        assert len(history) >= 1
        latest_commit = history[0]
        assert custom_msg in latest_commit["message"]

    def test_history_for_nonexistent_device(self, git_store):
        """Test getting history for device with no commits."""
        history = git_store.get_config_history(device_id="nonexistent")

        assert history == []

    def test_restore_nonexistent_revision(self, git_store):
        """Test restoring from nonexistent revision."""
        git_store.save_desired_config(
            "device-a",
            {"vlans": {100: {"name": "Test"}}}
        )

        result = git_store.restore_config_from_revision(
            "device-a",
            "nonexistent123"
        )

        assert result is None
