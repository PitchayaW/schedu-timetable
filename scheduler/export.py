from __future__ import annotations

from io import BytesIO
import re

import pandas as pd

from .io import InputBundle
from .models import ScheduleParameters, ScheduleResult
from .time_utils import DAYS, TIMES, parse_fixed_time, split_items

PALETTE = [
    "#BA55D3",  # mediumorchid
    "#FA8072",  # salmon
    "#FFFF00",  # yellow
    "#00BFFF",  # deepskyblue
    "#DDA0DD",  # plum
    "#ADFF2F",  # greenyellow
    "#FFC0CB",  # pink
    "#32CD32",  # limegreen
    "#40E0D0",  # turquoise
    "#BC8F8F",  # rosybrown
    "#FF69B4",  # hotpink
    "#FFD700",  # gold
    "#FFA500",  # orange
]
HEADER_FILL = "#C0C0C0"
DAY_FILL = "#E6E6FA"
LUNCH_FILL = "#FF4500"
LUNCH_COLUMN = 9  # zero-based Excel column index: A=0, J=9


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _course_id_key(value: object) -> str:
    return re.sub(r"\s+", "", _clean_text(value)).upper()


def _section_text(value: object) -> str:
    text = _clean_text(value)
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return f" กลุ่ม {text}" if text else ""


def _sheet_name(value: str, used: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "-", _clean_text(value)) or "ตาราง"
    base = base[:31]
    candidate = base
    suffix = 2
    while candidate in used:
        tail = f" ({suffix})"
        candidate = f"{base[:31-len(tail)]}{tail}"
        suffix += 1
    used.add(candidate)
    return candidate


def _room_title(value: str) -> str:
    text = _clean_text(value)
    match = re.fullmatch(r"(?i)SC[. -]?(\d+)", text)
    return f"SC.{match.group(1)}" if match else text


def _time_headers() -> list[str]:
    headers: list[str] = []
    for index, start in enumerate(TIMES):
        start_hour, start_minute = [int(item) for item in start.split(":")]
        if index == 7:
            end_hour, end_minute = 12, 0
        elif index == len(TIMES) - 1:
            end_hour, end_minute = 18, 0
        else:
            next_time = TIMES[index + 1]
            end_hour, end_minute = [int(item) for item in next_time.split(":")]
        headers.append(
            f"{start_hour:02d}.{start_minute:02d} - {end_hour:02d}.{end_minute:02d}"
        )
    return headers[:8] + ["Lunch Break"] + headers[8:]


def _event_key(course_id: object, section: object) -> str:
    return f"{_clean_text(course_id)}{_section_text(section)}".strip()


def _scheduled_events(result: ScheduleResult) -> list[dict]:
    events: list[dict] = []
    if result.assignments.empty:
        return events
    for _, row in result.assignments.iterrows():
        try:
            day = int(row.get("day_index", -1))
            start = int(row.get("start_slot", -1))
            duration = max(1, round(float(row.get("duration_hours", 0)) * 2))
        except (TypeError, ValueError):
            continue
        if day not in range(len(DAYS)) or start not in range(len(TIMES)):
            continue
        events.append(
            {
                "day": day,
                "start": start,
                "end": min(start + duration, len(TIMES)),
                "course_id": _clean_text(row.get("course_id")),
                "course_name": _clean_text(row.get("course_name")),
                "section": _clean_text(row.get("section")),
                "room": _clean_text(row.get("room")),
                "teachers": split_items(row.get("teachers")),
                "groups": split_items(row.get("student_groups")),
                "course_owner": "",
            }
        )
    return events


def _all_courses_fixed_events(bundle: InputBundle) -> list[dict]:
    events: list[dict] = []
    for _, row in bundle.all_courses.iterrows():
        blocks = parse_fixed_time(row.get("fixed_time"))
        if not blocks:
            continue
        for day, start, end in blocks:
            events.append(
                {
                    "day": day,
                    "start": start,
                    "end": end,
                    "course_id": _clean_text(row.get("course_id")),
                    "course_name": _clean_text(row.get("course_name")),
                    "section": _clean_text(row.get("section")),
                    "room": _clean_text(row.get("room_name")),
                    "teachers": split_items(row.get("teachers")),
                    "groups": split_items(row.get("student_groups")),
                    "course_owner": _clean_text(row.get("course_owner")),
                }
            )
    return events


def _student_fixed_events(bundle: InputBundle) -> list[dict]:
    """Fixed courses for student views, enriched from all_courses when possible."""
    events: list[dict] = []

    all_course_rows: dict[str, list[pd.Series]] = {}
    for _, all_row in bundle.all_courses.iterrows():
        key = _course_id_key(all_row.get("course_id"))
        if key:
            all_course_rows.setdefault(key, []).append(all_row)

    for _, row in bundle.student_courses.iterrows():
        group = _clean_text(row.get("group_id"))
        if not group:
            continue
        blocks = parse_fixed_time(row.get("fixed_time"))
        if not blocks:
            continue

        matches = all_course_rows.get(_course_id_key(row.get("course_id")), [])
        for day, start, end in blocks:
            matched_row = None
            for candidate in matches:
                if (day, start, end) in parse_fixed_time(candidate.get("fixed_time")):
                    matched_row = candidate
                    break
            if matched_row is None and len(matches) == 1:
                matched_row = matches[0]

            course_name = _clean_text(row.get("course_name"))
            section = ""
            room = ""
            teachers: list[str] = []
            if matched_row is not None:
                course_name = _clean_text(matched_row.get("course_name")) or course_name
                section = _clean_text(matched_row.get("section"))
                room = _clean_text(matched_row.get("room_name"))
                teachers = split_items(matched_row.get("teachers"))

            events.append(
                {
                    "day": day,
                    "start": start,
                    "end": end,
                    "course_id": _clean_text(row.get("course_id")),
                    "course_name": course_name,
                    "section": section,
                    "room": room,
                    "teachers": teachers,
                    "groups": [group],
                    "course_owner": _clean_text(row.get("course_owner")),
                }
            )
    return events


def _event_signature(event: dict, entity: str) -> tuple:
    return (
        entity,
        event["day"],
        event["start"],
        event["end"],
        _course_id_key(event.get("course_id")),
    )


def _teacher_label(event: dict) -> str:
    lines = [
        f"{event['course_id']}{_section_text(event.get('section'))}",
        event.get("course_name", ""),
    ]
    if event.get("room"):
        lines.append(event["room"])
    elif event.get("groups"):
        lines.append(" / ".join(event["groups"]))
    if event.get("teachers"):
        lines.append(" ".join(event["teachers"]))
    return "\n".join(line for line in lines if line).strip()


def _student_label(event: dict) -> str:
    lines = [
        f"{event['course_id']}{_section_text(event.get('section'))}",
        event.get("course_name", ""),
    ]
    if event.get("room"):
        lines.append(event["room"])
    if event.get("teachers"):
        lines.append(" ".join(event["teachers"]))
    elif event.get("course_owner"):
        lines.append(event["course_owner"])
    return "\n".join(line for line in lines if line).strip()


def _room_label(event: dict) -> str:
    lines = [
        f"{event['course_id']}{_section_text(event.get('section'))}",
        event.get("course_name", ""),
    ]
    if event.get("teachers"):
        lines.append(" ".join(event["teachers"]))
    elif event.get("groups"):
        lines.append(" / ".join(event["groups"]))
    return "\n".join(line for line in lines if line).strip()


def _group_display_names(bundle: InputBundle) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for _, row in bundle.student_groups.iterrows():
        group_id = _clean_text(row.get("group_id"))
        if not group_id:
            continue
        major = _clean_text(row.get("major"))
        year = _clean_text(row.get("year"))
        if re.fullmatch(r"\d+\.0", year):
            year = year[:-2]
        mapping[group_id] = f"{major} ปี {year}".strip() if major and year else group_id
    return mapping


def _global_course_color_index(
    bundle: InputBundle,
    result: ScheduleResult,
) -> dict[str, int]:
    """Stable course colors shared by professor, student, and room exports."""
    keys: set[str] = set()

    for frame, course_col, section_col in [
        (bundle.all_courses, "course_id", "section"),
        (bundle.department_courses, "course_id", "section"),
        (result.assignments, "course_id", "section"),
    ]:
        if frame is None or frame.empty or course_col not in frame:
            continue
        for _, row in frame.iterrows():
            key = _event_key(row.get(course_col), row.get(section_col))
            if key:
                keys.add(key)

    # External fixed student courses may not carry a section in student_courses.
    if bundle.student_courses is not None and not bundle.student_courses.empty:
        for _, row in bundle.student_courses.iterrows():
            key = _event_key(row.get("course_id"), "")
            if key:
                keys.add(key)

    return {
        key: index % len(PALETTE)
        for index, key in enumerate(sorted(keys))
    }


def _build_entity_events(
    bundle: InputBundle,
    result: ScheduleResult,
    view: str,
) -> tuple[list[str], dict[str, list[dict]], dict[str, str]]:
    scheduled = _scheduled_events(result)
    fixed_all = _all_courses_fixed_events(bundle)
    student_fixed = _student_fixed_events(bundle)
    entity_events: dict[str, list[dict]] = {}
    display_names: dict[str, str] = {}

    if view == "teacher":
        entities = sorted(
            {
                teacher
                for event in scheduled + fixed_all
                for teacher in event.get("teachers", [])
                if teacher
            }
        )
        entity_events = {entity: [] for entity in entities}
        seen: set[tuple] = set()
        for event in scheduled + fixed_all:
            for teacher in event.get("teachers", []):
                if teacher not in entity_events:
                    continue
                signature = _event_signature(event, teacher)
                if signature in seen:
                    continue
                seen.add(signature)
                entity_events[teacher].append({**event, "label": _teacher_label(event)})
        display_names = {entity: entity for entity in entities}
        return entities, entity_events, display_names

    if view == "student":
        group_names = _group_display_names(bundle)
        entities = list(group_names)
        # Include any groups present in result even if the group table is incomplete.
        for event in scheduled + student_fixed:
            for group in event.get("groups", []):
                if group and group not in group_names:
                    group_names[group] = group
                    entities.append(group)
        entity_events = {entity: [] for entity in entities}
        seen = set()
        for event in scheduled + student_fixed:
            for group in event.get("groups", []):
                if group not in entity_events:
                    continue
                signature = _event_signature(event, group)
                if signature in seen:
                    continue
                seen.add(signature)
                entity_events[group].append({**event, "label": _student_label(event)})
        display_names = group_names
        return entities, entity_events, display_names

    if view == "room":
        entities = [
            room
            for room in bundle.rooms.get("room_name", pd.Series(dtype=object)).map(_clean_text)
            if room
        ]
        for event in scheduled + fixed_all:
            room = event.get("room", "")
            if room and room != "กำหนดภายหลัง" and room not in entities:
                entities.append(room)
        entity_events = {entity: [] for entity in entities}
        seen = set()
        for event in scheduled + fixed_all:
            room = event.get("room", "")
            if room not in entity_events:
                continue
            signature = _event_signature(event, room)
            if signature in seen:
                continue
            seen.add(signature)
            entity_events[room].append({**event, "label": _room_label(event)})
        display_names = {entity: entity for entity in entities}
        return entities, entity_events, display_names

    raise ValueError(f"Unknown timetable view: {view}")


def _write_timetable_workbook(
    bundle: InputBundle,
    result: ScheduleResult,
    *,
    view: str,
) -> bytes:
    entities, entity_events, display_names = _build_entity_events(bundle, result, view)
    output = BytesIO()

    # Keep course colors stable across professor, student, and room workbooks.
    course_color_index = _global_course_color_index(bundle, result)

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book
        workbook.set_properties(
            {
                "title": "SchEDU timetable",
                "comments": "Generated by SchEDU",
            }
        )

        border = 1
        base_format = workbook.add_format(
            {
                "border": border,
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
                "font_size": 10,
            }
        )
        header_format = workbook.add_format(
            {
                "border": border,
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
                "bg_color": HEADER_FILL,
                "bold": True,
                "font_size": 10,
            }
        )
        day_format = workbook.add_format(
            {
                "border": border,
                "align": "center",
                "valign": "vcenter",
                "bg_color": DAY_FILL,
                "bold": True,
                "font_size": 10,
            }
        )
        lunch_format = workbook.add_format(
            {
                "border": border,
                "align": "center",
                "valign": "vcenter",
                "bg_color": LUNCH_FILL,
                "bold": True,
                "font_size": 10,
            }
        )
        title_format = workbook.add_format(
            {
                "bold": True,
                "align": "center",
                "valign": "vcenter",
                "font_size": 22,
            }
        )
        event_formats = [
            workbook.add_format(
                {
                    "border": border,
                    "align": "center",
                    "valign": "vcenter",
                    "text_wrap": True,
                    "bg_color": color,
                    "font_size": 10,
                }
            )
            for color in PALETTE
        ]

        headers = _time_headers()
        used_names: set[str] = set()

        if not entities:
            worksheet = workbook.add_worksheet("ไม่มีตาราง")
            writer.sheets["ไม่มีตาราง"] = worksheet
            worksheet.write(0, 0, "ไม่พบข้อมูลสำหรับมุมมองนี้", base_format)
        else:
            for entity in entities:
                display = display_names.get(entity, entity)
                sheet_name = _sheet_name(display, used_names)
                worksheet = workbook.add_worksheet(sheet_name)
                writer.sheets[sheet_name] = worksheet

                title_rows = 1 if view == "room" else 0
                header_row = title_rows
                first_day_row = header_row + 1
                last_day_row = first_day_row + len(DAYS) - 1

                worksheet.hide_gridlines(2)
                worksheet.freeze_panes(first_day_row, 1)
                worksheet.set_landscape()
                worksheet.fit_to_pages(1, 1)
                worksheet.set_margins(0.25, 0.25, 0.4, 0.4)

                if view == "room":
                    worksheet.set_column(0, 0, 14)
                    worksheet.set_column(1, 19, 13)
                    worksheet.set_row(0, 42)
                    worksheet.merge_range(
                        0, 0, 0, 19, _room_title(display), title_format
                    )
                    worksheet.set_row(header_row, 42)
                    for row_index in range(first_day_row, last_day_row + 1):
                        worksheet.set_row(row_index, 105)
                else:
                    worksheet.set_column(0, 0, 16)
                    worksheet.set_column(1, 19, 25)
                    worksheet.set_row(header_row, 80)
                    for row_index in range(first_day_row, last_day_row + 1):
                        worksheet.set_row(row_index, 80)

                worksheet.write_blank(header_row, 0, None, header_format)
                for col_index, header in enumerate(headers, start=1):
                    worksheet.write(header_row, col_index, header, header_format)
                for day_offset, day_name in enumerate(DAYS):
                    worksheet.write(first_day_row + day_offset, 0, day_name, day_format)

                worksheet.merge_range(
                    first_day_row,
                    LUNCH_COLUMN,
                    last_day_row,
                    LUNCH_COLUMN,
                    "พักเที่ยง",
                    lunch_format,
                )

                # Pre-format blank cells so borders remain visible.
                for row_index in range(first_day_row, last_day_row + 1):
                    for col_index in list(range(1, LUNCH_COLUMN)) + list(
                        range(LUNCH_COLUMN + 1, 20)
                    ):
                        worksheet.write_blank(row_index, col_index, None, base_format)

                # Logical 5 x 18 grid. Multiple labels are retained if fixed inputs overlap.
                grid: list[list[list[str]]] = [
                    [[] for _ in range(len(TIMES))] for _ in range(len(DAYS))
                ]
                grid_keys: list[list[list[str]]] = [
                    [[] for _ in range(len(TIMES))] for _ in range(len(DAYS))
                ]

                for event in entity_events.get(entity, []):
                    label = event.get("label", "")
                    key = _event_key(event.get("course_id"), event.get("section"))
                    for slot in range(event["start"], min(event["end"], len(TIMES))):
                        if label and label not in grid[event["day"]][slot]:
                            grid[event["day"]][slot].append(label)
                            grid_keys[event["day"]][slot].append(key)

                for day in range(len(DAYS)):
                    slot = 0
                    while slot < len(TIMES):
                        labels = grid[day][slot]
                        if not labels:
                            slot += 1
                            continue

                        value = "\n\n".join(labels)
                        keys = grid_keys[day][slot]
                        color_key = keys[0] if keys else ""
                        end_slot = slot + 1
                        while (
                            end_slot < len(TIMES)
                            and grid[day][end_slot] == labels
                            and grid_keys[day][end_slot] == keys
                        ):
                            end_slot += 1

                        # Excel includes one extra Lunch column between slots 7 and 8.
                        excel_start = 1 + slot + (1 if slot >= 8 else 0)
                        final_slot = end_slot - 1
                        excel_end = 1 + final_slot + (1 if final_slot >= 8 else 0)
                        fmt = event_formats[course_color_index.get(color_key, 0)]
                        excel_row = first_day_row + day

                        if excel_end > excel_start:
                            worksheet.merge_range(
                                excel_row,
                                excel_start,
                                excel_row,
                                excel_end,
                                value,
                                fmt,
                            )
                        else:
                            worksheet.write(excel_row, excel_start, value, fmt)
                        slot = end_slot

    return output.getvalue()


def professor_timetable_excel_bytes(bundle: InputBundle, result: ScheduleResult) -> bytes:
    return _write_timetable_workbook(bundle, result, view="teacher")


def student_timetable_excel_bytes(bundle: InputBundle, result: ScheduleResult) -> bytes:
    return _write_timetable_workbook(bundle, result, view="student")


def room_timetable_excel_bytes(bundle: InputBundle, result: ScheduleResult) -> bytes:
    return _write_timetable_workbook(bundle, result, view="room")


def timetable_excel_files(bundle: InputBundle, result: ScheduleResult) -> dict[str, bytes]:
    return {
        "professor": professor_timetable_excel_bytes(bundle, result),
        "student": student_timetable_excel_bytes(bundle, result),
        "room": room_timetable_excel_bytes(bundle, result),
    }


def schedule_excel_bytes(
    bundle: InputBundle,
    result: ScheduleResult,
    parameters: ScheduleParameters,
) -> bytes:
    """Detailed solver export retained for diagnostics and backward compatibility."""
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        summary = pd.DataFrame(
            [
                ["solver_status", result.status],
                ["assigned_sessions", len(result.assignments)],
                ["unassigned_sessions", len(result.unassigned)],
                ["candidate_placements", result.candidate_count],
                ["average_preference", result.average_preference],
                ["wall_time_seconds", result.wall_time_seconds],
                ["max_teaching_hours_per_day", parameters.max_teaching_hours_per_day],
                ["max_study_hours_per_day", parameters.max_study_hours_per_day],
                ["max_course_hours_per_day", parameters.max_course_hours_per_day],
                [
                    "max_courses_per_teacher_per_day",
                    parameters.max_courses_per_teacher_per_day,
                ],
                ["avoid_consecutive_days", parameters.avoid_consecutive_days],
                ["strict_zero_preference", parameters.strict_zero_preference],
            ],
            columns=["metric", "value"],
        )
        frames = [
            ("summary", summary),
            (
                "schedule",
                result.assignments.drop(
                    columns=["day_index", "start_slot"], errors="ignore"
                ),
            ),
            ("unassigned", result.unassigned),
            ("options", result.option_summary),
            ("diagnostics", pd.DataFrame({"diagnostic": result.diagnostics})),
        ]
        for sheet_name, frame in frames:
            frame.to_excel(writer, sheet_name=sheet_name, index=False)

        workbook = writer.book
        header = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#171536",
                "border": 0,
                "align": "center",
                "valign": "vcenter",
            }
        )
        for sheet_name, frame in frames:
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes(1, 0)
            worksheet.hide_gridlines(2)
            worksheet.set_row(0, 24, header)
            for column_index, column in enumerate(frame.columns):
                width = min(
                    max(
                        len(str(column)) + 2,
                        frame[column].astype(str).str.len().max() + 2
                        if not frame.empty
                        else 12,
                    ),
                    38,
                )
                worksheet.set_column(column_index, column_index, width)
    return output.getvalue()
