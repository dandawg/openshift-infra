import os
import subprocess
from collections.abc import Sequence
from pathlib import Path


def run_cmd(
    cmd: Sequence[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        list(cmd),
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=True,
        env=merged_env,
    )
