from __future__ import annotations

import subprocess
from pathlib import Path


def pytest_configure() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    generated_runtime = (
        repo_root / "backend/api/predictions/v1/predictions_rbt.py"
    )
    if generated_runtime.exists():
        return

    subprocess.run(
        ["rbt", "generate"],
        cwd=repo_root,
        check=True,
    )
