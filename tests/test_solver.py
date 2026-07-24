import unittest

import pandas as pd

from scheduler.io import InputBundle
from scheduler.solver import ScheduleParameters, solve_schedule


class SolverTest(unittest.TestCase):
    def test_teacher_and_room_do_not_overlap(self):
        courses = pd.DataFrame(
            [
                {
                    "course_id": "A",
                    "course_name": "A",
                    "section": 1,
                    "capacity": 20,
                    "student_groups": "STAT ปี 1",
                    "session_hours": 1.5,
                    "teachers": "Teacher A",
                    "room_type": "lecture",
                    "fixed_time": "",
                    "course_category": "required",
                },
                {
                    "course_id": "B",
                    "course_name": "B",
                    "section": 1,
                    "capacity": 20,
                    "student_groups": "STAT ปี 2",
                    "session_hours": 1.5,
                    "teachers": "Teacher A",
                    "room_type": "lecture",
                    "fixed_time": "",
                    "course_category": "required",
                },
            ]
        )
        all_courses = courses.rename(
            columns={"room_type": "teaching_mode"}
        ).assign(room_name="")
        rooms = pd.DataFrame(
            [
                {
                    "room_id": 1,
                    "room_name": "R1",
                    "capacity": 40,
                    "room_type": "lecture",
                    "graduate_only": "no",
                    "unavailable_time": "",
                }
            ]
        )
        groups = pd.DataFrame(
            [
                {
                    "group_id": "STAT ปี 1",
                    "major": "STAT",
                    "year": 1,
                    "student_count": 20,
                    "max_study_hours_per_day": 8,
                },
                {
                    "group_id": "STAT ปี 2",
                    "major": "STAT",
                    "year": 2,
                    "student_count": 20,
                    "max_study_hours_per_day": 8,
                },
            ]
        )
        bundle = InputBundle(
            all_courses=all_courses,
            department_courses=courses,
            student_courses=pd.DataFrame(
                columns=["group_id", "course_id", "course_name", "course_owner", "fixed_time"]
            ),
            rooms=rooms,
            student_groups=groups,
        )
        result = solve_schedule(
            bundle,
            ScheduleParameters(time_limit_seconds=5, allow_unassigned=False),
        )
        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(len(result.assignments), 2)
        first, second = result.assignments.iloc[0], result.assignments.iloc[1]
        overlap = (
            first.day_index == second.day_index
            and first.start_slot < second.start_slot + second.duration_hours * 2
            and second.start_slot < first.start_slot + first.duration_hours * 2
        )
        self.assertFalse(overlap)


if __name__ == "__main__":
    unittest.main()
