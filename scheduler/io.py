from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from .time_utils import normalize_course_id, parse_capacity

CANONICAL_SHEETS = [
    "all_courses",
    "department_courses",
    "student_courses",
    "rooms",
    "student_groups",
]

REQUIRED_COLUMNS = {
    "all_courses": [
        "course_id",
        "course_name",
        "section",
        "capacity",
        "teachers",
        "fixed_time",
        "room_name",
        "teaching_mode",
    ],
    "department_courses": [
        "course_id",
        "course_name",
        "section",
        "capacity",
        "student_groups",
        "session_hours",
        "teachers",
        "room_type",
    ],
    "student_courses": [
        "group_id",
        "course_id",
        "course_name",
        "course_owner",
        "fixed_time",
    ],
    "rooms": [
        "room_id",
        "room_name",
        "capacity",
        "room_type",
        "graduate_only",
        "unavailable_time",
    ],
    "student_groups": [
        "group_id",
        "major",
        "year",
        "student_count",
        "max_study_hours_per_day",
    ],
}


@dataclass
class ValidationMessage:
    level: str
    sheet: str
    message: str


@dataclass
class InputBundle:
    all_courses: pd.DataFrame
    department_courses: pd.DataFrame
    student_courses: pd.DataFrame
    rooms: pd.DataFrame
    student_groups: pd.DataFrame
    preferences: dict[str, dict[tuple[int, int], float]] = field(default_factory=dict)
    messages: list[ValidationMessage] = field(default_factory=list)
    source_format: str = "canonical"

    @property
    def has_errors(self) -> bool:
        return any(message.level == "error" for message in self.messages)


def _source_for_pandas(source: str | Path | bytes | BinaryIO) -> str | Path | BytesIO | BinaryIO:
    return BytesIO(source) if isinstance(source, bytes) else source


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [str(column).strip().lower() for column in result.columns]
    return result.dropna(how="all").reset_index(drop=True)


def _legacy_to_canonical(sheets: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    teach = sheets["teach"].copy()
    study = sheets["study"].copy()
    student = sheets["student"].copy()
    room = sheets["room"].copy()

    all_courses = pd.DataFrame(
        {
            "course_id": teach.get("รหัส"),
            "course_name": teach.get("ชื่อวิชา"),
            "section": teach.get("กลุ่ม"),
            "credits": teach.get("หน่วยกิต"),
            "capacity": teach.get("จำนวน"),
            "semester": teach.get("ภาค"),
            "course_category": teach.get("ประเภท"),
            "student_groups": teach.get("สาขาวิชารวม", teach.get("สาขาวิชา")),
            "teachers": teach.get("ชื่อผู้สอน"),
            "fixed_time": teach.get("วัน-เวลา สอน"),
            "room_name": teach.get("ห้อง", ""),
            "teaching_mode": teach.get("การสอน", "lecture"),
            "note": teach.get("หมายเหตุ", ""),
        }
    )
    department_mask = teach.get("ลำดับสอนในสาขา", pd.Series(index=teach.index)).notna()
    if "ประเภท" in teach:
        department_mask |= teach["ประเภท"].astype(str).isin(["บังคับ", "เลือก"])
    department = teach.loc[department_mask]
    department_courses = pd.DataFrame(
        {
            "course_id": department.get("รหัส"),
            "course_name": department.get("ชื่อวิชา"),
            "section": department.get("กลุ่ม"),
            "credits": department.get("หน่วยกิต"),
            "capacity": department.get("จำนวน"),
            "course_category": department.get("ประเภท"),
            "student_groups": department.get(
                "สาขาวิชารวม", department.get("สาขาวิชา")
            ),
            "session_hours": department.get("เวลาสอน"),
            "teachers": department.get("ชื่อผู้สอน"),
            "room_type": department.get("การสอน", "lecture"),
            "fixed_time": department.get("วัน-เวลา สอน", ""),
            "note": department.get("หมายเหตุ", ""),
        }
    )
    student_courses = pd.DataFrame(
        {
            "group_id": study.apply(
                lambda row: f"{row.get('สาขา', '')} ปี {row.get('ปี', '')}", axis=1
            ),
            "major": study.get("สาขา"),
            "year": study.get("ปี"),
            "course_id": study.get("รหัสวิชา"),
            "course_name": study.get("ชื่อวิชา"),
            "credits": study.get("จำนวนหน่วยกิต"),
            "student_count": study.get("จำนวน"),
            "course_owner": study.get("สาขาของวิชา"),
            "fixed_time": study.get("เวลา"),
            "note": "",
        }
    )
    actual_count = student.get("จำนวน0", student.get("จำนวน"))
    student_groups = pd.DataFrame(
        {
            "group_id": student.apply(
                lambda row: f"{row.get('สาขา', '')} ปี {row.get('ชั้นปี', '')}",
                axis=1,
            ),
            "major": student.get("สาขา"),
            "year": student.get("ชั้นปี"),
            "student_count": actual_count,
            "max_study_hours_per_day": 8,
            "note": "",
        }
    )
    rooms = pd.DataFrame(
        {
            "room_id": room.get("id"),
            "room_name": room.get("name"),
            "capacity": room.get("capacity", room.get("capacity0")),
            "room_type": room.get("type"),
            "graduate_only": room.get("master_degree", "no"),
            "unavailable_time": "",
        }
    )
    return {
        "all_courses": all_courses,
        "department_courses": department_courses,
        "student_courses": student_courses,
        "rooms": rooms,
        "student_groups": student_groups,
    }


def load_main_workbook(source: str | Path | bytes | BinaryIO) -> InputBundle:
    raw_sheets = pd.read_excel(
        _source_for_pandas(source), sheet_name=None, engine="openpyxl"
    )
    sheets = {str(name).strip().lower(): frame for name, frame in raw_sheets.items()}
    source_format = "canonical"

    if all(name in sheets for name in CANONICAL_SHEETS):
        canonical = {name: _clean_frame(sheets[name]) for name in CANONICAL_SHEETS}
    elif all(name in sheets for name in ["teach", "study", "student", "room"]):
        canonical = {
            name: _clean_frame(frame)
            for name, frame in _legacy_to_canonical(sheets).items()
        }
        source_format = "legacy"
    else:
        empty = pd.DataFrame()
        bundle = InputBundle(empty, empty, empty, empty, empty)
        bundle.messages.append(
            ValidationMessage(
                "error",
                "workbook",
                "ไม่พบชีตหลักทั้ง 5 ชีต และไม่ใช่รูปแบบเดิม teach/study/student/room",
            )
        )
        return bundle

    bundle = InputBundle(
        all_courses=canonical["all_courses"],
        department_courses=canonical["department_courses"],
        student_courses=canonical["student_courses"],
        rooms=canonical["rooms"],
        student_groups=canonical["student_groups"],
        source_format=source_format,
    )
    _validate(bundle)
    return bundle


def _validate(bundle: InputBundle) -> None:
    for sheet_name in CANONICAL_SHEETS:
        frame = getattr(bundle, sheet_name)
        missing = [
            column for column in REQUIRED_COLUMNS[sheet_name] if column not in frame
        ]
        if missing:
            bundle.messages.append(
                ValidationMessage(
                    "error",
                    sheet_name,
                    f"ขาดคอลัมน์: {', '.join(missing)}",
                )
            )

    if bundle.has_errors:
        return

    for frame in [bundle.all_courses, bundle.department_courses, bundle.student_courses]:
        frame["course_id"] = frame["course_id"].fillna("").astype(str).str.strip()
        frame["_course_key"] = frame["course_id"].map(normalize_course_id)

    bundle.rooms["capacity"] = bundle.rooms["capacity"].map(parse_capacity)
    bundle.department_courses["capacity"] = bundle.department_courses[
        "capacity"
    ].map(parse_capacity)
    bundle.student_groups["student_count"] = bundle.student_groups[
        "student_count"
    ].map(parse_capacity)

    duplicate_rooms = bundle.rooms["room_name"].astype(str).duplicated(keep=False)
    if duplicate_rooms.any():
        bundle.messages.append(
            ValidationMessage("error", "rooms", "พบชื่อห้องซ้ำ")
        )
    if (bundle.rooms["capacity"] <= 0).any():
        bundle.messages.append(
            ValidationMessage("error", "rooms", "ความจุห้องต้องมากกว่า 0")
        )
    if (bundle.department_courses["capacity"] <= 0).any():
        bundle.messages.append(
            ValidationMessage(
                "error", "department_courses", "จำนวนนักศึกษาต้องมากกว่า 0"
            )
        )

    session_values = bundle.department_courses["session_hours"].apply(
        lambda value: (
            [float(value)]
            if isinstance(value, (int, float)) and pd.notna(value)
            else [
                float(item)
                for item in re.findall(r"\d+(?:\.\d+)?", str(value or ""))
            ]
        )
    )
    missing_sessions = session_values.apply(len).eq(0)
    if missing_sessions.any():
        bundle.messages.append(
            ValidationMessage(
                "error",
                "department_courses",
                "ต้องระบุ session_hours ให้ครบทุกวิชา",
            )
        )
    unequal_sessions = session_values.apply(
        lambda values: bool(values)
        and (
            any(value <= 0 for value in values)
            or any(abs(value - values[0]) > 1e-9 for value in values[1:])
        )
    )
    if unequal_sessions.any():
        bundle.messages.append(
            ValidationMessage(
                "error",
                "department_courses",
                "session_hours ของแต่ละวิชาต้องเป็นค่าบวกและยาวเท่ากันทุกครั้ง",
            )
        )

    missing_course_groups = (
        bundle.department_courses["student_groups"].fillna("").astype(str).str.strip()
        == ""
    )
    if missing_course_groups.any():
        bundle.messages.append(
            ValidationMessage(
                "error",
                "department_courses",
                "ต้องระบุกลุ่มนักศึกษาทุกสาขา/ชั้นปีที่จะเรียนแต่ละวิชา",
            )
        )

    known_groups = set(bundle.student_groups["group_id"].astype(str).str.strip())
    course_groups = {
        group.strip()
        for value in bundle.department_courses["student_groups"].fillna("")
        for group in str(value).split("/")
        if group.strip()
    }
    unknown = sorted(course_groups - known_groups)
    if unknown:
        bundle.messages.append(
            ValidationMessage(
                "warning",
                "department_courses",
                f"กลุ่มที่ไม่มีใน student_groups: {', '.join(unknown[:8])}"
                + (" …" if len(unknown) > 8 else ""),
            )
        )

    bundle.messages.append(
        ValidationMessage(
            "success",
            "workbook",
            f"อ่านข้อมูล {len(bundle.department_courses):,} รายวิชา "
            f"{len(bundle.rooms):,} ห้อง และ {len(bundle.student_groups):,} กลุ่ม",
        )
    )


def load_preferences(source: str | Path | bytes | BinaryIO) -> dict[str, dict[tuple[int, int], float]]:
    sheets = pd.read_excel(
        _source_for_pandas(source), sheet_name=None, header=None, engine="openpyxl"
    )
    preferences: dict[str, dict[tuple[int, int], float]] = {}
    for sheet_name, frame in sheets.items():
        monday_rows = frame.index[
            frame.apply(
                lambda row: row.astype(str).str.strip().eq("จันทร์").any(), axis=1
            )
        ].tolist()
        if not monday_rows:
            continue
        start = monday_rows[0]
        teacher = (
            str(sheet_name).split("อ.", 1)[-1].strip()
            if "อ." in str(sheet_name)
            else str(sheet_name).strip()
        )
        preferences[teacher] = {}
        for day in range(5):
            row = frame.iloc[start + day]
            slot = 0
            for column in range(1, min(frame.shape[1], 20)):
                if column == 9:
                    continue
                value = pd.to_numeric(row.iloc[column], errors="coerce")
                if pd.notna(value) and slot < 18:
                    preferences[teacher][(day, slot)] = float(value)
                slot += 1
    return preferences
