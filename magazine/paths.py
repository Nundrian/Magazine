"""Path helpers for magazine."""

from pathlib import Path


def resolve_contained_path(root: str, relative_path: str) -> Path:
    """Resolve *relative_path* against *root* and ensure it stays inside *root*.

    Raises
    ------
    ValueError
        If *relative_path* is empty / whitespace-only, absolute, or escapes
        the resolved root directory (including ``..`` traversal).
    """
    # --- reject empty / whitespace-only ------------------------------------
    if not relative_path or not relative_path.strip():
        raise ValueError("relative_path must not be empty or whitespace-only")

    # --- reject absolute paths ---------------------------------------------
    if Path(relative_path).is_absolute():
        raise ValueError("relative_path must not be absolute")

    # --- resolve the root and the candidate ---------------------------------
    root_resolved = Path(root).resolve()
    candidate = (root_resolved / relative_path).resolve()

    # --- containment check --------------------------------------------------
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"path escapes root: {candidate} is not under {root_resolved}"
        )

    return candidate


def execution_file_path(run_dir: Path | str) -> Path:
    """Return the canonical path of a run's execution metadata file.

    The execution-ownership claim (``execution.json``) lives inside the run
    directory, alongside the frozen ``state.json`` and ``.lock``:
    ``<run_dir>/execution.json``.

    Parameters
    ----------
    run_dir:
        The run directory (a ``Path`` or an os-string path).

    Returns
    -------
    Path
        ``run_dir / "execution.json"`` (as an absolute-ish ``Path``, exactly
        as the caller supplied the run dir).
    """
    return Path(run_dir) / "execution.json"
