from __future__ import annotations

from collections import Counter, defaultdict
from time import perf_counter

import pandas as pd
from ortools.sat.python import cp_model

from .io import InputBundle
from .models import ScheduleParameters, ScheduleResult
from .time_utils import (
    DAY_THAI,
    DAYS,
    TIMES,
    normalize_course_id,
    occupied_slots,
    parse_fixed_time,
    parse_session_hours,
    split_items,
    time_label,
)

def _empty_result(
    status: str,
    diagnostics: list[str],
    *,
    candidate_count: int = 0,
    option_summary: pd.DataFrame | None = None,
    unassigned: pd.DataFrame | None = None,
) -> ScheduleResult:
    return ScheduleResult(
        status=status,
        assignments=pd.DataFrame(),
        unassigned=unassigned if unassigned is not None else pd.DataFrame(),
        option_summary=(
            option_summary if option_summary is not None else pd.DataFrame()
        ),
        diagnostics=diagnostics,
        candidate_count=candidate_count,
    )


def _preference(
    preferences: dict[str, dict[tuple[int, int], float]],
    teachers: list[str],
    day: int,
    start: int,
    duration: int,
) -> float:
    scores = [
        preferences.get(teacher, {}).get((day, slot), 1.0)
        for teacher in teachers
        for slot in range(start, start + duration)
    ]
    return min(scores) if scores else 1.0


def _overlaps(left: dict, right: dict) -> bool:
    return (
        left["day"] == right["day"]
        and left["start"] < right["start"] + right["duration"]
        and right["start"] < left["start"] + left["duration"]
    )


def _candidate_blockers(
    candidate: dict,
    scheduled_rows: list[dict],
    parameters: ScheduleParameters,
    teacher_busy: dict[str, set[tuple[int, int]]],
    group_busy: dict[str, set[tuple[int, int]]],
    teacher_fixed_course_days: dict[tuple[str, int], set[str]],
    group_limits: dict[str, int],
) -> set[str]:
    reasons: set[str] = set()
    day = candidate["day"]
    duration = candidate["duration"]
    flexible_rows = [row for row in scheduled_rows if not row.get("locked", False)]

    for row in scheduled_rows:
        if _overlaps(candidate, row):
            if (
                candidate["room"] == row["room"]
                and candidate["room"] != "กำหนดภายหลัง"
            ):
                reasons.add("ห้องชนกับคาบที่จัดแล้ว")
            if set(candidate["teachers"]) & set(row["teachers"]):
                reasons.add("อาจารย์สอนชนกัน")
            if set(candidate["groups"]) & set(row["groups"]):
                reasons.add("กลุ่มนักศึกษาเรียนชนกัน")

        if row["course_key"] != candidate["course_key"]:
            continue
        if row["day"] == day:
            reasons.add("วิชาเดียวกันเกิน 1 คาบในวันเดียว")
        if parameters.avoid_consecutive_days and abs(row["day"] - day) == 1:
            reasons.add("วิชาเดียวกันอยู่ในวันติดกัน")

    max_teacher_slots = round(parameters.max_teaching_hours_per_day * 2)
    for teacher in candidate["teachers"]:
        fixed_slots = len({slot for fixed_day, slot in teacher_busy[teacher] if fixed_day == day})
        scheduled_slots = sum(
            row["duration"]
            for row in flexible_rows
            if row["day"] == day and teacher in row["teachers"]
        )
        if fixed_slots + scheduled_slots + duration > max_teacher_slots:
            reasons.add("ชั่วโมงสอนของอาจารย์ต่อวันเกินกำหนด")

        fixed_courses = teacher_fixed_course_days[(teacher, day)]
        scheduled_courses = {
            row["course_key"]
            for row in flexible_rows
            if row["day"] == day and teacher in row["teachers"]
        }
        new_course = 0 if candidate["course_key"] in scheduled_courses else 1
        if (
            len(fixed_courses) + len(scheduled_courses) + new_course
            > parameters.max_courses_per_teacher_per_day
        ):
            reasons.add("จำนวนวิชาของอาจารย์ต่อวันเกินกำหนด")

    default_group_limit = round(parameters.max_study_hours_per_day * 2)
    for group in candidate["groups"]:
        fixed_slots = len({slot for fixed_day, slot in group_busy[group] if fixed_day == day})
        scheduled_slots = sum(
            row["duration"]
            for row in flexible_rows
            if row["day"] == day and group in row["groups"]
        )
        limit = min(group_limits.get(group, default_group_limit), default_group_limit)
        if fixed_slots + scheduled_slots + duration > limit:
            reasons.add("ชั่วโมงเรียนของกลุ่มต่อวันเกินกำหนด")

    if duration > round(parameters.max_course_hours_per_day * 2):
        reasons.add("ชั่วโมงของวิชาเดียวกันต่อวันเกินกำหนด")
    return reasons


def _format_rejection_reasons(reason_counts: Counter[str]) -> str:
    if not reason_counts:
        return "ไม่พบตำแหน่งที่ผ่านข้อจำกัดเบื้องต้น"
    return " / ".join(
        f"{reason} ({count:,} ตำแหน่ง)"
        for reason, count in reason_counts.most_common(3)
    )


def _minimum_blocker_summary(
    candidates: list[dict],
    scheduled_rows: list[dict],
    parameters: ScheduleParameters,
    teacher_busy: dict[str, set[tuple[int, int]]],
    group_busy: dict[str, set[tuple[int, int]]],
    teacher_fixed_course_days: dict[tuple[str, int], set[str]],
    group_limits: dict[str, int],
    status: str,
) -> str:
    best_size: int | None = None
    best_reasons: Counter[str] = Counter()
    for candidate in candidates:
        reasons = _candidate_blockers(
            candidate,
            scheduled_rows,
            parameters,
            teacher_busy,
            group_busy,
            teacher_fixed_course_days,
            group_limits,
        )
        if best_size is None or len(reasons) < best_size:
            best_size = len(reasons)
            best_reasons = Counter(reasons)
        elif len(reasons) == best_size:
            best_reasons.update(reasons)

    if best_size == 0:
        if status == "FEASIBLE":
            return "หมดเวลาค้นหาก่อนพิสูจน์ว่าจัดได้ครบทุกคาบ"
        return "ต้องสลับคาบอื่นพร้อมกันหลายรายการจึงจะเพิ่มคาบนี้ได้"
    return " / ".join(reason for reason, _ in best_reasons.most_common(3))


def solve_schedule(
    bundle: InputBundle, parameters: ScheduleParameters
) -> ScheduleResult:
    started = perf_counter()
    if bundle.has_errors:
        return _empty_result(
            "INVALID_INPUT",
            [message.message for message in bundle.messages if message.level == "error"],
        )

    courses = bundle.department_courses.reset_index(drop=True).copy()
    rooms = bundle.rooms.reset_index(drop=True).copy()
    groups_table = bundle.student_groups.reset_index(drop=True).copy()

    course_records: list[dict] = []
    sessions: list[dict] = []
    fixed_assignments: list[dict] = []
    for course_index, row in courses.iterrows():
        course_key = f"{normalize_course_id(row['course_id'])}::{row.get('section', 1)}::{course_index}"
        teachers = split_items(row.get("teachers"))
        groups = split_items(row.get("student_groups"))
        record = {
            "course_index": course_index,
            "course_key": course_key,
            "course_id": str(row.get("course_id", "")).strip(),
            "course_name": str(row.get("course_name", "")).strip(),
            "section": str(row.get("section", "1")).strip(),
            "capacity": int(row.get("capacity", 0)),
            "teachers": teachers,
            "groups": groups,
            "room_type": str(row.get("room_type", "lecture") or "lecture").strip().lower(),
            "category": str(row.get("course_category", "")).strip(),
        }
        course_records.append(record)
        fixed_blocks = parse_fixed_time(row.get("fixed_time"))
        if fixed_blocks:
            for block_index, (day, start, end) in enumerate(fixed_blocks):
                fixed_assignments.append(
                    {
                        **record,
                        "session_id": f"{course_key}:fixed:{block_index}",
                        "day": day,
                        "start": start,
                        "duration": end - start,
                        "room": str(row.get("room_name", "") or "กำหนดภายหลัง"),
                        "preference": _preference(
                            bundle.preferences, teachers, day, start, end - start
                        ),
                        "locked": True,
                    }
                )
            continue
        for session_index, duration in enumerate(
            parse_session_hours(row.get("session_hours"))
        ):
            sessions.append(
                {
                    **record,
                    "session_id": f"{course_key}:session:{session_index}",
                    "session_index": session_index,
                    "duration": min(duration, 8),
                }
            )

    room_records = []
    for room_index, row in rooms.iterrows():
        room_records.append(
            {
                "room_index": room_index,
                "name": str(row.get("room_name", "")).strip(),
                "capacity": int(row.get("capacity", 0)),
                "type": str(row.get("room_type", "lecture") or "lecture").strip().lower(),
                "graduate_only": str(row.get("graduate_only", "no")).strip().lower()
                in {"yes", "true", "1", "ใช่"},
                "busy": occupied_slots(parse_fixed_time(row.get("unavailable_time"))),
            }
        )

    teacher_busy: dict[str, set[tuple[int, int]]] = defaultdict(set)
    teacher_fixed_course_days: dict[tuple[str, int], set[str]] = defaultdict(set)
    for _, row in bundle.all_courses.iterrows():
        blocks = parse_fixed_time(row.get("fixed_time"))
        if not blocks:
            continue
        key = normalize_course_id(row.get("course_id"))
        for teacher in split_items(row.get("teachers")):
            teacher_busy[teacher].update(occupied_slots(blocks))
            for day, _, _ in blocks:
                teacher_fixed_course_days[(teacher, day)].add(key)

    group_busy: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for _, row in bundle.student_courses.iterrows():
        blocks = parse_fixed_time(row.get("fixed_time"))
        if not blocks:
            continue
        group = str(row.get("group_id", "")).strip()
        busy = occupied_slots(blocks)
        group_busy[group].update(busy)

    model = cp_model.CpModel()
    candidates: dict[tuple[int, int, int, int], cp_model.IntVar] = {}
    candidate_metadata: dict[tuple[int, int, int, int], dict] = {}
    session_vars: dict[int, list[cp_model.IntVar]] = defaultdict(list)
    room_slot_vars: dict[tuple[int, int, int], list[cp_model.IntVar]] = defaultdict(list)
    teacher_slot_vars: dict[tuple[str, int, int], list[cp_model.IntVar]] = defaultdict(list)
    group_slot_vars: dict[tuple[str, int, int], list[cp_model.IntVar]] = defaultdict(list)
    course_day_vars: dict[tuple[str, int], list[cp_model.IntVar]] = defaultdict(list)
    teacher_day_duration: dict[tuple[str, int], list[tuple[cp_model.IntVar, int]]] = defaultdict(list)
    group_day_duration: dict[tuple[str, int], list[tuple[cp_model.IntVar, int]]] = defaultdict(list)
    teacher_course_day: dict[tuple[str, str, int], list[cp_model.IntVar]] = defaultdict(list)
    objective_terms: list[cp_model.LinearExpr] = []
    placed_vars: dict[int, cp_model.IntVar] = {}
    rejection_counts: dict[int, Counter[str]] = defaultdict(Counter)

    for session_index, session in enumerate(sessions):
        duration = session["duration"]
        is_graduate = any(
            "โท" in group or "master" in group.lower()
            for group in session["groups"]
        )
        for room in room_records:
            for day in range(5):
                for start in range(0, len(TIMES) - duration + 1):
                    rejection_reasons: set[str] = set()
                    if room["type"] != session["room_type"]:
                        rejection_reasons.add("ประเภทห้องไม่ตรง")
                    if room["capacity"] < session["capacity"]:
                        rejection_reasons.add("ความจุห้องไม่พอ")
                    if room["graduate_only"] and not is_graduate:
                        rejection_reasons.add("ห้องสงวนสำหรับบัณฑิตศึกษา")
                    if start < 8 < start + duration:
                        rejection_reasons.add("คาบเรียนคร่อมช่วงพักกลางวัน")
                    covered = {(day, slot) for slot in range(start, start + duration)}
                    if covered & room["busy"]:
                        rejection_reasons.add("ห้องไม่พร้อมใช้งาน")
                    if any(covered & teacher_busy[teacher] for teacher in session["teachers"]):
                        rejection_reasons.add("อาจารย์ติดคาบที่ล็อกไว้")
                    if any(covered & group_busy[group] for group in session["groups"]):
                        rejection_reasons.add("กลุ่มนักศึกษาติดคาบที่ล็อกไว้")
                    pref = _preference(
                        bundle.preferences,
                        session["teachers"],
                        day,
                        start,
                        duration,
                    )
                    if parameters.strict_zero_preference and pref <= 0:
                        rejection_reasons.add("Preference = 0")
                    if rejection_reasons:
                        rejection_counts[session_index].update(rejection_reasons)
                        continue
                    key = (session_index, room["room_index"], day, start)
                    variable = model.new_bool_var(
                        f"x_s{session_index}_r{room['room_index']}_d{day}_t{start}"
                    )
                    candidates[key] = variable
                    session_vars[session_index].append(variable)
                    room_fit = max(
                        0,
                        1
                        - (room["capacity"] - session["capacity"])
                        / max(room["capacity"], 1),
                    )
                    late_slots = max(0, start + duration - 16)
                    score = round(
                        pref * parameters.preference_weight * 100
                        + room_fit * parameters.room_fit_weight * 100
                        - late_slots * parameters.late_slot_penalty * 10
                    )
                    objective_terms.append(variable * score)
                    candidate_metadata[key] = {
                        **session,
                        "day": day,
                        "start": start,
                        "room": room["name"],
                        "preference": pref,
                    }
                    for slot in range(start, start + duration):
                        room_slot_vars[(room["room_index"], day, slot)].append(variable)
                        for teacher in session["teachers"]:
                            teacher_slot_vars[(teacher, day, slot)].append(variable)
                        for group in session["groups"]:
                            group_slot_vars[(group, day, slot)].append(variable)
                    course_day_vars[(session["course_key"], day)].append(variable)
                    for teacher in session["teachers"]:
                        teacher_day_duration[(teacher, day)].append((variable, duration))
                        teacher_course_day[
                            (teacher, session["course_key"], day)
                        ].append(variable)
                    for group in session["groups"]:
                        group_day_duration[(group, day)].append((variable, duration))

        if not session_vars[session_index]:
            if parameters.allow_unassigned:
                continue
            return _empty_result(
                "INFEASIBLE",
                [
                    f"ไม่พบตัวเลือกที่เป็นไปได้สำหรับ {session['course_id']} "
                    f"ครั้งที่ {session['session_index'] + 1}: "
                    f"{_format_rejection_reasons(rejection_counts[session_index])}"
                ],
                candidate_count=len(candidates),
            )
        if parameters.allow_unassigned:
            placed = model.new_bool_var(f"placed_{session_index}")
            model.add(sum(session_vars[session_index]) == placed)
            placed_vars[session_index] = placed
            objective_terms.append(placed * 1_000_000)
        else:
            model.add_exactly_one(session_vars[session_index])

    for variables in room_slot_vars.values():
        model.add_at_most_one(variables)
    for variables in teacher_slot_vars.values():
        model.add_at_most_one(variables)
    for variables in group_slot_vars.values():
        model.add_at_most_one(variables)
    for variables in course_day_vars.values():
        model.add(sum(variables) <= 1)

    course_day_indicator: dict[tuple[str, int], cp_model.IntVar] = {}
    for key, variables in course_day_vars.items():
        indicator = model.new_bool_var(f"course_day_{len(course_day_indicator)}")
        model.add(sum(variables) == indicator)
        course_day_indicator[key] = indicator

    if parameters.avoid_consecutive_days:
        for record in course_records:
            for day in range(4):
                left = course_day_indicator.get((record["course_key"], day))
                right = course_day_indicator.get((record["course_key"], day + 1))
                if left is not None and right is not None:
                    model.add(left + right <= 1)

    max_teacher_slots = round(parameters.max_teaching_hours_per_day * 2)
    for (teacher, day), terms in teacher_day_duration.items():
        fixed_slots = len({slot for d, slot in teacher_busy[teacher] if d == day})
        model.add(sum(variable * duration for variable, duration in terms) + fixed_slots <= max_teacher_slots)

    group_limits = {
        str(row.get("group_id", "")).strip(): round(
            float(row.get("max_study_hours_per_day") or parameters.max_study_hours_per_day)
            * 2
        )
        for _, row in groups_table.iterrows()
    }
    for (group, day), terms in group_day_duration.items():
        fixed_slots = len({slot for d, slot in group_busy[group] if d == day})
        limit = min(
            group_limits.get(group, round(parameters.max_study_hours_per_day * 2)),
            round(parameters.max_study_hours_per_day * 2),
        )
        model.add(sum(variable * duration for variable, duration in terms) + fixed_slots <= limit)

    max_course_slots = round(parameters.max_course_hours_per_day * 2)
    for (course_key, day), variables in course_day_vars.items():
        duration_map = {
            session_index: sessions[session_index]["duration"]
            for session_index, _, candidate_day, _ in candidates
            if candidate_day == day and sessions[session_index]["course_key"] == course_key
        }
        if duration_map:
            terms = []
            for candidate_key, variable in candidates.items():
                session_index, _, candidate_day, _ = candidate_key
                if (
                    candidate_day == day
                    and sessions[session_index]["course_key"] == course_key
                ):
                    terms.append(variable * sessions[session_index]["duration"])
            model.add(sum(terms) <= max_course_slots)

    teacher_course_indicators: dict[tuple[str, str, int], cp_model.IntVar] = {}
    for key, variables in teacher_course_day.items():
        indicator = model.new_bool_var(f"teacher_course_day_{len(teacher_course_indicators)}")
        model.add(sum(variables) == indicator)
        teacher_course_indicators[key] = indicator
    by_teacher_day: dict[tuple[str, int], list[cp_model.IntVar]] = defaultdict(list)
    for (teacher, _, day), indicator in teacher_course_indicators.items():
        by_teacher_day[(teacher, day)].append(indicator)
    for (teacher, day), indicators in by_teacher_day.items():
        fixed_count = len(teacher_fixed_course_days[(teacher, day)])
        model.add(
            sum(indicators) + fixed_count
            <= parameters.max_courses_per_teacher_per_day
        )

    model.maximize(sum(objective_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = parameters.time_limit_seconds
    # The Streamlit UI launches this native solver in an isolated process.
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = parameters.random_seed
    status_code = solver.solve(model)
    status = solver.status_name(status_code)
    if status_code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _empty_result(
            status,
            [
                "ไม่พบตารางภายใต้พารามิเตอร์ปัจจุบัน",
                "ลองเปิด allow unassigned เพิ่มชั่วโมงสูงสุด หรือผ่อนเงื่อนไขคะแนน 0",
            ],
            candidate_count=len(candidates),
        )

    rows = list(fixed_assignments)
    assigned_session_indices: set[int] = set()
    for key, variable in candidates.items():
        if solver.value(variable):
            session_index = key[0]
            assigned_session_indices.add(session_index)
            rows.append({**candidate_metadata[key], "locked": False})

    assignment_rows = []
    for row in rows:
        assignment_rows.append(
            {
                "course_id": row["course_id"],
                "course_name": row["course_name"],
                "section": row["section"],
                "session": row.get("session_index", 0) + 1,
                "day_index": row["day"],
                "day": DAY_THAI[row["day"]],
                "time": time_label(row["start"], row["duration"]),
                "start_slot": row["start"],
                "duration_hours": row["duration"] / 2,
                "room": row["room"],
                "teachers": " / ".join(row["teachers"]),
                "student_groups": " / ".join(row["groups"]),
                "preference": row["preference"],
                "category": row["category"],
                "locked": row["locked"],
            }
        )
    assignments = pd.DataFrame(assignment_rows)
    if not assignments.empty:
        assignments = assignments.sort_values(
            ["day_index", "start_slot", "room", "course_id"]
        ).reset_index(drop=True)

    candidates_by_session: dict[int, list[dict]] = defaultdict(list)
    for key, metadata in candidate_metadata.items():
        candidates_by_session[key[0]].append(metadata)

    option_rows = []
    unassigned_rows = []
    for index, session in enumerate(sessions):
        feasible_options = len(candidates_by_session[index])
        is_assigned = index in assigned_session_indices
        option_rows.append(
            {
                "course_id": session["course_id"],
                "course_name": session["course_name"],
                "section": session["section"],
                "session": session["session_index"] + 1,
                "duration_hours": session["duration"] / 2,
                "feasible_options": feasible_options,
                "status": "จัดแล้ว" if is_assigned else "ยังไม่จัด",
            }
        )
        if is_assigned:
            continue
        if feasible_options == 0:
            blocking_constraints = _format_rejection_reasons(
                rejection_counts[index]
            )
        else:
            blocking_constraints = _minimum_blocker_summary(
                candidates_by_session[index],
                rows,
                parameters,
                teacher_busy,
                group_busy,
                teacher_fixed_course_days,
                group_limits,
                status,
            )
        unassigned_rows.append(
            {
                "course_id": session["course_id"],
                "course_name": session["course_name"],
                "section": session["section"],
                "session": session["session_index"] + 1,
                "duration_hours": session["duration"] / 2,
                "feasible_options": feasible_options,
                "blocking_constraints": blocking_constraints,
            }
        )
    option_summary = pd.DataFrame(option_rows)
    unassigned = pd.DataFrame(unassigned_rows)
    average_preference = (
        float(assignments.loc[~assignments["locked"], "preference"].mean())
        if not assignments.empty and (~assignments["locked"]).any()
        else 0.0
    )
    diagnostics = [
        f"สถานะตัวแก้ปัญหา: {status}",
        f"สร้างตัวเลือกตำแหน่งวัน–เวลา–ห้อง {len(candidates):,} ตัวเลือก "
        f"สำหรับ {len(sessions):,} คาบที่ยังไม่ล็อกเวลา",
        "จำนวนนี้เป็นตำแหน่งที่ผ่านข้อจำกัดเบื้องต้น ไม่ใช่จำนวนตารางสมบูรณ์ทั้งหมด",
        f"ใช้เวลาคำนวณ {solver.wall_time:.2f} วินาที",
    ]
    if not unassigned.empty:
        diagnostics.append(
            f"มี {len(unassigned):,} ช่วงเรียนที่ยังไม่ได้จัด; ระบบไม่บังคับให้เกิดตารางที่ผิดเงื่อนไข"
        )
    return ScheduleResult(
        status=status,
        assignments=assignments,
        unassigned=unassigned,
        option_summary=option_summary,
        diagnostics=diagnostics,
        candidate_count=len(candidates),
        objective_value=solver.objective_value,
        wall_time_seconds=perf_counter() - started,
        average_preference=average_preference,
    )
