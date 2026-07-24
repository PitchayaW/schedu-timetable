from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ScheduleParameters:
    max_teaching_hours_per_day: float = 8.0
    max_study_hours_per_day: float = 8.0
    max_course_hours_per_day: float = 6.0
    max_courses_per_teacher_per_day: int = 3
    strict_zero_preference: bool = True
    avoid_consecutive_days: bool = True
    allow_unassigned: bool = True
    preference_weight: int = 75
    room_fit_weight: int = 20
    late_slot_penalty: int = 8
    time_limit_seconds: int = 45
    random_seed: int = 69


@dataclass
class ScheduleResult:
    status: str
    assignments: pd.DataFrame
    unassigned: pd.DataFrame
    diagnostics: list[str] = field(default_factory=list)
    objective_value: float = 0.0
    wall_time_seconds: float = 0.0
    average_preference: float = 0.0
