from __future__ import annotations

import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

from .io import InputBundle
from .models import ScheduleParameters, ScheduleResult


def run_solver_isolated(
    bundle: InputBundle, parameters: ScheduleParameters
) -> ScheduleResult:
    """Run native OR-Tools outside Streamlit's script thread."""
    with tempfile.TemporaryDirectory(prefix="schedulab-") as directory:
        temp_dir = Path(directory)
        input_path = temp_dir / "input.pkl"
        output_path = temp_dir / "output.pkl"
        input_path.write_bytes(pickle.dumps((bundle, parameters)))
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scheduler.worker",
                str(input_path),
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=parameters.time_limit_seconds + 30,
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            detail = completed.stderr.strip() or completed.stdout.strip()
            return ScheduleResult(
                status="ERROR",
                assignments=pd.DataFrame(),
                unassigned=pd.DataFrame(),
                diagnostics=[
                    "ตัวแก้ปัญหาหยุดทำงานก่อนส่งผลลัพธ์",
                    detail[-1200:] if detail else f"exit code {completed.returncode}",
                ],
            )
        return pickle.loads(output_path.read_bytes())
