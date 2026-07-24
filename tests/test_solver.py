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
        self.assertGreater(result.candidate_count, 0)
        self.assertEqual(len(result.option_summary), 2)
        self.assertTrue((result.option_summary["feasible_options"] > 0).all())
        first, second = result.assignments.iloc[0], result.assignments.iloc[1]
        overlap = (
            first.day_index == second.day_index
            and first.start_slot < second.start_slot + second.duration_hours * 2
            and second.start_slot < first.start_slot + first.duration_hours * 2
        )
        self.assertFalse(overlap)

    def test_reports_static_constraint_when_no_candidate_exists(self):
        courses = pd.DataFrame(
            [
                {
                    "course_id": "FULL",
                    "course_name": "Too large for every room",
                    "section": 1,
                    "capacity": 100,
                    "student_groups": "STAT ปี 1",
                    "session_hours": 1.5,
                    "teachers": "Teacher A",
                    "room_type": "lecture",
                    "fixed_time": "",
                    "course_category": "required",
                }
            ]
        )
        rooms = pd.DataFrame(
            [
                {
                    "room_id": 1,
                    "room_name": "R1",
                    "capacity": 20,
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
                    "student_count": 100,
                    "max_study_hours_per_day": 8,
                }
            ]
        )
        bundle = InputBundle(
            all_courses=courses,
            department_courses=courses,
            student_courses=pd.DataFrame(),
            rooms=rooms,
            student_groups=groups,
        )
        result = solve_schedule(bundle, ScheduleParameters(time_limit_seconds=5))

        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(len(result.unassigned), 1)
        self.assertIn(
            "ความจุห้องไม่พอ",
            result.unassigned.iloc[0]["blocking_constraints"],
        )

    def test_reports_conflict_with_selected_schedule(self):
        courses = pd.DataFrame(
            [
                {
                    "course_id": course_id,
                    "course_name": course_id,
                    "section": 1,
                    "capacity": 20,
                    "student_groups": "STAT ปี 1",
                    "session_hours": 1.5,
                    "teachers": teacher,
                    "room_type": "lecture",
                    "fixed_time": "",
                    "course_category": "required",
                }
                for course_id, teacher in [
                    ("A", "Teacher A"),
                    ("B", "Teacher B"),
                ]
            ]
        )
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
                }
            ]
        )
        preferences = {
            teacher: {
                (day, slot): (
                    5.0 if day == 0 and slot in {0, 1, 2} else 0.0
                )
                for day in range(5)
                for slot in range(18)
            }
            for teacher in ["Teacher A", "Teacher B"]
        }
        bundle = InputBundle(
            all_courses=courses,
            department_courses=courses,
            student_courses=pd.DataFrame(),
            rooms=rooms,
            student_groups=groups,
            preferences=preferences,
        )
        result = solve_schedule(bundle, ScheduleParameters(time_limit_seconds=5))

        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(len(result.assignments), 1)
        self.assertEqual(len(result.unassigned), 1)
        blockers = result.unassigned.iloc[0]["blocking_constraints"]
        self.assertTrue(
            "ห้องชนกับคาบที่จัดแล้ว" in blockers
            or "กลุ่มนักศึกษาเรียนชนกัน" in blockers
        )


if __name__ == "__main__":
    unittest.main()
