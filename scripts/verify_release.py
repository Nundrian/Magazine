#!/usr/bin/env python3
"""Run all source-level Magazine release checks without inference or Docker."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    python = sys.executable
    run([python, "-m", "pytest", "-q", "tests"])

    function_dir = ROOT / "integrations" / "open_webui" / "function"
    runner_tests = sorted(str(path.relative_to(ROOT)) for path in (function_dir / "tests").glob("test_*.py"))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(function_dir) + os.pathsep + env.get("PYTHONPATH", "")
    run([python, "-m", "pytest", "-q", *runner_tests], env=env)

    ui_tests = sorted(str(path.relative_to(ROOT)) for path in (function_dir / "tests").glob("test_*_ui.cjs"))
    run(["node", "--test", *ui_tests])
    if (ROOT / "integrations/open_webui/frontend/tests/launcher_contract.test.cjs").exists():
        run(["node", "--test", "integrations/open_webui/frontend/tests/launcher_contract.test.cjs"])
    run([python, "scripts/embed_core.py", "--check"])
    if (ROOT / "release/publication-manifest.json").exists():
        run([python, "scripts/build_release_archive.py"])
    run([python, "scripts/build_release_archive.py", "--manifest", "release/function-only-manifest.json"])
    print("release source verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
