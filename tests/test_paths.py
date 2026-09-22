"""Tests for magazine.paths.resolve_contained_path."""

import pytest
from pathlib import Path
import tempfile
import os

from magazine.paths import resolve_contained_path


@pytest.fixture()
def tmp_root():
    """Create a temporary directory to serve as the root."""
    with tempfile.TemporaryDirectory() as td:
        yield td


# 1. normal relative path inside the root is accepted
def test_normal_relative_path(tmp_root):
    result = resolve_contained_path(tmp_root, "subdir/file.txt")
    assert result.is_relative_to(Path(tmp_root).resolve())


# 2. nested relative path inside the root is accepted
def test_nested_relative_path(tmp_root):
    result = resolve_contained_path(tmp_root, "a/b/c/d.txt")
    assert result.is_relative_to(Path(tmp_root).resolve())


# 3. ``..`` traversal outside the root is rejected
def test_dotdot_traversal_rejected(tmp_root):
    with pytest.raises(ValueError, match="escapes root"):
        resolve_contained_path(tmp_root, "../outside")


# 4. absolute path is rejected
def test_absolute_path_rejected(tmp_root):
    with pytest.raises(ValueError, match="must not be absolute"):
        resolve_contained_path(tmp_root, "/etc/passwd")


# 5. empty or whitespace-only path is rejected
def test_empty_path_rejected(tmp_root):
    with pytest.raises(ValueError, match="must not be empty"):
        resolve_contained_path(tmp_root, "")

    with pytest.raises(ValueError, match="must not be empty"):
        resolve_contained_path(tmp_root, "   ")


# 6. symlink inside root pointing outside is rejected
def test_symlink_escape_rejected():
    with tempfile.TemporaryDirectory() as tmp_root:
        # Create a directory *outside* the root using a separate managed temp dir
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)
            outside.joinpath("secret.txt").touch()

            # Create a symlink inside the root pointing to the outside directory
            link_path = Path(tmp_root) / "link"
            link_path.symlink_to(outside)

            # Resolving through the symlink should be rejected
            with pytest.raises(ValueError, match="escapes root"):
                resolve_contained_path(tmp_root, "link/secret.txt")
