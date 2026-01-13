"""Git integration for configuration versioning.

Provides:
- Automatic git repository initialization
- Auto-commit on config changes
- History viewing
- Version restore
"""
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CommitInfo:
    """Information about a git commit."""
    hash: str
    short_hash: str
    author: str
    date: datetime
    message: str
    files_changed: list[str]


class GitManager:
    """
    Manages git operations for config versioning.

    The git repo is initialized in the configs/ directory,
    tracking all desired state changes.
    """

    def __init__(self, repo_path: Path):
        """
        Initialize GitManager.

        Args:
            repo_path: Path to the configs directory (will be git root)
        """
        self.repo_path = repo_path
        self._initialized = False

    def _run_git(
        self,
        *args: str,
        check: bool = True,
        capture_output: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a git command in the repo directory."""
        cmd = ["git", "-C", str(self.repo_path)] + list(args)
        logger.debug(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=False,  # We'll handle errors ourselves
        )

        if check and result.returncode != 0:
            logger.error(f"Git command failed: {result.stderr}")
            raise GitError(f"Git command failed: {result.stderr}")

        return result

    def is_initialized(self) -> bool:
        """Check if the git repo is initialized."""
        git_dir = self.repo_path / ".git"
        return git_dir.exists()

    def init(self) -> bool:
        """
        Initialize git repo if not already done.

        Returns:
            True if newly initialized, False if already exists
        """
        if self.is_initialized():
            logger.debug("Git repo already initialized")
            return False

        # Initialize repo
        self._run_git("init")

        # Configure git
        self._run_git("config", "user.name", "switchcraft")
        self._run_git("config", "user.email", "switchcraft@local")

        # Create .gitignore
        gitignore = self.repo_path / ".gitignore"
        gitignore.write_text(
            "# Switchcraft config gitignore\n"
            "*.tmp\n"
            "*.bak\n"
            "__pycache__/\n"
        )

        # Initial commit
        self._run_git("add", ".")
        self._run_git("commit", "-m", "Initial config repository", "--allow-empty")

        logger.info(f"Initialized git repo at {self.repo_path}")
        self._initialized = True
        return True

    def commit(
        self,
        message: str,
        files: Optional[list[str]] = None,
        author: Optional[str] = None,
    ) -> Optional[str]:
        """
        Commit changes to the repo.

        Args:
            message: Commit message
            files: Specific files to commit (default: all changes)
            author: Author name for audit trail

        Returns:
            Commit hash if successful, None if nothing to commit
        """
        if not self.is_initialized():
            self.init()

        # Stage files
        if files:
            for f in files:
                self._run_git("add", f)
        else:
            self._run_git("add", ".")

        # Check if there are staged changes
        result = self._run_git("diff", "--cached", "--quiet", check=False)
        if result.returncode == 0:
            logger.debug("No changes to commit")
            return None

        # Build commit message with metadata
        full_message = message
        if author:
            full_message += f"\n\nApplied by: {author}"

        # Commit
        self._run_git("commit", "-m", full_message)

        # Get commit hash
        result = self._run_git("rev-parse", "HEAD")
        commit_hash = result.stdout.strip()

        logger.info(f"Committed: {commit_hash[:8]} - {message.split(chr(10))[0]}")
        return commit_hash

    def get_history(
        self,
        file_path: Optional[str] = None,
        limit: int = 20,
    ) -> list[CommitInfo]:
        """
        Get commit history.

        Args:
            file_path: Filter by file (e.g., "desired/brocade-core.yaml")
            limit: Maximum commits to return

        Returns:
            List of CommitInfo objects
        """
        if not self.is_initialized():
            return []

        # Build git log command
        # Format: hash|short|author|date|subject
        format_str = "%H|%h|%an|%aI|%s"
        args = ["log", f"--format={format_str}", f"-n{limit}"]

        if file_path:
            args.extend(["--", file_path])

        result = self._run_git(*args, check=False)
        if result.returncode != 0:
            return []

        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            parts = line.split("|", 4)
            if len(parts) < 5:
                continue

            try:
                commit = CommitInfo(
                    hash=parts[0],
                    short_hash=parts[1],
                    author=parts[2],
                    date=datetime.fromisoformat(parts[3]),
                    message=parts[4],
                    files_changed=[],
                )
                commits.append(commit)
            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse commit: {e}")

        return commits

    def get_file_at_revision(
        self,
        file_path: str,
        revision: str = "HEAD",
    ) -> Optional[str]:
        """
        Get file contents at a specific revision.

        Args:
            file_path: Path relative to repo root
            revision: Git revision (commit hash, HEAD~1, etc.)

        Returns:
            File contents or None if not found
        """
        if not self.is_initialized():
            return None

        result = self._run_git("show", f"{revision}:{file_path}", check=False)
        if result.returncode != 0:
            return None

        return result.stdout

    def restore_file(
        self,
        file_path: str,
        revision: str,
    ) -> bool:
        """
        Restore a file from a specific revision.

        Args:
            file_path: Path relative to repo root
            revision: Git revision to restore from

        Returns:
            True if successful
        """
        if not self.is_initialized():
            return False

        # Get file content from revision
        content = self.get_file_at_revision(file_path, revision)
        if content is None:
            return False

        # Write to working directory
        full_path = self.repo_path / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)

        logger.info(f"Restored {file_path} from {revision}")
        return True

    def diff(
        self,
        file_path: Optional[str] = None,
        revision1: str = "HEAD~1",
        revision2: str = "HEAD",
    ) -> str:
        """
        Get diff between revisions.

        Args:
            file_path: Specific file to diff (optional)
            revision1: First revision (older)
            revision2: Second revision (newer)

        Returns:
            Diff output
        """
        if not self.is_initialized():
            return ""

        args = ["diff", revision1, revision2]
        if file_path:
            args.extend(["--", file_path])

        result = self._run_git(*args, check=False)
        return result.stdout

    def get_changed_files(self, revision: str = "HEAD") -> list[str]:
        """Get list of files changed in a commit."""
        if not self.is_initialized():
            return []

        result = self._run_git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", revision,
            check=False
        )
        if result.returncode != 0:
            return []

        return [f for f in result.stdout.strip().split("\n") if f]

    def tag(self, name: str, message: Optional[str] = None) -> bool:
        """Create a tag at the current commit."""
        if not self.is_initialized():
            return False

        args = ["tag", name]
        if message:
            args.extend(["-m", message])

        result = self._run_git(*args, check=False)
        return result.returncode == 0

    def list_tags(self) -> list[str]:
        """List all tags."""
        if not self.is_initialized():
            return []

        result = self._run_git("tag", "-l", check=False)
        if result.returncode != 0:
            return []

        return [t for t in result.stdout.strip().split("\n") if t]


class GitError(Exception):
    """Exception raised for git operation failures."""
    pass
