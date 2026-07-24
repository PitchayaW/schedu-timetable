"""Core scheduling package for SchEDU.

Native OR-Tools is intentionally not imported here so the Streamlit process
stays lightweight; the UI runs the solver in an isolated worker process.
"""

from .io import InputBundle, load_main_workbook, load_preferences
from .models import ScheduleParameters, ScheduleResult
from .runner import run_solver_isolated

__all__ = [
    "InputBundle",
    "ScheduleParameters",
    "ScheduleResult",
    "load_main_workbook",
    "load_preferences",
    "run_solver_isolated",
]
